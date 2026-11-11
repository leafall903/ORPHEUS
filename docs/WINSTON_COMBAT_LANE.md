# Winston combat lane (build 104319)

Hero 02E0000000000009; current runtime body 04000000000001C0. Acceptance remains OPEN.
Baseline 100c66c45bea81083a410a4ba13830ab5532ca93. No original-client
combat acceptance has been performed for this change.

## Current checkpoint

Enforcer's authored 142F category switch now resolves its mounted AI
controller and writes category 0210 to entity variable 9956. The native
104319 predicate tests for component 92 on the selected entity. The host
reconstructs this from the controller definition and live hosted script.
All five Talon controller definitions declare component 92. The current
catalog mounts only Enforcer and Assassin controllers, and Assassin has no
matching category switch. The Winston-hit event producer remains open.

Both alternating Primal damage states, 00FE states 35 and 36, deal 40
damage to each Talon body in the two-swing test. The fixture keeps the
target in range and clears the first swing's impulse to isolate cadence.
Separate mover tests still exercise the resulting displacement.

Tesla's breaking tick on an actual projected Zarya barrier gives neither
body damage, hit credit nor ultimate charge. Continued fire gives actual
damage and charge on the next tick. An actual Nano release gives Winston
no charge for healing received. Tesla charge follows the boosted health
loss and returns to the ordinary rate at expiry. The server computes
these awards from admitted damage and the authored 0B8B arithmetic.

Kanezaka Deathmatch and Practice Range now have matching Tesla tests with
the same numeric team ID. Deathmatch grants damage, one elimination and
charge capped by the victim's remaining health. Practice Range rejects
that friendly hit. Both stock mode records allow the five-point passive
tick in the fixture. All five actual Talon corpses reject repeat damage,
elimination and ultimate credit. The test uses local flat collision and
leaves the native mode score and target-tag pipeline open.

Incoming team checks use the same positions and actual attack inputs for
enemy and allied Winston. Tesla, rifle, Helix, melee and Flashbang damage
the enemy and award no damage stats for an ally. Enemy Tesla awards the
attacker charge for actual health loss. Damage taken does not charge the
victim in these cases. Four actual Talon firearm paths fire in both team
fixtures, and Assassin only damages and pins the enemy Winston.

Heavy 1408 node 134 now feeds live candidates into the authored clash
branches. Opposed charges stun both Heavy bodies for two seconds. Actual
Orisa Fortify ends the charge without either stun. Same-facing and allied
charges do not clash. Winston and Primal stay on the separate pin and
graze paths. The host reconstructs capsule, cone, nearest-first ordering
and line of sight. Native 052F and 224E comparison remains open.

Heavy's authored 1408 clash actions 132 and 140 now resolve 224E through
the combat host. A controlled list in 597 proves that the Heavy and its
first recipient receive the authored two-second stun. Winston in the
second slot receives no control or damage. Native 224E presentation remains
open. Timed control uses the existing
reconstructed server status path.

Deployed Ice Wall occludes Heavy's charge searches for Winston's body and
Trooper and Heavy melee. The reconstructed ray runs to the nearest capsule
surface. Removing the wall restores the authored pin and graze branches
and the melee damage. Heavy still damages the marked pillar, and a victim
already bound on the near side still receives the separate wall crush.

Heavy's reconstructed wall ray also checks solid allied Ice Wall pillars.
This uses the authored world-wall branch and its victim crush. Node 100
still selects a hostile marked pillar for node 102's 175 damage. Both walls
stop the charge and release Winston. A projected Zarya barrier cast after
the pin absorbs the crush without spilling into health or changing the
pillar's damage. Native collision classification remains open.

An allied Ana can lead a Sleep Dart into a charging Heavy Assault while it
holds Winston in 1407. The impact stops the charge, retires 1407 and leaves
Winston at 500 HP with fresh Jump Pack input available. The same cast from
behind a lateral wall fails to reach the Heavy, and the unbroken charge
finishes its wall crush at 205 HP. The caster pose and lead are controlled
in the fixture.

Mei's Ice Wall placement can lift a pinned Winston out of Assassin
contact. The host ends the authored held attack and removes the victim
controller scope and movement restrictions during placement confirmation.
An actual-input test confirms that active repeated damage stops and wall
cancel does not restart it. The lift and immediate contact validation are
server reconstructions.

Sniper's authored 142C MoveTo now ends on contact with a deployed Ice
Wall pillar. The existing six-second recast gate applies after the blocked
move. With the wall removed and the cooldown elapsed, the same move crosses
the former wall position. NPC external impulses use the same swept contact.
An authored Primal punch still deals 40 damage to a Trooper in front of
the wall, and the wall stops its subsequent knockback. These contacts are
server reconstructions. Native destination selection and pillar physics
remain open.

All five Talon NPCs stop at an Ice Wall pillar while following a scripted
route and resume the route after Mei cancels the wall. Ordinary movement
uses the pillar sweep with each NPC's standing dimensions. Assassin's
strike checks the pillar along its contact axis. The strike test uses a
controlled contact pose and authored ability input. These wall interactions
are server reconstructions. Autonomous targeting and special movement remain open.

Actual Trooper, Enforcer, Heavy and both Sniper weapon graphs now hit
deployed Ice Wall pillars before reaching Winston. Canceling the wall
restores body contact. Hitscan and swept projectiles use the nearest
pillar alongside static geometry, Winston barriers and Matrix. A Helix
impact that breaks the contacted pillar still uses the pre-impact
pillar set for body splash occlusion.

An airborne Blizzard can anchor on a deployed pillar face. The area
uses the same pillar geometry for damage and frost occlusion. Canceling
the wall restores area exposure while the storm is active. The box
contacts, impact-side anchor and splash occlusion are server reconstructions.
Wider splash-to-pillar combinations remain open.

Deployed Ice Wall pillars now participate in Winston's posed movement.
The host sweeps the player's feet against capsule-expanded pillar boxes,
projects inward velocity off the contact and offers a live wall top to
the existing ground probe. Placement lifts an overlapping living player.
Actual input tests cover side blocking and reopening, Jump Pack landing
on a pillar once, stable support and falling after owner cancel without
replaying landing damage. Contact skin, wall-top support and placement lift
are reconstructed. Native capsule corner behavior and
remaining projectile and area combinations stay open.

Actual Mei 0622 preview and confirmation now create five hosted Ice Wall
pillars. Their 400 health and 961 marker come from the original 0636 graph.
The host uses oriented boxes for placement and Tesla occlusion. A Tesla
slice can damage an enemy pillar and preserves its occlusion for that
slice when it breaks. Friendly pillars keep their health. Expiry, owner
cancel, preview interruption and early recast are exercised.

Winston's quick melee sphere, Primal punch capsule and Jump Pack launch
and landing spheres now contact the hosted pillar boxes. A wall crossing
can reject a body hit and its impulse. Host geometry computes capsule
contact and applies the authored landing falloff to pillar distance.
Actual casts verify 30 and 40 damage for melee and Primal, the one-point
launch hit and a separate landing hit. Removing the wall restores melee
and Primal body damage at the same poses. NPC pillar movement collision,
other heroes' projectile paths and native contact ordering remain open.

