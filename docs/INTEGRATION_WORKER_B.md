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
  hit/pull and shield awards (batch `856ce426`, canonical `94ec713f`).
- Meteors now mount confirmation/input filtering, target locking, ground warp,
  capsule-range/falloff/cover selection, real health loss, launch, protection,
  interruption/death/retirement and snapshots. Do not claim the whole Doomfist
  line while Uppercut and remaining shared-service consumers are unresolved.
- The source null-case Switch semantics now run authored indexed phases while
  retaining the earlier1248 weapon-combo adapter. Positive external launches
  leave their floor before the authored landing limit can attach them again.
- All22 original Slam checks,21 Punch and13 VM runtime checks pass (56 total).
  Original Slam expectations remain; four reads of the source persistent
  `temporary_pool_used` field use the canonical retained shield-object presence.
  McCree Roll, numeric modifier and Lucio velocity regressions: all41 pass.
- Meteor batch `89d4114e` is accepted as canonical `67c2a296`.
- Meteor's original capsule-boundary range test passes both subcases. Its
  enclosing-server active snapshot passes getter-free repeatability and stored
  confirmation causality after real neutral-input charging. The full28-check
  original suite completed in380s: four assertion failures (two are subcases of
  one released-Hand-Cannon check), seven errors. Preserve the full log at
  `verification/worker-b-doomfist-meteor-full.log`. Failures expose the remaining
  eleven-pellet Hand Cannon and the then-unmounted Uppercut passive. Errors
  include old protected/SOMBRA/ana_sleeps/ANA_SLEEP_EFFECT APIs and a direct
  lookup before canonical's lazy respawn vitals creation. No check was removed.
- Uppercut target/damage consumers now mount through the retained passive
  module using the delivered sphere, lower/upper rays, active Cryo cover,
  damage, passive shields and survivor launch. Health/motion reports are posed
  queues, discarded on unposed input or VM fault and included in passive
  snapshots; no unposed state callback may directly damage or launch a body.
- Uppercut23+Slam22+Punch21+runtime13 ran79 checks, with78 passing and one
  cap-metadata assertion then fixed. The focused cap/HUD/decay/incoming damage
  follow-up passes all5 checks. Queued Uppercut report getter-free enclosing
  server snapshot repeatability/causality also passes. Source fixture cap/delay
  reads use the existing canonical shield-object fields; assertions remain.
- Uppercut batch `8759d171` submitted. Hand Cannon now mounts original radial
  eleven-pellet scatter, seeded jitter, explicit critical handling and null
  sentinel. Original21 Hand Cannon checks all pass.
- The source aggregate shield add/decay API and low-range pool identity now
  mount over canonical TemporaryShield; other producers retain their existing
  default identity. Original13 passive checks all pass, including capped-delay
  retention and exact pool2/clear-to0 wire checks. Doomfist damage charge uses
  ordinary health loss, excluding temporary shield damage as the source did.
  Combined21+13+the Meteor temporary-shield charge regression: all35 pass.
- Hand Cannon/pool batch `7f9b0e73` submitted. Preserve TemporaryShield's
  existing fifth positional activation argument; pool_id is appended after it
  and the Doomfist producer passes pool_id by keyword. The existing positional
  snapshot fixture plus13 passive checks pass (14 total).
- All9 original Meteor lifecycle scenarios now pass across the first7-pass run
  and the two focused API follow-ups. Their observations use actual sleep
  deadlines, frost storage, immunity admission, lazy body-vitals acquisition,
  and Hack's actual application/deadline, retaining the original5000ms check.
- Retained original resources/interrupts/throttles/held-primary suites: all22
  checks pass without behavior/assertion changes. These exercise real ability
  input, magazine clocks, Flashbang exits, movement gates and retained fire.
- Input/lifecycle batch `f694f758` submitted; canonical `728e6abe` synchronized
  in local merge `2ac8acae` before the cross-hero defense audit.
- Retained original Wraith10 and Deflect15 scenarios; all pass with current
  mounted Reaper/Genji services and the explicit complete projectile-tag input.
  The source reflection-budget endpoint check found a genuine free epsilon
  displacement; separation now uses and charges only remaining flight budget.
