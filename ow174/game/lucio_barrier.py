"""Reconstructed Sound Barrier delivery and temporary-pool decay for 104319.

073A owns charge consumption, windup, search radius and duration. 073B
supplies the tagged shield. The normal bundle omits that optional receiver;
its authored 750 capacity is the fallback for the same pool semantics.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000079
POOL = 0x04A0000000000191
CAPACITY = 750.0


def grounded_owner(world, cfg, instance):
    """Resolve only the exact 073A/32 grounded self-filter operand."""
    if instance is None or instance.graph.index != 0x073A or world.match is None:
        return NotImplemented
    node = instance.graph.node(32)
    operands = node.field("m_condition.m_configVars") or ()
    if not operands or cfg is not operands[0]:
        return NotImplemented
    owner = next((player for player in world.match.players if player.has_body and player.body == world.body), None)
    return owner is not None and owner.mover.air_ticks == 0 and not world.match.combat.is_dead(owner)


@dataclass
class Lease:
    target: object
    capacity: float
    duration: float
    last_tick: int
    effect: runtime.Instance | None


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.seen: dict[tuple[int, int], int] = {}
        self.leases: dict[int, Lease] = {}

    def retire(self, body):
        for key in tuple(self.seen):
            if key[0] == body:
                del self.seen[key]
        self.drop(body)

    def drop(self, body):
        lease = self.leases.pop(body, None)
        if lease is None:
            return
        vitals = self.fight.vitals.get(body)
        if vitals is not None:
            self.fight._sync_body_health(lease.target.owner, vitals)
        effect = lease.effect
        if effect is not None and not effect.stopped:
            effect.component.destroy(effect)

    def command(self, player):
        from ow174.game.combat import capsule_visible, segment_distance

        script = player.body_script
        if script is None or script.hero != HERO:
            return
        fight = self.fight
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index != 0x073A:
                continue
            windup = instance.states.get(14)
            if windup is not None and windup.active:
                # Poll only the authored landing transition; the selected
                # runtime does not schedule this general state's recheck.
                windup.try_complete()
                instance.component.run_now()
            apply, search = instance.states.get(0), instance.states.get(8)
            if apply is None or search is None or not apply.active:
                continue
            key = player.body, instance.id
            if self.seen.get(key) == apply.counter:
                continue
            self.seen[key] = apply.counter
            duration = expr.to_float(instance.context().var(graph.ENTITY, 1212))
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            if not math.isfinite(duration) or not math.isfinite(radius) or duration <= 0 or radius <= 0:
                continue
            origin = fight._eye(player)
            for target in fight._targets():
                ally = not fight.match.game_map.free_for_all and target.team == player.team
                if (target.owner is not player and not ally) or fight.vitals_of(target).dead:
                    continue
                receiver = getattr(target.owner, "body_script", None)
                if receiver is None:
                    continue
                a, b = target.segment()
                if segment_distance(origin, a, b) - target.radius > radius:
                    continue
                if target.owner is not player and not capsule_visible(
                    fight._collision(), origin, a, b, target.radius, walls=fight.walls,
                ):
                    continue
                self.grant(player.body, target, receiver.component, duration)

    def grant(self, source, target, component, duration):
        self.drop(target.entity)
        effect = None
        capacity = CAPACITY
        child = graph.graph(0x073B)
        if child is not None:
            effect = component.create(child, values={(graph.INSTANCE, 27): expr.Entity(source)})
            runtime.send_instance_message(effect, 0x024000000000000F, {0x2B: duration}, source)
            effect.update()
            state = effect.states.get(2)
            if state is None or not state.active:
                component.destroy(effect)
                return
            capacity = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
        if capacity <= 0 or not math.isfinite(capacity):
            if effect is not None:
                component.destroy(effect)
            return
        vitals = self.fight.vitals_of(target)
        vitals.grant_temporary_shields(POOL, capacity, capacity)
        self.leases[target.entity] = Lease(target, capacity, duration, self.fight.tick, effect)
        self.fight._sync_body_health(target.owner, vitals)

    def update(self, tick, dt):
        if not self.leases:
            return
        live = {target.entity for target in self.fight._targets()}
        for body, lease in tuple(self.leases.items()):
            vitals = self.fight.vitals.get(body)
            if body not in live or vitals is None or vitals.dead:
                self.drop(body)
                continue
            elapsed = max(0, tick - lease.last_tick) * dt
            lease.last_tick = tick
            vitals.decay_temporary_shields(POOL, lease.capacity * elapsed / lease.duration)
            pool = vitals.temporary_shields[POOL]
            if not pool.now:
                self.drop(body)
                continue
            if lease.effect is not None and not lease.effect.stopped:
                lease.effect.write((graph.INSTANCE, 199), pool.now)
                lease.effect.update()
            self.fight._sync_body_health(lease.target.owner, vitals)