Heavy 1408 node 100 now consumes a live deployed pillar's 961 marker. Its
authored 175 damage reduces the pillar to 225 HP. With Winston already
bound, the same contact also runs node 29's separate 300-point crush
and releases him. Pillar health, placement, overlap and the collision
arbitration are hosted reconstructions. The complete pillar VM, observer
replication, NPC movement and remaining attack interactions stay open.

Orisa's 0CF4 Fortify runs from actual input. State 5 supplies the incoming
0.6 multiplier through var 8243. State 9 supplies var 9161 and starts the
6601 stack. The combat host applies the multiplier before armour and
uses the active Fortify scope to reject impulses and timed control.
The original four-second scope and cooldown advance on the idle clock.
Native combat-mod stacking and contact filters remain separate.

Heavy 1408 node 134's collision search requires a live candidate with 9161
or an opposed 596 charge, and excludes the actor already selected in 562.
Node 131 routes 9161 to the 559 branch. Otherwise nodes 132 and 140 request
two seconds of 224E on the Heavy and the first target in 597 respectively.
The loaded graphs reference 052F in 1408 nodes 32, 96 and 134 and in
140E node 6. The clash recipient dispatch is covered with controlled
tracker output and live opposed charges. Native filter equivalence remains open. Ordinary Winston and Primal
do not author either candidate flag. Fortify's 9161 and 6601 stacks also
exclude Heavy graze and frontal pin in nodes 32 and 96.

Node 100 requires candidate entity var 961 in addition to the reconstructed
capsule overlap. The loaded graph catalog has one writer for this var, Ice
Wall pillar graph 0636 node 27. Ordinary Winston, Primal Winston and fortified
Orisa do not enter node 102 damage or node 189 stun from close contact alone.
Tagged dispatch tests also run the actual 0636 writer in an isolated body
fixture. Real Mei input now covers deployment and the hosted overlap above.

Node 188 resolves the 224E resource and its 0.65-second override, with no
target config. The captured PlayScript path at 906E49 reads the target
config at +B8. Iterator dispatch 1B803D0 returns false for null, and the
empty-target branch at 906F1A bypasses the request builder. An actual node
188 resolver test verifies that it emits no request and never reads
invocation context. No targetless stun is synthesized on recovery.

The pre-Enforcer-target frozen 104319 regression passed all 909 selected tests with no failures,
errors, skips or expected failures. The manifest covers Python under
`ow174` and `tests`. Static acceptance remains open for the rows below.

Blizzard now takes its actual 064A launch speed, gravity and lifetime through
the 0946 projectile path. Its 065E area profile has a 200 ms activation delay,
a radius of 10, 4.25 seconds of exposure and 20 damage per second. The 073C
frost path uses a separate source from Mei spray, with a 0.5 initial value and
0.040000003 increments every 200 ms. The host reconstructs ballistic chords,
first static contact, capsule range, eye occlusion and per-tick damage. Area
timers and frost deadlines are causal server state. The 065E auxiliary
filter includes 003A from the 01C3 sphere. The host reconstructs that match
as a shell crossing for area exposure. The body health target list stays
separate, so these crossings consume no sphere HP. An actual 064A cast in
the host enters an already active sphere while still airborne and leaves
Winston and the sphere at full health until the landed area arms inside.
The first active tick damages and chills Winston and preserves all 700
sphere HP. Native flight contact remains open because the projectile's
051C contact set does not establish the response to a sphere in the
decoded fields. Damage mode and exact native filter ordering remain open.

An actual lethal Tesla hit during Mei's accepted 064A windup prevents
release. The same lethal hit after release leaves the projectile and
subsequent area damage in play. The fixture sets Mei to one HP before
each cast to isolate the timing of the lethal hit.

An actual Blizzard freezes a Winston whose Primal Rage is still active.
On the authored Primal expiry frame, the server removes his temporary
health and transformation on a world update with no player commands.
The Blizzard area and freeze remain active through that frame.

Genji's 03A3 state 10 supplies the reflection boxes and incoming angle for
0946, whose target tags omit 0145. Reconstructed eye-relative contact
redirects the remaining ballistic chord along Genji's aim and transfers
area allegiance and damage attribution. Static obstruction and Matrix can
win earlier contacts. Native reflection output and observer ownership
remain open.

The 50-case defense regression includes the actual Blizzard, Genji, Matrix
and Winston barrier test modules. In those cases the sphere blocks exposure
in either crossing direction and allows exposure when the ray stays on the
same side. Genji reflection credits the landed area to the reflector, rejects
rear and friendly contact and follows the reflector's body lifetime.

Mei's actual 05FE Cryo-Freeze can now enter and leave its authored protection
state in Winston combat. The host completes the otherwise unsupported entry
gate at state 41. Active state 6 excludes damage and impulse, and health state
13 supplies the four-second duration and 200-point total through authored
variables. The host distributes healing over elapsed time, accounts for each
activation once, and advances Mei's scope during missing input. The authored
secondary cancel and cooldown remain in charge. The host applies Ana's grenade
modifier to each elapsed body frame during sparse updates. An actual enemy
grenade and an actual allied grenade each expire during Cryo-Freeze. Two-grenade
cases also cover both expiry orders, with antiheal taking priority while the
windows overlap. Single-frame and sparse updates agree on recovered health and
healing credit, and Winston's first Tesla tick after thaw deals 0.96 damage.
These are reconstructed server rules. Native targeting and modifier order remain open.

The first current-source aggregate ran 889 tests with unchanged Python
hashes, one failure, nine errors and zero skips. The EMP Trooper test used
the hero graph bundle instead of the Talon bundle. The damage notification
and NPC idle clock also assumed every bot script exposed a VM component.
The fixture now scopes the Talon bundle to the Trooper test, and both hooks
check component availability. The historical corrected aggregate passed all 891 selected
tests with zero failures, errors, skips or expected failures and unchanged
Python hashes. A separate 20-test server snapshot and StateScript run also
passes. These results cover the selected regression suites. The static matrix
and original-client practical acceptance remain open.

Enforcer's actual controller 13EA can now select its original behavior
from a supplied live hostile target. The host binds that input to selector
zero and reconstructs its visibility record with an eye-to-eye static ray.
The controller initializes the authored 15 and 16 defaults before entry.
Node 69 supplies distance to nodes 64 and 67, and the host polls node 63's
transition while it is active. Node 65 owns controller var 12299. Body
13EF reads it as a 0.4 speed multiplier, keeping the NPC outside Winston's
Tesla range in the paired route test. The authored one-second wait at node
68 ends the speed modifier after the exit condition. Clearing the target
or retiring the selected body ends the branch and restores full speed.

The captured 104319 `A37A910D` evaluator at CCB2B0 converts its entity and
integer inputs and calls the entity-instance vtable slot +238. The captured
99B9D0 implementation checks component 70 before calling BC7FA0, which
searches selector/entity pairs. An 18-case offline probe of BC7FA0 verifies
both table layouts, first-match behavior for duplicate selectors, and
zero for a missing selector. The companion `D379F515` cache lookup from
the Baptiste lane verifies that only a present target record with status
one returns true. Automatic selection, native visibility record production,
the separate 1506 animation event and unrelated 13EA AI branches remain open.
In 1506, state 6 at node 12 emits through `m_806B068F` to node 9, which
starts a wait driven by var 13235 and a separate two-second wait. The first
wait owns both the animation and a directional movement modifier. The
trigger, parameter production and native movement application still need
evidence before these states can be counted as combat coverage.