- Preserve the source pool grant deadline/cap separately from decay integration
  cursor/depletion. Together with13 passive checks, the Deflect run passes28.
- Original20 Grasp checks are retained. The first run has one failure and four
  errors: source aggregate deadline/rate observations differ from canonical
  native extra pools, and three checks refer to the source pool_reports queue.
  Audit actual pending grant/posed/fault semantics during023B integration; do
  not weaken or remove these checks or assume a missing name means no Grasp.
- Shared Echo/Genji Deflect33 checks have7 failures/2 errors both before and
  after this reflection change. The baseline uses HEAD's exact _sweep_rocket
  function in process, preserving the WIP tree. Evidence is retained outside
  Git at worker-b-reflection-shared.log and its -baseline counterpart.
- Defense batch `6f7ef665` submitted. Synchronized canonical NPC-control modes
  `9cad40e8` in local merge `b8f98009`; use its mounted melee/charge paths rather
  than implementing a second NPC driver. Earlier missing-mode observation was
  from the older728e6abe tree, not the current canonical implementation.
- Winston delivery0009: retain the complete original winston.py and mount
  Primal's health transform, explicit world-clock lifetime, source passive
  cadence, target/tag damage charge and body retirement. Preserve the existing
  Tesla prototype code but stop routing it alongside the deduplicated original
  Tesla path; the two had charged/damaged the same command twice.
- Original source fixture is test_delivered_winston.py; existing canonical
  test_game_winston.py is preserved unchanged. Source charge27 plus canonical
  Tesla12/runtime13 run52 checks:49 pass. Remaining cases are unmounted Jump
  Pack's1 launch damage, old barriers.actors observation and the DVa pilot
  profile/controlled-mech interlock. Explicit DVa graph/mover supplements allow
  both source definitions to spawn; lazy vitals observation is adapted, but the
  pilot profile still expects .96 loss where current controlled-mech admission
  leaves that player pool unchanged. Do not bypass the actual DVa lifecycle.
- Focused DVa/cadence snapshot/modifier/Lucio28 checks:27 pass, only that DVa
  profile check fails. Active Primal enclosing-server getter-free capture and
  cadence causality pass (fixture charge setup, not natural-charge evidence).
  Register the active VoiceSetOverride class; retain all owner cadence fields
  in both body-VM and enclosing-server snapshots.
- Synchronized accepted parallel batches from `a51925d5` in local merge
  `fe851616`, preserving Reaper shadows and Bastion's input suppression bridge.
- All21 original Rocket Punch checks and all13 existing VM runtime checks pass.
  The active enclosing-server snapshot probe is recorded outside Git at
  `verification/worker-b-doomfist-active-punch-snapshot.log`.
- Original Punch expectations are unchanged. Its shared combat fixture imports
  the existing combined Sombra module name; unrelated fixture cases still need
  their shared-service API reconciliation.

## Exact next work

1. Mount Winston JumpPack/PrimalPunches over current wall, impulse and barrier
   services without installing duplicate bubble controllers. Reconcile source
   Tesla barrier/volume checks, then retain the remaining original source suites.
2. Continue Doomfist barrier/NPC equivalence audit using9cad40e8 or later NPC
   controls. Track Sigma native extra-pool/pending-grant differences for023B.
3. Reconcile native body-forward, ray/ground and velocity-action adapters and
   actual original source checks. Do not replace shared files wholesale.
4. Retain source docs and remaining Doomfist checks; reconcile reflected damage,
   resource charge, NPC and status interlocks. Then perform a feature-level
   completion audit before a formal line merge.
5. Advance the remaining six tank deliveries, auditing canonical equivalents
   before importing similarly named helpers. Missing source modules are not
   automatically missing behavior; existing narrow prototypes are not proof of
   complete owner implementation either.

Commit identities and SHA delivery are available from the branch log and parent
coordination record at `verification/parallel-merge/coordination.json`. Every
completed code batch is sent to the parent immediately, followed by more work.
