# Original Volskaya lift integration

Scope: original00DD physical 104319 lift, same Match, collision, server replication and native wall actors. Appearance migration stays excluded. Immutable original bundle is referenced through the prepared v19 profile; no copies into runtime or source archives.

## Layered collision receipt

Restored original LayeredEngine and LayeredWorld query behavior. Added only the original layered branch to collision fingerprint and physics static references; retained all stronger current plain-World validation, shape identity and cache validation. Preserved original layered mover/contact snapshot method without changing assertions.

Validation: test_physics_snapshot and test_collision_fingerprint, 31/31 pass in 0.570 seconds. Production platform setup, replication, wall attachment and whole-server storage remain pending and this commit does not claim them complete.

## Resume ownership

Root exclusively owns Volskaya and its Match/select/scene/remote placement, Mei wall attachment and collision/storage seams. Three workers continue independent original consumers and commit batches. Finish lift consumers on existing native actors, not a parallel wall manager; preserve strict Scene field metadata updated by workers.