Enforcer 1506 node 12, Heavy Assault 14EB node 12 and 13AC node 25 share
the same trigger class and selector 35AE, animation category 9 and 0.25
selector parameter. In the live server probe, an authored Winston Primal
punch deals 40 and applies a physical impulse to each of Enforcer and Heavy
Assault. Enforcer's trigger state stays inactive and Heavy's stays active.
Neither populates animation variables 13233 through 13235 or starts either
playback wait. The current physical impulse path is therefore independent of
this trigger family. The resource comparison identifies a shared reaction
candidate but does not establish which engine event supplies its parameters.
See `talon-1506-trigger-frontier.log` in the owning lane.

The 104319 capture contains the static registration list at RVA 32969D8.
The probe traverses all 1,735 links, applies the native priority rule and
rebuilds the out-of-image heap buckets for 1,687 distinct types. There is
no direct registration for trigger class 26B87F59. The original state
factory selects parent 7C37840C and binds generic vtable 27AC800 for both
Talon layouts. As a positive control, the original lookup uses the direct
6E5C5F66 action record at RVA 3293DD8 and binds vtable 27AC260 in the same
table. The probe covers the captured registration set. Other host
registrations, the trigger event and parameter production remain open.
See `talon-reaction-factory-native.log`.

Controlled playback of 1506, 14EB and 13AC now publishes the authored
selector-1 vector and five numeric rows in the strict VM. The probe supplies
the selector output, playback duration and body axes. The original reader
preserves the input vertical component before adding an offset, and the
vector record retires when the playback wait ends.

Trooper's entity override 25EF selects 1231 through 0E62 node 120. The local
host projects the authored graph-only configuration and starts the graph on
its owner. 1231 starts 13AC as a child through state 7. Aborting the startup
state retires the owned played instance and its child. This is a scoped
reconstruction of the native PlayScript state and resource dispatch.

The same startup path loads 142F for Enforcer, 14D1 for Sniper and 0E54 for
Heavy Assault. Assassin has no 25EF entry. All five bodies load their common
0E5E and the 0E20 listener after 0E62's 125 ms wait. An idle body advances its
script clock through this wait. Controlled 077A delivery on Heavy Assault
starts the authored 266F branch, while 35AE leaves directional playback
unchanged. The producer of original 077A and 35AE events remains open.

Controlled playback drives collision movement on actual Trooper, Enforcer and
Heavy Assault bodies during the authored wait. The server derives horizontal
axes from body yaw and keeps the vertical movement path. The Trooper case uses
the initialized child from normal startup. On initialized 1506 and 14EB,
the injected output sets entity var 888 and node 14 aborts the playback wait.
Native selector event production remains open.

The native 6E5C5F66 wrapper forwards offset 88 only for the matching action
STU. With the trigger STU as a mismatch control, it returns successfully
without calling the dispatcher. The VM now models that action explicitly.
Strict graph tests invoke the authored node 9 in 1506 and 14EB and verify
the abort and both wait starts are queued. These tests exercise the output
side of the graph after dispatch. The upstream trigger remains open.

Talon Trooper 102E binds its volley to a 30-round magazine. Actual held fire
exhausts it, and the authored logical 14 reload restores rounds before the
firing gate reopens. Enforcer 13EB consumes four shots and automatically
refills before allowing the fifth. Sniper 1122 and 1134 and Heavy 114F leave their volley
ammo output unbound. Tests hold each beyond its initialized ammo value.
The NPC clock advances Trooper and Enforcer reload while their authored
reload gate is active, including a sparse world tick with released inputs.

Sniper 142C node 16 now runs through the NPC clock and the collision-backed
mover. The static controller selects a nearby hostile target and an escape
point using the Rialto checkpoint's reconstructed distance and cooldown policy.
The original state supplies speed 20. Actual Flashbang, Winston Jump Pack
impulse and lethal Tesla end the move. Native A45/controller routing and the
node 39 anchor search remain open.

Talon Sniper's authored weapon manager (0015) selects slot 2 through logical
button 16 and returns to slot 1 through button 15. Slot 2 runs 1134 and emits
23BB at speed 200 with 100 m of travel and 5 damage. Slot 1 runs 1122 and
emits 1E80 for 100 damage. The alternate removes 2.5 from full Winston armour
and 5 from a deployed sphere. Release, missing slot, team, wall and range
rejections have executable coverage.

Heavy Assault graze and the Trooper/Heavy melee paths consume their authored
horizontal and vertical impulse magnitudes on a living body hit. They reuse
Winston's collision-backed external impulse route. Radial direction and
physics integration are reconstructed; native impulse modes and ring semantics
remain open. A projected barrier that absorbs a graze prevents the impulse.

Heavy Assault's bound wall impact consumes node 29's authored 300 damage
before release. Node 100 contact with an eligible 961 candidate drives the original 102 damage
entry (175) and 189 control request (2 seconds). The host pairs that request
with successful damage because the original action runs before the queued
health entry. A simultaneous bound victim takes the separate node 29 amount.
Armour and projected Zarya absorption apply through the ordinary damage route.
Wall dispatch, collision admission, timed control and recovery are reconstructed.
The other 224E request branches, native target tags and knockback remain open.

Heavy Assault 1408 frontal search now opens the original 1407 on the current
victim body through state 158. While the actual attachment state remains
active, Winston cannot start actions and follows the charging NPC. The host
uses capsule separation for the unimplemented animated bone transform and
the authored opposite-facing angle. Wall exit, Flashbang, replacement and
source removal retire the binding. The host rejects acquisition through a
projected Zarya barrier.

Heavy Assault 1408 node 32 now selects side contacts and its node 35 amount
uses authored var 980 (50). The reconstructed side cone excludes frontal
grab candidates and requires capsule overlap and static line of sight. One
contact per body per charge routes through existing armour and projected
barrier damage. Native filter details remain open.

Heavy Assault 1408 now takes actual logical button 35 and enters its authored
0.8-second windup. Node 71 supplies a forward speed of 15. The reconstructed
host advances that motion on the NPC clock and uses the authored 2 m forward
wall probe to enter the graph's wall exit. Natural expiry and wall exit clear
movement and start the authored 15-second cooldown. Actual Flashbang interrupts
windup. The native movement transform remains open.

Actual Talon Trooper 1422 and Heavy Assault 140E melee now enter through logical
button 31. Their track nodes supply distinct local offsets and radii. The damage
nodes consume authored var 476 for 30 and 80 damage, which removes 25 and 75
from fully armoured Winston. The host rotates the authored offsets by body yaw
and tests hostile capsules and static LOS. Contact with a deployed Winston sphere
uses the same reconstructed volume rules as player melee. Pending attacks
advance during idle windows and actual Flashbang can interrupt them. Native
filter ordering, knockback fields, shield-only acquisition and remaining NPC
specials remain open.

