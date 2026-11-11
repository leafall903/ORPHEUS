"""Bounded Reinhardt0095 health bridge; Pet creation and world pose remain open.

The normal scalar follows native295F1CDE -> C1BBB0, ruleset254B/default1.
Until authoritative Pet entities exist, the exact0095/#38 health queries read
the owner's authored stored barrier health586. This is a data-only server
reconstruction, not an entity alias or a general health-query implementation.
"""

import hashlib
import json
import math
import os
from functools import cache
from pathlib import Path

from ow174.game import (
    brigitte_shield,
    orisa_barrier,
    sigma_barrier,
    symmetra_barrier,
    winston_barrier,
    zarya_barriers,
)
from ow174.game.content import REINHARDT
from ow174.game.script import expr, runtime

GRAPH = 0x95
HEALTH_SCALAR_KEY = 0x254B
GEOMETRY_SHA256 = "523e98ccdde7233bcf4565fe1640e2ca8dd099eea7c627dc67315be59543f9a9"
# Declared active-pose reconstruction: align the shield with current aim, using
# the authored hand bind Y and standing capsule0.7 + Pet forward0.2. The
# model's negative Z faces outward. Animated lateral/rotation changes are not
# recovered. The complete authored curved mesh is retained.
POSE_HEIGHT = 1.5414069890975952
POSE_FORWARD = 0.9


@cache
def _geometry(path):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
        raise ValueError("Reinhardt barrier geometry checksum mismatch")
    data = json.loads(raw)
    shape, = data["shapes"]
    return tuple(tuple(p) for p in shape["vertices"]), tuple(tuple(t[:3]) for t in shape["triangles"])


def instance(actor):
    script = getattr(actor, "body_script", None)
    if (getattr(getattr(actor, "hero", None), "guid", None) != REINHARDT
            or script is None or script.retired or script.component.owner_dead):
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)


def active(actor):
    inst = instance(actor)
    state = inst.states.get(17) if inst is not None else None
    return (inst if state is not None and state.active and not state.ending
            and expr.to_float(inst.vars[586].value()) > 0 else None)


def condition(world, state):
    """Proxy0095's #38 presence from authored Pet lifetime, without a fake ID.

    Re-evaluate once per VM frame. The actual false subgraph owns the2s wait
    and200/s chase; raising the shield aborts it, lowering starts a fresh wait.
    This bridge does not create or replicate the unresolved Pet entity.
    """
    if (world.hero != REINHARDT or state.instance.graph.index != GRAPH
            or state.node.pos != 18 or state.instance.component.entity != world.body):
        return NotImplemented
    state.polled = True
    state.ticks()
    pet = state.instance.states.get(17)
    return pet is not None and pet.active and not pet.ending


def clock_key(shield):
    if isinstance(shield, brigitte_shield.Shield):
        return (shield.instance.component.entity,)
    if isinstance(shield, zarya_barriers.Bubble):
        return (shield.entity,)
    if isinstance(shield, (winston_barrier.Bubble, orisa_barrier.Shield,
                           sigma_barrier.Shield, symmetra_barrier.Shield)):
        return (shield.body,)
    pet = shield.states[17]
    return shield.component.entity, pet.counter, pet.start


def is_live(shield):
    if isinstance(shield, zarya_barriers.Bubble):
        return zarya_barriers.Barriers.live_source(shield)
    if isinstance(shield, symmetra_barrier.Shield):
        return shield.health > 0
    if isinstance(shield, winston_barrier.Bubble):
        return not shield.retired and shield.health > 0
    if isinstance(shield, (brigitte_shield.Shield, orisa_barrier.Shield, sigma_barrier.Shield)):
        return shield.active()
    return shield.states[17].active


