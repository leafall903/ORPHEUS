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

## Exact next work

1. Continue Pharah's original curve reflection eye/seed/lifetime restart
   (scoped002B interruption is mounted), then splash/self-splash/impulse and remaining
   charge/control consumers. Do not replace the combined projectile host.
   Root owns NPC bots/navigation/snapshot edits while reconciling sniper helpers.
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