Sombra's 0B31 release runs the 0B32 victim script, whose body branch drains
character shields and starts 0B1F. The search uses the authored radius and
reconstructed capsule and wall checks. The host also checks hostile deployed
Winston spheres at release, including casts with an empty body target list.
Sphere admission and destruction are reconstructed. A covering Zarya personal
or projected barrier absorbs the 0B32 node 3 amount, grants energy for its
remaining capacity and excludes its wearer from the pulse. Actual Tesla inputs
also exercise two overlapping barrier arrangements. The reconstructed host
consumes a Zarya personal barrier before an allied projected barrier, and a
hostile Winston shell before a projected barrier covering its wearer. A breaking
tick never spills through to the next layer. Zarya energy follows absorbed HP,
and Winston charge starts on the first body hit. Native target tags and barrier
ordering remain open.

Sombra's 0B1B states acquire and retain an enemy body through the host's capsule
and LOS checks. Node 141 queues the actual 0B1F graph on the target. Its node 96
provides a five-second duration. The host gates Winston's Jump Pack, Barrier
Projector and Primal entry while preserving ordinary weapon input and an
existing Primal form. Retiring the victim body removes its application.
The target filter, status consumption and damage-event routing are reconstructed.
An active Zarya barrier also blocks Hack acquisition and maintenance without
spending capacity. Zarya's authored timers advance during missing input.

Mei's actual 05E6 spray volley supplies the projectile target limit and damage.
The host uses 0B77 and 05E7's default frost values. Sources accumulate separately,
with the strongest unexpired source determining slow. A full meter locks movement
input and aim for 1.3 seconds. Continued hits leave that deadline intact.
These are reconstructed spray rules. Native effect instances, mode-dependent
frost settings, Blizzard and full defensive cleansing remain open.

Ana's actual 0B9E target and release states drive Nano. The host search uses the
authored range and base cone with reconstructed ranking and line of sight.
0269 leads to B9F, where nodes 33 and 34 specify the additive damage modifiers
and node 36 specifies the 250 heal. The source's duration variable supplies
the eight-second deadline. Anti-heal blocks the ordinary Nano heal while
the modifier can still apply. The tested Primal refill follows its own tag.
Barriers receive outgoing amplification and body mitigation follows barrier
interception. Projectile outgoing strength is captured at release. Native
effect hosting and presentation remain open.

Actual Ana grenades route 0267 splash into 50 damage or 100 allied healing.
The B99 and C72 paths supply a four-second modifier window and B99 supplies
the 1.5 allied healing multiplier. Ordinary healing uses the modifier for each
retained field tick, with anti-heal taking precedence. Both Winston and projected
Zarya barriers intercept the tested enemy grenade before the body modifier applies.
Primal refill carries 293E in 0109 node 8. The host interprets the corresponding
C72 node 17 filter as an exclusion, so Primal refills its expanded pools and
preserves the active anti-heal window for ordinary healing. This is a resource
based reconstruction. Native filter execution and grenade flight equivalence
remain open.

The incoming Ana dart uses the 0BDD node 14 direct hit definition. Its authored
5 damage and 026F effect enter through projectile collision and body damage.
The host models sleep as a five-second world deadline, with early wake on
subsequent successful damage and cleanup on body retirement. A deployed Winston
barrier can intercept the dart. An ally dart can release Assassin control by
sleeping the source. Native sleep timing and presentation remain open.

The frozen checkpoint below predates the Assassin shield-admission and Ana
sleep increments. Their focused test records are kept with the lane evidence.

805 frozen-source regressions pass with no failures, errors, skips or expected
failures; current Python hashes exactly match that run. Ten new attachment
methods include real ally melee/Flashbang/Primal rescue, existing Jump and
enemy Primal victim force, graph/identity retirement and full-server NPC pitch
causality.31 related checks,24 adapted native default-handler comparisons
and Ruff/diff pass. Original28/30 drive physical following/opposite facing
through declared body/transform/phase and rescue policies. Native transform/
message/filter equivalence and the complete controls/defenses/NPC combat
matrix remain open. No new commit/export/merge readiness is claimed.

Actual Assassin initial25, declared100/sec after original1s warmup, idle/sparse
clock, current-capsule/axis-LOS contact, body identity and distinct controller1177
now have executable evidence. Original1C0 and02A3 standing/crouched eye offsets
also pass actual low-cover/source/target tests. See
[Assassin core and limits](WINSTON_ASSASSIN_CORE.txt) and
[notification lifetime](STATE_NOTIFICATION_LIFETIME.txt). Older dated research
and checkpoint counts below are historical; they do not describe current skips
or expected failures.

## Implemented partial restoration

Actual 00FE weapon graph st10 TrackTargets and st13 ModifyHealth must both
be active. The server evaluates the authored radius, angle and amount, then
routes continuous damage through Combat._damage, including real pools,
notices, statistics, kill attribution and retained-NPC death handling.
One simulation command frame is consumed at most once per current body.
Body retirement clears the guard. Allies are excluded by the ordinary match
team rules; FFA retains the ordinary FFA semantics. No healing is converted.

DATA from the external 104319 graph bundle: radius evaluates to 8, var243
angle to 40, and amount to 60. The loadout is 0B9000000000002F. Graph 00FE
is a target-search/ModifyHealth weapon, not a WeaponVolley hitscan.

RECONSTRUCTED, NOT NATIVE-VERIFIED: angle as cone half-angle, capsule-width
cone edge, source at the existing combat eye, nearest capsule-axis height,
amount per second, line-of-sight to that axis point, and 20% beam reduction
while armour remains. These require original-client/native comparison. The
beam armour branch prevents applying five points of per-hit mitigation on
every 16 ms continuous-damage slice; existing discrete damage is unchanged.
Native target-search payloads are still empty, so visible electricity locks
and their replicated target list are not established by this server change.

## Acceptance matrix

Rows record executable static evidence from authored inputs, runtime state and
health-pool assertions. Explicit PVE statements use the five authored Talon
bodies and mover profiles. Static cases with reconstructed target selection,
movement or geometry retain the limits listed below.

Action | Enemy player | Enemy NPC | Ally | Original client
--- | --- | --- | --- | ---
Tesla Cannon | Actual input/state -> pools, multi-target, kill tested | Bot pools/death/corpse tested | no damage tested | OPEN
Secondary input | Input triggers no attack and consumes no ammo in normal form or Primal on build 104319 | No separate damage output | No separate damage output | OPEN
Quick melee | Actual 0043 input deals 30 once and uses var 1257 for its horizontal impulse. Tests cover 1 s cooldown, 0.15 s press buffering, release, held input, stale/future command rejection, and Tesla interruption/recovery. | All five actual bodies take 30 once and use the reconstructed radial impulse path. Range, rear, team and wall cases reject both. | No damage or impulse in the tested cases | Native targeting, impulse direction and original-client acceptance OPEN
Jump Pack movement/launch/landing/knockback | Launch, collision landing, damage, cooldown, death and control tests. Landing moves active Deflect and Matrix targets and leaves an active Wraith target stationary. Personal and projected Zarya barriers absorb the landing without impulse. | All five launch1/landing50/falloff/motion/range/team/wall/lethal corpse cases | Launch and landing protection tested | OPEN
Barrier Projector placement/HP/lifetime/collision | Actual cast and damage paths cover placement, HP, expiry, boundary crossings and rifle/Helix contact order with a static collision wall | Trooper/Enforcer/Sniper/Heavy primaries and Trooper/Heavy melee have actual-contact cases | Friendly Tesla, rifle and Helix pass tested | OPEN
Primal Rage activation/health/expiry | actual input, charge gate, +500 HP, heal, expiry, death tested | Four actual NPC primaries and Assassin's entry hit reduce armour while the +500 health cap is active. The cap falls on an idle world tick and armour damage persists. | n/a | OPEN
Primal Rage punches/knockback/Jump reset | Tests cover 40 damage, windup, cycles, range, LOS, motion, wall and shield contact, Jump reset, 2 s cooldown, 6 s restoration, air recast and speed. Held and fresh fire at expiry preserve ammo and charge boundaries. | All five take 40 damage with motion and landing covered | No friendly damage in the tested cases | OPEN
Passive and ultimate credit | Original arithmetic, world cadence, active-form and cap gates, actual loss, Practice Range and Deathmatch covered | Actual damage, overkill and retained corpse credit checked for all five | No credit from tested friendly hits | Native target tags and original-client acceptance OPEN
Taking weapon damage | Actual Tesla, rifle, Helix, melee and Flashbang plus direct damage/pool tests | Four actual Talon firearms and Assassin strike damage Winston | Matching friendly cases preserve health. Flashbang and Assassin add no friendly control. | OPEN
Death/respawn | Death suppresses Tesla. An actual Peacekeeper kill during Nano and Primal clears temporary health and respawns on the mode deadline. The replacement fires Tesla on the first fresh input, with future and replayed commands rejected. | retained NPC not replaced tested | n/a | OPEN

