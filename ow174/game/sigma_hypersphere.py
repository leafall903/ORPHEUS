"""Authored 104319 Sigma Hypersphere pair with reconstructed world ricochet."""

import math
from dataclasses import dataclass

from ow174.game import contacts
from ow174.game.collision import MOVER
from ow174.game.script import expr


@dataclass
class Motion:
    body_radius: float
    wall_radius: float
    bounces: int
    normal_scale: float
    tangent_scale: float


def wall_contact(collision, origin, direction, distance, radius):
    if collision is None:
        return None, None
    destination = tuple(p + distance * d for p, d in zip(origin, direction, strict=True))
    hit = contacts.scene_sweep(collision.engine, origin, destination, ((0., 0., 0.),), radius, MOVER)
    if hit is None or sum(d * n for d, n in zip(direction, hit[2], strict=True)) >= 0:
        return None, None
    return hit[0] * distance, hit[2]


def bounce(rocket, point, normal, elapsed):
    sphere = rocket.sphere
    velocity = tuple(rocket.speed * axis for axis in rocket.direction)
    projection = sum(axis * n for axis, n in zip(velocity, normal, strict=True))
    reflected = tuple(expr.f32((axis - projection * n) * sphere.tangent_scale
                                - projection * n * sphere.normal_scale)
                      for axis, n in zip(velocity, normal, strict=True))
    rocket.speed = math.hypot(*reflected)
    if rocket.speed:
        rocket.direction = tuple(axis / rocket.speed for axis in reflected)
    sphere.bounces -= 1
    rocket.position = tuple(p + .001 * n for p, n in zip(point, normal, strict=True))
    rocket.left -= elapsed


def hypersphere_motion(state):
    """Read both stock Hypersphere launch rows for the scoped sphere host."""
    node = state.node
    if state.instance.graph.index != 0x187F or node.pos not in (45, 56):
        return None
    bounce = node.field("m_E365DB20") or {}
    motions = node.field("m_projectileMotions") or ()
    if (bounce.get("$") != "STU_3FE1A93B" or len(motions) != 1
            or motions[0].get("$") != "STU_E1A5B579"):
        return None

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))

    hit = node.field("m_6396149F")
    return (value(motions[0], "m_599AB8D7"),
            value(hit, "m_57DAE1B5"), value(hit, "m_1FFD8312"),
            expr.to_int(state.evaluate(bounce.get("m_360294F0"), watch=False)),
            value(bounce, "m_0D20DAFD"), value(bounce, "m_C29D35E8"))
