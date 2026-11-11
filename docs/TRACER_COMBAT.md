# Tracer combat lane (0003)

Target: Overwatch 1.74.0.0.104319, hero `02E0000000000003`, body
`0400000000000001`. **OPEN: full hero combat is not accepted.**

Latest uncommitted source includes Winston sphere interception, verified
Tracer/McCree first-person offsets, shared activation-baked `spread_ramp` and the
authored Pulse vertical launch bias. The former incoming torso regression
now passes with 20 damage and a body-hit notice. A separate standing head aim
produces 40 damage and a critical notice, and a real wall blocks it.
The eight original Pulse failures pass
with the authored +0.2 direction adjustment and the existing fixture distances.
The direction calculation matches 160 native samples. Animated hardpoint and
aimID transforms remain open.

The roster probes exercise all 32 default bodies. Tracer pistol and Pulse
damage resolve against each, and 30 default incoming primary cases produce
damage and attribution. Mercy's selected pistol adds one case. The Winston
case uses 00FE's active search and damage states, authored radius and amount,
and the reviewed lane 0009 cone and frame policy. Its eye-origin query,
half-angle interpretation, beam armour and shield rules are reconstructed.
Brigitte now hits in the same 5 m probe using actual 1248 flail and combo
states with lane 0195's reconstructed sweep volume. Zarya's actual 0241
beam consumes the simple TrackRay results and deals continuous damage.
Symmetra's actual 157C beam deals damage and advances through all three
authored charge rates. Reinhardt's 0048 hammer deals 85 damage after the
authored windup at 3 m using lane 0007's reconstructed sweep. The 5 m
default probe and the separate 3 m hammer scenario are reported independently.
D.Va's default 040A pilot now fires three 02B8/10 Light Gun projectiles at 5 m
for 42 total damage. The direct component43 query uses live Match body owners
with the native-backed scalar semantics reviewed in lane 007A at 190bc7c.
Explicit 0407 selection with lane 007A's read-only graph and mover supplements
executes both directions at 2 m. Fusion Cannons deliver eleven independent
2-damage pellets, and Tracer's two bullets remove six points of mech armour.
A separate Micro Missiles burst delivers 126 damage, rejects a cooldown press,
and can kill Tracer on reuse. Actual Recall blocks a Fusion Cannon volley.
Actual 02CC Defense Matrix commands intercept both Pulse Pistol bullets and
Pulse Bomb before contact under reconstructed box and target-tag admission.
Matrix uses the aim and eye for the impact pose, with the authored box
dimensions and resource graph. Pulse can sample a complete observed batch. Earlier body and world contacts retain priority, and the
hitscan path also orders Matrix against Winston's sphere. Pulse absorption
retires the flight and tracked projectile without damage or explosion.
The model manifest records the explicit projectile tag catalog.

Default mech possession, transitions, native pellet geometry, the full native
Matrix filter and attachment, and historical Matrix poses for other attacks
stay open.
Seven shared spread and ten actual Tracer spread methods pass. A new actual
Tracer stored-state replay verified the baked ramp and retained shot angles on
the earlier source.
Eleven barrier and four eye/cover methods pass. The two earlier Winston actor
replay cases passed on the pre-ramp layout and are historical evidence.
Full server-group verification on this source remains open. Native random seed,
animated muzzle and critical-region fidelity remain unaccepted. Full64-bit
character IDs distinguish hardpoint3 from bone3; bind pose alone does not prove
animated world emission. See external typed-hardpoint evidence.
Five selected actual NPC attacks now damage Tracer and pause during Recall.
Native Assassin AI/controller/clock/animation, reflection admission/geometry/
replication and the complete defense/status/economy matrix remain open.
Original-client acceptance is separate and pending. No new own commit or patch
is made until the complete static goal passes. See [Assassin evidence](TRACER_ASSASSIN.txt).

The following shared mover update now consumes input properties17/18/19.
Seven actual-resource Tracer fixtures pass, including natural scope expiry and
next-pose restoration. Two further methods guard actual empty-magazine Recall
and held-primary recovery after the shared notification fixes; see
[notification scope](TRACER_NOTIFICATION_LIFETIME.txt).
See [movement scope](TRACER_MOVEMENT_MODS.txt). Native attack/status dispatch is
still open. The actual Winston00FA primary-interception failure is repaired;
Actual Pulse inputs now cover the selected bubble cases below.
Other ballistic, native filter, pet and wire interactions remain open. See
[barrier/eye scope](TRACER_WINSTON_BARRIER.txt). Pulse explicitly names hardpoint3;
using the corrected eye as a muzzle still lacks the native pose/aim services.

## Verified server change

The actual pistol graph 0016, state8, reports 20 shots/second, two projectiles per
shot, six damage/projectile and two ammunition consumed per shot. The old combat
consumer resolved only one bullet per report. It now resolves each hitscan bullet
separately so armour mitigation applies per bullet. An unobstructed close-range
40-round magazine deals 240 body damage in the controlled fixture, versus 120
before the count fix. The later bloom/cone follow-up is described below.

Tracer now has count-based bloom and individually sampled rays; native random
table correspondence, muzzle origins, critical defaults and animated hitboxes
remain open. Other heroes'
multi-projectile hitscan reports also use the count; their complete weapon
behavior is not accepted by this patch. Ballistic projectile counts are unchanged.

## Acceptance matrix

The lane now has an active full static-acceptance goal. Its scope also includes
resource conditions, duration/end/cancel, cooldown rejection/reuse and ultimate
charge lifecycle. Passing selected fixtures below does not complete that goal.

Blink's actual A807207A action now calls a narrowly scoped body service. The
resource supplies the 7.5-metre distance and the existing graph owns three charges
with three-second replenishment. Horizontal throttle selection, clipping with a
full capsule sweep and preserving momentum are explicitly reconstructed policies.
The same-frame movement snapshot is replaced after the action so owner corrections
carry the displaced pose. The extracted Practice Range collision cache also
verifies that the balcony railing clips Blink, keeps Tracer on the balcony
and consumes one charge. Native step-up/edge behavior and control interactions
are still open. The callback only handles the actual live, posed Tracer action;
other heroes/graphs retain their previous unsupported behavior.

Recall uses body-local posed history and the actual001A warmup/playback states.
The resource's fade-in0.375 seconds completes the missing native54FEB21C gate;
41B3FC2B playback uses its authored0.9 seconds. These completion policies are
reconstructions. Successful playback restores the three-second-old position and
beneficial health, refills the authored weapon capacity, and lets the existing
graph write its12-second cooldown. Translation/fire/Blink are excluded during
Recall, as are incoming target/damage resolution. Cancelled casts spend cooldown
without restoring health. History/timers cannot modify replacement bodies.
Cleanse, exact native event timing and the full cross-hero controls remain open.

Pulse Bomb now has a separate authoritative physics/fuse service. Its damage
query reads the verified015D.130 default350 rather than returning null; actual
mode0025 takes the graph's450 branch. The projectile uses authored speed15,
gravity30, sphere radius0.2, maximum lifetime5, contact damage5 and explosion
rings2m/full to4m/20%. F969FA7A's value1 is interpreted as an impact fuse;
that field interpretation and sticking/attachment/Recall detachment/LOS policies
are explicitly reconstructed, not native instruction proofs. Sphere flight,
fuse, follow, blast occlusion, self damage and source retirement execute on the
world clock. A thrown bomb survives owner death; unreleased windup cancels on
death/ability stop. Self damage gives no damage/kill credit.

Two selective resources were read from the original104319 CASC and verified
against its manifest before XML inspection (outside Git, lane pulse-resources):
015D.130 has A0E5F3ED.m_3E5360A1=350, SHA256
b9e534c209c06b39da264bc6f6bc78a71469e9ccd2baf7d73bf1a002a54199b2.
The projectile's0C1B graph was also verified; native effects remain client-pending.

S = selected server fixture passed; O = open; F = executed missing behavior.
Original-client results remain open.
NPC columns mean the specific requested class, not a training-bot substitute.
For all five bodies, the reload fixture spends the magazine on missed shots,
checks empty-fire rejection, and observes a refill before firing recovers.
The first recovered pair deals 12 damage and grants 12 charge. The current
Pulse fixtures supersede the earlier contact and attachment failures in this
table. They observe the stuck target and armed fuse before blast damage.
The Recall fixture spends ammo and takes an actual attack from each NPC,
then stops manual control before Blinking and rewinding. It verifies the
restored position, health and ammo, cooldown rejection and both damage
directions after restarting combat. A second attack near cooldown expiry
wounds Tracer at a new position. An early Recall press leaves her position,
health and ammo unchanged. A fresh press at zero rewinds to the new
position, restores health and reloads the pistols without adding charge.
One frame before the first NPC impact, Recall already protects
Tracer, including from an otherwise lethal hit. On the impact
frame the attack resolves first. A surviving Tracer can rewind
the damage, except while the Assassin pin is active. Stopping
that pin allows a fresh press to restore the damage. Lethal
same-frame hits prevent Recall and retain the NPC's kill credit.
Both Blink inputs cross each NPC capsule
without contact damage or charge gain. The first cast lands 7.5 metres past
the starting position and both sides can hit from the new positions. The
second cast uses a restored charge after the manual attack stops. A static
wall before each NPC clips Blink at capsule clearance and still consumes
one charge. Holding the input through cover removal does not spend another
charge. A fresh press travels the full distance past the NPC.

The static host processes the NPC attack before a Blink command
on the same frame. Projectile and hitscan damage still applies,
and the Assassin's pin prevents Blink without spending a charge.
One frame earlier, a 7.5 m sidestep evades the Enforcer and Sniper
projectiles and the Assassin's first contact. These attackers
later hit at the new position. Trooper and Heavy Assault aim
their first rays at the new position and still hit.

| Action | Enemy player | Trooper | Enforcer | Sniper | Assassin | Heavy Assault | Original client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pulse Pistols body damage | S | S | S | S | S | S | O |
| Reload and subsequent fire | S | S | S | S | S | S | O |
| Pulse Pistol critical damage and attribution | S (Tracer bind pose) | S (height) | S (height) | S (height) | S (height) | S (height) | O |
| Native critical defaults and hit-region fidelity | O | O | O | O | O | O | O |
| Selected bloom / cone / individual wall rays | S | S | S | S | S | S | O |
| Native RNG / dual muzzle / full spread fidelity | O | O | O | O | O | O | O |
| Miss / range exclusion / far falloff floor | S | S | S | S | S | S | O |
| Quick melee | S | S | S | S | S | S | O |
| Blink through actors / displacement / charges | S | S | S | S | S | S | O |
| Blink static geometry clipping in matchup fixtures | S | S | S | S | S | S | O |
| Blink one frame before or on first NPC impact | N/A | S (re-aim) | S (evade) | S (evade) | S (evade / pin) | S (re-aim) | O |
| Blink complete control / cancel / cross-hero matrix | O | O | O | O | O | O | O |
| Recall position / health / ammo / cooldown | S | S (manual) | S (manual) | S (manual) | S (manual) | S (manual) | O |
| Recall cleanse / complete cross-hero controls | O | O | O | O | O | O | O |
| Recall selected invulnerability / targeting | S | O | O | O | O | O | O |
| Pulse Bomb selected throw / stick / fuse / explosion | S | S | S | S | S | S | O |
| Pulse Bomb static cover / team / blast falloff | S | S | S | S | S | S | O |
| Native Pulse attachment and pose fidelity | O | O | O | O | O | O | O |
| Ultimate spending / windup / end / cancel | S | O | O | O | O | O | O |
| Two earned lethal Pulse Bomb cycles from zero | S (Soldier: 76) | S | S | S | S | S | O |
| Actual lethal attack before and after Pulse release | S (Soldier rifle / Helix) | S | S | S | S | S | O |
| Ultimate selected passive / damage gain | S | S | S | S | S | S | O |
| Ultimate full native eligibility / long skipped-frame recovery | O | O | O | O | O | O | O |
| Incoming primary fire | S (standing bind-region hitscan) | S (manual) | S (manual) | S (manual) | S (manual) | S (manual) | O |
| Lethal NPC attack / single death notice / normal respawn | N/A | S (manual) | S (manual) | S (manual) | S (manual) | S (either ray) | O |
| Recall protection from actual NPC primary | S | S (manual) | S (manual) | S (manual) | S (manual) | S (manual) | O |
| Recall before or on first NPC impact, including lethal hits | N/A | S | S | S | S (pin lock) | S | O |
| Static-wall melee exclusion | S (Soldier, Sigma) | S | S | S | S | S | O |
| Tracer melee aim / range / team gates and release / reuse | S (Soldier) | S | S | S | S | S | O |
| Incoming Flashbang: selected expiry / interrupt / Recall | S (McCree) | O | O | O | O | O | O |
| Damage to Flashbang-controlled targets | S (Soldier) | S | S | S | S | S | O |
| Allied Flashbang interrupts NPC attacks | O | S (manual) | S (manual) | S (manual) | S (live pin) | S (manual) | O |
| Allied Winston dome vs NPC attacks | O | S (first volley) | S | S | S (manual pin crosses) | S (first volley) | O |
| Reinhardt / Sigma / Orisa mesh vs NPC attacks | O | S (first ray) | S | S | S (manual pin crosses) | S (volley and break) | O |
| Genji Deflect / D.Va Matrix / Sigma Grasp vs NPC attacks | O | S (first volley) | S | S | S (manual pin persists) | S (first volley) | O |
| Projected barrier vs NPC attacks | O | S (first ray and break) | S | S | S (damage only) | S (volley and break) | O |
| Sleep Dart breaks active pin / Tracer pistol wakes | O | O | O | O | S | O | O |
| Tracer attack shortens sleep after wake grace | S (Soldier) | S | S | S | S | S | O |
| Pulse blast during and after wake grace | S (Soldier) | S | S | S | S | S | O |
| Ana grenade anti-heal / boost with Harmony | S (anti-heal) | S | S | S | S | S | O |
| Released Pulse and pin on the same lethal frame | O | O | O | O | S (static, including gaps) | O | O |
| Controlled death / retained NPC corpse | S | S | S | S | S | S | O |
| Other incoming abilities / full controls | O | O | O | O | O | O | O |
| Outgoing death / notices | S | S | S | S | S | S | O |
| Manual NPC corpse retention | N/A | S | S | S | S | S | O |

Allied McCree Flashbang stuns an opposing Soldier while leaving
Tracer able to attack. Pulse Pistols, quick melee and Pulse contact
each damage the stunned target. Lethal hits from each attack clear
the stun and credit Tracer. Nonlethal hits preserve the stun deadline.
Soldier cannot fire during the stun and can fire on a fresh press
after expiry. A nonlethal Pulse contact leaves enough time for that
return shot before the attached bomb detonates and kills Soldier.

An allied Ana Sleep Dart puts each of the five NPC classes to sleep.
An early pistol pair deals damage without moving the deadline.
After the wake grace period, pistols, quick melee or Pulse contact
set the remaining sleep to the half-second wake interval. Each
NPC resumes attack input on the new deadline. After pistol and
melee follow-ups, the NPC hits Tracer again. After Pulse contact,
the NPC wakes while the bomb is still attached and live.

Pulse splash tests place the sleeping NPC 3.875 m from the bomb
at its capsule surface, outside the throw path. The 87.5-point
blast preserves the original sleep deadline during wake grace.
After grace it starts the half-second wake interval. In both
surviving cases the NPC resumes attack input on the deadline and
hits Tracer. A lethal splash clears sleep immediately and credits
Tracer without a separate contact hit on the sleeper.

Allied Ana can also sleep an opposing Soldier player. Early pistol
damage preserves the sleep deadline. Late pistol, melee and Pulse
contact shorten it, and a lethal hit clears both sleep fields.
Pulse splash reproduces the grace, shortened and lethal outcomes
against a separate Soldier. His rifle cannot fire on the last
sleep frame and hits Tracer on the wake frame.

The additional training-NPC fixture kills with a paired shot and retains the body
past the normal respawn deadline. It disables NPC script delivery; it proves only
damage/counting/lifetime integration, not one of the five PVE classes or its AI.

Tracer and Soldier: 76 exchange quick melee inputs in both directions.
Facing away, standing out of reach, a two-sided static wall and allied
teams each prevent damage, hit notices and charge. Removing the gate
while the original input is still held leaves both targets unharmed.
A fresh press deals 30 per side. A press during cooldown deals no
immediate damage. Holding that press delivers one delayed swing.
Releasing an early press suppresses the delayed swing, and a fresh
press after cooldown hits again.

Tracer's quick melee follows the same sequence against each of the
five extracted NPC capsules. Facing away, standing at 5 m, placing
a wall in reach or joining the NPC's team prevents damage. After
each gate clears, only a new press hits. Each accepted swing removes
30 from the NPC's active pools and credits exactly 30 damage and charge.

At 25 m, an actual magazine fires 20 pairs against each of the five
extracted NPC capsules. Both opening bullets hit. Later pairs register
zero, one or two hits. Actual damage matches the stat and ultimate
credit. At 4 m a two-sided wall splits the fourth pair's full-bloom
rays. Each NPC takes one six-point hit and Tracer gains six charge
while spending two rounds.

Actual pistol damage against fresh bodies of each NPC class funds
two lethal Pulse Bombs from zero with passive charge disabled.
The meter follows actual loss and caps at the cast cost. Each
cast spends the meter, sticks and kills after the fuse. A second
cycle begins from zero. A miss or allied aim at six charge short
gives no damage, hit notice or charge, and the cast is rejected.

In the player fixture, one Soldier: 76 respawns on the normal timer
after each death. Pistol damage and manual reloads fund two lethal
bombs from zero with passive charge disabled. The meter tracks
actual health loss up to the cost, and each attached bomb kills
after the fuse without generating charge.

A wall on the throw path prevents attachment and damage to each of
the five NPC classes. An allied NPC also takes neither contact nor
blast damage. For each class, a separate throw sticks to a hostile
NPC and detonates against nearby bodies. Uncovered hostile bodies
take 175 at 3.25 m and 87.5 at 3.875 m from the capsule surface.
A target at 4.125 m, a target behind a static wall and an ally in
range take zero. The anchor takes its separate contact and blast
hits. The damage stat counts actual pool loss and Pulse adds no
ultimate charge.