NPC class | Winston -> NPC | NPC -> Winston | corpse/client
--- | --- | --- | ---
Trooper | Tesla, melee, Jump, Primal and retained corpse | 102E primary, 30-round magazine and timed reload. 1422 melee damages Winston and the bubble and produces a radial impulse. | Native melee contact filtering and AI admission remain open.
Enforcer | Tesla, melee, Jump, Primal and retained corpse | 227A controller, 25 physical pellets, four damage indices, four-round volley reload and world-clock recovery. Manual or opt-in automatic hostile selection runs the original 13EA distance and visibility gates, and 13EF applies the 0.4 speed modifier. An opt-in reconstructed trigger fires 13EB at Winston or his barrier and releases when the target becomes allied. | Native target ranking and fire input, 1506 and other AI branches remain open.
Sniper | Tesla, melee, Jump, Primal and retained corpse | 1122 and 1134 actual projectile paths and barrier interception. 142C MoveTo tests cover collision, Jump takeover, Flashbang and death. | Original reposition anchor lookup and controller ingress remain open.
Assassin | Tesla, melee, Jump, Primal and retained corpse | 1157 contact deals 25 and admits victim graph 1177. Continuous damage, attachment, facing and release execute with authored graphs and a host movement bridge. | Native attachment flags, AI approach and dynamic collision remain open.
Heavy Assault | Tesla, melee, Jump, Primal and retained corpse | 114F primary, 140E melee, 1408 windup, graze, 1407 pin, wall damage and live clash. Node 100 rejects unmarked Winston and Primal and accepts a deployed Ice Wall pillar. Node 134 excludes Winston and Primal from clash. | Native charge filtering, animation attachment, pillar VM and 224E presentation remain open.

The normal baseline runtime does not expose these actors in bots.table()['heroes'].
The opt-in NPC suite reads parent's prepared pve_npcs_174.json and graph bundle
from ORPHEUS_PVE_NPC_DATA; no runtime files are copied. It imports the exact
reviewed parent NPC module at a15d554 and projectile cleanup at c2d3ae1.
Difficulty/conditional pools and production NPC AI are not covered. Explicit
primary-fire input is delivered to the actual weapon VM; no damage is fabricated.
Reviewed Soldier161a060 weapon-init/input/full-health hunks restore Sniper's
declared weapon instances. Trooper20, Sniper100 and Heavy5 first actual hits
remove15,95 and2.5 from Winston armour respectively. Enforcer now uses its actual
associated controller and original25-projectile volley; at4m default difficulty
loses25 armour. Assassin executes the original initial25 and declared
continuous health against Winston. Its actual-input measurement runs in setUp
so exceptions remain errors.

The five-NPC Primal test casts the actual ultimate, records the first hit
from each authored attack, retires the attacker to isolate expiry, then
advances the match clock without an owner command. Armour losses for
Trooper, Enforcer, Sniper, Assassin and Heavy Assault are 15, 25, 95, 20
and 2.5. The 500 bonus health is still present after each hit. Expiry
removes it and preserves the observed armour loss. The probe log is
`winston-primal-npc-world-expiry-probe.log`. A separate test uses sustained
primary fire from one and two actual Snipers to exhaust armour and reduce health to
610 and 210 before expiry. After a world tick without owner input, the base
maximum returns to 350 and current health becomes 350 and 210 respectively.
See `winston-primal-sniper-overflow-probe.log`.

Cross-hero interaction | Status
--- | ---
ordinary health/armour and enemy/team filtering | partial actual-pool tests
static world occlusion | Static triangle walls block actual Tesla, quick melee, Jump Pack takeoff and landing, and Primal punch. Clear-path controls restore damage. Quick melee still spends its cooldown while blocked. Native geometry OPEN.
Winston bubble hitscan/projectile/beam crossing | actual weapon tests; native entity/filter policies OPEN
Genji Deflect | 11 actual-cast tests: beam passes; frontal melee/Primal parry; rear/expiry/cooldown controls; native filter OPEN
Defense Matrix | Seven actual0407/02CC command tests pass: rifle positive control, Tesla/melee/Primal/Jump pass, bubble ordering. Native filter/attachment/history and broader matrix remain OPEN
Reaper Wraith | 9 actual-cast tests: Tesla/melee/Primal/Jump rejected without impulse; expiry/cooldown restores vulnerability; other immunity OPEN
Mei Cryo-Freeze | Actual 05FE input excludes Tesla, melee, Jump takeoff/landing and Primal without damage or impulse. Tests cover Tesla ammo and charge, cancel, idle expiry, self-healing, fresh cast after cooldown and body replacement.
Orisa Fortify | Actual 0CF4 input reduces Tesla, melee, Jump and Primal damage before armour. Primal and Jump impulses and Flashbang stun are rejected during the scope. Idle expiry restores vulnerability and early recast is rejected.
Zarya personal/projected barrier | 11 actual-cast tests: absorption/energy/full breaking-event block, impulse suppression, projected selection/LOS/teams, Genji ordering, ally Winston protection, shield recovery
Flashbang stun | 11 Winston command tests; NPC motion/control/recovery; native224E/remaining control edges OPEN
Hack/EMP, Sleep Dart, spray and Blizzard frost, damage/healing modifiers | Actual Sombra, Ana and Mei inputs have focused tests. Zarya barriers block Hack and absorb EMP. Blizzard applies separate timed frost from its landed area. Other control, invulnerability and cross-target combinations remain open.
Healing, armour and damage modifiers | Ana grenade healing, antiheal, timed Nano, armour interaction and Primal's tagged refill have actual-input cases. Wider combinations and timing remain open.
native target list/electric arcs/audio/HUD/ammo/reload | OPEN

## Repeatable checks