def damage(fight, inst, amount, shooter):
    """Barrier damage has no body hit/critical/Reaping/ultimate-charge feedback."""
    if isinstance(inst, winston_barrier.Bubble):
        fight.winston_bubbles.damage(inst, amount)
        return
    if isinstance(inst, brigitte_shield.Shield):
        fight.brigitte_shields.damage(inst, amount)
        return
    if isinstance(inst, symmetra_barrier.Shield):
        fight.symmetra_barriers.damage(inst, amount)
        return
    if isinstance(inst, sigma_barrier.Shield):
        fight.sigma_barriers.damage(inst, amount)
        return
    if isinstance(inst, orisa_barrier.Shield):
        fight.orisa_barriers.damage(inst, amount)
        return
    if isinstance(inst, zarya_barriers.Bubble):
        fight.zarya_bubbles.damage(inst, amount, shooter)
        return
    remaining = max(0.0, expr.to_float(inst.vars[586].value()) - amount)
    inst.write((expr.INSTANCE, 586), expr.f32(remaining))
    if remaining <= 0:
        # The actual authored break entry zeros health, sets5s and aborts Pet51.
        runtime.send_instance_message(inst, 0x0240000000000053, {}, inst.component.entity)
    inst.component.run_now()


def _surfaces(fight, shooter):
    from ow174.game.combat import aim

    service = fight.brigitte_shields
    for target in fight._enemies(shooter):
        shield = service.by_body.get(target.entity)
        if shield is not None and shield.active() and fight.now >= shield.broken_until:
            vertices, triangles, bottom, rear = service.geometry
            centre, basis = brigitte_shield.pose(
                target.owner.mover, bottom, rear, target.radius, target.height,
            )
            yield shield, centre, basis, vertices, triangles
        inst = active(target.owner)
        if inst is None:
            continue
        path = os.environ.get("ORPHEUS_REINHARDT_BARRIER_GEOMETRY")
        if not path:
            raise ValueError("Reinhardt shield requires ORPHEUS_REINHARDT_BARRIER_GEOMETRY")
        vertices, triangles = _geometry(path)
        mover = target.owner.mover
        forward = aim(mover.yaw, mover.pitch)
        yaw = mover.yaw * math.pi / 32768
        right = (-math.cos(yaw), 0.0, math.sin(yaw))
        up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
              -forward[1] * right[0])
        # right x forward is up in the game's left-handed aim convention.
        up = tuple(-v for v in up)
        centre = tuple(mover.position[k] + (POSE_HEIGHT if k == 1 else 0)
                       + forward[k] * POSE_FORWARD for k in range(3))
        yield inst, centre, (right, up, tuple(-v for v in forward)), vertices, triangles

    service = fight.orisa_barriers
    for actor in service.enemies(shooter):
        vertices, triangles, bottom = service.geometry
        x, y, z = actor.position
        yaw = actor.yaw * math.tau / 65536
        sine, cosine = math.sin(yaw), math.cos(yaw)
        basis = ((cosine, 0, -sine), (0, 1, 0), (sine, 0, cosine))
        yield actor, (x, y - bottom, z), basis, vertices, triangles

    for actor in fight.sigma_barriers.enemies(shooter):
        vertices, triangles = fight.sigma_barriers.geometry
        yield actor, actor.position, sigma_barrier.basis(actor), vertices, triangles

    for actor in fight.symmetra_barriers.enemies(shooter):
        vertices, triangles = fight.symmetra_barriers.geometry
        yield actor, actor.position, symmetra_barrier.basis(actor), vertices, triangles


def intercept(fight, shooter, origin, direction, reach):
    """Nearest enemy active shield in the reconstructed current pose."""
    best = min(
        ((distance, actor) for distance, actor, _ in
         fight.winston_bubbles.intercept(shooter, origin, direction, reach)),
        key=lambda hit: hit[0], default=None,
    )
    attached = fight.zarya_bubbles.intercept(shooter, origin, direction, reach)
    if attached is not None and (best is None or attached[0] < best[0]):
        best = attached
    for inst, centre, basis, vertices, triangles in _surfaces(fight, shooter):
        delta = tuple(origin[k] - centre[k] for k in range(3))
        local_o = tuple(sum(delta[k] * axis[k] for k in range(3)) for axis in basis)
        local_d = tuple(sum(direction[k] * axis[k] for k in range(3)) for axis in basis)
        for a, b, c in triangles:
            t = _triangle(local_o, local_d, vertices[a], vertices[b], vertices[c])
            if t is not None and t <= reach and (best is None or t < best[0]):
                best = (t, inst)
    return best


