# Original0016 shared geometry query consumers

C explicitly transferred Combat.cover_ray, visible_target, trace_barrier,
_splash_targets and _splash_barriers to root. The original Sentry retained-impact
proof calls them; their names were absent in the unified host. Root restores
those actual entry points through pure prediction_geometry functions, reading
existing Walls/Photon/raised Reinhardt/world Winston/Orisa/Sigma actors and
existing mesh/capsule geometry. Legacy selected native Reinhardt field geometry
is read only when no raised actor owns that body. No actor, manager, health pool,
controller or stored runtime field is added.

cover_ray includes terrain, solid walls and active player Cryo capsules, with
the original0068 optional exclude argument. visible_target uses the existing
nearest-axis capsule ray. trace_barrier returns the real object and real owning
service; an Ice Wall result is its geometry piece and the same fight.walls
object, not its backing actor or a wrapper. Splash queries respect supplied
predicted Target poses, suppress the exact impact object/service pair and sample
other current geometry without spending health. Generic area application stays
in the existing combat path; these methods serve original read-only prediction.

These readers intentionally do not call Reinhardt.sync or Sigma.update: those
methods can allocate actors, advance time or run VMs. Their authoritative actors
are settled by the existing world/body drivers. No fallback bounding geometry
replaces a missing mesh. Closest points use existing triangle, plane and sphere
primitives. Particle-barrier state/phase admission stays with C's actual
recipient proof and current damage/defense hosts.

An actual encrypted-peer ServerSession probe passes real Mei five-pillar solid
ray/object/service identity, explicit predicted Target pose and amount, splash
impact exclusion and whole-server stored digest stability. Actual raised
Reinhardt surface/identity/splash/exclusion/digest checks also pass. Both run
under Component.run_now/run_frame, Reinhardt.sync and Sigma.update rejection
guards: queries neither run VMs nor synchronize actors.

The Winston fixture initially failed to produce a live actor: its raw input
was ahead of the body/world admission window, and Session.tick advances only one
world tick unless missed ticks are explicitly supplied. Driving that actual
clock correctly produces the real actor; Winston surface/object/service/splash/
exclusion and whole-server readonly digest checks now pass too. No production
clock was changed. The initial separate Orisa raw cast fixture had no live
barrier. Subsequent actual paced world ticks let its current deployable land and
complete its existing deployment; no rocket or actor was injected. Orisa and
Sigma surface/object/service identity, splash inclusion, impact exclusion and
whole-server readonly digest checks now pass.

The initial Photon horizontal input produced no valid actor. Actual ground-aim
preview/confirm input, followed by the existing windup, produces its real actor;
all corresponding geometry/identity/splash/exclusion/readonly digest checks pass.
Together with the earlier Mei/Reinhardt/Winston receipts, all six existing
geometry families now have scoped actual-server query evidence. This does not
claim every original combat/placement contract is closed. No production clock,
serializer or gameplay assertion is changed for these fixture corrections.
Initial and current probes/logs remain external:
parallel-merge/root-prediction-geometry-actual-probe.py/.log,
root-prediction-remaining-geometry-probe.py and its-paced.log,
and root-prediction-photon-geometry-probe.py/.log.


Body-splash deduplication is limited to a target that is exactly the backing
actor of a piece in the current walls map. Unbound original native pillars keep
their body damage path. An actual ServerSession unbound-pillar query returns80
splash without spending health or changing the whole-server digest; no geometry
clone or actor is created by the query. Explicit predicted-target lists still
retain their passed objects/poses. Probe/log:
parallel-merge/root-prediction-unbound-pillar-probe.py/.log.