From this clone, with PYTHONPATH equal to this clone and ORPHEUS_RUNTIME_DIR
set to D:\overwatch 1.7.4\runtime-104319, use the original .venv Python:

    python -B -m unittest discover -s tests -p test_game_winston.py -q
    python -B -m unittest discover -s tests -p test_game_combat.py -q
    ruff check ow174/game/combat.py tests/test_game_winston.py

The lane's `run_static_checkpoint.py` records exact test IDs and SHA-256 hashes
of every Python file under `ow174` and `tests`. Duplicate test IDs, source drift,
errors, expected failures and unresolved skips prevent checkpoint success.
With `--geometry-runtime "D:\overwatch 1.7.4\runtime-104319"`, it reruns only
mover cases skipped for missing collision caches against that runtime. The
rerun must pass the exact skipped IDs on the same source manifest. The report
records the initial skips, successful reruns and any unresolved skips separately
from static matrix completion.

For individual matrix rows, `test_game_winston_npc.py` covers the actual Talon
bodies, while `test_game_winston_assassin_attachment.py` covers binding,
following and release. `test_game_winston_charge.py` covers passive and damage
credit, eligibility gates and Primal's world-clock expiry.
`test_game_winston_defense_clock.py` covers input silence and defense expiry.
The `biotic`, `nano`, `sleep`, `hack`, `emp` and `frost` Winston test modules
isolate the corresponding cross-hero cases. Each focused validation JSON in the
lane records the measured scope and open boundaries.

The base 104319 bundle binds
Mei body 08BA to graph 064A, whose volley at node position 9 references entity
0946 with speed 20, gravity 9.8 and projectile lifetime 60. The Mei lane
traces that entity to graph 065E and documents its radius, activation delay,
damage and separate frost profile in `docs/MEI_COMBAT.txt`. Its host consumer
was reviewed alongside Winston's flight and damage paths. Remaining Blizzard
rows include additional invulnerability and interruption combinations.

## Client and integration

Observed an existing Overwatch process PID12292/window title 鐎瑰牊婀滈崗鍫ユ敱, without
an attributable lane lease. Process command line/path were not readable.
No lease was acquired, client was not controlled, and no normal service was
stopped or reconfigured. This is not a running client acceptance session.

The lane stays on private clone main. Parent must review/apply the patch;
there is no root-main merge or push. Publication and exporter execution are
left to parent integration as required by lane isolation.

## Jump bridge limits

010A node 10 enters the movement state and node 70's rearmable 250 ms
Wait st26 while node 59 writes the cooldown. The host uses st26's start
to launch once per cast. A standalone cooldown write produces no new
flight on the ground or in the air. An actual Primal air recast renews
st26 and replaces the previous flight.

The short-gap regression uses a descending Jump Pack on flat collision.
Five world-only ticks leave the landing unresolved. A fresh command then
recovers the missing poses, lands once and awards 50 damage and 50 ultimate
charge. A command from the recovered window leaves both values unchanged.

010A's original missing velocity actions leave var681 zero and st2 alive. The
bridge keys the accepted branch's Wait st26 start, reads resource speed30
and node85 bounds25/90, and interprets those bounds as launch elevation. It feeds
the existing collision-backed Mover; an actual landing ends st2 and its children,
allowing the next ready cast to execute again. Source death cancels without an
impact. Launch uses authored radius4/damage1; landing uses radius5/maximum50,
with reconstructed linear distance falloff against capsule surfaces. Player
knockback uses the authored horizontal/vertical ModifyHealth fields interpreted
as additive impulse; the full control/modifier policy and Bot impulse movement
remain OPEN. Transport/native presentation and parameter interpretations are
not accepted by these static tests.

The shared Ability early-buffer defect was observed with a click at ~1.6s:
its timestamp refreshed every frame until the six-second cooldown ended, so
it cast while released. Restoring the original timestamp rejects that stale
click while retaining a real press within the authored 0.15s early window.

## Barrier resource and behavior evidence

The build104319 asset 01C3.003 references modelD02.00C. A read-only single-model
probe retained the private raw file outside Git. The model MSTU has a sphere
shape4614D0E5, radius fieldED61D926 = float5.0; wrapper2DD96D6D motion1 links it
to model node1. The static prop extractor intentionally omits this dynamic
sphere. The resource probe and raw assets remain under lane0009/research and
resource-probe, outside the source checkout. No client/archive copy was made.

The combat actor uses graph00FA var587=700 HP and entityvar567=9s. The 50 ms
deployment Wait st3 must finish and enter the effect Wait st1 at the same VM
time. The host keys the deployment counter to prevent duplicate spheres.
A controlled st3 abort after actual input skips the effect Wait. Writing
the cooldown afterward still leaves Winston exposed to enemy fire. Native
entity creation, 00FC graph hosting and replication remain open.

Actual inputs exercise Primal Rage on the barrier's accepted input, during
the 50 ms wait and on its release frame. In each case the completed wait
creates one sphere at its ordinary time. An enemy rifle hit removes 20
sphere HP and leaves Primal's 1000 HP intact. A new barrier press after
Primal starts does not enter the wait or spend cooldown. It does not replay
on idle expiry, and a fresh normal-form press deploys.

Incoming original Soldier rifle/Helix graphs exercise real damage interception.
Enemy attacks from outside hit the entry; attacks from inside to another target
inside do not cross, while exiting attacks are blocked. Friendly attacks pass.
Static walls earlier on the ray take priority. A break consumes the current hit;
its blast and all Tesla paths in the current slice retain the shape, preventing
same-hit spill. HP=0 or expired spheres stop blocking subsequent attacks.
These policies remain reconstructed pending native comparison; no complete
barrier interaction or client presentation claim is made.

## Primal and NPC mover evidence

00FE's alternating swing Wait states preserve .65s periods. Its 54FEB21C st20/21
windup configuration is .25s; the legacy VM never ended them, so the original
ModifyHealth states35/36 never began. Only this Winston path now finishes those
states after their configured time, then consumes the actual40-damage activation.
This windup interpretation, local capsule transform and one-sample hit policy
remain reconstructed. Damage uses no fabricated volley, no critical multiplier,
no ammo and no Primal ultimate charge. Shared native status policies remain open.

The temporary impulse Mover uses real collision and NPC-authored dimensions,
not a position-only displacement. npc.Definition may carry validated MoverData;
spawn passes it to Bot's speed/acceleration, target capsule, gravity and force
mover. Lane npc-catalog-with-movers.json was independently derived with the
existing extractor from each original003 XML, and stays outside Git. Heights
are Trooper1.7, Enforcer1.9, Sniper1.75, Assassin1.5 and Heavy2.3; Heavy radius.7,
others.5. Absent optional mover data retains legacy fallback and is not accepted
as real-NPC physical evidence. Mode/difficulty, mass/resistance and NPC AI remain
unaccepted. Set ORPHEUS_PVE_NPC_CATALOG to the authored external enriched catalog
for the current NPC matrix. Normal runtime data is not modified.

The actual Primal graph already resets Jump cooldown, selects2s while transformed
and restores6s on expiry. The bridge now restarts an accepted air recast's effect
subtree after retiring the prior flight. Actual running velocity is5.5 normally,
7.150390625 during Primal and5.5 after expiry; this checks the shared movement
modifier application, not merely its configured scalar.


## Cross-hero defense evidence (2026-11-09)