def blossom_targets(fight, shooter, origin, reach, *, obscured=None):
    """Acquire shields by nearest mesh surface and a reconstructed LOS sample."""
    from ow174.game.combat import ray_world
    from ow174.game.contacts import closest_on_triangle

    collision = fight._collision()

    def clear_ice(instance, direction, distance):
        if fight.walls.cast(origin, direction, distance) is None:
            return True
        if obscured is not None:
            obscured.append(instance)
        return False

    for bubble in fight.zarya_bubbles.enemies(shooter):
        delta = tuple(a - b for a, b in zip(bubble.centre(), origin, strict=True))
        size = math.sqrt(sum(value * value for value in delta))
        distance = max(0.0, size - zarya_barriers.RADIUS)
        if distance > reach:
            continue
        direction = tuple(value / size for value in delta) if size else (0.0, 0.0, 1.0)
        wall = ray_world(collision, origin, direction, distance)
        if (wall is None or wall >= distance - 1e-6) and clear_ice(bubble, direction, distance):
            yield bubble
    for actor in fight.winston_bubbles.actors.values():
        if not is_live(actor) or not fight.winston_bubbles.enemy(actor, shooter):
            continue
        delta = tuple(a - b for a, b in zip(actor.centre, origin, strict=True))
        size = math.sqrt(sum(value * value for value in delta))
        distance = abs(size - winston_barrier.RADIUS)
        if distance > reach:
            continue
        sign = 1 if size >= winston_barrier.RADIUS else -1
        direction = tuple(sign * value / size for value in delta) if size else (0.0, 0.0, 1.0)
        wall = ray_world(collision, origin, direction, distance)
        if (wall is None or wall >= distance - 1e-6) and clear_ice(actor, direction, distance):
            yield actor
    for inst, centre, basis, vertices, triangles in _surfaces(fight, shooter):
        delta = tuple(origin[k] - centre[k] for k in range(3))
        local = tuple(sum(delta[k] * axis[k] for k in range(3)) for axis in basis)
        nearest = min(
            (closest_on_triangle(vertices[a], vertices[b], vertices[c], local)
             for a, b, c in triangles),
            key=lambda point: math.dist(point, local),
        )
        distance = math.dist(nearest, local)
        if distance > reach:
            continue
        if distance > 0:
            local_delta = tuple(nearest[j] - local[j] for j in range(3))
            direction = tuple(sum(local_delta[j] * basis[j][k] for j in range(3)) / distance
                              for k in range(3))
            wall = ray_world(collision, origin, direction, distance)
            if ((wall is not None and wall < distance - 1e-6)
                    or not clear_ice(inst, direction, distance)):
                continue
        yield inst


def _triangle(origin, direction, a, b, c):
    def dot(x, y):
        return sum(v * w for v, w in zip(x, y, strict=True))

    def cross(x, y):
        return (x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0])

    e1, e2 = tuple(y-x for x, y in zip(a, b, strict=True)), tuple(y-x for x, y in zip(a, c, strict=True))
    p = cross(direction, e2)
    det = dot(e1, p)
    if abs(det) < 1e-9:
        return None
    s = tuple(x-y for x, y in zip(origin, a, strict=True))
    u = dot(s, p) / det
    if not 0 <= u <= 1:
        return None
    q = cross(s, e1)
    v = dot(direction, q) / det
    if v < 0 or u + v > 1:
        return None
    t = dot(e2, q) / det
    return t if t >= 0 else None


def native(world, cfg, instance):
    if (world.hero != REINHARDT or instance is None or instance.graph.index != GRAPH
            or instance.component.entity != world.body):
        return NotImplemented
    if cfg.get("$") == "STU_295F1CDE":
        value = world.ruleset(HEALTH_SCALAR_KEY)
        return expr.f32(1.0 if value is None else expr.to_float(value))
    if (cfg.get("$") == "STU_783B4CF0"
            and cfg.get("m_entity") == {"$": "STUConfigVarDynamic", "m_identifier": 38,
                                       "m_60DB8F99": 0, "m_EE729DCB": 0}
            and cfg.get("m_7E8B666C") == {"$": "STU_297E1A30", "m_7E8B666C": 0,
                                        "m_EE729DCB": 0}
            and cfg.get("m_7BE02F89") is None):
        # Evaluate the actual dynamic so dependency tracking observes health writes.
        return instance.evaluate({"$": "STUConfigVarDynamic", "m_identifier": 586,
                                  "m_60DB8F99": 0, "m_EE729DCB": 0})
    return NotImplemented
