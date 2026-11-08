# Tracer combat lane (0003)

Target: Overwatch 1.74.0.0.104319, hero `02E0000000000003`, body
`0400000000000001`. **OPEN: full hero combat is not accepted.**

## Verified server change

The actual pistol graph 0016, state8, reports 20 shots/second, two projectiles per
shot, six damage/projectile and two ammunition consumed per shot. The old combat
consumer resolved only one bullet per report. It now resolves each hitscan bullet
separately so armour mitigation applies per bullet. An unobstructed close-range
40-round magazine deals 240 body damage in the controlled fixture, versus 120
before the fix. This is resource-backed counting, not a reconstruction of spread.

All bullets still share the command's ray. Authored spread, muzzle origins,
critical-hit defaults and animated hitboxes need separate evidence. Other heroes'
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
carry the displaced pose. Native step-up/edge behavior and control interactions
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

S = selected server fixture passed; O = open, including original-client result.
NPC columns mean the specific requested class, not a training-bot substitute.

| Action | Enemy player | Trooper | Enforcer | Sniper | Assassin | Heavy Assault | Original client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pulse Pistols body damage | S | S | S | S | S | S | O |
| Reload and subsequent fire | S | O | O | O | O | O | O |
| Critical hits / spread | O | O | O | O | O | O | O |
| Miss / range exclusion / far falloff floor | S | O | O | O | O | O | O |
| Quick melee | S | S | S | S | S | S | O |
| Blink selected displacement / collision / charges | S | O | O | O | O | O | O |
| Blink complete control / cancel / cross-hero matrix | O | O | O | O | O | O | O |
| Recall position / health / ammo / cooldown | S | O | O | O | O | O | O |
| Recall cleanse / complete cross-hero controls | O | O | O | O | O | O | O |
| Recall selected invulnerability / targeting | S | O | O | O | O | O | O |
| Pulse Bomb selected throw / stick / fuse / explosion | S | S | S | S | S | S | O |
| Ultimate spending / windup / end / cancel | S | O | O | O | O | O | O |
| Ultimate selected passive / damage gain | S | S | S | S | S | S | O |
| Ultimate full native eligibility / long skipped-frame recovery | O | O | O | O | O | O | O |
| Incoming primary fire | S (Soldier) | S (manual) | F (no attack) | S (manual) | F (no attack) | S (manual) | O |
| Recall protection from actual NPC primary | S | S (manual) | O | S (manual) | O | S (manual) | O |
| Static-wall melee exclusion | O | S | S | S | S | S | O |
| Incoming abilities / control / death | O | O | O | O | O | O | O |
| Outgoing death / notices | S | S | S | S | S | S | O |
| Manual NPC corpse retention | N/A | S | S | S | S | S | O |

The additional training-NPC fixture kills with a paired shot and retains the body
past the normal respawn deadline. It disables NPC script delivery; it proves only
damage/counting/lifetime integration, not one of the five PVE classes or its AI.
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
| Barriers | O; health-pool shields do not establish barrier collision. |
| Deflect / absorption | O; no reflected/absorbed-projectile acceptance. |
| Invulnerability / status / control | O; generic `_damage` has no complete status gate. |
| Ally healing | No healing or team semantics changed by this patch. |

## Reproduction and results

Run from the lane's `source` checkout with `PYTHONPATH` pointing to that checkout
and `ORPHEUS_RUNTIME_DIR=D:\overwatch 1.7.4\runtime-104319`. Reuse the original
checkout's `.venv\Scripts\python.exe` without writing to its source or the runtime.

```
python -B -m unittest discover -s tests -p test_game_tracer.py -v
python -B -m unittest discover -s tests -p test_game_tracer_pulse.py -v
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
Duplicate commands grant no more. Full elapsed-time recovery is OPEN. Ordinary
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

Current follow-up:259 distinct tests pass (245 regular plus14 full server
snapshot/replay); native72-case execution is counted separately.
Eight actual-class tests cover36 class subcases. Manual controller input causes
Trooper20 damage at frame29, Sniper100 at128 and Heavy Assault10 at32 against
Tracer. Heavy's authored5 is doubled by the existing approximate head criterion;
this does not validate native animation hitboxes. All three cease damage for80
frames after stop. Casting Recall before their first shot blocks actual attack
damage throughout its active phases; their real attacks damage Tracer afterward.
Each NPC uses its actual body/weapon graphs and data; no direct-damage replacement
is used. Observer projectile transport remains an explicit omitted adapter.
Autonomous acquisition/navigation, specials and production mounting remain open.

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
remain unverified. Current production spread output and command rays are still
constant; no spread acceptance is asserted. Tracer's null m_A872C70E critical
field still lacks a verified native default. No game bytes enter this patch.

## Client and delivery status

No original-client session was started or controlled, no lease was claimed, and
no client acceptance is asserted. Existing Overwatch PID12292 and original server
Python PID22924 were observed and left alone; absence of a lease does not make
those processes lane-owned. Runtime assets were read in place and not copied.

Development stays on the private clone's main. The integration commit and
format-patch are listed in the adjacent lane STATUS.txt. No root-source merge,
manual push or public deployment is performed by this lane.
