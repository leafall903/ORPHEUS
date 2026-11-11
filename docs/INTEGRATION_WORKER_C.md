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

This entry is committed with `feat: integrate delivered Bastion repair and charge`.
After receipt, find this commit by subject before applying later worker commits.

## Exact next work

1. Retain Bastion Recon239B571E's authored between-shot spread decay and launch
   count, Sentry's no-decay ramp and the rear-core critical capsule/pose history.
2. Reconcile the remaining Tank splash/impulse/lifecycle consumers and external
   cross-hero helpers; compare owner features, not just ancestry or file names.
3. Continue the other seven deliveries. Substantial missing owner families include
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
