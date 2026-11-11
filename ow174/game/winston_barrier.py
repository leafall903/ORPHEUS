"""Server collision for Winston's authored Barrier Projector deployment."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph

RADIUS = 5.0


@dataclass
class Dome:
    owner: int
    team: int
    centre: tuple[float, float, float]
    health: float
    until: float


def deploy(combat, body, instance, node):
    if instance.evaluate(node.field("m_script")) != expr.Asset(0x05800000000000FC):
        return
    owner = next((p for p in combat.match.players if p.body == body and not p.gone and p.has_body), None)
    if owner is None or combat.is_dead(owner):
        return
    params = {graph.guid(entry["m_B5051BCE"]): expr.to_float(instance.evaluate(entry["m_value"]))
              for entry in node.field("m_params") or ()}
    health, duration = params.get(0x0A40000000000044, 0), params.get(0x0A40000000000494, 0)
    if not all(math.isfinite(value) and value > 0 for value in (health, duration)):
        return
    x, y, z = owner.mover.position
    combat.winston_domes[body] = Dome(owner.entity, owner.team, (x, y + 0.1, z),
                                      health, combat.now + duration)


def intersection(combat, shooter, origin, direction, length, *, radius=0.0):
    nearest = None
    for dome in combat.winston_domes.values():
        if (dome.health <= 0 or combat.now >= dome.until or dome.owner == shooter.entity
                or (not combat.match.game_map.free_for_all and dome.team == shooter.team)):
            continue
        offset = tuple(a - b for a, b in zip(origin, dome.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        distance2 = sum(v * v for v in offset)
        inside = distance2 < RADIUS * RADIUS
        shell = max(0.0, RADIUS - radius) if inside else RADIUS + radius
        discriminant = projection * projection - distance2 + shell * shell
        if discriminant < 0:
            continue
        root = math.sqrt(discriminant)
        hit = max(0.0, -projection + root) if inside else -projection - root
        if 0 <= hit <= length and (nearest is None or hit < nearest[0]):
            nearest = hit, dome
    return nearest
