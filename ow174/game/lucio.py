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


@dataclass
class Lease:
    source: object
    target: object
    instance: object
    created_tick: int


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
