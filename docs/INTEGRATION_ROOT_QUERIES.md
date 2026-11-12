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

The same probe's Winston cast fixture has not yet produced a live actor under
current body/world input admission; that is not counted as a passing Winston
geometry test. Orisa/Sigma/Photon stages are pending behind it. All five query
implementations are committed without waiting for whole-family acceptance, as
authorized; root continues these actual input/geometry checks while C resumes
source retained-impact proof consumers. No assertions/numbers are rewritten.
Probe/log: parallel-merge/root-prediction-geometry-actual-probe.py/.log.
