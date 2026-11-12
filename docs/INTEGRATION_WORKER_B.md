# Persistent merge worker B

Worktree: `verification/merge-worker-b`; branch: `integration/merge-worker-b`.
Start: `bf7fad86`. Main integration and intake are owned by the parent agent.

Scope: 0007 Reinhardt, 0009 Winston, 0040 Roadhog, 0068 Zarya,
012F Doomfist, 01CA Wrecking Ball, 023B Sigma.
Frozen deliveries are `refs/hero-deliveries/<ID>`; source checkouts are
`verification/hero-lanes/<ID>/source`.

Preserve original-language implementations. No appearance migration, added
feature/matrix, Rust cutover, Internet, push or game-client operation. Incomplete
acceptance does not prevent committing and integrating existing work. Ancestry
alone is not evidence that a line's consumers are actually mounted.

## Current integration

- `feat: integrate delivered Doomfist Rocket Punch host`: mounts the original
  owner Punches controller, charge/release contact, swept movement, control,
  wall bonus, authoritative impulse and passive-shield award consumer. The
  previous Doomfist punch-contact path remains preserved but is no longer routed
  alongside it. Reinhardt's existing path remains in use.
- The source's input-only control flag now gates new and already queued down
  edges. It does not erase Punch knockback velocity as a generic player stun.
  Bot input/control and passive snapshots share the same stored controller.
- First submitted batch: `41d6de0e`, accepted by canonical as `a51925d5`.
- Both delivered `doomfist.py` and `meteor.py` implementations are retained.
  Slams now mount ground-hop velocity, aerial target/movement, landing, wave,
  hit/pull and shield awards. Meteors are not yet mounted; do not claim the
  whole Doomfist line.
- The source null-case Switch semantics now run authored indexed phases while
  retaining the earlier1248 weapon-combo adapter. Positive external launches
  leave their floor before the authored landing limit can attach them again.
- All22 original Slam checks,21 Punch and13 VM runtime checks pass (56 total).
  Original Slam expectations remain; four reads of the source persistent
  `temporary_pool_used` field use the canonical retained shield-object presence.
  McCree Roll, numeric modifier and Lucio velocity regressions: all41 pass.
- All21 original Rocket Punch checks and all13 existing VM runtime checks pass.
  The active enclosing-server snapshot probe is recorded outside Git at
  `verification/worker-b-doomfist-active-punch-snapshot.log`.
- Original Punch expectations are unchanged. Its shared combat fixture imports
  the existing combined Sombra module name; unrelated fixture cases still need
  their shared-service API reconciliation.

## Exact next work

1. Mount `meteor.Meteors` targeting,
   protection/landing/damage through the current collision/health services.
   Preserve current Doomfist Uppercut/passive implementation while reconciling
   its older unmounted `doomfist_passive.py` consumers.
2. Reconcile native body-forward, ray/ground and velocity-action adapters and
   actual original source checks. Do not replace shared files wholesale.
3. Retain source docs and remaining Doomfist checks; reconcile reflected damage,
   resource charge, NPC and status interlocks. Then perform a feature-level
   completion audit before a formal line merge.
4. Advance the remaining six tank deliveries, auditing canonical equivalents
   before importing similarly named helpers. Missing source modules are not
   automatically missing behavior; existing narrow prototypes are not proof of
   complete owner implementation either.

Commit identities and SHA delivery are available from the branch log and parent
coordination record at `verification/parallel-merge/coordination.json`. Every
completed code batch is sent to the parent immediately, followed by more work.