The opening Pulse Pistol pair against a standing Tracer deals two
12-point critical hits through the model's bind head sphere.
A torso pair and a head-height pair outside that sphere each deal
two six-point body hits. Static cover and allied team checks block
both head rays. The five NPC capsules use the current height
classifier. Both high rays deal 12 and both body rays deal six.
A wounded target in each fixture takes 12 then five, with the
second notice marked critical and killing. Damage stats and charge
follow actual health loss. NPC anatomical regions, animated player
geometry and the native multiplier remain open.

Each manual NPC delivers a lethal attack during Pulse Bomb windup
and in a separate cast after release. Death during windup leaves
the spent cast without a projectile. A released bomb survives
Tracer's death, sticks and kills the attacking NPC after the fuse.
The kill still credits the original Tracer body.

Soldier: 76's rifle and Helix Rockets verify both outcomes in PvP.
A Helix fired before the cast reaches Tracer during windup and
prevents release. After release, either weapon kills Tracer while
the bomb is still in flight. The bomb deals five contact damage to
Soldier, kills him after the fuse and grants Tracer an elimination.

The parent has since provided a separate five-class catalog/script bundle and
NPC APIs (reviewed shared commitsc2d3ae1/a15d554, cherry-picked into this clone).
Five additional opt-in tests execute25 class subcases using actual definitions,
base pools, scripts and XML-extracted mover capsules mounted only in fixture
memory. Normal runtime lacks these mover entries and is unchanged. Transport is
an explicit adapter; production mounting, AI and reverse attacks remain open.
The catalog records unapplied difficulty/conditional pools and is not a claim
about retail difficulty totals. Variable221 routes to shields, not armour.

| Cross-hero behavior | Evidence / remaining work |
| --- | --- |
| Armour | Two six-damage bullets remove six armour, not seven; fixture passes. |
| Health-pool shields | Five shield points are consumed before seven health; fixture passes. |
| Friendly fire | Same-team player remains undamaged in a non-FFA fixture. |
| Walls | Both bullets stop when the collision ray reports a nearer surface. |
| Death | First lethal bullet gives one kill/death notice; second gives no extra damage. |
| Barriers | Selected Winston and Reinhardt primary, melee and Pulse cases pass. Other barriers, native filters and combined defenses remain open. |
| Deflect / absorption | Selected17 hitscan/melee and14 physical Pulse fixtures pass; native reflection and Matrix absorption remain open. |
| Invulnerability / status / control | Selected Recall/Flashbang fixtures pass; complete native status gates remain OPEN. |
| Ally healing | No healing or team semantics changed by this patch. |

## Reproduction and results

Run from the lane's `source` checkout with `PYTHONPATH` pointing to that checkout
and `ORPHEUS_RUNTIME_DIR=D:\overwatch 1.7.4\runtime-104319`. Reuse the original
checkout's `.venv\Scripts\python.exe` without writing to its source or the runtime.

```
python -B -m unittest discover -s tests -p test_game_tracer.py -v
python -B -m unittest discover -s tests -p test_game_tracer_pulse.py -v
python -B -m unittest discover -s tests -p test_game_tracer_spread.py -v
python -B -m unittest discover -s tests -p test_game_combat.py
python -B -m unittest discover -s tests -p test_game_projectiles.py
python -B -m unittest discover -s tests -p test_script_driver.py
python -B -m unittest discover -s tests -p test_game_tracer_npcs.py
python -B -m unittest discover -s tests -p test_game_npc.py
```

The32 Tracer tests cover actual body graphs and player command processing:
paired damage, per-bullet armour, shields, lethal first bullet, whole magazine,
wall occlusion, team filtering, explicit reload, melee, incoming Soldier fire,
and retained training-NPC death. Added checks exercise missed/out-of-range shots,
far falloff, empty-magazine rejection and eight Blink behaviors: displacement and
same-frame correction, directional/diagonal input, three-charge exhaustion and
recovery, actual wall collision, death rejection, secondary activation and enemy
pass-through, held/duplicate input rejection, and recharge cap.

Nine Recall tests add history/health/ammo, same-frame pose, both-phase protection,
motion/fire/Blink exclusion, cooldown reuse, cancellation, beneficial healing and
body retirement. Nineteen Pulse Bomb tests add charge rejection/spending, actual
release delay, contact/follow/fuse, misses, friendly targets, wall occlusion,
self damage, death/windup/retirement, Recall detachment, bounded lifetime,
scheduler gaps,350/450 mode branches, separate armour/shield stages and210
damage at3m surface distance.
Twenty-eight combat,37 projectile,27 driver, five actual-class NPC and six shared
NPC/catalog tests also pass. Eleven ultimate, two live Recall storage,12 script
ability,23 VM snapshot and14 server snapshot tests bring the total to216 distinct
tests;25 class subcases remain within five NPC tests.
Ruff and `git diff --check` pass. NPC fixtures require
`ORPHEUS_PVE_NPC_DATA_DIR` and `ORPHEUS_NPC_XML_DIR`; a skip is not acceptance.

The original pre-fix Blink probe found no movement. The lane-local executable
`run_static_audit.py` records Recall and Pulse Bomb execution in
`STATIC_ACCEPTANCE.json` and intentionally exits1 while the full matrix is open.
The earlier Recall position/health/cooldown failures now pass: the actual movement
and70-damage scenario restores health150, historical position and starts cooldown.
A bomb charged to the actual1260 cost now explodes and kills the unarmoured200-HP
target; its earlier195-HP/no-explosion result is retired. Full static acceptance
still fails its unverified rows; an empty executed-failure list is not completion.
The earlier900-point
ultimate probe was undercharged and must not be treated as a full-charge test.
The known skill
graphs are Recall001A, Blink0024 and Pulse Bomb0034. No general State fallback
or fault-free execution is treated as a gameplay implementation.

## Authored charge graph and snapshot follow-up

Graph0B8B owns normal live Tracer passive gain: its one-second Wait adds5 points,
with authored capability/busy gates, multipliers and cost clamping. Damage uses
actual message0012 and its original filters/actions, supplying resolved pool loss,
victim entity, authored damage tags and an empty exclusion-record tuple. Hit
and melee definitions retain original tags. Pulse contact/blast tags exclude
ultimate damage gain; Recall self healing grants none. Five actual NPC classes
each grant12 from paired primary damage. Training tag02ED is a classification
alternative, not a half-gain policy. Special-target03C3/011E filters remain open.

Live-owner eligibility, actor mask1, hostile teams and the empty-record fold are
explicit reconstructed host services scoped to Tracer0B8B, not full native filter
emulation. Debug charge-time overrides remain available; a non-hosted VM retains
the debug policy. Earned charge survives player death/respawn but resets on an
explicit hero change; this retention policy is reconstructed.

A20-second probe skipping command and world updates grants25, not100, points:
the existing256-frame driver catch-up cap collapses overdue recurring Waits.
Duplicate commands grant no more. Full elapsed-time recovery is OPEN.
Command-only skips of 63, 126 and 256 frames match contiguous input.
A command-only 1250-frame probe gives 95 points with contiguous commands
and 20 after a jump, with no additional credit on duplicate input. Its
75-point deficit matches the earlier world-and-command probe. The normal
Player.take_commands path has the same deficit even with uninterrupted
world ticks. The body waits for the next command, then releases held fire
before replaying the retained frames. A 256-frame gap leaves ammo unchanged
and matches the contiguous charge ticks. At 1250 frames, resumed charge is
25 versus 100, and the next two charge ticks are each 23 frames late. See
TRACER_PASSIVE_HOSTED_GAP_CURRENT_PROBE.json for both traces. An offline
probe releases held input first and then replays the omitted command frames
in chronological chunks through the same body entry point. At 1250 and
2500 frames it matches the contiguous 100 and 200 charge totals, retains
38 ammo and preserves both subsequent charge ticks. Synchronous replay took
about 0.87 s and 3.46 s in these samples, compared with about 0.18 s for the
capped path. See TRACER_PASSIVE_ORDERED_REPLAY_COST_PROBE.json. Ordinary
passive loop, cap, multipliers, resolved armour/shield loss, friendly/miss
exclusion, Pulse exclusions and death retention execute in tests.

Graph0016 sets a0.2-second post-volley guard with a0.15-second early-press buffer.
A rapid re-press is delayed until that guard ends, then fires; the initial short
observation window did not establish a lost-input bug.

Read-only VM/server snapshots now register both Recall states, bounded history/
session and active Pulse storage. Tests forbid variable evaluation and payload
execution during export, retain deterministic hashes, detect changed history/
fuses and reject unknown fields. These are observation formats, not restorable
checkpoints. The14-test server replay suite passes on frozen source; source edits
mid-replay invalidate its pinned package manifest.

## Actual NPC incoming and native spread evidence follow-up

Reviewed shared parent commits fbcaf76 (manual engagement/controller), d1286b7
(weapon manager and sparse slots),6debff3 (array selection) and7cd1ed7 (melee LOS)
are reused only in this private clone. The parent owns integration of shared
changes. The melee conflict retains this lane's authored damage-tag forwarding.

Prior NPC checkpoint:259 distinct tests pass (245 regular plus14 full server
snapshot/replay); native72-case execution is counted separately.
Eight actual-class tests cover36 class subcases. Manual controller input causes
Trooper20 damage at frame29, Sniper100 at128 and Heavy Assault10 at32 against
Tracer. A later trace shows that Heavy's first 10 damage comes from two
authored 5-point rays in the same frame. All three cease damage for80
frames after stop. Casting Recall before their first shot blocks actual attack
damage throughout its active phases; their real attacks damage Tracer afterward.
Each NPC uses its actual body/weapon graphs and data; no direct-damage replacement
is used. Observer projectile transport remains an explicit omitted adapter.
Autonomous acquisition/navigation, specials and production mounting remain open.

The first attacks of all five manual NPCs can kill a wounded Tracer.
Heavy Assault is tested at five and seven health to exercise death
on either ray of the same volley. Each case records one NPC-attributed
death, notice and respawn deadline. The retained engagement resumes
damage against the new body at an offset spawn after normal respawn.

The fresh full-five-class audit records Enforcer and Assassin at zero damage in
200 frames with scripts retained. They remain executed missing-attack failures.
Array selection fixes Enforcer's config value but does not supply its missing
controller context. These two failures are not converted into passing tests that
assert no attack. Five-class melee tests use actual wall triangles and authored
capsules: no through-wall loss, hit notice or ultimate gain. Existing clear-path
melee tests still deal30 to every class.

Offline execution of captured104319 native functionD80A50 passes72 combinations
without engine-service stubs. It returns1 for a denominator below1e-7; otherwise
min(1, initial + max(0,count)/denominator), with scalar float32 operations. The
239B571E launch initializerD88673..D8869B maps its+38 field(m_AC20B1C1) to runtime
+28, used as that denominator. Tracer's value3 therefore means three shots to
full normalized spread, not growth per second. D5FF60 passes zero-based shot
index for firing; D5F920 passes shots_at(elapsed)+1 for the linked spread output.
D5EAA0 interpolates authored min/max then converts degrees to radians. The0016
recovery Chase moves231 toward0 at rate4 after the0.2-second6618 delay.

The existing captured image SHA256 is
b06c3fb0ff82ba6521740b64e2b6d1394f3e692793b61df2d888e627f4bca9b2.
Lane-local SPREAD_NATIVE_EVIDENCE.json and SPREAD_NATIVE_VALIDATION.json retain
provenance, RVAs and results outside Git. The native D80090 sampler uses indexed
random tables and axis rotations; seed/table, anisotropy and dual-muzzle mapping
remain unverified. The subsequent selected bloom/cone implementation below
replaces the constant output/rays while leaving full native fidelity open.
No game bytes enter this patch.

## Sigma Kinetic Grasp

Actual Sigma input activates 1D02. Two Pulse Pistol rounds supply six
points each to its authored accumulation path. Actual Pulse Bomb is
removed before contact or explosion and supplies the reconstructed sum
of its five-point contact and 350-point blast. The nearer static wall,
body and existing defenses take precedence.

At expiry, the authored 1D03 request supplies the amount, cap and decay
inputs. The server creates an independent temporary shield pool.
An absorbed pistol pair grants 7.2 points, and an absorbed Pulse grants
213. Subsequent pistol hits spend the temporary pool before the base
shield. Decay waits three seconds, then removes five points per second.
Turned Grasp, allied fire and melee are covered separately. With a complete
input batch, a late Grasp press preserves the earlier Pulse attachment and
five damage. A turn after absorption preserves the 355-point credit and the
213-point shield grant. When an observed batch crosses natural expiry,
Pulse resolves against the captured activation before its shield request.
Two earlier pistol rounds and one Pulse yield 220.2 temporary shield once,
with the same decay start as separate updates. Actual rear Flashbang before
contact ends Grasp and allows attachment. Flashbang after interception keeps
the bomb absorbed and cancels the shield grant. A complete batch preserves
both outcomes and the stun deadline, even if its endpoint crosses the
scheduled expiry. The cooldown clock and wake use the Flashbang impact
frame, and the cancelled activation retains earlier absorption credit.
An early press is rejected, a press on the ready frame starts a new Grasp,
and that activation absorbs a fresh pistol pair. Unaligned input, other
interruption sources, native Grasp filters and conversion parity remain open.

## Combined defenses

Actual inputs verify Genji transfers both Pulse and hitscan ownership before
the returning attack tests Winston's sphere. A bubble allied to Tracer
passes the outbound shot and stops the reflected attack. A bubble allied
to Genji intercepts the outbound Pulse before Deflect. Its blast can still
damage an enemy on the bomb's side of the shell, with the original credit.

With live 02CC Matrix and 00FA bubble, moving the bubble across the Matrix
front exchanges which defense receives the attack. A four-player fixture
also passes the outbound Pulse through its allies and absorbs it after
Genji's reflection, before it reaches the bubble on the return. These
fixtures cover the reconstructed server order. Native comparison, other
defense pairings and replication remain open.

## Winston bubble and Pulse

The same 00FA sphere used by hitscan now supplies inner and outer Pulse
sweeps expanded by the authored projectile radius. Hits inside the same
bubble and shots crossing an allied bubble still use ordinary body contact.
The bubble stays at its deployed point when Winston moves. A stuck Pulse
keeps the exact bubble actor identity, and retirement leaves the bomb at
its last world point with the remaining fuse. Native detachment motion
and dynamic sphere filters remain open.

The active sphere absorbs the blast for protected bodies, including when
that blast destroys the barrier. Shield damage is resolved after target
occlusion and at most once per bubble. A nearer static wall blocks contact
and explosion. The geometry, filter and attenuation policies are
reconstructed server behavior. Native timing, other projectiles, combined
defense cases and visual replication remain open.

## Reinhardt shield and hammer

Actual 0095 input raises the Barrier Field. Its prepared mesh, yaw/pitch
attachment and resource model follow lane 0007's reconstruction. The
front Tracer pair removes 12 of 1600 shield health and leaves the owner's
500 pools intact. A rear pair removes six armour. At six shield health,
the first pellet breaks the field and the second deals three armour damage.
A nearer wall prevents both shield and body damage. Release starts the
regeneration delay, and a fresh raise intercepts after regeneration.
Actual Tracer melee resolves separately against the visible shield mesh and
the body capsule. At 4 m the shield takes 30 and the body is out of reach.
At 3 m the shield takes 30 and Reinhardt's armour loses 25.

