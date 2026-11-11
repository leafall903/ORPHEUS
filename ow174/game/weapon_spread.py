"""Captured cone and ring geometry for scoped weapon launches."""

import math
from functools import cache

from ow174.game.script.expr import f32, to_float

MCCREE = 0x02E0000000000042


@cache
def uniform_table() -> tuple[int, ...]:
    """Generate the captured 184BE10/184BF30 table, using seed 1 and reversed batches."""
    words = [1]
    for i in range(1, 624):
        last = words[-1]
        words.append((1812433253 * (last ^ (last >> 30)) + i) & 0xffffffff)
    parity = (1, 0, 0, 0x13c9e684)
    inner = 0
    for word, mask in zip(words, parity):
        inner ^= word & mask
    for shift in (16, 8, 4, 2, 1):
        inner ^= inner >> shift
    if not inner & 1:
        words[0] ^= 1
    state = [sum(words[i + lane] << (32 * lane) for lane in range(4))
             for i in range(0, 624, 4)]
    masks = (0xdfffffef, 0xddfecb7f, 0xbffaffff, 0xbffffff6)
    full = (1 << 128) - 1
    values = []
    while len(values) < 16384:
        previous, last = state[-2:]
        for i in range(156):
            current, ahead = state[i], state[(i + 122) % 156]
            right = sum(((((ahead >> (lane * 32)) & 0xffffffff) >> 11) & mask)
                        << (lane * 32) for lane, mask in enumerate(masks))
            left = sum(((((last >> (lane * 32)) & 0xffffffff) << 18) & 0xffffffff)
                       << (lane * 32) for lane in range(4))
            result = current ^ ((current << 8) & full) ^ (previous >> 8) ^ right ^ left
            state[i] = result
            previous, last = last, result
        batch = [(word >> (lane * 32)) & 0xffffffff for word in state for lane in range(4)]
        values.extend(reversed(batch))
    return tuple(values[:16384])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _rotate(vector, axis, angle):
    cos, sin = math.cos(angle), math.sin(angle)
    dot = sum(a * b for a, b in zip(axis, vector, strict=True))
    perpendicular = _cross(axis, vector)
    return tuple(vector[i] * cos + perpendicular[i] * sin + axis[i] * dot * (1 - cos)
                 for i in range(3))


def _seed_index(base, shot, pellet):
    return uniform_table()[((base & 0xffff) * 131 + shot * 23 + pellet * 3) & 16383] & 16383


def seed_base(owner, instance, state, activation):
    """D5EB70 arithmetic. The caller supplies the host identity mapping."""
    return ((owner & 0xffff) + (instance & 0xffff) * 23
            + (state & 0xffff) * 7 + (activation & 63)) & 0xffff


def direction(forward, cone: float, base: int, shot: int, pellet: int = 0) -> tuple[float, ...]:
    """D5F8D0 seed index and D80090/884090 circular spread with uniform radius."""
    values = uniform_table()
    seed = _seed_index(base, shot, pellet)
    angle = f32(f32(f32(values[seed]) * f32(math.tau)) * 2.0 ** -32)
    radius = f32(f32(f32(values[(seed + 1) & 16383]) * f32(cone)) * 2.0 ** -32)
    forward = tuple(f32(value) for value in forward)
    right = _cross(forward, (0, 1, 0))
    # Preserve the tiny horizontal component of a quantized vertical aim.
    squared = f32(sum(f32(value * value) for value in right))
    right = (tuple(value / math.sqrt(squared) for value in right)
             if squared >= 1.1754943508222875e-36 else (1, 0, 0))
    return tuple(f32(value) for value in _rotate(forward, _rotate(right, forward, angle), radius))


def _unit_cross(a, b):
    cross = _cross(a, b)
    squared = f32(sum(f32(value * value) for value in cross))
    if squared < 1.1754943508222875e-36:
        return (1.0, 0.0, 0.0)
    return tuple(f32(value / math.sqrt(squared)) for value in cross)


