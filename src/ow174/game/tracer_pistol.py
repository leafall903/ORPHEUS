"""104319 Tracer bloom, with reconstructed deterministic server cone sampling.

D80A50/D5FF60/D5F920 supply count-based normalization and linked-output timing.
Native random-table indexing and animated dual-muzzle transforms remain open.
"""

import math
import random

from ow174.game.script import expr

HERO = 0x02E0000000000003
MAX_RETAINED = 256


def active(state):
    return (state.instance.component.world.hero == HERO and state.instance.graph.index == 0x0016
            and state.node.pos == 12)


def normalized(state, count):
    launch = state.node.field("m_5EC1EB38") or {}
    denominator = getattr(state, "spread_ramp", None)
    if denominator is None:
        denominator = expr.to_float(state.evaluate(launch.get("m_AC20B1C1"), watch=False))
    if denominator < expr.f32(1e-7):
        return 1.0
    increment = expr.f32(expr.f32(max(0, count)) / denominator)
    return min(1.0, expr.f32(state.spread0 + increment))


def angle(state, count):
    launch = state.node.field("m_5EC1EB38") or {}
    low = expr.to_float(state.evaluate(launch.get("m_7F0FED02"), watch=False))
    high = expr.to_float(state.evaluate(launch.get("m_429A6C85"), watch=False))
    degrees = expr.f32(low + expr.f32(expr.f32(high - low) * normalized(state, count)))
    return max(0.0, expr.f32(degrees * expr.f32(math.pi / 180)))


def remember(state, number, time, activation):
    if not active(state):
        return
    values = getattr(state, "shot_spread", None)
    if values is None:
        values = state.shot_spread = {}
    # Capture the activation's bloom before a later activation changes spread0.
    values[(*activation, number, time)] = angle(state, number)
    while len(values) > MAX_RETAINED:
        del values[next(iter(values))]


def rays(state, number, time, activation, direction, count):
    if not active(state):
        return (direction,) * count
    key = (*(activation or (state.counter, state.start)), number, time)
    retained = getattr(state, "shot_spread", {})
    radius = retained[key] if key in retained else angle(state, number)
    if radius == 0:
        return (direction,) * count
    dx, dy, dz = direction
    horizontal = math.hypot(dx, dz)
    right = (dz / horizontal, 0.0, -dx / horizontal) if horizontal > 1e-7 else (1.0, 0.0, 0.0)
    rx, ry, rz = right
    up = (dy*rz - dz*ry, dz*rx - dx*rz, dx*ry - dy*rx)
    result = []
    for bullet in range(count):
        # Reconstructed host RNG, independent of other graph random queries.
        # The native sampler uses an indexed table; matching its seed is not claimed.
        seed = f"{state.instance.component.entity}:{state.instance.id}:{state.index}:{key}:{bullet}"
        rng = random.Random(seed)
        azimuth, radial = rng.random() * math.tau, rng.random() * radius
        cosine, sine = math.cos(radial), math.sin(radial)
        around = tuple(r*math.cos(azimuth) + u*math.sin(azimuth) for r, u in zip(right, up, strict=True))
        result.append(tuple(d*cosine + a*sine for d, a in zip(direction, around, strict=True)))
    return tuple(result)
