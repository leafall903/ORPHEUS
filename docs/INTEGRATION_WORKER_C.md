# Integration worker C

Assigned by the user-authorized three-worker merge on 2026-11-12.

- Worktree: `verification/merge-worker-c`; branch: `integration/merge-worker-c`.
- Starting canonical commit: `bf7fad86`.
- Assigned deliveries: 0006 Torbjorn, 0008 Pharah, 0015 Bastion,
  0016 Symmetra, 0065 Junkrat, 00DD Mei, 012E Sombra, 0200 Ashe.
- Preserve and mount existing original-language implementations; no new gameplay,
  acceptance matrix, Rust runtime cutover, network, publication or client session.
  Commit partial functional batches despite incomplete whole-line acceptance.
  Root receives the committed batches and reconciles shared-file overlap.

## Current delivery

The first batch mounts Bastion's authored Self-Repair reports, bounded sampling,
actual restored-health notification and passive/damage ultimate gates. Existing
incoming8243 already applies Ironclad0.8; do not multiply another0.8 adapter.
The original scalar query is retained for delivery consumers and checks.
The original combat and charge check files remain unchanged.

First functional batch: `cd7cb8f350b21f3960abe2ff98b5ffeb053743cb`
(`feat: integrate delivered Bastion repair and charge`), received by root as
canonical0be0ca2a. Root retains the subsequent Component.input_suppressed bridge.

The second batch mounts Recon/Sentry authored spread, Recon critical default,
Sentry rear-core capsule and historical yaw. Its commit subject is
`feat: integrate delivered Bastion spread and rear core`; resolve its SHA from
this branch before reception. Four owner checks pass; shared checks pass46/49,
with all three failures reproduced unchanged atcd7cb8f3.

Third batch: `feat: integrate delivered Bastion Tank projectiles` mounts the
original Tank charge/contact/self-splash/impulse/body-retirement consumers.
Second batch SHA: `247f0bce`, received by root as canonical566dc09a.
The11 Tank player checks pass using the frozen real sniper helper solely to
satisfy the unchanged NPC fixture import; its normal import remains unresolved.
Shared Helix/Tracer projectile run records7 passes,1 external Matrix skip and
1 missing sigma_accretion import. No client or NPC acceptance is claimed.

Third batch SHA: `9d667dfd`, root received as canonical00fabf7a.
Local canonical synchronization: `a4e85027` (coordination merge, do not replay
as a functional batch). All49 original Bastion combat/charge checks now pass.

Fourth batch: `feat: integrate delivered Pharah hover and Jump Jet` mounts
0025 hover force,0027 reset/vector action and body-yaw query. All40 checks
(Pharah movement12, McCree roll15, script runtime13) pass. Root receives the
single functional SHA after the coordination merge; it already has its parents.

Fourth batch SHA: `c46b41e3`.
Fifth batch: `feat: integrate delivered Pharah Barrage channel` mounts the
mode0 persistent world-velocity SET and LogicalButton/WeaponVolley ultimate
query. Five original owner checks and37 shared physics/Tracer/Echo checks pass.
This does not mount the oscillating projectile consumer yet.

Fifth batch SHA: `adf49f18`.
Sixth batch: `feat: integrate delivered Pharah Barrage motion` mounts original
sampler and actual launch/curved chord/range consumers. Three original motion
checks pass with the real frozen sigma_grasp helper as fixture import fallback.
Shared Helix/Tank/server-snapshot run is active in tool session33339; inspect
`../worker-c-pharah-motion-shared-check.log` and retain the result next commit.
The unchanged `tests/test_pharah_control.py` is copied but deliberately not in
this batch: baseline8 checks records5 failures (channel abort, McCree double hit
and Flashbang boundary differences); do not weaken or drop those assertions.
Torbjorn pending resources check differs from source (including150 versus200 tick
fire cadence sampling); preserve both originals under distinct names later.

Sixth batch SHA: `1ecc533b`.
Seventh batch: `feat: integrate delivered Pharah channel interruption` mounts
source002B stack abort in combat and Sombra consumers. Original13 control/channel
checks retain4 baseline subcase failures in two other-owner timing/hit-count
methods; the real Barrage abort/recast now passes. Real Sombra service probe and
all26 script-runtime/Reaper-Shadow checks pass. `test_pharah_control.py` is now
submitted unchanged rather than left as an untracked fixture.
C6 full shared run remains in session33339; poll the external log before deciding
whether any new failure needs correction. Do not silently claim it passed.

Seventh batch SHA: `2d2da024`.
Eighth batch: `feat: integrate delivered Pharah curved reflections` mounts the
source restart/eye/seed/fallback/catch-up consumer, spring-aware launch and
point-wall/body-assistance policy. Five original curve/reflection cases pass.
Original eye/Helix/channel run:18/19 (retained McCree double-hit expectation).
Genji/Baptiste43 records6 failures/2 errors; all6 failures and Mei bubble error
reproduce at the preceding commit; one NPC data configuration error remains.
External logs: worker-c-pharah-{barrage-reflect-final,reflect-eye-shared-final,
reflect-services,reflect-services-baseline}-check.log.
Root received C5 asc254331d and C6 as37577fbd, with42 and14 focused checks passing.
C6 full server_snapshot session33339 is still active; do not rerun or label green.