The Deflect service is scoped to live Genji0029/03A3 st10 and a reconstructed
front hemisphere. It rejects quick-melee/Primal admitted damage and impulse,
passes Tesla and Jump area damage, and ends with the actual State. These tests
do not claim projectile reflection or native reflector geometry for Winston.
Reaper0002/0020's configured zero-duration54FEB21C entry is explicitly finished;
only actual st3 scope is intangible. Damage target selection and direct damage
both reject that scope. Other invulnerability is not inferred from this helper.

Reviewed Zarya0068 b17930b supplies personal0244 and projected025C barriers and
0033 shield recovery. TrackTargets11/18 routes only the projected resource's
search to an explicit live-allied/range/cone/static-LOS host service. Command aim
is causal VM state and is included in body snapshots. Barrier absorption occurs
before melee parry; the boolean damage result gates associated Winston impulses.
Capacity200, energy message and duration/cooldown States come from the resource;
full breaking-event blocking, host filtering and recovery timing are reconstructed.
No pet entity hosting, native control immunity or original-client proof is claimed.

Additional PVE cases run the five original bodies through Tesla range/rear/team/
wall rejection and quick melee30 once plus the same rejection controls. Primal
also checks all four rejection conditions with no impulse. Static melee geometry
now reuses parent7cd1ed7 nearest capsule-surface visibility. Parent6debff3 typed
array selection is independently regression-tested here, but the Enforcer's
missing controller readiness is not supplied a forced Boolean. Assassin1157's
targeted attack is separate from ordinary primary volley input. Both remain unmet.

## Actual Matrix intake (2026-11-09)

Reviewed D.Va lane190bc7c/301c61c/6941c22/ee2db6d infrastructure and Matrix
consumer, plus601d748 independent Fusion Cannon rays. Explicit0407 body selection
uses its own original graphs,300 health/300 armour and mover; it does not pretend
that canonical pilot040A is a mech. Additional graph/mover/tag inputs are additive,
read only and stay in lane007A outside Git. Set ORPHEUS_GRAPH_SUPPLEMENT to
mech-supplement.json.gz, ORPHEUS_MOVERS_SUPPLEMENT to mech-movers.json and
ORPHEUS_PROJECTILE_TAGS to projectile-tags.json before module imports.

Shared ruleset restoration preserves typed non-null values and referenced-variable
fallback. Direct component43 ownership and live possession queries use Match
identities. Their native evidence and boundaries are documented in
BODY_OWNER_QUERY.txt and RULESET_VALUE_FALLBACK.txt; alternate input handling is
documented in BODY_FORM_INPUTS.txt. These services are required for the actual
02CC resource initialization and ordinary Matrix activation, not forced flags.

Matrix absorption uses four authored boxes, currently attached to the combat eye
and command aim. Projectile tag0145 is a reconstructed exclusion; unknown metadata
passes. See DVA_MATRIX.txt for the native boundary. Hitscan/projectile sweeps now
compare body/static cover, Winston bubble and Matrix distances before consuming
an event. A Matrix-absorbed projectile retires without direct/splash damage or
explosion release. Non-Volley Tesla, quick melee, Primal and Jump damage bypass
this interception path. No extra per-hit Matrix resource drain is invented.

WinstonMatrixTests runs real Matrix commands, including a hostile Soldier rifle
positive control, Tesla beam armour/charge, quick-melee armour, Primal armour and
physical impulse, Jump launch/landing, and nearest bubble/Matrix ordering. The
landing fixture casts on physical descent: casting immediately on launch exhausts
the real Matrix resource before this full vertical jump lands. It injects neither
landing state nor damage. Native geometry/filters/history, complete NPC Matrix
interactions and original-client presentation are separate unmet requirements.

All new combat service state and body-query contexts are explicitly included in
server stored-state exports. Body VM snapshots retain both command aim and the
ruleset target cache; unsupported new state remains an error, not a missing hash.


## Control and actual NPC Jump evidence (2026-11-09)

Parent64bf71c recognizes Flashbang053/0079 via0228's0.8/1.7s duration branch;
this lane reuses its exclusive world deadline, held-input release, Ability.stop,
buffer cleanup and route pause. Complete224E execution, native status/immunity,
cleansing and client presentation remain open. Effects run only after admitted
damage: Zarya absorption and a nearer Winston bubble cannot leak control. Static
wall-impact splash starts on the approach side to retain contact occlusion.

Eleven actual-command tests check Winston Tesla/ammo/movement interruption,
charged-Q refusal with no delayed buffer/spend, body/death cleanup, Jump cooldown,
existing barrier survival/protection, projected Zarya protection and no delayed
quick-melee/Primal damage after control. The real0109 health lifetime survives
control and ends on its original clock; no special forced form flag is used.
Ending the temporary health pool preserves the20 armour lost to Flashbang,
leaving480 rather than refunding it. This is the existing reconstructed pool rule.

All five actual NPCs now have launch1 from front/rear, full landing50, analytic
lower-capsule falloff including Heavy radius.7, external movement, range/team/wall
rejection and lethal melee/Primal/launch/landing corpse cases. The independent
analytic falloff expectation does not call the production distance function.
Low scaled impulses can land before20frames; height peaks are sampled rather
than assuming every victim remains airborne at an arbitrary later frame.
The build has no Winston secondary weapon; actual NPC secondary holds consume
no ammo, deal no damage and grant no damage charge.

Actual Flashbang plus Primal40 composes with external motion on every NPC.
Trooper/Sniper/Heavy fire before control, cannot create accepted shots during
control and recover after a fresh release/press. Old physical projectiles may
finish. Explicit graph input and bot_stream=None transports are fixtures, not
production AI. Other NPC resource/control/special paths remain open.

ENFORCER_CONTROLLER_PREDICATE.txt records eight native predicate-tail cases:
controller75 present and92 absent. Lookup/output adapters and untested full
namespace/controller allocation are explicit. Assassin
targeted incoming test remains unmet; no forced Boolean/target is used in production.


## Enforcer controller and physical volley (2026-11-09)

NPC_ASSOCIATED_CONTROLLERS.txt describes the optional227A controller namespace,
actual13EA Stack9134,25 physical pellets,four damage indices,flight falloff,
release/reload/range/defense and controller causal-snapshot cases. It does not
force B96D3A98 or fabricate a shot. The native predicate-tail proof remains a
separate limited result. The new external combat-supplement.json.gz combines
that controller's five initial roots with the existing seven-graph mech input.
Set ORPHEUS_GRAPH_SUPPLEMENT to this owned additive file for the latest suite;
existing runtime/NPC/mover/tag inputs remain read only. Preparation is repeatable
with tools.prepare_npc_controllers, and reports unexpanded controller ref147B.

Parent2ca9178 input joins are reviewed/reused, including their two causal Instance
fields and10 tests. Enforcer is removed from expectedFailure only after direct
controller/weapon/physical-pellet proofs. Assassin's declared target/controller/
health core now passes without an expected failure. Complete specials/resources,
contact/attachment and remaining defenses/statuses still prevent goal completion;
native charge/scoring eligibility remains a separate unresolved interpretation.


## Historical Assassin target/input boundary (2026-11-09)