Pulse Bomb sweeps its authored radius against the current shield mesh.
An attached bomb follows the mesh transform, deals five at contact and
spends up to 350 shield health on detonation. A ground detonation also
damages a nearby visible shield. Static cover and shield occlusion are
checked before blast damage changes shield health. Lowering the shield
clears attachment and retains the last world point and fuse. This is a
server reconstruction. Blizzard's June 2018 notes establish barrier
sticking in the game family. Exact build104319 attachment, detachment
movement, animation and replicated pet lifecycle remain open.
[June 2018 Blizzard patch notes](https://overwatch.blizzard.com/en-us/news/patch-notes/live/2018/6/)

Actual 0048 range probing retains the 5 m roster miss. The attack reaches
its active state on frame 27 with either the original 24-frame hold or a
40-frame hold. The level 4.5 m control deals 85 damage. Both torso and level
aims miss at 5 m under the current reconstruction. Native reach and
animation-driven sweep remain open.

Actual 00E2 Fire Strike input now completes the empty windup marker
and uses the authored 0.5-second release. The projectile travels at
25 m/s and uses the authored 0.85 m direct-hit extent in a reconstructed
capsule sweep. It deals 100 to both Tracer and a second body behind her
once each. Active Recall protects Tracer while the shot continues to
the second body. An early press during cooldown launches no second shot.
A fresh press at zero cooldown releases a second strike and can kill
Tracer. A pistol kill on a wounded caster cancels windup before release.
An already released strike remains live and can hit after the caster dies.
With the 104319 projectile tags loaded, actual Genji Deflect transfers the
projectile and team, protects allied Tracer, and returns 95 damage after
Reinhardt armour. Matrix and Grasp consume the projectile before Tracer,
with 100 damage credited to Grasp. Fire Strike spends up to 100 on each
Reinhardt barrier, Winston bubble or Sigma projected shield once per
projectile, then continues to the body behind it. A nearer static wall
stops it before any shield or body damage. With an active Winston bubble
and Deflect or Grasp, the projectile resolves the nearer surface first.
The bubble loses 100 if hit before the active defense and stays full if
the active defense intercepts first. Tracer is protected in all four cases.
Two opposing Genjis can reflect the same Fire Strike in succession. Each
reflection transfers ownership and team while keeping elapsed life and the
body hit history. The returning shot skips its previous victim, reaches
Tracer and expires on its original lifetime. Other combined defenses,
shield re-deployments during one flight and native marker timing remain open.
In team mode, Fire Strike passes allied Tracer and hits the enemy behind
her. A static wall before two hostile targets stops the projectile and
both targets retain full health.

The historical grounded Earthshatter probe spent 1540 charge with no ground
point or wave origin. The hosted 00BE TrackRay now samples ground and starts
the authored wave after windup. The sampled ground front travels at 40 m/s
up to 20 m, deals 50 at range and temporarily locks Tracer's inputs. The
250-point close hit kills. Recall, allied teams, range and static cover
exclude the tested hits. A raised Reinhardt shield blocks the tested wave
even when it breaks on contact. Strafing into the central path before
contact stops the central front and shields Tracer. Entering after that
front passes leaves Tracer damaged and stunned, even when an off-axis
front hits the shield. Zarya's projected barrier absorbs the 50-point
ranged hit and prevents its stun even when the barrier breaks. At 3 m the
250-point close hit breaks the full barrier and gives Zarya 40 energy.
00BE's ground-wave filter rejects the current candidate when it is in the
close TrackTargets output (instance var 468). The host records close
contacts before damage resolves, so that ground wave leaves the same
Tracer at full health and unstunned after the break. Winston's bubble and
Sigma's deployed,
stationary barrier each take 50 and shield Tracer. Sigma's barrier also
takes 50 and shields Tracer while projecting toward the wave. The wave tests
relative movement between the two sampled barrier poses, including four
launch offsets that exercise different contact times within a tick. Mei's
Ice Wall stops the ground front. Removing the wall before contact lets the wave hit Tracer.
Removing it after contact leaves the stopped front inactive. A wave that
stuns during Pulse windup cancels the throw without a refund. A bomb already
attached survives the stun and detonates. Casting during a normal jump
spends charge immediately. The scoped node 44 reconstruction follows the
landing branch once the caster is grounded and the airborne wait reaches
the authored 0.383-second minimum. Actual inputs exercise landing on both
sides of that minimum and a 6 m fall from above the ground probe's reach.
Tracer's lethal pistol fire before release prevents the wave without
refunding the cast, both in flight and during the remaining wait on the
ground. After release the wave survives the caster's death and can still
damage and stun Tracer. The sampled front crosses 0.5 m rises and drops,
and climbs a ramp that rises 1.5 m over 2 m. It stops at 3 m rises, drops
and gaps, and at a ramp that rises 1.5 m over 0.75 m. Blinking into the
path ahead of the front gives 50 damage and stun when it reaches Tracer.
Blinking into the same lane behind the front leaves Tracer unharmed.
Other airborne sources, moving Reinhardt shield poses, frame-boundary
timing and native terrain edge cases, knockdown presentation and client
acceptance remain open.

## Standing hitscan region against Tracer

The extracted build104319 model 02B1 motion-1 wrapper 282 has classification 4,
region resource 0x0DA000000000000D and a sphere bound to node 0x11. Its bind-pose
centre and radius are recorded in TRACER_HIT_REGION_EVIDENCE.json. The server
rotates the bind offset by viewed body yaw and uses the same retained feet and
stance sample as the body capsule. A ray crossing the head sphere uses the
head contact distance for range and nearer-occluder checks. Other standing
rays retain the body capsule. Actual crouch input uses the authored 1.25 m
movement capsule with a 0.5 m radius. Soldier's shot does 20 to the low body
and 40 at the crouched view height under the server's height classifier.
Aiming at the standing head height misses, and static cover stops a valid
crouched shot. The same standing-head shot with view delay can hit the
retained standing sample for 40 even after the target crouches.

Mei's icicle and Zenyatta's primary orb hit the crouched body and upper
region, and miss at the standing head height. Static cover stops the
icicle. Crouching after icicle launch avoids the high shot, and standing
before contact restores the standing critical hit. A raised Pulse Bomb
throw likewise misses a target who crouches after launch and attaches
for five damage to a target who stands before contact. Pulse samples the
body and Recall state at each observed frame of a complete input batch.
The stance after crossing no longer changes an earlier attachment. An
attached bomb also uses the target pose at fuse expiry, so batched
movement produces the same blast position and damage as individual ticks.
A batched Recall detaches the bomb at the last unprotected position, and
its explosion leaves the still-protected target at 145 health. Pulse also
uses the observed Deflect state and Genji's aim at contact. Turning afterward
does not change an earlier reflection or stick. A late Deflect press leaves
an earlier stick and its five damage intact. Pulse also samples D.Va's active
Matrix box, eye and aim on each observed tick. Late activation preserves an
earlier stick, and a later turn preserves an earlier absorption.
Winston's 00FA shield publishes on the authored deployment frame. A late
cast leaves the bomb attached to the target. A shield that expires after
interception retains the earlier damage and releases the attachment. Actual
Flashbang before the cast rejects it without spending cooldown. Flashbang
after the cast begins leaves its independent deployment wait intact. A fresh
cast is accepted after the stun clears. Unaligned inputs, other historical
projectile-defense poses and original-client timing remain open.

Native crouched and animated regions, other body regions, projectile hit
region fidelity and native sentinel consumption remain open.

## Count-based bloom and reconstructed cone execution

Tracer0016 node12(state8) now uses the native-validated normalization. Firing shot
index0 has zero cone, index1 uses one third, index2 two thirds, index3 and later
full3.5 degrees. Linked output231 counts already-fired reports, so it reads one
third immediately after the first pair. Original graph recovery delays0.2 seconds
and then chases toward zero at rate4. A buffered rapid re-press retains remaining
bloom rather than granting a new fully accurate pair. Python normalization agrees
bit-for-bit with the72 captured native cases; no engine-service stub is used in
the native routine.

Each report captures its cone angle under its activation identity before a later
activation can replace spread0. Storage is bounded to256 entries per weapon;
strict read-only snapshots retain these angles. Report timing/counters and the
four-field shot queue contract are unchanged. This prevents queued shots from
using a newer activation's bloom after recovery or re-press.

The host samples a uniform polar angle and uniform angular radius per bullet,
then traces each unit ray separately against viewed capsules and actual static
collision. This is a deterministic reconstructed RNG keyed by body/instance/
state/activation/shot/bullet; it does not consume graph RNG. Matching the native
indexed random table, command seed, anisotropy and animated dual muzzles is still
unverified. Origins remain the existing eye model. This is selected server cone
behavior, not full native spread/hitbox acceptance.

Nine actual-input fixtures cover first pair versus linked output, growth/cap,
recovery, buffered re-press, retained old activation angles, deterministic distinct
unit rays, far-target misses and read-only hash sensitivity. A real half-wall
between the two sampled rays blocks one bullet while the other deals6 damage.
All earlier close-range per-bullet armour/ammo fixtures still pass.271 regular
regressions and14 server snapshot/replay tests pass (285 distinct tests).

A separate offline native execution now establishes the critical initialization
path: D88A9F loads -1, D8B52D..D8B548 evaluates null m_A872C70E through8F6350 and
stores that -1 sentinel at runtime+64. This is not a fixed multiplier1 or proof of
a final multiplier2. Downstream hit-region/sentinel resolution remains OPEN;
current critical handling is not accepted. CRITICAL_NATIVE_EVIDENCE.json and
probe_critical_default_native.py retain the reproducible offline evidence outside
Git. No live client or game startup was used.

The native direct-hit multiplier is copied from compiled hit +0x64 into
queue record +0x2C and survives B0E900 and B10D50 unchanged. B141D4
passes that record to D65850. Its two virtual selection calls use slot
+0xB0. The queue constructor at B1064E installs vtable 26557B0, which
maps that slot to D34A80. Ten offline executions of the two call sites
with five multiplier values each return true and do not read the
record. The apparent +0x2C read at B13F1E uses a base advanced by four
bytes and instead compares the neighbouring +0x30 field. Evidence is
in TRACER_CRITICAL_QUEUE_EVIDENCE.json and
TRACER_CRITICAL_QUEUE_SELECTOR_EVIDENCE.json outside Git. Other native
consumers and the final multiplier are still open.

## Actual Flashbang control and early-input correction

Reviewed parent64bf71c is reused in this clone as31911e6. Merge resolution keeps
Tracer Recall pose locking, per-bullet cone/tag forwarding and both Pulse/stun
snapshot storage. The effect ID is still read from the original hit definition;
053/0079 resolves to0228, whose node7 supplies224E duration schema00D7 with the
1.7/0.8 alternatives and mode flag13408. STUN_RESOURCE_EVIDENCE.json records a
read-only review of the exact wrapper data and parent extraction provenance.
This is the parent's reconstructed world-deadline control policy, not execution
of the complete224E/native status manager.

Thirteen actual-input Tracer control methods verify:
- A real enemy Flashbang causes25 damage and an exclusive50-tick default stun.
- Fire, melee, movement, Blink, Recall and Pulse inputs cannot spend resources or
  damage during control. Existing horizontal momentum and pistol fire/ammo stop.
  Expiry accepts fresh fire/Blink; Recall can restore the earlier Flash damage.
- Blink performed before contact escapes the original projectile trajectory;
  this is positional evasion, not a claim of native Blink invulnerability.
- Both active Recall phases exclude actual Flashbang damage and control. Recall
  starts cooldown normally afterward; an already-stunned player cannot cast it.
- Flashbang interrupts the actual0.15-second Pulse windup: charge stays spent,
  no late bomb appears, and a newly charged cast can work after expiry. A bomb
  already released survives owner stun and still explodes against its target.
- Actual lethal enemy primary and explicit body replacement retire old control.
  Old deadlines do not inhibit the new body's Blink. Walls, allies and distant
  targets receive no Flashbang damage/control.

A further real-input edge probe found a resource leak despite blocked damage.
Pistol frame1/release2, Flashbang7, early re-press10/release11 produces stun13;
the original guard re-enters at14 and spends ammo38->36 during control. Ability
buffers alone did not clear LogicalButton's early timestamp. Stun entry now
clears that timestamp too. The new regression fails before this change and
passes afterward: ammo stays38 throughout control, then a fresh post-expiry pair
spends2 and damages the enemy. BUFFERED_STUN_REGRESSION.json retains the trace.

Ten actual-class NPC methods cover56 class subcases. A real allied McCree casts
Flashbang; Tracer's actual primary, melee and Pulse then damage all five controlled
classes. Pulse attaches while control is active and later explodes normally.
An additional wounded-body fixture gives each actual class26 health/no auxiliary
pools; Flashbang leaves1 health and Tracer's real primary kills it, clears control,
posts one kill and retains the corpse past the normal deadline. Actual bodies,
scripts and authored capsules are retained; these are explicit wounded fixtures,
not claims about difficulty pool totals or native AI. Current tests remain
manual-controller/transport adapters.

Actual Flashbang interrupts the Trooper, Enforcer, Sniper and
Heavy Assault before their first hit, and breaks an active Assassin
pin. The five manual engagements deal no damage during stun and
hit Tracer after expiry. The Assassin establishes a new pin.
At 24 m, an Enforcer or Sniper round launched before Flashbang
still hits during stun. A lethal Sniper round retains NPC kill
credit. Neither shooter launches another round during stun.

With the extracted 104319 projectile tags loaded, Genji can
intercept Enforcer 23F0 and Sniper 1E80 rounds before they reach
Tracer. A frontal return leaves Tracer and Genji untouched and
deals 2 or 100 to the original NPC. A lethal Sniper return credits
Genji. Rear contact damages Genji, and an NPC-allied Genji lets
the round continue to Tracer. This covers the two projectile
definitions in the server fixture. Original-client interception
geometry and reflected projectile presentation remain open.

Sigma's active Grasp also intercepts both tagged NPC rounds.
Its authored accumulator receives 2 or 100 damage, then the
server grants 1.2 or 60 temporary shield at the end of Grasp.
Rear shots damage Sigma and grant no shield. When Sigma and
Genji both defend Tracer, the nearer defense resolves the Sniper
round. Grasp absorbs it once, or Deflect returns it to the NPC
without adding to Sigma's accumulator.

The same NPC rounds hit the active Winston, Reinhardt, Sigma
and Orisa barriers for 2 or 100, with no damage to protected
Tracer or the barrier owner. A Winston dome allied to the shooter
lets the round through. After a hostile dome expires, renewed
manual attacks hit Tracer. A nearer hostile dome stops a Sniper
round before Deflect, and a dome allied to the NPC stops the
returned round after ownership transfers to Genji.

An active D.Va Matrix absorbs both enemy rounds. Rounds from a
shooter allied to D.Va continue to Tracer. Releasing Matrix and
moving the Mech aside lets the next enemy round hit Tracer.
Zarya's projected barrier absorbs both rounds on Tracer and
credits 0.4 or 20 energy from a full barrier. When the test
starts the barrier at 1 or 50 health, the breaking round credits
0.2 or 10 energy. That round leaves Tracer untouched, and a
later round hits her after the barrier breaks.
With both defenses active, a nearer Matrix absorbs the Enforcer
round before it reaches Tracer's projected barrier. Zarya's
barrier remains at 200 and grants no energy for that round.

The Trooper's 1FE6 rifle and Heavy Assault's 206A guns use the
same shield, Deflect and Matrix paths as other tagged rays.
Winston's dome takes 20 or 10 from the first volley. Deflect
returns the Trooper round for 20. Two reflected Heavy rays kill
the 7-health fixture and credit Genji for the actual loss.
Sigma's Grasp now consumes the tagged rays as well, granting
12 or 6 temporary shield after absorbing that first volley.

Reinhardt, Sigma and Orisa mesh barriers absorb the Trooper's
first 20-point ray and both five-point Heavy Assault rays. At
four barrier health, the first Heavy ray breaks the mesh without
body damage. The second ray in the same frame hits Tracer for
five. The shield owners stand off the ray and remain unharmed.

Zarya's projected barrier absorbs the first Trooper and Heavy
volleys and grants 4 and 2 energy. At 10 barrier health, the
Trooper's breaking ray grants 2 energy and a later ray hits
Tracer for 20. At 4 barrier health, the first Heavy ray grants
0.8 energy and the second hits Tracer for 5 on the same frame.
The two Heavy rays can also break a four-point Winston dome and
spend five projected barrier health. Tracer remains unharmed and
Zarya gains 1 energy.

Zarya can project onto Tracer before or during an Assassin pin.
The projected barrier absorbs contact and continuous damage while
the current manual pin still locks input. A one-point pool absorbs
the breaking tick, credits 0.2 energy and leaves the next tick to
damage Tracer. The pin preserves the authored 302F tag in both
damage phases. In separate matches, an allied Winston dome or
Reinhardt, Sigma or Orisa mesh intersects the line from the
Assassin to Tracer. The manual host admits the pin and applies
25 contact damage, then 16 across ten steady ticks while the
barrier's health stays unchanged. Blink and Recall are rejected
during the pin. Blink works after manual stop. Native admission
and damage across all four barriers remain open.

Separate two-attacker fixtures activate Genji Deflect, D.Va Matrix
or Sigma Grasp before starting a Trooper and an Assassin. Each
defense intercepts the first Trooper ray during the pin. Deflect
returns 20 damage, Grasp receives 20 and Matrix spends resource
while intercepting the ray. The Assassin still deals 25 contact
damage and applies a later continuous tick to Tracer while each
defense remains active. Native pin eligibility for these defenses
remains open.

The Assassin approaches and retargets a newly respawned or reselected
Tracer. Same-frame reuse of the body ID still starts with fresh contact.

Contiguous and sparse updates resolve an already-active Assassin pin
on the same lethal frame, preserving attribution and the respawn deadline.
The long-gap fixture also checks Sound Barrier decay before pin damage.
A new pin can start within the ordinary retained combat window.
Nano mitigation ends on the same Assassin damage tick under
contiguous, 24-frame and 100-frame updates. An allied Sleep Dart
releases a live pin. Tracer's pistol hit after the wake grace period
shortens the sleep, and the Assassin pins again at the new deadline.
An already-released Pulse Bomb can detonate during the pin. A lethal
blast releases shielded Tracer for a new Blink. A full-health
Assassin survives at 145 and keeps the pin. Without the projected
barrier, the close lethal blast also kills Tracer.

328 regular regressions and14 full server snapshot/replay tests pass on the
fixed source (342 distinct tests), including the additional five-class death
fixture. The superseded snapshot process was confirmed lane-owned and stopped
specifically for the new production bug fix. No observation timeout caused a
restart. No package source changes are made during the replacement replay run.
Native immunity/cleanse/stacking/unstoppable, other control types, barriers,
reflection/absorption, dynamic mode status and presentation are still OPEN.
Parent2ca9178's native state-input join fix is applied privately as e3416f5;
current initialized Tracer graphs contain no such node, which does not prove its
absence from all later child/effect graphs. Ten join,96 Tracer and23 VM snapshot
tests passed after reuse; this does not establish full hero acceptance.

Uncommitted owner-health work resolves NPC STU_9B7648C3 from the existing
body health input. Native CEB6C0/D177C0 selects component51 and calls its scalar
getter; aggregate combat health is the current host mapping, with complete
native pool policy and foreign entity lookup still open. Manual spawn installs
its pools before graph initialization, preserving initial shields/armour.
A real-input fixture for all five classes covers initial totals, paired pistol
damage and death returning zero; it failed for all five before the bridge.
Possession/controller queries F7262B36 and B96D3A98 remain unresolved in this
NPC host. Enforcer/Assassin attacks are not accepted by this health repair.
Further own modifications remain uncommitted until full static acceptance.

## Client and delivery status

Uncommitted Deflect extension:17 actual-input fixtures now cover reflected
Tracer pistol pairs and melee block, attribution, mitigation, redirected aim,
team/wall, Recall/Blink, expiry/cancel/reuse and owner lifecycle. Admission is a
reconstructed frontal capsule and damage-tag policy; native box/filter/transform
fidelity remains open. The initial PulseQ/active03A3 stick failure is now repaired
under a separate scoped server policy:14 actual command cases cover transfer,
old prediction retirement, target/control/death/replace and physical lifetime.
Native admission/boxes/angle/redirect/fuse details and new-owner reflected visual
replication remain unaccepted. See TRACER_DEFLECT.txt for exact scope and evidence.

Latest uncommitted Enforcer extension: the shared parent's explicit manual
component-set predicate is scoped to actual13EB owner queries. Four actual NPC
weapon graphs now damage Tracer. Enforcer fixtures cover actual-input damage,
stop, Recall protection/recovery, support Flashbang, real pistol kill/corpse
retention and static-wall exclusion/recovery. A static partition spanning
the walkable floor now verifies both attack directions for Trooper, Enforcer,
Sniper, Assassin and Heavy Assault. The wall prevents damage and Tracer
charge gain. Removing it restores both attack directions. Switching the same
five engaged NPCs to Tracer's team releases their active attack input, clears
an active Assassin pin and prevents damage and pistol charge. Restoring the
hostile team resumes NPC damage. After stopping the resumed NPC attack,
Tracer can damage the hostile NPC and gain charge again. At 105 metres
the manual engagement gate blocks NPC attacks and Tracer's pistols deal no
damage or charge. Restoring the original five-metre separation restores
both damage paths. At 25 metres, a body hit with both bullets deals 3.6 and
grants 3.6 charge on each of the five NPC capsules. A deliberate miss
grants neither damage nor charge. The Assassin first closes to pin range. Native allocation/namespace,
weapon fidelity and Assassin remain open; see ENFORCER_MANUAL_CONTEXT.txt.
The8-case native predicate-tail probe has explicit lookup/output adapters and
does not prove the sandbox component declaration is a native NPC controller.

No original-client session was started or controlled, no lease was claimed, and
no client acceptance is asserted. Existing Overwatch PID12292 and original server
Python PID22924 were observed and left alone; absence of a lease does not make
those processes lane-owned. Runtime assets were read in place and not copied.

Development stays on the private clone's main. The integration commit and
format-patch are listed in the adjacent lane STATUS.txt. No root-source merge,
manual push or public deployment is performed by this lane.

## Orisa Protective Barrier

The isolated lane uses the verified 104319 model 4062.00C collision
descriptor from lane 013E via ORPHEUS_ORISA_GEOMETRY. The 0BD7 input
launches the authored gravity projectile. Static contact queues deployment,
and the 0BD8 instance initializes 600 health and a 20-second lifetime.
Switching bodies removes an in-flight cast. An already deployed barrier
survives the switch and retires when its authored lifetime and tail end.

Actual Tracer pistols spend 12 barrier health without a body hit. A pellet
that breaks the barrier does not spill into Orisa. The next pellet can
reach her and deal 3 damage through armour. Orisa's Fusion Driver passes
through her own shield and deals 11 damage to Tracer. An enemy Earthshatter
breaks a weakened barrier without damaging or stunning the Tracer behind
it. Stored body VM and server snapshot checks cover the active 0BD8 graph.

Tracer's quick melee spends 30 barrier health with the owner out of
reach. Blink passes through the mesh without damaging it. The next primary
volley can hit Orisa from behind. Pulse Bomb attaches to the mesh and
spends 5 on contact and 350 once at detonation. An intact shield or one
broken by the blast protects Orisa from the same blast. If the contact
breaks the shield, the bomb detaches at the original anchor, retains its
fuse and can damage Orisa. A ground-stuck bomb can damage the visible mesh.
A nearer static wall blocks both attachment and splash.

With Orisa and Winston barriers stacked in either order, the first
shield takes contact and blast. It protects the fully occluded shield
even if it breaks from that blast. Actual Tracer pistol fire can break
the outer shield before detonation. The bomb keeps its anchor and fuse,
and the newly exposed inner shield takes the blast.

Partial shield exposure, other stacked shield combinations, placement
against dynamic cover, the full Orisa ability matrix and original-client
deployment remain open.

## Orisa Fortify

Actual ability input activates 0CF4 state 4, which supplies the 40% damage
reduction before body armour. Tracer's first body volley deals 3.6
through armour instead of 6. Quick melee deals 13 and activating Fortify
after Pulse Bomb attaches reduces its detonation to 205 through armour.
The original timer ends the reduction. An early re-press is rejected
until the authored cooldown ends, then a fresh press restores Fortify.
An allied Zarya barrier absorbs the full first 6-point pellet and grants
1.2 energy. After that pellet breaks the barrier, the second pellet
reaches Fortify and armour and removes 1.8 from Orisa.

The live 9161 control flag blocks Earthshatter stun during Fortify.
Orisa still takes 25 damage through armour and an unfortified Tracer on
the same front takes 50 and is stunned. After expiry, Orisa takes 45 and
is stunned. Actual Soldier and Tracer headshots verify critical
suppression and recovery. Tracer's multiplier is reconstructed for
the hosted weapon. Native sentinel resolution remains open.

Sleep Dart removes 1.5 health through Fortify and armour, without
starting sleep. Accretion removes 32 without stun or displacement.
Orisa can return fire and hit Tracer after either attack. With Fortify
expired, the same shots apply their ordinary damage and control. The
expiry Accretion also moves Orisa. Activating Fortify clears existing
partial frost from Mei's primary. Continued spray damages Orisa without
building frost until Fortify expires. Fresh spray can then freeze her.

Other control classes and original-client status presentation remain
open.

## Orisa Halt

The actual 0CCB secondary input launches a 172C orb and writes its
identity into the casting graph. The authoritative entity registry and
owner query admit the armed second press and route its original detonation
message. The orb stops immediately. The 0CCE target instance starts the
delayed MoveTo and owns its active movement and post-slow states. Use
ORPHEUS_ORISA_HALT with the verified 104319 0CCC and 0CCE supplement
from lane 013E.

Actual player inputs verify no damage, a rejected pre-arm press, cooldown
rejection and reuse, and cleanup on caster body replacement. Tracer can
Blink out of acquisition range or Recall after acquisition to clear the
pull before motion. Allied teams and static cover prevent acquisition in
the tested geometry. Defense Matrix and Kinetic Grasp consume the orb
before it can pull Tracer. Deflect transfers the orb to Genji's team.
A reflected wall impact pulls enemy Orisa and leaves allied Tracer
unaffected.

The host uses configured visibility samples with a reconstructed fallback.
Native target filters, partial or dynamic cover, movement arbitration with
other effects, NPC pull hosting and original-client replication remain
open.

## Orisa Supercharger

Actual Orisa ultimate input runs 0CF5 and launches 174E. The deployed actor
uses the verified 42A2 collision mesh and the original 0D00 and 25B4 graphs
from lane 013E. Set `ORPHEUS_ORISA_SUPERCHARGER` to that lane's
`orisa_supercharger_174.json`. The device starts at 200 HP and expires after
the authored 15 seconds. Its presence gates Orisa's ultimate charge, and
the allied target graph 0D01 supplies a 1.5× outgoing damage multiplier.

Actual inputs verify Tracer's two pistol rounds deal 18 instead of 12
inside the allied aura, and deal 12 beyond range or when a wall blocks
the aura while the shot remains clear. Against an enemy device, the
rounds deal 12 to the mesh. A killing volley removes the device and its
buff. Orisa's actual primary round then deals 11 to Tracer, down from
16.5 during the buff. Authored expiry clears the buff and the charge gate.

Tracer's quick melee deals 30 to a hostile device in reach. The same
input leaves allied and out-of-reach devices at full health. Pulse Bomb
deals 5 on contact and destroys the device on detonation. If contact
breaks the device, the bomb detaches at the contact position and keeps
its fuse. An adjacent ground detonation also damages the hostile mesh,
while a static wall blocks contact and splash. Allied devices take no
contact or splash damage. Lethal Tracer pistol fire during Orisa's
windup prevents launch and leaves the spent charge at zero. If her
projectile has already left, the same lethal input allows it to land
and boost an ally while she remains dead.

A deployed device keeps boosting allied Tracer through Orisa's death.
The scheduled respawn preserves the device, deadline and original source
body. The replacement body receives the original 0859 deployment callback
and its #842 lock stops the server's passive meter. An ultimate press
during the lock produces no flight. Actual expiry releases the lock and
returns Tracer's two-round damage to 12. The test accelerates the host
passive clock to fill the meter again, then verifies a fresh input spends
the charge and deploys a new device owned by the new body. Orisa's native
passive cadence and damage-derived charge remain open in this lane.

The aura raises Tracer's melee to 45, Pulse contact to 7.5 and Pulse
blast to 525 against an unarmoured target with a seeded health pool.
The bomb keeps its launch-time multiplier after Tracer leaves the aura.
An enemy Talon Trooper allied to Orisa hits Tracer for 30 through the
manual controller. After Tracer destroys the device with pistol fire,
a new Trooper engagement lands the same attack for 20.

Aura contact uses the target capsule and an eye-position visibility sample.
Departure removes the buff immediately, overlapping devices use the
strongest boost, and the host adds the Nano and Supercharger bonuses.
The host tests the device mesh as a damage target and excludes it from
the shields that occlude Pulse splash. Native linger, stacking, animated
sampling, deployable collision policy, other NPC interactions and
original-client replication remain open.

## Sigma Experimental Barrier

Actual Sigma secondary input runs the 1B55 deployment and recall phases.
The Pet and 1A2A play request bind to a reconstructed host actor with the
verified build 104319 C015 mesh supplied by `ORPHEUS_SIGMA_BARRIER_MODEL`
and its adjacent `model-integrity.json`. Phase 2 advances the mesh and
phase 3 holds it in place. Linked cooldown changes notify waiting graph
conditions so that a fresh press works after the recall lockout.

Two pistol rounds deal 12 to the mesh while Sigma stays at 400 HP. When
the first round breaks a barrier at 6 HP, the second deals 6 to Sigma.
Quick melee deals 30 to the barrier. Pulse deals 5 on contact and 350
at detonation while the barrier protects Sigma. Recall removes
interception, the barrier regenerates to 700, and redeployment restores
protection after an early press is rejected during cooldown.

The host reconstructs child graph movement, recharge and collision ordering.
Native child execution, replication, moving Pulse attachment behavior,
combined Sigma defenses remain open.

## Sigma Accretion

Actual 1BBB input launches at the authored speed 37 with 0.1 vertical bias
and gravity 18. A direct hit deals 70 and sends the tagged damage message
through 1BBB. The host checks the 224E target gates and applies the
authored 0.8-second duration to its stun clock. Tracer's held pistol
input stops and a fresh Blink works at expiry.

The collision mover carries direct and splash impulses from the authored
rings. A static wall clips the direct push. A rock that misses the
capsule and hits a nearby wall deals 40 and pushes Tracer without
stunning. Active Recall prevents damage, stun and impulse. A cast during
cooldown produces no rock, a fresh cast at zero cooldown hits again,
and caster body replacement removes a rock already in flight.

The host reconstructs sweep ordering, impulse application and 224E
execution. Full projectile and target admission, native effect and
replication behavior and NPC impulse remain open.

## Sigma Gravitic Flux

Actual ultimate input rejects insufficient charge and spends 1960 when
funded. The host uses a static ray for 1E41 placement and a capsule volume
with static line of sight for its target array. The confirmed request
starts 1DBC on the selected Tracer body with Sigma as source. The entry
hit deals 50, the effect lifts Tracer, and the grounded slam deals 75
against 150 maximum health. The instance retires without repeating damage.

Actual caster jump and crouch steer flight vertically. An unconfirmed cast
expires and the caster lands. A wall, an ally, active Recall or moving
beyond the volume with Blink excludes Tracer from selection. Recall
pressed during lift does not start or spend cooldown. A new press works
after the effect ends. Tracer's pistol kill stops an unconfirmed cast.
Caster body replacement after lift cancels the pending slam. Both Blink
inputs are rejected without spending a charge during lift. Holding the
input through the slam does not cast, and a fresh press after landing works.
Tracer can still throw Pulse Bomb while lifted. The bomb sticks to airborne
Sigma for 5 damage, then detonates for 350. The Flux slam still resolves
if Sigma survives. If Tracer wounds Sigma with pistol fire during the fuse
and the blast kills him, the effect retires and Tracer lands without the
75-point slam. The 50-point entry hit remains.

Placement, target geometry, caster flight and the grounded damage boundary
use reconstructed host services. Dynamic surfaces, complete native filters
and control timing, NPC target effects, replication and original-client
behavior remain open.

## Sigma Hyperspheres

Actual 187F primary input launches two spheres. The host sweeps the
authored body and wall radii, applies gravity and bounce coefficients,
and resolves an explosion when the original flight budget expires.
A direct pair deals 110 and the explosion configuration pulls Tracer
slightly toward the impact. At 22 metres, an offset pair deals 60 from
two expiry bursts. A wall reverses the spheres and occludes their blast.

Blink can move beyond the expiry blasts and active Recall rejects the
incoming damage. Allies take neither contact nor blast damage. Replacing
the caster body retires its flying spheres. In the live Winston fixture,
the breaking sphere spends the barrier without leaking its blast through
the bubble. Genji transfers ownership and remaining flight to the
reflected sphere and earns the hit credit. Active Kinetic Grasp absorbs
both spheres before they reach its allied Tracer. Active D.Va Matrix
does the same with the extracted 104319 projectile tags and Mech graph.

Actual Tracer and Sigma quick melee deal 30 once in either direction.
Static cover blocks both swings and active Recall protects Tracer.

Native projectile collision, reflection and absorption filters,
replication and the remaining cross-hero and NPC combinations remain open.

## McCree Peacekeeper and Fan the Hammer

Actual 01D2 primary input spends one round and deals 70 torso damage
to Tracer. She can return fire for 12. Fan uses the separate authored
node 37 and six distinct shot activations. At close range a six-round
burst deals 50 per hit and uses the explicit 1× critical scalar.
Against unmodified Tracer health, three hits kill and the other three
spend ammunition without adding damage or a second elimination.
Duplicate command submission adds no extra shots.

The host uses 104319 table and cone math with a reconstructed seed
mapping from the body, instance, node and activation counter. Each
reported shot keeps its cone angle for later consumption and snapshot
export. Actual off-axis Fan hits where the unscattered control rays
miss. Long-range, team and wall cases reduce or prevent damage.

Blink after the first hit avoids the rest of the burst. Recall already
in progress blocks all six rounds, and primary hits again after Recall
and weapon recovery. Manual reload restores six rounds, and held
primary resumes after its recovery phase. An actual Flashbang prevents
Tracer's Recall input and a subsequent Fan finishes the kill.

Native seed identities, muzzle and primary critical defaults, full
falloff and defense matrices, the remaining NPC pairings and
original-client presentation remain open.

## McCree Combat Roll

Actual 01F0 input uses the authored velocity action and queues its
delta for the next posed frame. A lateral Roll from rest reaches
15 m/s, moves about 6 m on open ground and refills the Peacekeeper
to six rounds. Tracer's shot at the original position misses after
the Roll. Fresh aim hits McCree, and his primary can hit Tracer in
return. Held Fan fires all six refilled rounds after the recovery.

Static collision clips the standing capsule before a wall. The
authored cooldown rejects early presses and permits a fresh press
after expiry. Actual Flashbang ends the Roll, and lethal Tracer
fire before the next posed frame clears the queued velocity and
prevents displacement. Pending velocity participates in server
snapshots.

The host reconstructs the yaw basis, next-frame velocity delta
and end clamp. Native phase ordering, complex landings, other
control sources, NPC matchups and original-client presentation
remain open.

## McCree Deadeye

Actual 022F input rejects an empty meter and spends a filled one.
Separate in-cone and visible searches feed the 01EE per-target
effects. The verified 01EE resource comes from the parent Deadeye
runtime. For this lane, set `ORPHEUS_GRAPH_SUPPLEMENT` to
`deadeye-mech-supplement.json.gz` in the lane root to load it alongside
the existing mech records. The source runtime remains read-only.

The actual release aims at the acquired Tracer even when she is off
the crosshair. The shot spends one round and uses the charge produced
by the target graph. A fully charged shot kills Tracer once. An enemy
at 150 m can be locked and still be beyond the release ray's range.
Static cover pauses charge and blocks release damage. A live Winston
bubble absorbs the shot before it reaches Tracer. Allies never get a
lock.

Blink outside the cone removes the search result and then the old
effect. Returning creates a new lock. Recall protects Tracer even if
release begins before the old effect is retired. Secondary cancel and
natural expiry retire the effects without firing. Lethal Tracer fire,
actual Flashbang and body replacement prevent a pending release.

The host reconstructs cone and capsule samples and attaches the effect
instances through a scoped bridge. Native target filters and aging,
full charge economy, other defenses, NPC pairings and original-client
presentation remain open.

## Reaper Hellfire Shotguns and Reaping

Actual Reaper primary input runs graph 0019 and consumes one shell.
Its four authored rings supply 20 independently resolved pellets.
A close torso shot deals 20 × 6 to Tracer, who survives at 30 HP.
A shot whose centre ray misses can still land outer pellets. Partial
static cover blocks the intersecting rays. Full cover, a complete miss,
the 102 m range fixture and an allied Tracer all prevent damage and
Reaping. Actual manual reload locks fire through refill to eight
shells, then held primary damages Tracer again.

A live Reaper with graph 0075 uses its 35% factor on actual hostile
pool loss. The host settles that healing through its ordinary health
path. The close torso shot heals 42 with space in Reaper's health pool.
A killing pellet against 5 remaining HP heals 1.75. Tracer can damage
Reaper with her pistols, Recall to avoid his next shell, restore health,
and take damage again after protection ends. Both heroes' quick melees
deal 30. Reaper's melee heals him for 10.5 without spending a shell.

After the next world update publishes Tracer's Blink position, the same
shotgun aim misses her on the far side and hits again after the return
Blink. The same-frame probe still hit the pre-Blink pose in the retained
hitscan history. Native ordering for that frame remains open.

The ring orientation and phase are reconstructed. Native headshot
defaults, spread seed and animated muzzle transforms remain open.
The host applies Reaping from resolved damage without dispatching the
complete native passive graph.

## Reaper Shadow Step

Actual input wakes the authored 00F2 idle transition through its
logical-button dependency. The selector uses the authored 35 m reach.
The host requires a visible walkable static surface, a free standing
capsule and an unoccupied destination. Confirmation locks the selected
point until the 1.5-second cast marker ends. Changing aim does not
retarget the cast. An actor entering the landing capsule prevents
teleport. Canceling selection preserves cooldown, and holding an early
retry does not start a new selection when the 10-second cooldown clears.
A fresh press can reopen it.

Actual Tracer pistol fire hits before the linked phase marker, passes
through during it and hits again after the teleport. Lethal fire before
phase cancels the pending teleport. Actual Pulse Bomb passes through
the phase and sticks to a player behind Reaper. An allied McCree's
actual Flashbang closes the confirmed cast before phase and prevents
teleport. The server snapshot retains selector input state. Native
destination adjustment, dynamic landings, other control sources,
command gaps and original-client presentation remain open.

## Reaper Death Blossom

Actual ultimate input spends the authored 2100 charge and starts the
0044 channel. Its live wait and target tracker supply 3 seconds and 8 m.
The host uses 34 damage and a 0.2-second interval from lane 0002's
extracted 0045 effect graph for the human damage branch. It reconstructs
target acquisition, midpoint visibility, and world-time effect ticks.
The 0044 visual volley does not apply damage.

Against a Tracer with a seeded 1000 HP pool, the channel lands 15 pulses
and deals 510. An undercharged press is refused. An owner input gap
does not repeat or extend pulses. Cover, range and team gates exclude
Tracer. Blink departure clears acquisition and return starts a new
clock without backfilled damage. Recall protects Tracer until the
next eligible pulse. A single Winston bubble pulse covers both allied
bodies, including on the breaking hit. Actual Pulse Bomb kills Reaper
and stops later pulses. Actual Flashbang closes the authored wait and
stops damage. The channel deadline is included in server snapshots.

Native effect hosting, other mode branches, full charge and recast
behavior, independent deployable targeting, other defense interactions
and original-client presentation remain open.

## Mercy Caduceus Staff and Blaster

Mercy's actual primary and secondary inputs on 006B acquire allied
Tracer through search states 4 and 51 and retain her through state 5.
The server interprets the authored cone and radius using capsule-centre
ranking and static world occlusion. Effect 0019 state 33 applies the
authored parameter 002A at 55 health/s through the shared healing pool.
Twenty-five 16 ms frames restore 22 health. Release stops healing and
repressing restores up to the 150 health cap, with healing stats and
body health reflecting only health actually gained. A hostile team and
a static wall reject healing. An injected anti-heal expiry verifies the
shared health gate.

Effect 0016 state 35 reads the bonus from 006C node 19. While the staff
retains Tracer, her actual pistol pair deals 15.6 and her melee deals
39. Release restores the pair to 12 and leaving staff range restores
melee to 30. Pulse snapshots the bonus at launch. After Mercy releases,
the attached bomb still deals 6.5 contact and 455 blast damage. Damage
credit stays with Tracer. Hostile Mercy can equip her blaster and damage
Tracer with its projectile, and Tracer's return pistol pair deals 12.

The lane supplement `mercy-staff-mech-supplement.json.gz` adds extracted
006C and 0079 from lane 0004's `research/game_graphs_effects_174.json.gz`
to lane 007A's `mech-supplement.json.gz`. Supply it through
`ORPHEUS_GRAPH_SUPPLEMENT` for the three boost tests in
`test_game_tracer_mercy`. Healing consumes 006B's authored parameter
through the server health path. 006C supplies the boost value and
the existing outgoing settlement applies it alongside other sources.
The test supplement SHA-256 is
`725fa49b9d8d95dbb2827c9c6cd30d432c370d7d64684863a35ccbd4f917764c`.

Native staff targeting and retention, beam transition timing, overlap
and attack eligibility, Mercy boost statistics and assists, and the
remaining NPC and hero interactions stay open.

## Mercy Guardian Angel, Resurrect and Valkyrie

Guardian Angel 006E uses its authored selection states 5 and 14 and
passes the chosen body geometry to the existing MoveTo. Actual Shift
flies toward friendly Tracer at 17 m/s. A real Blink by Tracer after
launch leaves the fixed flight destination in place. Completion and
release enter the 1.5 s cooldown. Tests verify rejection during
cooldown and successful reuse, hostile rejection, range and static
cover, flight toward a friendly corpse, and actual enemy fire ending
Mercy's flight. The lethal fixtures seed health and use a real Soldier
rifle shot to cause death.

Resurrect 00CA uses its two corpse searches and action at node 20.
Actual enemy fire kills Tracer, then one Mercy ability press completes
the 1.75 s cast, restores the same body's 150 health and clears the
ordinary respawn timer. It also credits resurrection and enters the
30 s cooldown. Living and hostile bodies are rejected without cooldown.
Leaving cast range or killing Mercy during the cast leaves Tracer dead.
The caster-death case starts the authored cooldown.

The server uses the authored 00CB wait values for a reconstructed
1.5 s input lock and 2.25 s damage immunity. Tracer's actual pistol
input during the lock and enemy fire during immunity deal zero damage.
Both actors deal damage again after the windows. Lethal Assassin
contact and continuous ticks each clear the old pin. After actual
Resurrect, the retained engagement waits through damage immunity
and pins on the first unprotected frame. Tracer can Blink at the
earlier rising-lock deadline. Full-pool restoration,
foreign corpse eligibility, geometry and control ownership are host
policies. Native endpoint adjustment, subject effect ownership, the
full interaction matrix and original client acceptance remain open.

The actual Valkyrie graph rejects charge below 1820 and spends 1820 at
start. Releasing the button preserves the 15 s active window. Expiry
clears the active marker and resumes passive charge. A real hostile
Tracer Pulse sticks to airborne Mercy, follows her lateral flight and
kills her during the active ultimate. The caster graph stops and the
bomb retires.

Valkyrie state 15 supplies the embedded movement profile and state 33
enables the kind 16 flight hook. The server reconstructs pitched forward
flight, vertical controls, flight-plane acceleration and collision
movement. Actual inputs reach 9 m/s in the pitched test, descend with
crouch input and stop at a static wall, ceiling or floor. At expiry,
ordinary gravity and the base mover return. Tracer's aim at the old
ground position misses, an aimed pistol pair hits airborne Mercy for
12, and Mercy deals blaster projectile damage to Tracer from the air
while her magazine stays at 20.

Flight orientation, residual momentum and native collision parity
stay open.

### Valkyrie staff chains

The effect pack mounts 0079 and 006C for the main beam. Their
authored target searches start 10DC and 10DD for secondary
recipients through message 000F. The effect component binds the
primary target as caller, keeps Mercy as the original subject and
passes the 60 health/s parameter to the healing child. Each effect
component is stored independently. The shared health settlement
applies the health cap and status modifiers.

Ordinary staff healing stays on the main target. Valkyrie restores
48 to secondary Tracer in 50 frames, and release stops healing.
Blink exits and reenters the anchor's range while the main beam
stays connected. A static wall between the anchor and secondary
removes Tracer's 30% boost while the main target keeps it. Pulse
retains the launch boost after staff release. The match clock
removes secondary boost at Valkyrie expiry even while Mercy sends
no commands. Tracer can then deal the ordinary 12-point pistol pair.

The lane input `mercy-valkyrie-chain-supplement.json.gz` adds 006C,
0079, 10DC and 10DD and their effect bindings from lane 0004's
`research/game_graphs_support_npcs_174.json.gz` to lane 007A's
`mech-supplement.json.gz`. Load it with `ORPHEUS_GRAPH_SUPPLEMENT`.
Its SHA-256 is
`4e819f51b857a095c635e52815879b56e577120d6d77956c0641d1726c9a3631`.
The supplement without effect bindings still supplies ordinary staff
behavior.

The server reconstructs the 10 m anchor search with capsule
geometry, static visibility and live friendly bodies.

### Valkyrie chains with the five NPC classes

Actual staff input heals secondary Tracer through each of Trooper,
Enforcer, Sniper, Assassin and Heavy Assault as the primary target.
Tracer can also anchor healing to each of the five. Both directions
restore 24 health in 25 frames. Switching the secondary NPC to the
opposing team ends its healing while the main effect stays active.
Manual removal of a primary NPC destroys both effect components
immediately and stops further healing to Tracer.

With opposing Mercy and a friendly primary anchor, manually selected
NPC attacks hit Tracer with a live secondary boost. First damage is
26 for Trooper, 2.6 for Enforcer, 130 for Sniper, 32.5 for Assassin
and 13 for Heavy Assault in the fixture that uses controller eye aim.
Actual Enforcer and Sniper rounds launched under the boost retain
their damage after Mercy releases. Recall blocks those same rounds.
Assassin's continuous pin damage uses the current bonus per tick.
Four boosted ticks deal 8.32. Releasing the beam keeps the pin
active and the next four ticks deal 6.4.

These cases use the real NPC graph and authored capsule inputs with
manual target selection and a projectile transport adapter. Native
filters, effect ownership and timing, autonomous engagement,
remaining cross-hero and NPC cases, and original-client
presentation stay open.

## Lucio Sonic Amplifier and Soundwave

An actual Sonic Amplifier primary press lands four projectiles
for 80 total damage. Tracer's return pistol pair deals 12.

The authored 0776 ModifyHealth state settles Soundwave once per
activation. The host reads the search radius and full cone angle
from state 10 and checks capsule range, aim, relationship and
static visibility against the selected player pose. The authored
ordinary impulse expression supplies magnitude 17. Tracer loses
25 health and moves through the existing capsule collision path.
A wall behind Tracer stops the displacement. Rejected aim, range,
cover and allied cases produce no health or movement effect.

Actual Blink evades the cone and allows return fire from the new
position. Recall blocks both effects when already active and
restores health and position when started after a hit. The
cooldown rejects an early second activation and permits a fresh
one after expiry. Zarya's projected barrier spends 25 health,
adds 5 energy and prevents displacement. Lethal Soundwave during
Pulse windup cancels the unreleased bomb. A bomb already released
can still kill Lucio with attribution to Tracer's original body.

All five manual Retribution NPCs take the authored hit through
their actual health and armour pools. A surviving enemy uses
its authored capsule to carry the impulse starting on the next
world frame, and Tracer can hit it in the air. Allied NPCs and
NPCs killed by the hit receive no impulse. Removing a body
clears its forced movement.

Allied Lucio can push the Assassin outside pin range. The
manual controller releases the pin and Tracer can Blink again.
A static wall behind the Assassin stops the capsule. After
landing and settling, the Assassin can approach and pin again.

Native filter and resistance semantics, exact direction and pose
timing, autonomous NPC force arbitration, dynamic collision and
other defense pairings stay open.

### Sound Barrier

The actual 073A cast rejects undercharge and spends 2940 on an
accepted press. Its cast timer and a reconstructed grounded-owner
predicate control the slam. Actual air and ground controls show
that the cast waits for landing. State 8 selects self and live
allies within authored range and static sight. The 0191 binding
starts 073B with message 000F and the authored duration. The
child's amount creates a separate 750-point temporary shield.
The server decays that pool over six seconds without reducing
base health. The live child and decay clock participate in the
server snapshot. Retained scheduler catch-up frames preserve
the order of observed projectile impacts and shield decay. The
20-frame catch-up Pulse case matches the per-frame result.

Tracer's actual pistol pair and Pulse spend 367 points from
enemy Lucio's shield. Near expiry a pistol pair breaks the pool
and deals 4 to base health. A fresh seeded charge permits another
cast after the previous ability ends. Friendly Tracer receives
the shield and Recall leaves spent shield and elapsed decay in
place. Allied range and cover rejection, enemy exclusion, caster
death during windup and ally shield survival after caster death
all execute through actual casts and attacks.

The lane input `lucio-barrier-supplement.json.gz` adds 073B and
0191 from lane 0079's `lucio-status-supplement-rebuilt.json.gz` to
`mercy-valkyrie-chain-supplement.json.gz`. Load it with
`ORPHEUS_GRAPH_SUPPLEMENT`. Its SHA-256 is
`bd08a5246ec4fb9dcbc5145c062c574f859c8672f3a04ba37b7dd61d6a55e659`.

Each of the five extracted Retribution NPC classes receives 750
temporary shields when allied with Lucio. An opposing Tracer's
pistols and Pulse spend 367 total shield, plus 2 shield per
elapsed 16 ms frame from decay. Explicit NPC removal retires
its shield lease.

The common 0B8B graph also processes Lucio's earned charge.
Its ordinary passive loop gives five points per second. Actual
Soundwave damage can leave the meter one point below threshold,
and effective Crossfade healing to Tracer can finish the charge.
The cast spends that meter, suppresses healing gain during
windup, and allows healing gain again after the ability ends.
The damage entry credits resolved base-pool loss. A Soundwave
that spends only temporary shields gives zero charge. Actual
Sonic Amplifier contact then credits the base damage after the
remaining temporary shield is spent. The selected Roadhog tag
branch credits 18.75 for a 25-point Soundwave.

Native propagation, exact landing and sparse-clock timing,
overlapping sources, nondefault charge rules and native actor
filters stay open.

### Crossfade and Amp It Up

The actual 0266 search selects nearby visible allies and self.
Each receiver has a separate status component running 0267 and
its 07C7 movement controller. That component mirrors the current
source song and Amp inputs into local slots. The shared health
and mover services consume the resulting graph state.

Actual movement verifies 25% ordinary and 60% amplified speed.
Switching to the healing song restores Tracer's base speed.
Ordinary healing gives Tracer 16 health/s and Lucio 16/1.3.
Amp raises those rates to 52 and 40. Two ordinary sources do
not stack healing in the tested scene. Blink can leave and
reenter range. Static cover and a team change stop healing.
Actual Ana anti-heal blocks it until the debuff expires. An
applied Amp keeps healing through an actual Flashbang stun.
The timed boost ends on the world clock with Lucio's command
frame idle. With input running, early recast is rejected and
a new press works after the authored cooldown.

Each of the five extracted Retribution NPC classes heals
alongside allied Tracer at 16 health/s and 52 health/s with Amp.
A team change drops the source, switching back restores it,
and explicit NPC removal clears its receiver. During a manual
Assassin approach to Tracer, the capsule mover consumes the 07C7
speed scalar. The test measures +25% ordinary speed, +60%
amplified speed, and a return to +25% after Amp expires.

The lane input `lucio-aura-supplement.json.gz` adds 0267, 07C7
and effect 00A4 from lane 0079's
`lucio-status-supplement-rebuilt.json.gz` to the Sound Barrier
supplement. Load it through `ORPHEUS_GRAPH_SUPPLEMENT`. Its
SHA-256 is
`b2829f2be7a5da64c30e26b47791f6c1bd080444ededd2c0bc85be220afae176`.

Native search, source priority outside the tested overlap, exact
timing for source changes inside skipped world intervals, other
NPC locomotion and autonomous NPC behavior, and original-client
presentation stay open.

### Wall Ride and wall jump

Actual 0263 input acquires a static side wall and progresses
through contact phases 2, 3 and 4. The host uses paired capsule
height rays, a static plane test and the existing capsule sweep.
The 104319 hero setting enables jumping on release. Leaving a
finite wall ends contact, and releasing Jump queues the authored
velocity rows for the next pose. The mover snapshot stores the
contact and pending jump. Low and distant surfaces reject
acquisition.

Tracer's actual pistol pair deals 12 to a moving Lucio without
ending the ride. A lethal pair clears contact and pending jump.
Actual Flashbang also interrupts the ride without creating a
wall jump. An aimed Pulse sticks to Lucio during the ride,
follows him after release and kills him with Tracer attribution.

Moving-surface contact velocity, native contact sweeps and
phase math, settings beyond the default path, remaining combat
pairings and original-client presentation stay open.

## Moira Biotic Grasp

Actual secondary input opens F76 state 106 and publishes Tracer's entity
to variable 375. The same posed command consumes state 22's authored
damage and state 45's self-heal. At 16 ms per frame, 20 hits deal 16
damage and heal Moira for 7.68. The authored node 48 expression recovers
fuel on actual damage. Misses, allies, out-of-range targets and static
cover give no damage or self-heal. Release clears the selection, Recall
breaks it until reacquisition, and lethal Tracer fire ends the attack.
A Winston bubble absorbs the breaking damage frame without healing
Moira. The next frame can acquire and damage Tracer.

Actual primary input emits F76 state 6 spray packets. A single packet
heals allied Tracer for 3.5, followed by 35 over the two-second trailing
effect after a 0.2-second delay. Release stops new packets and the tail
expires. Primary consumes authored fuel, refuses to emit when empty and
works again after recovery. Team, range and static cover block healing.

Damage selection uses reconstructed camera projection, capsule volume
and visibility. Spray uses a widened capsule sweep and a refreshed
nonstacking trailing effect. Native rank and tag filters, effect hosting,
other defenses, full charge, NPC pairings and original-client
presentation remain open.

## Moira Biotic Orb

Actual 10E4 input releases the 1E61 damage or 1E60 healing orb. The
server uses the reviewed 1100 and 10FF aura resources for the damage
and healing profiles. A damage orb deals 16 over 20 frames to enemy
Tracer and spends 16 of its 200 energy. A healing orb restores 20.8
over 20 frames to allied Tracer and spends 20.8 of its 300 energy.
A launch-owner ruleset scalar is sampled at release. Actual pool
changes, including amplified healing, cannot exceed the remaining
budget.

Both variants bounce from static geometry and their auras respect
static cover. A released damage orb persists after lethal Tracer
fire. Recall suspends damage and energy spending until reacquisition.
A damage orb can exhaust the full 200 budget across bounces. An orb
with remaining energy expires after 7 seconds, and repeating one
world frame does not move it or spend energy twice. Canceling
selection starts no cooldown. Early attempts cannot release either
variant during the shared cooldown, and a fresh press after expiry
can switch variants.

Genji's actual Deflect transfers the damage orb to his team, after
which it damages the previously allied Tracer. Sigma's actual Grasp
consumes the damage orb before it reaches Tracer and receives the
nominal 100 from the release row. D.Va's held Matrix consumes both
variants before they affect Tracer. Orb energy participates in
server snapshots.

The point sweep, tether visibility, slowed flight and defense poses
are reconstructed. Native aura and projectile presentation, heal
orb reflection, all defense orderings, owner replacement credit and
the complete NPC matrix remain open.

## Moira Coalescence

Actual 1007 input requires and spends 2800 charge. The live graph
supplies the 0.5-second startup and separate 30 m ally and enemy
volumes. TrackTargets writes ally and enemy arrays to variables 1184
and 1182, and each ModifyHealth state reads its own array. Over 20
normal command frames the human damage branch deals 22.4 to enemy
Tracer, heals allied Tracer for 44.8, or heals Moira for 16.

The beam can heal allied Tracer and damage an enemy behind her on the
same frame. It passes through Winston's bubble without damaging the
bubble and through active Genji Deflect without reflection. Static
cover and range exclude Tracer. Blink avoids the beam after the new
position is published. Recall stops damage until protection ends.
Actual lethal Tracer fire and Flashbang interrupt the active beam.
The natural end and a subsequent charged press are verified. The
fixture fills the meter directly for the recast.

Capsule acquisition and sampled visibility are reconstructed.
Native target filters, other mode branches, full ultimate gain,
command gaps, NPC pairings and original-client presentation remain
open.

## Wraith Form, Fade and Cryo-Freeze

The host completes the zero-timing 54FEB21C entry used by these three
stock graphs. Their active graph states and entity variable 2575 drive
combat admission. Actual pistol rounds and Pulse pass through Wraith Form
to the player behind. Pistols pass through Fade and stop at Cryo-Freeze.
Quick melee deals no damage during each protection and resumes afterward.

Fade detaches a Pulse already on Moira. Actual movement takes her out of
the remaining blast. Wraith Form can cancel after its initial lock,
rejects an early recast and works again after cooldown. Wraith Form and
Fade also retire an existing Flux lift before slam. Native collision masks,
Cryo-Freeze healing and remaining Pulse and cross-hero cases stay open.

## Zarya Particle Barrier

The actual 0244 and 025C Pet activations bind to individual 200-point
pools. Projected Barrier selects a live allied body in the authored
range and the host's eye-relative cone with static line of sight.
Damage spent on either pool reaches the caster's 0242 energy receiver.

A pistol pair spends 12 and grants 2.4 energy. A six-point barrier
stops the first round and the second round hits the body. Melee spends
30 and grants six energy, which increases the return beam's damage.
Pulse spends five on contact, then the fuse spends the remaining
barrier health without damaging Zarya. Contact that breaks a weak
barrier releases the attachment and the original fuse keeps running.
The personal barrier rejects an early cast and protects again after
cooldown. Replacing the projected target body invalidates its binding.

Collision uses the protected body capsule. Native surface and energy
timing, full replication, Zarya's remaining attacks and other cross-hero
and NPC combinations remain open.

## Zarya secondary and Graviton Surge

Actual 0241 secondary launches at speed 25 with acceleration 9.8.
One exposed splash deals 47. A wall or allied team prevents damage.
Blink can move away after launch and Recall protects against a nearby
wall impact.

Graviton rejects an underfunded press and spends 2100 when charged.
Its 025F launch uses the authored acceleration and deploys the
0260 field on impact. A direct hit deals five. The six-metre field
lasts four seconds and deals 20 more to a continuously exposed Tracer.
The world clock pulls Tracer even without input and releases her at
expiry or caster-body replacement. Charge gain pauses during flight
and the field. Blink can escape before impact, and Recall prevents
acquisition while active. The field can reacquire after Recall ends. With
the field already active, a Blink across its edge between two outside
positions avoids damage and pull. A Blink landing inside takes damage and
is pulled on the next world tick. The fixture positions Tracer before each
cast, then uses actual Blink input and checks charge consumption. After
capture, the host rejects new Blink and Recall presses. First capture also
discards pending Blink and Recall input. An actual Recall press during its
cooldown is discarded whether released or held through Graviton expiry. A rejected press
held through expiry does not cast. Fresh presses work after release and
primary fire works while pulled. An actual Pulse Bomb cast during pull
spends charge, sticks to Zarya for 5 damage and detonates for 350. Tracer
can finish Zarya with pistol fire. The field still damages and pulls until
its scheduled expiry. Two allied Zaryas can each deploy a field on Tracer.
Both fields deal damage while overlapping. The last field continues to
pull and reject mobility after the first expires, and fresh presses work
after the last expires. The mobility gate follows
the [July 2017 mobility lock](https://overwatch.blizzard.com/en-gb/news/patch-notes/live/2017/07/).

The runtime graph closure omits 0261. The host uses the damage and
pull scalar from its extracted 104319 XML. Interpreting the scalar as
a movement speed, capsule and static LOS admission, native ability
locks during capture, NPC movement and
replication remain separate acceptance work.

Actual active Genji boxes reflect the tagged secondary projectile and
Graviton. The returned secondary deals 47 to Zarya. The reflected field
belongs to Genji and his team, protecting his allied Tracer. Rear
approach retains Zarya as owner. Matrix and Grasp absorb both
definitions without a field. Winston's bubble spends 5 on Graviton
contact and the field starts on its approach side. The bubble shields
Tracer from a secondary explosion even when that blast breaks it.

Definition-tag filtering, eye-space box transforms and contact order are
host policies. Native reflection and absorb filters, returned visuals,
full multi-defense ordering and field interaction with a live shield
remain open.

## Mei primary frost

Actual 05E6 spray damage adds frost to each hit body. The reconstructed
0B77 aggregation keeps per-source progress, applies a movement slow and
clears partial frost after a one-second gap. Sustained stock spray in
the fixture freezes Tracer for 1300 ms, locks movement and aim, and can
interrupt a charged Pulse during windup. A fresh Blink works at thaw.

Blink can escape before freeze. Active Recall clears partial frost and
rejects further spray damage and frost. Static cover, teams and Winston's
bubble prevent exposure. A spray projectile hits each eligible enemy
once, including Tracer behind another enemy. Body replacement clears
the old frost state.

Native effect hosting and replication, full immunity and source
combinations, Icicle and Blizzard acceptance remain open.

## Mei Blizzard

Actual 064A input requires 1610 charge. The snowball flies at 20 m/s with
authored gravity, passes through bodies and deploys on static contact.
The host applies the extracted 065E values. The area starts after
200 ms, covers a 10-metre radius and lasts 4250 ms. A continuously
exposed Tracer takes 85 damage and freezes as area frost accumulates.

Two Blinks with the authored interval escape before activation. Recall
clears partial frost and rejects exposure while active. Static walls and
allied teams exclude targets. Deflect transfers the snowball and area
owner, while Matrix stops deployment. Death during windup prevents release.
A released snowball can still land after death. When Tracer sticks Mei with
Pulse Bomb during an active Blizzard, one Blink clears the bomb's blast
while staying in the field. Mei dies, and the field still freezes Tracer
and deals its full 85 damage before expiring. A later throw can stick
before Tracer freezes and detonate while she remains frozen. If freeze
arrives during windup, the cast spends charge but releases no bomb,
including after thaw. Body replacement removes
the old area.

Native field hosting, focused dynamic wall and defense ordering checks,
replication, and the remaining player and NPC combinations stay open.


## Mei Ice Wall

The 0622 preview handles rotation, cancellation and confirmation. A successful
cast places up to five 400 HP pillars for 313 simulation ticks, with a 12 second
cooldown. The host reconstructs pillar volumes, placement clearance and player
collision. Tracer's pistol rounds and melee damage individual enemy pillars.
Blink and ordinary player movement consult the same pillar volumes. Ordinary
projectile contact, splash visibility and Blizzard visibility use the wall
geometry.

Pulse Bomb can stick to a pillar, dealing 5 on contact and 350 at the end of
the original fuse. Detonation checks pillar cover before applying pillar damage
so a body behind the pillar remains covered for that blast. Early wall removal
clears the attachment and preserves the bomb's world position and remaining
fuse. Recall reaches an unobstructed historical destination across a new wall.

If a pillar occupies the destination when Recall finishes, choose the first
clear newer position from the cast-time history. If every recorded position
is occupied, retain the current position. Health and ammunition still use
the original Recall rules. A wall removed before completion leaves the
original recorded destination available. Player movement uses the pillar
top as ground and supports standing, jumping, landing and walking off.

Actual Pulse Bomb casts verify both orders of Ice Wall and Winston barrier.
The first surface receives contact and detonation damage, and the rear
surface is occluded. An Ice Wall between Tracer and the barrier also blocks
pistols and melee from damaging the barrier.

A placed Ice Wall also prevents manual selection of an Enforcer behind it
and suspends an existing attack while it blocks line of sight. Removing the
wall allows the existing Enforcer attack to resume and Tracer's pistols to
hit. Assassin's manual pursuit planner checks pillar clearance during both
search and movement. Assassin detours around an active wall before pinning Tracer.
Raising a wall during a pin releases Tracer and stops its damage. Native
autonomous navigation and pillars raised directly through NPCs remain open.

The remaining combined barrier and wall cases, native Recall fallback,
native contact geometry, wall replication and original-client comparison
remain open.


## Mei Icicle

Actual 05E6 state 16 releases projectile 108B at 115 m/s. The host
reconstructs the omitted 2× critical multiplier for that volley and uses
Tracer's standing head region. A body hit deals 75 and a head hit deals
150 with critical kill attribution against full-health Tracer. Icicle adds
no frost. Ice Wall cover, team checks, Blink, Recall, Winston barrier,
Deflect, Matrix and Grasp have dedicated input cases.

Actual Tracer fire kills low-health Mei before release and prevents the
shot. Killing Mei after release leaves the shot in flight and its later hit
awards her no ultimate charge. Hero replacement retires the outstanding
unreflected shot. Native head geometry for other postures and bodies, the
remaining defense combinations and exact projectile wire behavior are open.


## Ana Sleep Dart

The actual 0BDD ability launches projectile 15E8 at 60 m/s. A direct hit
deals 5 and installs effect 026F for 313 simulation ticks. The server uses
the 224E health-message branch timing. Damage in the first 0.5 seconds
leaves the deadline intact. Later damage shortens the remainder to at most
0.5 seconds unless it carries the excluded 191F tag. Sleep and other
control deadlines are tracked independently.

Actual input verifies suppressed movement and attacks, an interrupted Pulse
Bomb windup without a refund or delayed throw, and a fresh cast after sleep
expires and charge is restored. A Pulse Bomb released before the dart hits
still sticks to Ana and kills her while Tracer remains asleep. Blink, Recall,
static cover, teams,
Winston barrier, Matrix and Genji Deflect verify distinct avoidance or
interception paths. Lethal darts and body replacement clear control.
Native animation and status transport, tag-specific source coverage, and
the remaining defense and NPC combinations are open.


## Ana Biotic Grenade

The 0B97 ability throws 1426 with an initial speed of 30 m/s and an authored
downward acceleration of 9.8 m/s². Effect 0267 leads to the 0C71, 0B99 and
0C72 resources used for the reconstructed four-second healing modifiers.
The 4 m blast deals 50 to enemies and denies healing. It heals allies by
up to 100 before installing a 1.5× healing multiplier. Actual Soldier
Biotic Field pulses verify both modifiers and healing after expiry.

Recall restores historical health and clears the active healing block in
this host reconstruction. With an actual grenade and Mei spray both applied,
Recall clears partial frost on entry and blocks new frost through warmup and
playback. Anti-heal persists during both phases, then clears on completion
before its normal deadline. Mei can apply fresh frost after Recall. A direct
graph abort during either phase preserves the grenade's original anti-heal
deadline and starts Recall cooldown. An active allied Biotic Field heals
only after that deadline. Static
cover, Winston barrier, Matrix absorption and cooldown reuse have actual input tests. Native status transport, other healing sources,
remaining defense combinations stay open.


## Ana Nano Boost

Ana's 0B9E graph supplies the 2100 charge requirement and the delayed
release. The host uses a 40 m allied cone with a reconstructed target
ranking and visibility check. Release applies up to 250 healing and an
eight-second modifier of 1.5× outgoing and 0.5× incoming damage.

Actual team-mode inputs verify Tracer's pistols, quick melee and Pulse
Bomb, and enemy rifle damage against Tracer. Pulse retains release-time
damage through its fuse after Nano expires. Reflection updates the source
modifier when ownership changes. Native target ranking, reflection with
active Nano and the remaining lifecycle and defense cases remain open.


## Sombra EMP

Actual 0B31 release resolves a radial target search and mounts 0B32 on a
selected Tracer body. The 0B1F child effect applies the five-second ability
lockout. EMP spends 1400 charge on a valid cast even when the search finds
no target. Static cover, range, teams and Recall are verified gates.

EMP can drain shields without spending base health. The test gives Tracer
an explicit shield pool because her stock body has none. Pulse Bomb that
has already been released survives EMP. A pending release is canceled
without a refund.

The 0B32 shield branch applies its authored 10000 damage to a raised hostile
Reinhardt barrier when its anchor is in range and visible through the static
scene. The host uses the owner body ID to carry the barrier target. EMP
still applies the lockout to an independently visible Tracer behind the
barrier.

The same branch ends personal and projected Zarya barriers using the
protected body's capsule for range and static visibility. Tracer can
receive the lockout after her projected barrier breaks. EMP's barrier
damage leaves body health intact and grants no Zarya energy. Native
barrier entities, other deployable barriers, and other body hosts remain
open.

## Pharah Rocket Launcher against Tracer

Actual primary input at eight metres deals 40 direct and 80 splash damage
without a critical flag. A fresh shot after recovery kills the wounded
Tracer. A wall impact on the same side as an off-axis Tracer deals 80
splash alone. Miss, range, a wall between the impact and Tracer, and
allied team membership prevent damage. Recall rejects contact and Blink
evades a rocket already in flight. Tracer's pistol pair deals 12 to Pharah.
The primary rocket has no authored projectile entity. Its Pharah
`0023` state 6 launch marks it eligible for the existing D.Va Defense
Matrix sweep. The active hostile Matrix consumes the rocket before body
or splash damage. An inactive or rear-facing Matrix leaves the exposed
mech vulnerable, and an allied Matrix lets the rocket hit Tracer.
Genji's active frontal Deflect also admits this launch. It transfers the
existing rocket and its remaining lifetime to Genji's team, credits the
40 direct and 80 splash damage to him, and leaves Tracer unharmed.
Rear-facing and friendly Genji cases keep the original owner. Matrix
admission follows the new team after reflection, so a Matrix allied with
Pharah can stop the reflected rocket before it returns to her.
Sigma's frontal Kinetic Grasp consumes the primary rocket before impact
and feeds the full 120 damage budget to the authored accumulator. Tracer
stays unharmed. Rear-facing and friendly cases grant no absorption.
When Grasp ends, its shield request grants 72 temporary shields. A second
rocket spends those first and removes 48 from Sigma's base shield pool.
The primary rocket also uses the existing live shield sweep and splash
visibility check. Actual Winston and Reinhardt barriers each spend 120
per direct rocket contact, and Tracer stays protected from splash on the
far side. Friendly shields pass the rocket. Winston's sixth impact breaks
the bubble without hurting Tracer. The empty launcher rejects the next
fire input. After reloading, the next rocket reaches Tracer.
Sigma's deployed C015 mesh shares the shield sweep. It spends 120 on a
hostile rocket and protects Tracer within splash range. A friendly mesh
allows the rocket through. Recalling the hostile barrier opens the shot,
the immediate deploy input is rejected, and redeploying after cooldown
restores interception.

Zarya's projected barrier spends 120 on a direct rocket and the remaining
80 on the next rocket. The two impacts award 24 and 16 energy to Zarya.
The breaking impact leaves Tracer's health intact and the following rocket
reaches her. An off-axis wall impact instead spends 80 from the barrier
through splash alone and awards 16 energy. Native volume parity, remaining
defenses, impulse, other Pharah abilities and NPC combinations
remain open.

## Pharah Barrage against Tracer

The actual 002B volley rejects a cast below its authored charge cost,
spends the full charge when it starts, and continues after release.
The channel ends and all launched rockets eventually retire. A new cast
requires restored charge.
The first rocket deals 10 direct and 30 splash damage to an exposed
Tracer. Team filtering, static cover and Recall prevent damage during
the first rocket's approach.

An attached Pulse Bomb can kill Pharah during an active channel. Her
volley stops reporting new rockets, including while ultimate input is
held after death. Rockets already released keep advancing and retire
normally.

Barrage projectile 0052 uses the shared barrier sweep and splash
visibility checks. The first rocket spends 40 against an enemy Winston
bubble and protects Tracer on the far side. A bubble on Pharah's team
allows it through. With the extracted projectile tags, Genji reflects
the first rocket for 40 return damage and Sigma Grasp absorbs it for 40
accumulated damage. D.Va's frontal Matrix protects both defenders from
consecutive rockets. Zarya's projected barrier absorbs five 40-point
impacts, including the breaking hit, and grants her 40 energy. The next
rocket reaches Tracer.

With fixed aim and no defense, four rockets bring stationary Tracer
from 150 to 110, 70, 30 and 0. Only the final splash marks the kill.
Further rockets launch without hitting her dead body. The volley reports
76 launches before ending 156 ticks after the cast. The last rocket
retires at tick 188 relative to that cast.
Tracer can Blink sideways before the first impact or after taking 40.
The volley continues without hitting her outside the firing line. A
second Blink back exposes her to the next rocket. Recall restores the
pre-hit health and position, and damage resumes after its protection
ends. Pharah movement, native spread, nonlethal interruption, remaining
defense combinations and NPC cases stay open.

## Pharah Concussive Blast against Tracer

The actual 007B launch creates projectile 010F. Its zero-damage explosion
uses the authored 8-metre ring and horizontal 20 and vertical 5 values
through the reconstructed mover impulse. A direct hit separates Pharah
and hostile Tracer without changing health or emitting a hit notice.
Allied Tracer, static cover, Recall and a hostile Winston barrier reject
the push. Concussion leaves the barrier's health unchanged.

The actual ability rejects a fresh press during cooldown and launches
again after recovery. D.Va Matrix absorbs the projectile before impact
and prevents the push that reaches the inactive mech. Genji transfers
its ownership and redirects the push to Pharah. Sigma Grasp absorbs it
without accumulating damage or granting shields. Native force conversion,
remaining defenses and NPC cases stay open.

If Concussive Blast lands during Pulse Bomb windup, Tracer is displaced
and the charged throw still releases from her new eye position. After
release, the same bomb continues along its original lane while Tracer is
pushed away. Neither case damages Tracer.

In the actual Trooper fixture, Concussive Blast moves Tracer toward the
NPC without damage. Tracer's next pistol pair deals 12 to the Trooper,
and its authored primary deals 20 to her after manual attack selection.
Recall restores her original position and health. The Trooper hits
again after protection expires. NPC knockback and autonomous target
selection stay open.

In the manual Enforcer and Sniper fixtures, Tracer Blinks after the first
round launches. That projectile passes her old position or retires
without damage. Subsequent rounds hit at her new position for 2 and 100
damage respectively, and her next pistol pair deals 12 to each NPC.

With low health seeded for the lethal exchange, Tracer can kill the
Enforcer or Sniper after the first projectile leaves its weapon. Death
stops new launches and retains the corpse. The released projectile
still hits for 2 or 100. The Sniper keeps kill attribution if the
posthumous shot kills Tracer.

The reverse timing uses an actual lethal Sniper round against Tracer
with 100 health seeded. If she has already released Pulse Bomb, that
same bomb continues and kills the Sniper with attribution to Tracer's
original body. If the round kills Tracer during windup, no bomb appears
and the Sniper survives. The accepted input spends ultimate charge in
the windup case.

Recall can begin after the Enforcer or Sniper has released its first
projectile. Tracer stays at 150 through the approach and returns to the
same position. Newly launched attacks hit after protection ends for 2
and 100 damage respectively under manual target selection.

The controlled journal records manual NPC spawn and attack selection
alongside encrypted player input. A flat two-team scene replays a Tracer
pistol pair for 12, Trooper retaliation for 20 and the stop command,
matching the packet effects and checking the final health and attack
state. The verifier rejects a changed spawn position or an attack
command changed to stop. Full battle replay remains open.

With charge seeded six points below the 1260 cost, a real Tracer pistol
pair deals 12 to a hostile Trooper and fills the meter. The next Pulse
input spends that charge, sticks to the Trooper and kills it. A miss
or a shot at an allied Trooper leaves the meter undercharged and the
same Pulse input is rejected.

With a Trooper seeded at four health and Tracer six points below cost,
the lethal pistol volley credits four points. A new volley spends ammo
but gains no charge from the corpse. The following Pulse input remains
two points short and creates no bomb.

With passive gain disabled, the first charged Pulse kills a Trooper
and leaves charge at zero. Tracer then shoots an actual Heavy Assault
and manually reloads until the meter fills from damage. The second
Pulse spends that charge, attaches, deals 350 blast damage and retires.

Once the pistols can fire after release, a pair dealing 12 to Heavy
Assault adds 12 charge while Pulse is airborne or attached. The attached
bomb's 350 blast damage adds zero charge. Pistol damage continues to
credit charge after the bomb retires. Passive gain is disabled in these
two tests.

## Soldier: 76 Helix Rockets and Biotic Field

An actual ordinary 072C rocket deals 40 contact and 80 blast
damage to Tracer. Holding the button and pressing during the
cooldown do not launch another rocket. A fresh press after
cooldown can kill Tracer, with attribution to Soldier. Tracer
can return a 12-point pistol pair after a nonlethal hit. Blink
after launch and active Recall each avoid the rocket. Static
cover and the same-team case prevent damage.

Ordinary Helix now uses the existing barrier and projectile
defense consumers. An opposing Winston dome spends 120 health
and protects nearby Tracer from splash. A dome allied to Soldier
allows the shot through. Actual Genji Deflect redirects the
rocket to Soldier. Kinetic Grasp accepts its 120-point budget,
and Defense Matrix consumes the rocket before it reaches Tracer.
The reflect and absorb cases use extracted 104319 projectile
tags, including 02F9 on 072C.

Soldier's actual Biotic Field cast heals allied Tracer at 35
health per second after deployment. Blink out of range stops
healing, and Blink back in restores it. An opposing team does
not receive healing. Early reuse does not create a second field.
The first field expires, and a later cast heals Tracer again.

### Tactical Visor

Actual 0259 rejects undercharge and consumes a full meter.
Its default state 7 search activates after 1.2 seconds and runs
for six seconds. The server reads the 45-metre and 45-degree
search fields, tests the target capsule against the cone and
static visibility, and publishes the selected body IDs.
Ordering by angular alignment, distance and body ID and
aiming at the rewound capsule centre are reconstructed policies.

An off-target rifle input deals 20 to Tracer only while the
search can select her. Static cover, distance and team gate
selection. Blink can leave and reenter the cone, and Recall
removes Tracer until the rewind ends. A lethal Tracer pistol
pair ends the Visor and clears its target list. A subsequent
uncharged press does not restart the ability.

Actual ordinary rifle fire falls off at 40 metres. The same
shot during Visor deals 20 through 0254's authored Visor
falloff scalar. Soldier earns five passive points per second
through 0B8B. Actual rifle and Helix pool loss can bring the
meter to one point short, and Biotic Field healing can fill it.
The cast consumes that charge. Rifle hits during Visor give
no charge, and the first hit after expiry credits damage again.

During Visor the actual 0254 volley spawns 1C86 Helix. The
rocket follows manual aim even with Tracer selected, consistent
with Blizzard's [December 2018 patch notes](https://overwatch.blizzard.com/en-us/news/patch-notes/live/2018/12/).
The variant also loses its 120-point damage budget to an opposing
Winston dome and deals no splash damage to nearby Tracer.

### Sprint

Actual forward Sprint reaches 1.5 times ordinary run speed.
A stationary press is rejected and releasing forward movement
ends Sprint. A nonlethal Tracer pistol pair deals 12 without
ending the run. An attached Pulse Bomb follows the sprinting
body for more than three metres, then kills Soldier with
attribution to Tracer and retires.

Native target ordering, precise aim and filter semantics,
alternate Visor searches, nondefault charge settings, remaining
Visor defenses and Sprint controls, other Soldier and NPC
pairings, and original-client acceptance stay open.

## Hanzo primary bow against Tracer

A full draw releases one 110 m/s arrow on button up. The host
now applies the graph's 9.8 m/s² acceleration to its flight. In the
12 m body-shot fixture it deals 125. Blink sidesteps the
released arrow and Recall protects Tracer before impact.
The ordinary full and short draw arrows (0144 and 020B) now
use the existing projectile defense path. An allied Winston
dome stops each variant and loses the arrow's actual damage.
For a full draw, Genji returns 125 to Hanzo, D.Va Matrix
absorbs the arrow, and Sigma Grasp receives a 125-point budget.
Both Tracer and the defender avoid body damage.

A full-draw arrow removes 125 from Reinhardt, Sigma or Orisa's
deployed mesh. A 50-point mesh breaks without spill. A second
full draw after recovery reaches Tracer. A mesh allied to Hanzo
allows the first arrow through, while static cover in front of
a hostile mesh protects both mesh and body. Zarya's projected
barrier absorbs 125 or breaks at 100 without spill, crediting
25 or 20 energy. A fresh arrow after break damages Tracer.
Full and short Sonic Arrow and the first Storm Arrow now use
the same ballistic launch and defense path. A full Sonic hit
deals 125 and a Storm hit deals 70. A live Winston dome takes
the arrow damage instead in each variant. An explicitly mounted
Sonic profile reuses the target-build 010E/node 9 flag stack.
A full-draw body hit marks Tracer for Hanzo's team, follows
Tracer's translation and clears on expiry. A static wall impact
can mark Tracer across cover without damage and clears the flag
on range exit. Winston's dome intercepts the arrow before a
field forms. The six-second duration, nine-metre capsule search,
continuous refresh and feet-relative attachment use the existing
reconstruction. Pass --reconstructed-hanzo-sonic-profile PATH when
hosting locally. The external file is loaded once and mounted before
players join each new match. Short-draw and combined hits activate reveal
in server-created matches. Invalid input fails before lobby setup.
Genji Deflect transfers reveal ownership on the return hit. Kinetic Grasp
and Defense Matrix absorb the arrow without a field. A lethal hit leaves a
field without marking dead Tracer. After Tracer replaces their body, the
field stays at its last attachment point. An existing field survives
Hanzo's death and ends on departure. Overlapping fields release their
marks independently and preserve a prior flag. Hanzo marks all five Talon
classes. Allied Tracer lands pistol hits and all five marked NPCs return
expected damage. Assassin also applies its pin. A combined Sonic/Storm
ricochet deploys only at terminal body or static contact. Projected Barrier
prevents deployment. Remaining defenses, configured journal replay and client
parity are open.

Dragonstrike's authored 0101 phases now use their one-second windup and
1.2-second recovery in the server simulation. At 1680 charge, actual input
spends once and emits one 034F arrow after 1000 ms. It deals 125 to Tracer
at eight metres and expires before reaching Tracer at twelve metres.
Undercharge preserves the resource, and Tracer can kill Hanzo during windup
to prevent emission. The local server option
--reconstructed-hanzo-dragon-profile PATH loads the continuation profile
once and mounts it before player creation in each match. Both Hanzo profiles
can be enabled together. Dragons start at the arrow's flight limit or a
static impact. The reconstructed core applies 4.8 damage per 16 ms. Ally,
Blink and Recall defenses pass, and damage resumes when Recall ends. Kills
retain the original firing body after source body replacement. A flight
ends after 20 seconds or when its owner leaves. A live Winston dome stops
the initial arrow and prevents continuation. Moving dragons pass through
that dome while Zarya's projected barrier absorbs their damage. A complete
batch of Tracer movement uses the stored per-frame poses, preserving 33.6
damage across the core boundary. At three ranges, a 55-frame gap spanning
arrow-to-dragon transition preserves damage and age by splitting the first
sample at its world frame boundary. Confirmed command batches also retain
Tracer's Recall protection at each sampled frame. A batch entering Recall
preserves the earlier damage, and a batch ending Recall preserves the
protected interval. Moving dragons apply the first 4.8 damage to each of
the five Talon bodies while allied Tracer remains unharmed. Each NPC and
Tracer exchange weapon damage while the dragon is active. A wounded NPC
returned to the path dies with the original source credited, a death event
and its corpse retained. Genji can reflect the initial arrow and take over
the moving damage against Tracer. Sigma Grasp and Defense Matrix absorb the
initial arrow and prevent continuation. After the dragon forms, all three
active projectile defenses allow its damage through. Other defenses,
unaligned protection transitions, configured replay and native behavior
remain open.

Storm Arrow now retains its projectile across one reconstructed
specular wall bounce. An angled shot hits Tracer outside the
launch line for 70. A second wall stops it, and team checks,
Blink, Recall and Zarya's projected barrier still apply after
the bounce. The ordinary arrow stops at the first wall.
The combined Sonic and Storm volley keeps the Sonic projectile
definition and 70 damage while inheriting the one-bounce budget.
Sonic alone still stops at the first wall. Combined shots pass
the same damage, team, cover, mobility and projected-barrier checks.
Native restitution and other ricochet matchups remain open.

The script runtime rechecks state transitions after watched-variable
changes, so the authored Storm state ends when the fifth arrow spends
the last charge. Holding primary for 140 frames after combined activation
emits one Sonic and four Storm arrows. Winston's dome absorbs 350 damage
and Tracer stays at full health. A press during cooldown is rejected and
expires. A fresh press at zero cooldown emits five Storm arrows.

## Zenyatta Orb of Destruction

Actual 0164 primary and charged volleys launch 0236 and 0261.
The host consumes the authored 0.15-metre body-hit radius and
180-metre range. Both volleys deal 48 on body contact. The
selected Tracer head test reconstructs a 2× multiplier and
reports 96 with the critical hit flag. Tracer can return a
12-point pistol pair after either nonlethal primary hit.

Actual secondary release produces one orb on a tap and up to
five after charging. The charge increment checks the remaining
magazine, so a one- or three-round magazine cannot produce
extra shots. The test verifies the released damage and spent
rounds. Lethal Tracer pistol fire ends a held charge before
it can launch a projectile.

Static cover, team and range reject orb damage. Blink avoids
a launched shot. Active Recall excludes Tracer, and a new
shot damages her after recovery. Actual Winston barriers
spend 48 on each projectile type. Genji reflects the primary
orb back into Zenyatta. Kinetic Grasp absorbs the charged
orb before it can reach Tracer. Those defenses use the
extracted 104319 projectile tags.

Native head geometry and critical defaults, broader
interception and additional charge and reload controls stay open.

### Harmony and Discord

Posed orb input uses the 02F3 and 0176 cast search parameters,
including the 40-metre range, default 10-degree aim tolerance,
0.75-second recovery and three-second visibility grace. The
host reconstructs input, target ordering and effect settlement,
and writes the selected body to the source graph's 1606 slot.

Harmony heals allied Tracer for 30 per second. The test rejects
enemy, out-of-range and off-axis casts. Healing continues through
short static occlusion and stops when the grace expires.
Discord uses a reconstructed 1.25 incoming damage multiplier.
Actual primary orbs deal 60 to a marked Tracer, and an allied
Soldier rifle shot deals 25. An opposing Winston dome denies
the cast. Early retargeting is rejected, and a held input
requires a fresh press after recovery. Recall clears Discord,
prevents new application while active and allows a fresh cast
after recovery. Zenyatta death clears the link.

When a world update skips past retained attack frames, Harmony
settles effective healing and charge in frame order. An occluded
link restores 89.76 before its exclusive grace deadline and
clears its target slot on expiry. An uninjured, visible ally keeps
the link through a long open gap. After leaving range, it still
gets the full visibility grace. An actual Ana grenade blocks
Harmony until its deadline, including when the deadline falls
among the discarded frames.

An actual attached Pulse Bomb on Roadhog deals 6.25 at contact
while Discord is active. In sparse updates, a blast within the
retained frames deals 437.5 before Discord expires and 350 after.
The same damage applies when both the blast and the deadline are
earlier than the retained attack window. The host advances the
already admitted Pulse alongside orb effects in the skipped frames
using the latest known poses. In a separate flight case, Pulse
begins the gap unattached, sticks to Roadhog for 6.25 while
Discord is active, and explodes for 350 after it expires. Other
projectile families and unobserved historical poses remain open.

### Transcendence

The actual 017B cast rejects undercharge and spends the full
meter when admitted. The host completes its scoped zero gate
and derives the six-second world window from the authored
state 3 countdown. A wounded caster heals at activation.
Actual Orb of Destruction damage and effective Harmony
healing can fill the meter. Transcendence healing gives no
charge during the ability.

Tracer's pistols, melee and Pulse Bomb do no damage to the
active caster. Pulse can attach and explode while the caster
is immune. Actual Flashbang deals no damage and applies no
stun. The caster rejects weapon and repeated ultimate input.
The first Tracer pistol pair after world expiry deals 12 even
when the caster has sent no further commands.
An attached Pulse exploding before the retained attack window
still respects Transcendence. A blast during the ability leaves
Zenyatta at 200 health. A blast after expiry kills and records
the actual blast frame and time.
Pulse also attaches during Transcendence when the gap begins
in flight. The attachment deals no damage, and the matching
explosion cases preserve the same immunity and death timing.

The host applies 300 healing per second to a visible ally
within the 10-metre base radius. Allied Tracer receives 48
over ten frames. An enemy receives none. Static cover and
Blink beyond the radius interrupt healing. Healing resumes on
reentry and stops after the last active world frame.

Native orb acquisition and retention, multi-source effects,
nondefault charge rules, the native 0067 healing effect,
Transcendence movement and untested controls, and original-client
acceptance stay open.

Transcendence settles its remaining healing before the oldest
retained attack frame when a world update skips past the active
interval. The host uses the latest known poses for those frames.
An actual Ana grenade blocks healing until its exclusive deadline.
Then both continuous and sparse updates heal Tracer from 43.6 to
149.2 by the end of Transcendence. Tracer's next pistol pair hits
Zenyatta after expiry. An actual Winston dome and Mei Ice Wall
each stop healing and then expire in the discarded frames. The
remaining active frames resume healing in both cases. The attack
retention limit still applies. Other historical geometry and
support effects across that limit remain open.

### Zenyatta support effects on the five Retribution NPCs

Against each NPC, Tracer first deals actual pistol damage. An
enemy Ana grenade deals another 50 and prevents Harmony healing
and charge through the last blocked frame. Harmony stays linked
and heals 4.8 across the first ten eligible ticks. An allied
grenade heals 100 and increases ten ticks of Harmony to 7.2.
After Tracer wounds the fully healed NPC again on the last
boosted frame, the next tick heals 0.48. Effective healing
grants Zenyatta credit and leaves Tracer's earned charge intact.

Four server fixtures exercise Trooper, Enforcer, Sniper, Assassin
and Heavy Assault using the extracted graphs and authored capsules.
Tracer deals 12 with a pistol pair. Harmony rejects the hostile NPC,
then heals that damage after a team change and a fresh cast. It
restores 4.8 over ten frames, gives exactly 12 charge in total and
retires its link when the NPC is removed.

Discord raises the actual pistol pair from 12 to 15, clears when
the marked NPC becomes allied, and can be applied again after a
hostile switch and a fresh press. The marked NPC's manually
engaged attack still deals its normal damage to Tracer. Pulse Bomb
deals 6.25 on attachment. The blast deals up to 437.5 before health
clamping, with the surviving Assassin and Heavy Assault taking the
full amount. A lethal blast retires the link.

Transcendence spends its full meter. A wounded NPC gains 4.8 per
active allied frame sampled, including the last one. Hostile frames
and the first frame after expiry give no healing. The caster sends
no further commands and gains no charge from the healing.
Remaining NPC defense and control combinations, autonomous behavior,
native effect parity and client acceptance stay open.

### Roadhog Scrap Gun primary

Actual 01C6 node 26 consumes one of five rounds and launches
25 pellets using its authored ring counts and spread. The host
uses the shared tangent projection and fixed ring phase. At two
metres, the hostile torso volley kills Tracer on the 23rd hit.
Tracer's simultaneous pistol pair deals 12 to Roadhog. An
off-axis volley misses, and allied targets and static cover
prevent damage. Those three shots still consume ammunition.
Native spread phase, head geometry, primary range boundaries
and remaining Roadhog abilities and pairings stay open.

### Roadhog Scrap Gun secondary

Actual 01C6 node 29 consumes one round and launches a single
slug. A five-metre body contact deals 50 to Tracer. A wall
before expiry stops the shot without emitting fragments. At
expiry the host emits the 25-pellet child volley from the
authored callback and uses the remaining fraction of that frame
for child travel. At ten metres, 23 fragment hits kill Tracer
and the two surviving pellets retire when their lifetime ends.
Moving the capsule contact one centimetre to either side of the
expiry boundary selects direct slug damage or the fragment volley.
Neither path spends an additional round. Native callback timing,
critical geometry and remaining controls stay open.

### Roadhog shared magazine and projectile defenses

Alternating primary and secondary consumes one common five-round
magazine. Both inputs are rejected when empty. Auto-reload and
manual reload refill the magazine before the firing lock ends.
Held secondary and primary fire resume after the corresponding
reload finishes. The auto-reload test resumes with an actual
50-point slug hit on Tracer.

An actual Winston dome takes 165 from a complete primary volley
or the post-split fragments, and 50 from the unsplit slug. An
intercepted slug does not split. The allies inside the dome take
no damage in these fixtures. Actual Genji Deflect returns the
primary pellets and the unsplit slug to Roadhog. Mech Defense
Matrix and Sigma Grasp absorb both attacks. Grasp receives the
resolved 165 or 50 damage budget. The projectile filter uses
the extracted 0295 and 029A tags with the shared defense model.
Defense of split fragments beyond Winston, native filter parity,
reload interruption and additional controls stay open.

### Roadhog Chain Hook against Tracer

The actual 01C8 launch state starts a hosted sweep using the
authored range, radius and speed. The 01C9 pull data supplies
speed, minimum separation and recovery. A body contact deals
30 damage once and pulls Tracer to about three metres from
Roadhog. During the pull, Tracer cannot fire, Blink, Recall
or spend Pulse charge. A new pistol pair hits after release.
Once latched, the pull and release finish on world updates
without either player sending further commands.

Aim away, an ally, a static wall and a target beyond the
range all reject contact. An actual Blink leaves the sweep
and Recall prevents damage and pull. An actual Winston dome
takes the 30 damage without pulling Tracer. A press during
cooldown does not start a second launch. A fresh press after
expiry hits again.

The sweep hits one centimetre inside its configured range and
misses one centimetre outside. A new static wall breaks a live
pull. Actual lethal fire and departure release the hook when
either the caster or the target is removed. A surviving Tracer
can Blink after release.

Chain Hook interrupts an admitted Pulse windup before release.
The spent charge remains spent, and no delayed bomb spawns.
A Pulse already in flight survives the same control and kills
Roadhog in the released-bomb fixture. A batch that arrives after
release cannot execute its pistol and Blink inputs from the pull
interval. Fresh pistol input hits. The server projects the input
cutoff from the last observed command frame and world tick.

The server reconstructs the sweep and pull path. Mixed receipts
and additional client clock cases, other interacting controls,
full snapshot replay and original-client behavior stay open.

### Roadhog Chain Hook and Retribution NPCs

Actual Chain Hook damages and pulls Trooper, Enforcer, Sniper,
Assassin and Heavy Assault using their authored capsules. The
world owns their motion during the pull and manual weapon input
is suppressed. Tracer can land a real pistol pair on the moving
capsule. After release each class attacks and damages Tracer.
The Assassin enters attack range as a result of the pull.

An allied NPC stays untouched. A hostile NPC switching to the
caster's team releases the link. Tracer's lethal pistol damage
releases the NPC while keeping its corpse. Manual removal also
clears the link and forced velocity. A real Pulse attaches to a
moving Trooper, follows its pull and kills it after release.
The NPC tests use manual engagement. Autonomous behavior,
combined forced motion, other NPC defense cases, native parity
and client acceptance stay open.

### Roadhog Take a Breather

Actual 0240 input starts the damage stack and the two-second
world deadline. After windup, node 18 restores 300 to Roadhog
over the configured one-second interval. The server interprets
that interval as the healing duration and uses the shared health
pools. Other bodies receive no healing. Actual Ana anti-heal
blocks the restoration.

Tracer's pistol pair deals 6 while the stack is active. An
attached Pulse deals 2.5 on contact and 175 on detonation. A
detonation before expiry still gets that reduction when a world
gap skips past the retained attack window. The first tested
pistol pair after expiry deals 12 while the old body graph is
still waiting for input. A previously started heal settles its
remaining authored portion once when body commands resume.

Actual Flashbang stops the heal and damage stack. Early re-press
is rejected and a fresh cast after cooldown restores health again.
An actual Pulse can kill a wounded Roadhog during windup without
delayed healing. Historical anti-heal during skipped body input,
other status combinations, native parity and client acceptance
stay open.

### Roadhog Whole Hog

Actual 01EF input rejects undercharge, spends the 2240-point
meter on a valid cast, and ends without using Scrap Gun rounds.
A real 50-point secondary slug hit can supply the final charge.
Whole Hog damage does not replenish that meter during the cast.

The hosted 01C6 node 33 fires the authored 16-ray rings and reads
its force and distance values. A damaging volley applies one
force contribution per target. The selected torso fixture
receives about 10 m/s horizontal speed and 1.8 m/s lift, then
the ordinary collision mover displaces Tracer. Static cover,
allied targets and an actual Winston dome prevent damage and
force on Tracer. An uncharged re-press after expiry does nothing.
Actual Defense Matrix and Kinetic Grasp stop the volley before it
deals damage or applies force. Kinetic Grasp records absorbed damage.
Actual Genji Deflect redirects the volley toward Roadhog, transfers
hit credit to Genji and applies knockback along the reflected path.
An actual Flashbang or lethal Tracer pistol shot ends an active
cast. The volley stays off through the next 100 simulation frames.
The centre ray deals 7 damage at 25 m, 4.55 at 35 m and 2.1 at
99.99 m. Tracer's capsule contact at 100.01 m produces neither a
hit nor force. A head contact at 25 m also deals 7 damage, matching
the 104319 hit definition's 1.0 critical multiplier.

Trooper, Enforcer, Sniper, Assassin and Heavy Assault receive
actual damage and forced movement using their extracted capsules.
Tracer hits each displaced target. The server reconstructs ring
phase, force interpretation and movement. Take a Breather sends
effective self-healing to 0B8B. Actual casts verified 40 and 300
points of charge from restored health, a cast funded by self-healing,
rejection one point below cost, and zero charge under anti-heal.
An undercharged Q press is retained for the authored 150 ms. A
Scrap Gun slug that fills the meter within this window starts
Whole Hog. A later hit fills the meter and requires a new Q press.
Actual Whole Hog cannot damage or push Tracer during the
preparation or playback phase of Recall. The same cast resumes
damage and knockback after Recall ends.
A lateral Blink moves Tracer out of the aimed volley. Roadhog
can turn the same active cast to hit and push her at the new position.
The remaining defenses and original-client acceptance stay open.

### Bastion Recon fire against Tracer

Actual Recon fire at eight metres deals 20 damage to Tracer and
receives kill credit when lethal. Aim miss, same-team targets and
static cover prevent hits. Tracer's pistols damage Bastion in the
clear hostile lane, including when Bastion aims away.

The 0x012E weapon graph has a 35-round magazine and a 100 m reach.
Actual shots dealt 20, approximately 13 and approximately 6 damage
at 20, 35 and 99.99 m capsule contact. The 100.01 m shot missed.
Each shot spent one round. The server now notifies the authored
reload gate when the volley reaches zero rounds. On the 16 ms
test clock, held Recon fire empties all 35 rounds over 265 frames.
The magazine refills 56 frames later and held fire hits at frame
104 after depletion. Sentry empties all 300 rounds over 622 frames,
refills 107 frames after depletion and hits at frame 135.

Manual reload refills at frames 45 and 96 after the input for
Recon and Sentry respectively. Held fire next hits at frames 93
and 125. Flashbang before or after refill leaves the refill clock
intact. Fire resumes when both recovery and stun have ended.
An actual Sleep Dart before refill also preserves both refill
clocks. Held fire consumes no rounds during sleep and hits on
the first awake frame when reload recovery has already ended.
Self-Repair cancels a pending refill. After refill it preserves the
loaded magazine and the recovery timer finishes on schedule.
Reconfigure during reload preserves the Recon magazine on return
and refills Sentry on re-entry. An early fire tap during the
transformation produces no hit. Fresh fire hits after returning.
Lethal Tracer pistol fire before refill cancels the pending
reload for both weapons. A kill after refill leaves the loaded
magazine and the original recovery deadline. The Sentry exit
graph can refill the old magazine separately, 124 frames after
death in this fixture. Held fire on the corpse deals no damage.
Normal respawn allocates a new body with 35 Recon rounds. Recon
hits again, and redeployed Sentry hits with its own fresh magazine.
Original-client timing, other interruptions and NPC interactions
stay open.

Reconfigure blocks held fire until the first Sentry shot at frame
62 and the first Recon shot at frame 31 on the return transition.
At eight metres those shots deal 15 and 20 damage and spend one
round from their own 300-round and 35-round magazines. Releasing
Sentry fire stops the volley, and Tracer can hit Bastion in Sentry
form.

Sentry dealt 15, 9.75 and approximately 4.5 damage at 20, 40 and
99.99 m capsule contact. The 100.01 m shot missed. Blink leaves
the aimed stream of either gun, and Bastion can turn to hit Tracer
again. Both guns spend rounds in both Recall phases without
damaging Tracer. Held fire hits after Recall ends.

An actual Winston dome absorbs the full first shot from each
weapon. Genji Deflect redirects both weapons to Bastion and
credits the hit to Genji. D.Va's active Defense Matrix and Sigma's
Kinetic Grasp intercept both weapons and protect Tracer behind the
defender. Grasp stores 20 for a Recon round and 15 for a Sentry
round. Bastion can damage Tracer once the lane clears. Other
defenses, remaining Sentry lifecycle and NPC interactions stay open.

Recon and Sentry each hit the five extracted Retribution NPC
capsules. Their first torso shots settle against the authored
health and armor pools and report the NPC entity as the hit target.

### Bastion Self-Repair

The 0x0153/3 state applies its authored 90 health per second while
active. Its current and maximum health queries read the live health
pools. The existing resource graph drains the meter while Bastion
is wounded, stops at exhaustion and regenerates after recovery.
Actual Tracer pistol fire and a manual pistol reload keep Bastion
wounded long enough to verify exhaustion, rejection and reuse.

Normal release ends healing after the minimum repair interval.
Flashbang takes the authored recovery branch immediately, even
within that interval. A fresh input resumes healing after control.
Healing caps at full health without further resource loss. An actual
Ana grenade blocks healing until anti-heal ends. A stuck Tracer
Pulse Bomb can kill during active repair and ends further healing.
Other interrupts, sparse inputs, NPC interactions and original-client
acceptance stay open.

Actual Sombra Hack and EMP block new Self-Repair, Reconfigure and
Tank inputs for 313 ticks. Both end running Self-Repair and prevent
its deferred exit from applying more healing. A fresh press after
expiry heals again. All three weapons can still fire, and all five
already-started mode transitions retain their completion clocks.
EMP respects range, cover and team when applying Hack to Bastion.
Ana's Sleep Dart blocks Tank fire and interrupts active repair.
Fresh fire or repair works after the sleep deadline.
Mei's actual spray wounds Bastion and lets Self-Repair run during
partial frost. Full freeze enters repair recovery immediately.
No more healing is credited during the 1300 ms freeze. A fresh
repair input after thaw heals again.

### Bastion Configuration: Tank

An actual Recon hit gives 20 charge and effective Self-Repair
can fill the remaining charge. Anti-heal blocks healing and the
associated charge. The authored 0x0B8B loop grants five passive
points per second, caps at 2310 and pauses until Tank recovery ends.

An undercharged Q press leaves Recon active. A full charge is
spent on the transition and held fire is locked during deployment.
Once in Tank, a real shell at eight metres kills the wounded Tracer.
The shell does not spend Recon rounds or credit Tank damage toward
the next ultimate. Recon refills during the transition and is
available again after recovery.

Actual shell flight ends at 100 m. Capsule contact at 99.99 m
hits and contact at 100.01 m falls beyond the flight limit.
Wall-impact cases verify splash falloff, a miss outside the
splash radius, cover and team filtering. Winston's dome spends
205 health on a direct shell and blocks splash from a shell
that hits a nearby wall outside the dome. D.Va Matrix consumes
the shell, Sigma Grasp records 205 and Genji Deflect redirects
damage to Bastion. Blink and both Recall phases avoid an in-flight
shell and the wall splash behind Tracer. Tracer's pistols and a
Tank shell each hit the five extracted Retribution NPC capsules.
Actual Flashbang blocks Tank fire for the full stun. A fresh
trigger fires when control ends and Tank is still active.
Lethal Tracer pistol fire blocks new shots, while the launched
shell continues and retains Bastion's kill credit. After a first
Tank cast expires, actual Sentry damage to Winston bodies earns
all 2310 points from zero and funds a second Tank cast.

At the end of Tank, fire inputs at active offsets 499 and 500
launch, and offset 501 is rejected. At 90 m the launched shells
hit after Recon returns, retain Bastion's lethal attribution
and grant no charge. Death preserves already launched shells.
Replacing the shooter body clears both leased and unleased shells,
as verified against Tracer and all five extracted NPCs.

An admitted shell resolves on its impact tick across 24- and
100-frame updates. Actual hits on both sides of Nano expiry match
the contiguous damage and hit notices. A shell admitted only at
the update endpoint starts with one frame of travel there.
The same 100-frame update checks Winston dome expiry. The shield
takes the full 205 before expiry, and after expiry the shell
reaches Tracer and retains Bastion's kill credit.

Sleep Dart late in Tank spans the return to Recon. Held fire
during sleep spends no ammo and deals no damage. Continued fire
hits on the first awake frame. Releasing on that frame and
pressing again instead delays the hit by one frame. Both hits
spend one round and deal 20 Recon damage to Tracer.

Tank explosions use the authored 0.25 owner damage scalar.
Wall hits at 1 m and 2.5 m deal 23 and about 17.77 self-damage
after Ironclad and armor. A 5 m wall is outside the splash.
A lethal self-hit records a death without an elimination, hit
notice or charge credit.

Tank explosion knockback uses separate horizontal and vertical
ring strengths. Close wall shots push Bastion away and a floor
shot pushes him upward. Actual splash moves Tracer and surviving
direct hits move the extracted Talon NPCs. Lethal hits do not
apply an impulse. Native movement and hit region parity stay open.

Two actual wall splashes break a projected Particle Barrier with
no damage or displacement to Tracer. A fresh splash after the break
does both. Splash at 3.99 m from the capsule surface deals damage
and pushes, and 4.01 m produces neither. Fortify lets Orisa take
113 damage without moving, then the first shell after it ends
deals 195 and displaces her. A direct shell against Nano-boosted
Tracer in a team fixture leaves 47.5 health and still moves her.

Effective Self-Repair credits accumulate through fractional
healing. With passive charge disabled, the float32 value one
step below 2310 rejects Tank. At 2310 the next cast spends the
full meter, including when the final heal would exceed the cap.

Other defenses, remaining interrupts, NPC combinations and
original-client parity stay open.

Reinhardt's raised barrier, Sigma's projected barrier and Orisa's
deployed barrier each take 20, 15 and 205 from Recon, Sentry and
Tank respectively, while Tracer and the barrier owner stay intact.
Zarya's actual projected bubble protects Tracer from both guns
and their energy credit is 4 or 3. A Tank shell breaks the full
200-point bubble, grants 40 energy and leaves Tracer unharmed.
After the bubble ends, both guns hit and a fresh Tank shell kills.

### Bastion Ironclad

The server reads the active 012F Ironclad filter and applies its
authored 20% reduction before armor. With armor removed in the
fixture, a Tracer pistol hit at 8 m deals 12 in Recon, while
entering Sentry, and while deploying Tank from Recon. It deals
9.6 in Sentry, while leaving Sentry, while deploying Tank from
Sentry, in Tank, and during Tank recovery. Returning to Recon
restores 12. In Sentry with full armor, the same shot removes
4.8 armor. A stuck Pulse Bomb removes 2 armor on contact and
leaves 23 health in stable Sentry or Tank. It leaves 22.5 if
contact occurs before Sentry deploys and the blast after deployment.
Leaving Sentry between attachment and blast makes the hit lethal
in Recon. Original-client parity remains open.

### Bastion Sentry rear core

Stable Sentry mode uses the deployed core capsule from model 0B44,
bone 0933, in the matching 0E35 and 0EB3 poses. Hitscan uses the
core only when its intersection precedes the body. The combat pose
history retains Sentry facing for delayed fire, including while
forced movement updates the position sample.

At 3 m with Bastion's armor removed, Tracer's first pair deals
19.2 damage to the exposed core and 9.6 from the front or off
the core. Rotation changes the exposed side, and a wall or allied
team prevents the hit. With Bastion now facing the shooter, a
delayed shot against the earlier rear pose deals 19.2 while a
current shot deals 9.6. A lethal rear shot retains the critical and
killing flags and credits Tracer. Bastion's Recon headshot deals 40
to Tracer and Sentry deals its ordinary 15.

The Pulse Pistols and Recon rows omit the critical scalar. The
host uses a per-weapon 2× fallback. The extracted Sentry and
Tank rows specify 1×. Native sentinel consumption, animation
blending, projectile critical geometry and original-client parity
remain open.

### Bastion quick melee

Recon quick melee deals 30 and receives lethal credit. Aiming away,
excess distance, static cover and allied targets prevent damage.
Sentry and Tank reject the melee input. After returning to Recon,
a fresh press hits, an early re-press is rejected, and another
press after cooldown hits. Tracer avoids the attack with Blink
and in both Recall phases. At close range, Tracer's own melee
deals 30 in Recon and 24 in Sentry or Tank with armor removed.
