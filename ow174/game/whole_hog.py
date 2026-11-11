"""104319 Whole Hog force. Ring interpolation and mover delivery are reconstructed."""

import math

from ow174.game import breather
from ow174.game.script import expr

VOLLEY = (0x01C6, 33)
ULTIMATE = (0x01EF, 1)


def spec(state):
    modify = state.node.field("m_6396149F")["m_modifyHealth"]

    def value(cfg):
        return expr.to_float(state.evaluate(cfg, watch=False))

    values = tuple(value(modify[k]) for k in ("m_E33EFEE1", "m_10399AE7", "m_583C208B", "m_8394CF8C"))
    rings = tuple(sorted(tuple(value(row[k]) for k in ("m_radius", "m_9102DA3F", "m_579D7CFF", "m_10399AE7"))
                         for row in modify["m_knockbackRings"]))
    return values, rings


def ring_scale(rings, distance):
    previous = rings[0]
    if distance <= previous[0]:
        return previous[1:]
    for ring in rings[1:]:
        if distance <= ring[0]:
            share = (distance - previous[0]) / (ring[0] - previous[0])
            return tuple(a + (b - a) * share for a, b in zip(previous[1:], ring[1:], strict=True))
        previous = ring
    return 0.0, 0.0, 0.0


def push(fight, target, direction, distance, force):
    mover = getattr(target.owner, "mover", None)
    if (mover is None or fight.vitals_of(target).dead or fight._fortify_state(target.owner) is not None
            or fight.hooks.controlled(target.entity) or target.entity in fight.pins.values()):
        return
    (horizontal, cap, lift, lift_cap), rings = force
    flat_scale, lift_scale, cap_scale = ring_scale(rings, distance)
    dx, _, dz = direction
    size = math.hypot(dx, dz)
    if size <= 1e-9 or not (flat_scale or lift_scale):
        return
    x, y, z = mover.velocity
    speed = horizontal * flat_scale / size
    nx, nz = x + dx * speed, z + dz * speed
    magnitude = math.hypot(nx, nz)
    limit = cap * cap_scale
    if magnitude > limit:
        nx, nz = nx * limit / magnitude, nz * limit / magnitude
    ny = min(lift_cap * cap_scale, y + lift * lift_scale)
    mover.add_external_impulse((nx - x, ny - y, nz - z))


def interrupt(owner):
    script = getattr(owner, "body_script", None)
    if getattr(script, "hero", None) != breather.HERO:
        return
    for ability in script.ultimates:
        if (ability.instance.graph.index, ability.node.pos) == ULTIMATE:
            ability.presses.clear()
            ability.stop()