The additive input now has15 graphs/three entities. Associated1EBE/13C2 runs
its actual criterion and target1650 writer from explicit input14365; the original
full unknown filter clears the target. Declared logical36 drives1157, but damage
acceptance remains OPEN. Six boundary cases cover selection, cancellation cleanup,
body criterion, paired getters, input validation, isolated14695 condition plus
actual Primal, and retirement. Seven scalar-condition tests preserve native
candidate scope/conversion without granting full filter admission.

The expected failure now measures the actual targeted36/controller path rather
than the ordinary20 graph that has no damage output. NPC_ASSOCIATED_CONTROLLERS
records reconstruction and filter boundaries. Original-client acceptance and
the complete static goal remain OPEN.

Additional read-only charge research confirms shared0B8B node3's one-second
interval and node4's5 *9532 *12918 capped increment, plus node23's damage
multiplier expression. A live Winston VM has unresolved2426 and mode11002
gates, and no authored passive gain across130 commands with legacy fill off.
The legacy60-second placeholder remains unaccepted pending proper eligibility
and clock integration; resource arithmetic alone is not accepted full charging.


## Resource charge and server-clock Primal lifetime (2026-11-09)

Winston now consumes original0B8B's5/sec passive and damage multiplier expressions
instead of the60-second fill. Mode/native eligibility remains explicitly
reconstructed. Bound passive cadence uses world time, and original Wait3 cannot
be accelerated by command bursts when2426 is explicitly enabled.

The original0109/node25 lifetime ChaseVar also uses a scoped world-clock bridge.
Its original finish/stop actions remove health and modifiers during owner input
silence; a12-second skipped world update awards only2 post-Primal seconds.
No owner command frontier is advanced. Twenty new actual acceptance methods
cover rate/arithmetic, admission, clocks, real casts, expiry and causal storage.
See WINSTON_CHARGE_CLOCK.txt for exact native/reconstruction boundaries. Native
mode/scoring/tag/difficulty services and the full static matrix remain OPEN.


Historical fixed-source verification:729 run,728 pass,one unmet Assassin targeted
expected failure,zero errors/skips/unexpected successes. Twenty charge/clock
cases and additional generic runtime/scope regressions are included. Separate
full-server mutation check passes. Python hashes are unchanged through the run
and match the current source. This does not prove the complete static matrix.

A separate actual-cast frontier probe now proves Reaper Wraith's three-second
Wait remains active after ten world seconds when its owner sends no commands.
Existing nine Wraith cases use advancing owner input; they do not cover this
known gap. wraith-idle-clock-frontier.json is unmet acceptance. Review the
Reaper lane's distinct idle-body policy before compatible restoration.


## Defense idle time, input horizon and mover reconciliation (2026-11-09)

A controlled one-player stored-state journal sends raw Jump Pack and
Barrier Projector input on one frame, then sends the release window and
retransmits the original command. Replay verifies the recorded state at
each root and matches both consumed cast markers, the flight instance and
frame, and the sphere's position, HP and expiry. This is an event replay
from match creation through the recorded commands.

WINSTON_DEFENSE_CLOCK.txt documents the reviewed/adapted no-input policy,
original Wraith/Deflect/Matrix lifetime and cooldown/resource restoration,
finite established-flight replay, and current/stale/future input admission.
Winston also rejects future damage/action bursts while retaining his separate
Primal/charge clocks and unchanged owner frontier during input silence.
Historical Wraith/future-input unmet probes now have corrected actual-input
rows. Nineteen new cases cover these requirements; native transport/client
equivalence is not claimed.

All170 numeric fields in the5 private NPC mover profiles equal parent's new
npc-navigation-runtime extraction. Runtime43 revalidation is separate from the
main runtime38 + explicit authored catalog run;both use static collision fixtures.
There is no real-map cache/deployment/client claim. Runtime/hardlinks are read only.

Historical Assassin research isolated a proved empty-inline-rule/null-key callback
path for actual13C2/node10:enum2 needs no guessed global-registry default.
Historical controller masks have no components28/51/57, unlike the body;these
are historical availability facts,not a live filter-payload proof. Full lookup,
category projection, controller/body condition routing and attack/control
services remain unresolved. assassin-controller-filter-frontier.json records
source hashes;no target admission or damage has been granted by this research.


Historical exact-source checkpoint:748 run,747 pass,one unmet Assassin targeted
expected failure;zero errors/skips/unexpected successes,unchanged source and
current manifest exact. Alternate navigation-runtime41-case run passes40 with
the same unmet Assassin case and the identical current source manifest.
All170 mover fields and read-only input hashes were rechecked after that run.
Full-server anchor causal check also passes. All owned processes are terminal.
This supersedes the historical729/745 checkpoints,not the open full goal.

The November 10 frozen checkpoint ran 1,023 tests, with 1,014 passing
and nine map-cache skips in the mover module. The same mover module
passed all 47 tests with the original map caches, covering the nine
skips. The Python source manifest matched before and after the combined
run. This is a regression checkpoint for the selected modules. The
remaining target eligibility, NPC service and original-client rows stay
open. See `winston-nov10-composite-validation.json` in the owning lane.

The Trooper `0E20/34` handler consumes admitted `255F`, `2560` and `2596`
messages with the ordinary `0080` category inputs. It reads the 104319
`079C.021` candidate rows from `ORPHEUS_TALON_HIT_CANDIDATES` or
`talon_hit_candidates_174.json` in the prepared NPC data directory. Include all
three identifiers in that catalog for full range coverage. The host reconstructs
direction from body yaw and uses zero auxiliary query values. The authored
ordinal chooses from tied top candidates. If the direction has no admitted
candidate, the graph can use its default alias. The authored `0E20/27` wait
advances while the body is idle.

For Winston quick melee, Jump Pack landing and Primal punches, an admitted
nonlethal Trooper hit evaluates the three untagged range rows from `1231/6`.
The host interprets the bounds as inclusive actual health loss after mitigation.
It derives the direction from strike aim or radial landing geometry, using the
attacker's aim for a centred landing. It schedules one `077A` notification on
the active `0E20` graph at the world hit time. Normal landing falloff reaches
`2596`. The `2560` boundaries and the default alias at a side angle use a
supplied contact amount through the regular damage pipeline. The currently
modeled Winston contacts peak at 75 damage with Nano. The host's producer,
other reaction conditions, and original-client playback remain separate from
the captured candidate-selection evidence.

Heavy Assault's untagged `2560` row at `0E54/32` uses the same reconstructed
damage-loss rule. With the natural category unset, the host uses false `0407`
and queues the notification for `0E54` and the common `0E20` at the hit time.
The full `07C0.021` row set enables this route. The captured selector and
decoder return an empty alias. The wrapper writes a zero duration before the
authored `0E20/41` continuation clears `9607` without starting a playback wait.
A qualifying hit overwrites a prior duration, while a rejected hit leaves it intact. The regression covers a 75-point
Nano landing, a 50-point ordinary landing and an allied contact. The original
producer's flag selection remains open.

An admitted, controlled Trooper `38DB` notification uses the same `0E20/34`
action and selects `04B9` from the archived full model rows. The graph issues
one animation state begin with its authored zero-duration field, then keeps
the separate five-second wait and its reaction marker until idle-world expiry.
False `0407` does not start that path. The two `1231/6` priority-condition
rows refer to -10 and +10. The runtime comparator and automatic producer for
these rows remain open.
