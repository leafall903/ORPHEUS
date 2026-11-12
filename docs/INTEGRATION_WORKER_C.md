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

## Exact next work

1. At this clean commit boundary merge the latest root canonical locally,
   retaining input_suppressed and all received shared implementations. Then
   audit the remaining Bastion cross-hero helpers and original combat checks;
   compare owner features, not just ancestry or file names.
2. Continue the other seven deliveries. Substantial missing owner families include
   Torbjorn turret/Molten Core, Symmetra portals/turrets, Junkrat mine/tire,
   Mei Blizzard/frost, Sombra cloak/translocator and Ashe/BOB.
3. For Torbjorn also compare the root-discovered preserved file
   `verification/combined-server-pending-tests/test_game_torbjorn_resources.py`
   against incoming/current checks; retain differing expectations without overwrite.

## Evidence and limits

The unchanged37-check Bastion baseline recorded9 failures and10 errors.
Focused Self-Repair/resource/death/gap/Tank checks passed7/7 after this batch.
Further charge and storage results belong to CHANGELOG.md and external logs.
No complete Bastion or original-client acceptance is claimed. The other seven
assigned deliveries are not yet reconciled by this worker.
