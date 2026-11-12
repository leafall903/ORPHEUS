# Original Volskaya lift integration

Scope: original00DD physical 104319 lift, same Match, collision, server replication and native wall actors. Appearance migration stays excluded. Immutable original bundle is referenced through the prepared v19 profile; no copies into runtime or source archives.

## Layered collision receipt

Restored original LayeredEngine and LayeredWorld query behavior. Added only the original layered branch to collision fingerprint and physics static references; retained all stronger current plain-World validation, shape identity and cache validation. Preserved original layered mover/contact snapshot method without changing assertions.

Validation: test_physics_snapshot and test_collision_fingerprint, 31/31 pass in 0.570 seconds. Production platform setup, replication, wall attachment and whole-server storage remain pending and this commit does not claim them complete.

## Resume ownership

Root exclusively owns Volskaya and its Match/select/scene/remote placement, Mei wall attachment and collision/storage seams. Three workers continue independent original consumers and commit batches. Finish lift consumers on existing native actors, not a parallel wall manager; preserve strict Scene field metadata updated by workers.

## Physical platform, native wall and strict storage batch

Restored original00DD volskaya.py (LiftFactory, LiftHost, LiftScene, Lift, scoped 2ABA reset/motion handlers). Match selects only the original Assault/map pair with verified optional external input; original map placeable gets its current-pose create and post-ACK movement. Original default static placeable encoding is unchanged. The original immutable bundle remains external, selected via ORPHEUS_VOLSKAYA_PLATFORM prepared in v19.

Current TrackTargets owns the one typed Entity write, so LiftScene supplies original IDs through that actual service. Existing native Ice Wall actors remain authoritative for health, expiry and damage. Pillars are published once after their parent pose is known. Platform carry updates the same actor position and physical Pillar; transient swept geometry uses shallow probes because the existing Pillar constructor forwards to its actual actor. The original attached-wall membership also covers actual native columns before a geometry projection is bound. Existing native pillars on layered worlds derive only the moving layer engine, preserving both immutable map layers and strict layout identities.

Mounted Lift VM state, LiftScene, LiftHost, LiftFactory, Lift and startup/unit metadata into exact current snapshot layouts; retained default rejection for other shared scene factories/hosts. Current richer Scene field metadata is reused explicitly, not inferred from runtime attributes.

Original wall attachment probe: four methods pass (missing bundle, moving obstruction omits columns, low ceiling holds/resumes rider, attached wall blocks until cancellation). The fifth passes parent wire position, wall/rider delta and first create assertions but its old FakeClient late-entry path never calls current Match.spawn_updates, so it observes no late create. Tracked original active-capture fixture now invokes that actual entry producer; all 79 original assertions are AST-identical.

Original active server capture reached the same late FakeClient observation after all earlier VM readonly/RNG, whole-server stored type, attached-membership causal hash mutation/restore, moving VM and physics descriptor assertions passed (49.782 seconds). No snapshot field was omitted or relaxed. The earlier unmodified normal fixture preserves its fixed 110-tick return assertion failure: platform is still on segment2, 0.53067m above origin, no riders/blocked riders or VM fault. This numerical expectation is retained; it is not a submission gate.

Still verify the tracked actual-entry fixture and baseline non-lift consumers, inspect endpoint/platform return timing independently, and switch launcher to prepared v19 only after relevant integration checks. No services were started.

## Integrated entry receipt

Tracked test_delivered_volskaya plus existing test_game_world, test_physics_snapshot and test_collision_fingerprint: 56 run, 55 pass, one unchanged fixed-110-tick return assertion failure, 54.606 seconds. The actual late-entry producer now passes native parent wire pose equality. All earlier active VM/whole-server causal and physics assertions pass as described above. Obstruction, rider ceiling and wall cancellation methods pass. This is sufficient to submit and merge existing platform code; no complete acceptance matrix is required.
