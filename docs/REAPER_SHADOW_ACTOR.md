# Reaper Shadow Step actor (104319)

The actual00F2/node74/state43 CreateEntity was a passive State in the normal
body VM. Its1F0E output12105 remained absent, so node19/state10's0282 effect
had no real actor to resolve. This repair adds only this Reaper state factory.

Original role and field evidence

- Original e7 reflection typeC2DC60F6 maps E1B0C07F/m_clientOnly to+7A.
  BB1054F4 maps E95B313B to+188.1B71C90 returns the original graph node;
  these are not speculative offsets in a second compiled representation.
- Component ctor64C240 installs2611F68 and GameVM at+D0. BE9DE0 selects
  root+70 or+78 from the owner namespace high bits. +78=64C940 forwards
  argument5=1 (64C9B6) to GameVM+20=977F20, which calls1B95230 to set
  instance318 bit3. +70=64C9E0 forwards argument5=0 (64CA52).
- For the replicated/mirror namespace,8EAC30 tests that bit and skips this
  non-clientOnly state at8EAC57. The authoritative path calls8F9ED0. It resolves
  definition/position, creates through1830A50, binds through1B52630, then writes
  the actual entity identity through1BA4940. The server entity and its receiving
  namespace alias are distinct from an independently invented client cosmetic.
- The actual priority2 OnBegin consumes E8(definition),F8(position),100(scale),
  108(orientation) and188(output). It does not read authored118/120 values
  1883/12080 as Pet ownership. Scale/orientation are absent on this node.
-8EACF0 cleanup resolves and destroys only its cached entity, then invalidates
  the cache. It does not clear the graph output variable.

Implementation and storage

ShadowActor is selected only for non-strict Reaper00F2/74. The actual selector
confirmation and existing authored cast scope begin it; no second teleport or
mover is added. It uses Combat.allocate_combat_entity, world.create1F0E with
transform/scale, and the existing reliable EntityUpdate create/destroy path.
The record has no health, filter/team, Pet, model, collision, TTL or damage-target
overlay. The original definition supplies its client components. Position985 is
retained independently of the caster, and Match/BodyWorld lookup recognizes only
the live cached identity. Original1883/12080 are not repurposed.

The state itself is the explicit actor lease/registry: actor_entity and actor_point
are cold-initialized and belong to the source instance/state/counter lifecycle.
Normal cleanup and source BodyScript.retire release that cache idempotently.
The source retirement hook is required because legacy BodyScript.retire does not
run every State destructor before detaching its world. Output12105 deliberately
retains the now-unresolvable old entity after cleanup.

The exact ShadowActor type and both fields are registered in the strict body VM
layout. The full-server exporter reuses that same state registration; there is
no general restore or unknown-class escape. The existing allocator, entity
create-generation and actual create-ACK data remain causal server storage.
Actor point mutation changes both body-VM and full-server snapshot digests.

Delivery

The existing create-generation ACK is required per viewer before body chunks
referencing a live Shadow actor are sent. This is scoped in record_ready to this
actor's source body; movement and unrelated heroes retain their existing paths.
A queued create or a prior-generation ACK is not sufficient. Existing late-body
bootstrap emits the same actor identity, then waits for that generation's actual
create ACK before the current remote script snapshot. Entity destroy uses the
existing generation retirement to discard older pending creates.

Validation and boundary

One actual encrypted selector-confirm lifecycle drops the observer's first actor
create datagram, independently decodes actual create definition1F0E/position and
empty overlays, then checks create-before-reference on both owner and observer.
It also uses the existing late-bootstrap path, normal cast cleanup, unresolved
post-cleanup lookup, retained output, bodyVM/full-server export and causal point
mutation. The additional actual command confirmation checks source script
retirement and idempotent cleanup. Both focused methods passed together62.027s
on the private child based on formal d12e43d7, preserving its newer controlled-
mech possession gate. The earlier891-context runs were61.461s and1.352s.
The late scenario resets
an existing receiver; it is not a new connection or UI acceptance matrix.

Native allocator ABI and namespace handle representation are supplied by the
server's existing IDs/registry rather than a live native server. Native rendered
actor effects, camera/muzzle/model feedback and team-color presentation remain
unobserved. This producer repair is not original-client visual acceptance.
No GUI, pending manual session, active backend, frozen source, teleport policy,
shot spread or driver shell projection is changed by this patch.