Eighth batch SHA: `20598453`.
Ninth batch: `feat: integrate delivered Pharah blast impulses` mounts actual
Concussion/self-splash/ring/source force and modifier26/local ADD consumers.
All34 impulse/Helix/Tank/physics checks pass and curve/reflection5 still pass.
Explicit Rocket.is_barrage tags isolate the curve parser from Echo's motion
payload; Echo8 complete without the reported parser exception (6 pass,2 payload/
copied-charge differences in this pre-A7 tree). Root A7 must be synchronized
before calling these two residual differences current canonical behavior.
Logs: worker-c-pharah-{impulse-shared,motion-tag,echo-motion-tag}-check.log.
Root's Bastion/NPC audit found two interlocks to retain: Heavy capture changes
Sentry to Recon (wall295 versus235) and Heavy melee lacks displacement despite
correct damage. Do not remove original stance/movement bridges or assertions.
C6 full snapshot process53540/session33339 still runs; do not restart it.

Ninth batch SHA: `e9849f58`, received by root as2378f2d7; explicit motion tag
removed the Echo parser exception, with two original payload/charge differences
retained. C8 received asf3989db0. Clean local synchronization:45830030.
Tenth batch: `feat: retain delivered Pharah source sight and boundary behavior`
mounts the original independent middle/nearest source sight consumer. All45
original charge9/flight11/edges18/basic-combat7 checks pass; all17 impulse/Helix
checks pass. Source checks are unchanged; whole-line equivalence remains open.
C6 full server-snapshot process53540/session33339 remains active after26 CPU
minutes, with buffered PowerShell output still empty. Do not label it green or
start another copy; other concrete batches continue independently.

Tenth batch SHA: `c9b4f9e7`, root received as9b93d738; integrated profile62 pass.
Eleventh batch: `feat: integrate delivered prepared NPC impulse bodies` mounts
root-delegated narrow Bot/manual physics and owned Mei/Pharah target adapters,
plus the existing hook module's future shared-body handoff. Root attack_mode and
reposition cleanup are preserved; npc_control/npc_navigation are unchanged.
Original12 NPC cases:11 pass,1 Combat.hooks missing-consumer error remains.
All96 Bot/control/navigation checks pass, three original wall/team scenarios pass,
and explicit navigation resumes after settling in a direct handoff probe.
NPC inputs: ../retribution/npc-runtime/data; ../hero-lanes/0008/asset-research/
five-npc-ai-candidate.json.gz; ../hero-lanes/007A/mech-npc-movers.json.
Logs: worker-c-pharah-npc-impulse-integrated-check.log,
worker-c-npc-physics-shared-final-check.log, worker-c-pharah-npc-wall-focused-check.log,
worker-c-npc-impulse-navigation-probe.log. No source assertion was changed.

C6 full snapshot run is FINISHED:70/2615s,2 failures,10 errors,3 skips.
Three journal errors are INVALID as implementation evidence because ongoing
worker edits changed the captured model manifest; guards correctly rejected it.
Other legacy observation errors and Zarya/Moira assertions remain in the log.
Root explicitly requires any future long journal run on a frozen committed SHA
in a detached worktree, never the actively edited worker tree, and never a gate.
No running long test remains from session33339/process53540.

Eleventh batch SHA: `3e3aab4a` (ready for root receipt).
Twelfth batch: `fix: resolve Moira deployed target definitions` retains physical
Deployable targets while selecting their body metadata instead of Player.hero.
Actual Moira/Mei pillar probe:400->384, no VM fault. Two original Grasp checks
pass; one combined barrier/pillar test lacks its external descriptor and skips.
Logs: worker-c-moira-deployable-{contact-check,pillar-probe}.log.
No long test remains active. Future journal checks use frozen committed trees.

## Exact next work

1. Submit/reconcile C11/C12, synchronize latest canonical prepared-input entry
   and preserve root Echo curve_motion/source_entity fixes. Review remaining
   Pharah defense/barrier/control equivalence and continue the other six owner
   families. Moira Deployable correction is mounted; do not remove those targets. Core charge/flight/edges45 pass; NPC physical11/12 pass.
   Root/worker B still owns complete Roadhog hooks and root owns NPC input/nav;
   preserve the C11 narrow physical-body adapter when bringing those implementations in.
2. Audit the remaining Bastion cross-hero helpers; compare owner features, not
   just ancestry or file names. Core combat/charge49 checks already pass.
3. Continue the other six deliveries. Substantial missing owner families include
   Torbjorn turret/Molten Core, Symmetra portals/turrets, Junkrat mine/tire,
   Mei Blizzard/frost, Sombra cloak/translocator and Ashe/BOB.
4. For Torbjorn also compare the root-discovered preserved file
   `verification/combined-server-pending-tests/test_game_torbjorn_resources.py`
   against incoming/current checks; retain differing expectations without overwrite.

## Evidence and limits

The unchanged37-check Bastion baseline recorded9 failures and10 errors.
Focused Self-Repair/resource/death/gap/Tank checks passed7/7 after this batch.
Further charge and storage results belong to CHANGELOG.md and external logs.
No complete Bastion or original-client acceptance is claimed. The other seven
assigned deliveries are not yet reconciled by this worker.

Canonical synchronization after C9: f3989db0; preserve all original worker A/B/root
consumers. Only CHANGELOG required conflict resolution, retaining both entries.
C10 must verify received NPC/sniper, Doomfist and retained movement bridges.