def _sample(index, low, high):
    value = f32(uniform_table()[index & 16383])
    return f32(f32(f32(value * f32(high - low)) * 2.0 ** -32) + low)


def _angle_word(angle, scale):
    scaled = f32(f32(angle) * scale)
    rounded = math.trunc(f32(scaled + math.copysign(0.5, scaled)))
    return min(65535, max(0, rounded + 32768))


def quantized_direction(forward):
    """DB1050 and DB1360 launch direction round trip."""
    x, y, z = map(f32, forward)
    yaw = math.atan2(x, z) if x or z else 0.0
    elevation = math.copysign(math.pi / 2, y) if abs(y) > 0.9998455047607422 else math.asin(y)
    yaw = f32((_angle_word(yaw, 10430.3779296875) - 32768) * 9.58738019107841e-5)
    elevation = f32((_angle_word(elevation, 20860.755859375) - 32768) * 4.793690095539205e-5)
    horizontal = f32(math.cos(elevation))
    return (f32(f32(math.sin(yaw)) * horizontal), f32(math.sin(elevation)),
            f32(f32(math.cos(yaw)) * horizontal))


def _normalize_direction(forward):
    x, y, z = forward
    squared = f32(f32(f32(x * x) + f32(y * y)) + f32(z * z))
    if squared < 1.1754943508222875e-36:
        return (0.0, -1.0, 0.0)
    inverse = f32(1 / math.sqrt(squared))
    return tuple(f32(value * inverse) for value in forward)


def single_launch_direction(forward):
    return _normalize_direction(quantized_direction(forward))


def ring_directions(forward, degrees, jitter, phase, rings, base, shot):
    """D80310 ring spread with two independent per-pellet perturbations."""
    forward = quantized_direction(forward)
    right = _unit_cross(forward, (0, 1, 0))
    radians, tau = f32(math.pi / 180), f32(math.tau)
    if phase is None:
        phase = _sample(base + 7 * (shot & 65535), 0, tau)
    radial = max(0.0, f32(f32(f32(degrees) - f32(jitter)) * radians))
    jitter = f32(f32(jitter) * radians)
    rays = []
    for radius, count in rings:
        ring_angle = f32(radial * f32(radius))
        for index in range(count):
            angle = f32(f32(f32(index / count) * tau) + f32(phase))
            axis = _rotate(right, forward, angle)
            seed = _seed_index(base, shot, len(rays))
            ray = _rotate(forward, axis, f32(ring_angle + _sample(seed, -jitter, jitter)))
            if jitter:
                ray = _rotate(ray, _unit_cross(forward, axis), _sample(seed + 1, -jitter, jitter))
            rays.append(_normalize_direction(tuple(map(f32, ray))))
    return tuple(rays)


def owns_peacekeeper(shooter, state) -> bool:
    return (shooter.hero.guid == MCCREE and shooter.body_script is not None
            and state.instance.component is shooter.body_script.component
            and state.instance.graph.index == 0x01D2)


def fan_direction(shooter, state, forward, number: int, time: int, activation):
    if state.node.pos != 37 or not owns_peacekeeper(shooter, state):
        return forward
    launch = state.node.field("m_5EC1EB38")
    if (not isinstance(launch, dict) or launch.get("$") != "STU_239B571E"
            or state.spread_ramp is None):
        return forward

    low, high = (to_float(state.evaluate(launch.get(name), watch=False))
                 for name in ("m_7F0FED02", "m_429A6C85"))
    normalized = state.spread_at(time - state.start - state.offset)
    degrees = f32(f32(f32(high - low) * normalized) + f32(low))
    cone = f32(degrees * f32(math.pi / 180))
    # D5EB70 arithmetic with reconstructed server seed identities. The
    # association of the captured uniform radius mode with Fan is inferred.
    base = seed_base(shooter.body, state.instance.id, state.node.pos, activation[0])
    return direction(forward, cone, base, number)
