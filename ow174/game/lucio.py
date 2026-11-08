"""Lucio's reconstructed aura host using the authored 00A4.053/0267 graph.

Target acquisition and world-time integration are server reconstructions.
The status graph selects heal/speed, self/ally rates and Amp branches. This
does not claim native status allocation/replication or complete speed support.
"""

from dataclasses import dataclass

from ow174.game import stats
from ow174.game.script import expr, graph

HERO = 0x02E0000000000079
CROSSFADE = 0x04A00000000000A4
SOUND_BARRIER = 0x04A0000000000191
BARRIER_KEY = "lucio_sound_barrier"


def barrier_owner_filter(world, cfg, instance):
    """Narrow reconstructed slam gate; not a generic native filter decoder."""
    if instance is None or instance.graph.index != 0x073A or world.match is None:
        return NotImplemented
    target = cfg.get("m_entity") or {}
    nonzero = {key: value for key, value in (cfg.get("m_filter") or {}).items() if value not in (None, 0)}
    if (target.get("$") != "STU_B5A0CAF0"
            or nonzero != {"$": "STUConfigVarFilter", "m_84065FD1": 0x3FFFFFFF, "m_4EA686DB": 16}):
        return NotImplemented
    if graph.status_definition(SOUND_BARRIER) is NotImplemented:
        return False
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    return owner is not None and owner.mover.air_ticks == 0 and not world.match.combat.is_dead(owner)


@dataclass
class Lease:
    source: object
    target: object
    instance: object
    created_tick: int


@dataclass
class BarrierLease:
    source: int
    target: object
    instance: object
    capacity: float
    duration: float
    last_tick: int


class Auras:
    def __init__(self, combat):
        self.combat = combat
        self.leases: dict[tuple[int, int], Lease] = {}
        self.last_tick: int | None = None

    def retire(self, body):
        for key in list(self.leases):
            if body in key:
                self._drop(key)

    def _drop(self, key):
        lease = self.leases.pop(key)
        lease.instance.component.destroy(lease.instance)

    def refresh(self):
        from ow174.game.combat import EPSILON, _dot, _sub, ray_world, segment_distance

        definition = graph.status_definition(CROSSFADE)
        if definition is NotImplemented:
            return  # Legacy runtime has no status resources; do not invent them.
        child = graph.guid(definition.get("m_5A4051BC"))
        child = graph.graph(child & 0xFFFF) if child is not None else None
        if child is None or child.index != 0x0267:
            raise ValueError("Crossfade requires the actual 0267 status graph")
        fight, wanted = self.combat, set()
        targets = fight._targets()
        for source in targets:
            owner = source.owner
            hero = getattr(owner, "hero", None)
            if getattr(hero, "guid", None) != HERO or fight.vitals_of(source).dead:
                continue
            script = owner.body_script
            if script is None or script.retired:
                continue
            searches = [instance.states.get(3) for instance in script.component.instances.values()
                        if instance.graph.index == 0x0266]
            search = next((state for state in searches if state is not None and state.active), None)
            if search is None:
                continue
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            if radius <= 0:
                continue
            origin = fight._eye(owner)
            for target in targets:
                ally = not fight.match.game_map.free_for_all and source.team == target.team
                if (target.owner is not owner and not ally) or fight.vitals_of(target).dead:
                    continue
                target_script = getattr(target.owner, "body_script", None)
                if target_script is None:
                    continue
                a, b = target.segment()
                if segment_distance(origin, a, b) - target.radius > radius:
                    continue
                centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                offset = _sub(centre, origin)
                length = _dot(offset, offset) ** 0.5
                if target.owner is not owner and length > EPSILON:
                    direction = tuple(value / length for value in offset)
                    wall = ray_world(fight._collision(), origin, direction, length)
                    if wall is not None and wall < length:
                        continue
                key = (source.entity, target.entity)
                wanted.add(key)
                lease = self.leases.get(key)
                component = target_script.component
                if lease is not None and lease.instance.component is not component:
                    self._drop(key)
                    lease = None
                if lease is None:
                    instance = component.create(
                        child, values={(graph.INSTANCE, 27): expr.Entity(source.entity)},
                    )
                    lease = self.leases[key] = Lease(owner, target, instance, fight.tick)
                # Drain source-member notifications at the target's current
                # script clock; never rewind or advance its command simulation.
                lease.instance.update()
        for key in list(self.leases):
            if key not in wanted:
                self._drop(key)

    def update(self, tick):
        from ow174.game.combat import TICK, hit_def

        if self.last_tick is not None and tick <= self.last_tick:
            return
        self.refresh()
        previous = tick - 1 if self.last_tick is None else self.last_tick
        self.last_tick = tick
        for lease in list(self.leases.values()):
            elapsed = max(0, tick - max(previous, lease.created_tick)) * TICK
            if elapsed <= 0:
                continue
            for state in lease.instance.states.values():
                if not state.active or state.node.cls != "STUStatescriptStateModifyHealth":
                    continue
                # 0267's two continuous operations are healing. Do not infer
                # damage/status tags for arbitrary ModifyHealth graphs here.
                modify = state.node.field("m_modifyHealth") or {}
                if state.node.field("m_7B2B3F5C") != 1 or modify.get("m_93CDFD2F") != 0:
                    continue
                hit = hit_def(state, {"m_modifyHealth": modify})
                if hit is None or hit.amount <= 0:
                    continue
                vitals = self.combat.vitals_of(lease.target)
                if vitals.dead:
                    continue
                given = vitals.heal(hit.amount * elapsed)
                if given > 0:
                    lease.source.stats.add(stats.HEALING, given)
                    self.combat._sync_body_health(lease.target.owner, vitals)


