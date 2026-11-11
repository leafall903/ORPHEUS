"""Selected 1902 deployment and 1909 weapon amplification.

Static placement and segment crossing of the authenticated 92E3 mesh bounds
are reconstructed. The native preview and modifier hosts remain separate.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph

HERO = 0x02E0000000000221
GRAPH = 0x1902
DEFINITION = 0x0400000000002B50
HALF_WIDTH = 4.4999995
BOTTOM, TOP = -1.1999999, 3.7999997


@dataclass
class Window:
    owner: object
    team: int
    position: tuple
    forward: tuple
    created: int
    expires: int


class Matrices:
    def __init__(self, fight):
        self.fight = fight
        self.windows = {}

    def owner(self, instance):
        if instance.graph.index != GRAPH or instance.component.world.hero != HERO:
            return None
        return next((p for p in self.fight.match.players
                     if p.has_body and p.body == instance.component.entity and not p.gone), None)

    def native(self, instance, cfg):
        owner = self.owner(instance)
        if owner is None:
            return NotImplemented
        cls = cfg.get("$")
        if cls in ("STU_66F3B89E", "STU_021D01CD"):
            if instance.evaluate(cfg.get("m_entity")) != expr.Entity(owner.body):
                return NotImplemented
            angle = owner.mover.yaw * math.tau / 65536
            if cls == "STU_021D01CD":
                angle += math.pi / 2
            return expr.Vec3(math.sin(angle), 0, math.cos(angle))
        if cls == "STU_C5148EFE" and instance.evaluate(cfg.get("m_entity")) == expr.Entity(0):
            point = instance.evaluate(cfg.get("m_BD631144"))
            if isinstance(point, expr.Vec3):
                return point
        return NotImplemented

    def confirm(self, state):
        if state.index != 9:
            return NotImplemented
        owner = self.owner(state.instance)
        if owner is None:
            return NotImplemented
        from ow174.game.combat import aim

        if self.fight.is_dead(owner):
            return False
        reach = expr.to_float(state.evaluate(state.node.field("m_6386E7B4"), watch=False))
        slope = expr.to_float(state.evaluate(state.node.field("m_2ADABA5F"), watch=False))
        hit = self.fight._collision().cast_ray(
            self.fight._eye(owner), aim(owner.mover.yaw, owner.mover.pitch), reach,
        ) if reach > 0 else None
        valid = hit is not None and hit.normal[1] >= math.cos(slope)
        values = {"m_E21062B4": valid}
        if valid:
            values.update(m_30710B76=expr.Vec3(*hit.point), m_2339572B=expr.Vec3(*hit.normal),
                          m_1549F0F9=expr.Entity(0))
        for name, value in values.items():
            key = expr.lvalue(state.node.field(name))
            if key is not None:
                state.instance.write(key, value)
        return valid

    def spawn(self, instance, node):
        if node.pos != 54:
            return NotImplemented
        owner = self.owner(instance)
        if owner is None:
            return NotImplemented
        from ow174.game.combat import TICK

        definition = instance.evaluate(node.field("m_entityDefinition"))
        point = instance.evaluate(node.field("m_position"))
        direction = instance.evaluate(node.field("m_AD8E614B"))
        seconds = expr.to_float(instance.find_var(expr.INSTANCE, 567).value())
        if (not isinstance(definition, expr.Asset) or definition.guid != DEFINITION
                or not isinstance(point, expr.Vec3) or not isinstance(direction, expr.Vec3)
                or not all(math.isfinite(v) for v in (*point, *direction, seconds)) or seconds <= 0):
            return NotImplemented
        length = math.hypot(direction.x, direction.z)
        if not length:
            return NotImplemented
        entity = self.fight.next_bot
        self.fight.next_bot += 1
        tick = self.fight.tick
        self.windows[entity] = Window(owner, owner.team, tuple(point),
                                      (direction.x / length, direction.z / length),
                                      tick, tick + math.ceil(seconds / TICK))
        key = expr.lvalue(node.field("m_B3019338"))
        if key is not None:
            instance.write(key, expr.Entity(entity))
        return True

    def multiplier(self, source, start, end, time_span=None):
        for window in self.windows.values():
            if window.owner.gone or window.owner.hero.guid != HERO:
                continue
            if self.fight.match.game_map.free_for_all:
                if source is not window.owner:
                    continue
            elif source.team != window.team:
                continue
            x, y, z = window.position
            nx, nz = window.forward
            a = (start[0] - x) * nx + (start[2] - z) * nz
            b = (end[0] - x) * nx + (end[2] - z) * nz
            if abs(b - a) <= 1e-9:
                continue
            share = -a / (b - a)
            if not 0 < share <= 1:
                continue
            at = self.fight.tick
            if time_span is not None:
                first, last = time_span
                at = first + share * (last - first)
            if not window.created <= at < window.expires:
                continue
            side = (start[0] + share * (end[0] - start[0]) - x) * nz
            side -= (start[2] + share * (end[2] - start[2]) - z) * nx
            height = start[1] + share * (end[1] - start[1]) - y
            if abs(side) <= HALF_WIDTH and BOTTOM <= height <= TOP:
                modifier = graph.graph(0x1909)
                if modifier is not None:
                    amount = modifier.node(12).field("m_50530EAB")
                    return 1 + expr.to_float(expr.evaluate(amount, expr.Context()))
        return 1.0

    def update(self, tick):
        for entity, window in tuple(self.windows.items()):
            if tick >= window.expires or window.owner.gone or window.owner.hero.guid != HERO:
                del self.windows[entity]
