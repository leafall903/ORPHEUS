# Full Python hero integration with public main

This candidate reconciles the local full original-language integration at
`eaf33b759efc307eb1d92a2d36600bae03c7814e` with public Python main at
`7164b85c79c6603936c6c74ba8bcbe386f020385`. It does not migrate gameplay to Rust.
The existing 32 hero deliveries, shared runtime and tools are retained. Original
client data and prepared research inputs remain external and are not published.

## Resolved integration boundaries

- Adopt the public `src/` package, `scripts/` launchers and public/prepared-runtime
  test layout. Standalone local tools resolve the source package from `src/`.
- Retain the current public runtime preparation, CLI preflight, Wine relay
  changes, source packaging, dashboard and language-neutral behavior contracts.
- Combine upstream Uppercut facing, direction clamp, queued velocity changes,
  target tracking and post-VM damage with the existing hero services.
- Separate the upstream internal vector-delta record from the full integration's
  typed mover discriminator. Apply the shared scalar velocity channels once,
  before world-space deltas and collision, while retaining existing force APIs.
- Use the upstream vertical velocity channel for Piledriver descent, retaining
  the local windup, grapple, damage and lifecycle services.
- Keep linked weapon output invalidation and the full local spread/decay APIs;
  the existing reticle, reload and held-fire consumers continue to use them.
- Retain base and wave health-pool validation alongside the upstream temporary
  shield state. Both use the existing authoritative health pools.
- Extend the existing explicit snapshot schemas for the new tracked-damage,
  movement-sample, shield-state and bot-contact fields. Unknown fields remain
  errors; no general serializer fallback was added.

## Verification and limits

- Public unittest discovery: 953 methods executed. The preserved suite reports
  six failing subcases and three errors. Assertions and exception expectations
  were not weakened to obtain a passing result.
- CLI and shared framing/World contracts: 36 methods passed. Dashboard and NPC
  console JavaScript checks: six passed.
- Joint prepared movement/weapon/force run: 28 methods executed, with two Slam
  selector subcases failing. Uppercut movement and damage, Piledriver, cannons
  and the selected original force regressions pass in that run.
- The remaining public failures concern expression-array padding/index policy,
  owned-child stop ordering and the strict mission host. The strict host errors
  concern scoped PlayScript, component-86 control and subgraph scope support.
  These are preserved compatibility/evidence boundaries, not missing commits.
  Related strict host failures also reproduce against the previous local full
  integration with the same synthetic tests.
- The local complete Slam host and the narrower upstream selector test differ
  on grounded activation and one airborne-clearance case. The assertions remain
  unchanged; full equivalence is not claimed.
- Undefined-name and duplicate-definition checks pass. Full repository lint is
  recorded separately and is not claimed clean; existing style debt is retained.
- A disposable server-only startup probe binds separate lobby, Battle.net RPC,
  dashboard and game UDP ports. Its dashboard returns HTTP 200 and reports the
  external runtime available. The probe was stopped afterward. No game client
  or existing play session was controlled.
- Checks used Python 3.11.9 with the public pinned dependency versions available
  on the development machine. The public package and CI target Python 3.12;
  a local Python 3.12 run remains pending.

This is a review candidate. Complete original-client combat, presentation and
all hero acceptance are not asserted. Publication uses a source-only snapshot
based on the reviewed public main and a pull request; local research history is
preserved separately.

## Additional publication checks

The exact source-only publication checkout repeats the 953-method public result
(six failing subcases, three errors), passes all 36 CLI/contract checks and all
six JavaScript checks, and boots successfully with the external runtime on
separate disposable ports. The disposable services are stopped after inspection.
All Python files pass undefined-name and duplicate-definition checks.

A further 29-method prepared run preserves seven failing subcases, without
errors. In addition to the two Slam selector cases, the upstream Roll tests
expect the active profile in `Mover.data`, while the retained full typed-profile
implementation keeps its base in `data` and its active profile in `values`.
Adaptive Shield tests expect wire slot one; the retained full temporary-pool
allocator reserves that slot and publishes tagged pools in later slots. These
are unresolved API/wire expectations; the tests have not been rewritten.
A separate local Piledriver scenario also retains a rifle-hit assertion failure
after the changed descent timing. Full equivalence and complete acceptance are
not asserted. These checks are recorded for review rather than submission gates.