class Barriers:
    """One-shot team shielding from authored slam and073B pool initialization.

    Reconstruct a linear capacity/duration decay and immediate radial delivery.
    Native propagation, time-scaling295F1CDE and health-pool flags need further
    comparison. Applied shields persist if the caster subsequently dies.
    """

    def __init__(self, combat):
        self.combat = combat
        self.seen = {}
        self.leases: dict[int, BarrierLease] = {}

    def retire(self, entity):
        for key in list(self.seen):
            if key[0] == entity:
                del self.seen[key]
        if entity in self.leases:
            self._drop(entity)

    def _drop(self, entity):
        lease = self.leases.pop(entity)
        vitals = self.combat.vitals.get(entity)
        if vitals is not None:
            vitals.remove_temporary_shields(BARRIER_KEY)
            self.combat._sync_body_health(lease.target.owner, vitals)
        lease.instance.component.destroy(lease.instance)

    def command(self, player):
        from ow174.game.combat import EPSILON, _dot, _sub, ray_world, segment_distance
        from ow174.game.script import runtime

        if player.hero.guid != HERO:
            return
        script = player.body_script
        if script is None or script.retired or self.combat.is_dead(player):
            return
        for instance in list(script.component.instances.values()):
            if instance.graph.index != 0x073A:
                continue
            apply, search = instance.states.get(0), instance.states.get(8)
            if apply is None or search is None:
                continue
            key = (player.body, instance.id)
            previous = self.seen.get(key, 0)
            self.seen[key] = apply.counter
            if previous == apply.counter or not apply.active:
                continue
            definition = graph.status_definition(SOUND_BARRIER)
            child_guid = graph.guid(definition.get("m_5A4051BC")) if isinstance(definition, dict) else None
            child = graph.graph(child_guid & 0xFFFF) if child_guid is not None else None
            if child is None or child.index != 0x073B:
                raise ValueError("Sound Barrier requires actual073B status resources")
            duration = expr.to_float(instance.context().var(graph.ENTITY, 1212))
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            if duration <= 0 or radius <= 0:
                continue
            origin = self.combat._eye(player)
            for target in self.combat._targets():
                ally = not self.combat.match.game_map.free_for_all and target.team == player.team
                if (target.owner is not player and not ally) or self.combat.vitals_of(target).dead:
                    continue
                receiver = getattr(target.owner, "body_script", None)
                if receiver is None:
                    continue
                a, b = target.segment()
                if segment_distance(origin, a, b) - target.radius > radius:
                    continue
                centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                offset = _sub(centre, origin)
                length = _dot(offset, offset) ** 0.5
                if target.owner is not player and length > EPSILON:
                    direction = tuple(value / length for value in offset)
                    wall = ray_world(self.combat._collision(), origin, direction, length)
                    if wall is not None and wall < length:
                        continue
                if target.entity in self.leases:
                    self._drop(target.entity)
                effect = receiver.component.create(
                    child, values={(graph.INSTANCE, 27): expr.Entity(player.body)},
                )
                runtime.send_instance_message(effect, 0x024000000000000F, {0x2B: duration}, player.body)
                effect.update()
                state = effect.states.get(2)
                capacity = (
                    expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False)) if state else 0
                )
                if state is None or not state.active or capacity <= 0:
                    receiver.component.destroy(effect)
                    continue
                vitals = self.combat.vitals_of(target)
                vitals.grant_temporary_shields(BARRIER_KEY, capacity)
                self.leases[target.entity] = BarrierLease(
                    player.body, target, effect, capacity, duration, self.combat.tick,
                )
                self.combat._sync_body_health(target.owner, vitals)

    def update(self, tick):
        from ow174.game.combat import TICK

        live = {target.entity for target in self.combat._targets()}
        for entity, lease in list(self.leases.items()):
            vitals = self.combat.vitals.get(entity)
            if entity not in live or vitals is None or vitals.dead:
                self._drop(entity)
                continue
            elapsed = max(0, tick - lease.last_tick) * TICK
            lease.last_tick = max(tick, lease.last_tick)
            vitals.drain_temporary_shields(BARRIER_KEY, lease.capacity * elapsed / lease.duration)
            pool = vitals.temporary_shields.get(BARRIER_KEY)
            if pool is None:
                self._drop(entity)
                continue
            lease.instance.write((graph.INSTANCE, 199), pool.current)
            lease.instance.update()
            self.combat._sync_body_health(lease.target.owner, vitals)
