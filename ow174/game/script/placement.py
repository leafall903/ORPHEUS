"""Reconstructed static Call Mech placement; native preview/filter semantics remain open."""

import math
from dataclasses import dataclass

from ow174.game import contacts
from ow174.game.collision import MOVER, NOT_GROUND
from ow174.game.script import expr


@dataclass(frozen=True)
class Result:
    point: expr.Vec3 | None  # None is a completed, rejected placement.


def _vector(value):
    try:
        return type(value) is expr.Vec3 and all(type(axis) in (int, float) and math.isfinite(axis)
                                              for axis in value)
    except OverflowError:
        return False


def place(match, state):
    """Ground ray, slope, static capsule clearance and upright body overlap policy.

    This supports the actual026A/node38 primary downward cast. It does not
    interpret opaque .097 profiles or synthesize the client's preview outputs.
    """
    if state.instance.graph.index != 0x26A or state.node.pos != 38:
        return NotImplemented
    if state.node.field("m_D14637AE") is not None or match.collision_world is None:
        return NotImplemented
    cast = state.node.field("m_75198C98")
    if not isinstance(cast, dict) or cast.get("$") != "STU_70C754CD":
        return NotImplemented
    origin = state.evaluate(cast.get("m_11EFE48E"), watch=False)
    direction = state.evaluate(cast.get("m_35961153"), watch=False)
    length = state.evaluate(cast.get("m_castLength"), watch=False)
    radius = state.evaluate(state.node.field("m_BA42208A"), watch=False)
    height = state.evaluate(state.node.field("m_C72356CD"), watch=False)
    slope = state.evaluate(state.node.field("m_5EF7CDF0"), watch=False)
    if (not _vector(origin) or direction != expr.Vec3(0, -1, 0)
            or any(type(x) not in (int, float) or not math.isfinite(x)
                   for x in (length, radius, height, slope))
            or length <= 0 or radius <= 0 or height < 2*radius or not 0 <= slope < 90):
        return NotImplemented
    world = match.collision_world
    hit = world.cast_ray(tuple(origin), tuple(direction), length, MOVER)
    if hit is None or hit.surface & NOT_GROUND or hit.normal[1] < math.cos(math.radians(slope)):
        return Result(None)
    body = contacts.CharacterBody(radius, height-radius, world=world.engine, category=MOVER)
    body.teleport(hit.point)
    if not body.fits(radius, height-radius):
        return Result(None)
    ignores = {state.instance.component.entity}
    for cfg in state.node.field("m_ignoreEntities") or ():
        identifier = expr.to_entity(state.evaluate(cfg, watch=False))
        if expr.valid_entity(identifier):
            ignores.add(identifier)
    x, y, z = hit.point
    low, high = y+radius, y+height-radius
    for target in match.combat._targets():
        if target.entity in ignores or match.combat.vitals_of(target).dead:
            continue
        a, b = target.segment()
        dy = max(a[1]-high, low-b[1], 0)
        distance2 = (target.feet[0]-x)**2 + (target.feet[2]-z)**2 + dy*dy
        if distance2 < (radius+target.radius)**2:
            return Result(None)
    return Result(expr.Vec3(*(expr.f32(axis) for axis in hit.point)))
