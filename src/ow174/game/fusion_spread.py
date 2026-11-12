"""Fusion Cannons ring directions from build 104319 D80310 at aspect one."""

import math
from functools import cache

from ow174.game.script.expr import f32


@cache
def uniform_table() -> tuple[int, ...]:
    """184BE10/184BF30, seed1 at59DF88, reverse buffer at184B3DF."""
    words = [1]
    for i in range(1, 624):
        last = words[-1]
        words.append((1812433253 * (last ^ (last >> 30)) + i) & 0xffffffff)
    parity = (1, 0, 0, 0x13c9e684)
    inner = 0
    for word, mask in zip(words[:4], parity, strict=True):
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


def seed_base(owner_word: int, instance_word: int, state_word: int, counter: int) -> int:
    """D5EB70 arithmetic; callers must document their four identity mappings."""
    return ((owner_word & 0xffff) + (instance_word & 0xffff) * 23
            + (state_word & 0xffff) * 7 + (counter & 63)) & 0xffff


def seed_index(base: int, shot: int, pellet: int) -> int:
    """D5F8D0/184B250, including both14-bit masks."""
    return uniform_table()[((base & 0xffff) * 131 + shot * 23 + pellet * 3) & 16383] & 16383


def _cross(a, b):
    return a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0]


def _axis(a, b):
    cross = _cross(a, b)
    squared = f32(sum(f32(value * value) for value in cross))
    if squared < 1.1754943508222875e-36:
        return (1.0, 0.0, 0.0)
    return tuple(f32(value / math.sqrt(squared)) for value in cross)


def _rotate(vector, axis, angle):
    cosine, sine = math.cos(angle), math.sin(angle)
    dot = sum(a * b for a, b in zip(axis, vector, strict=True))
    perpendicular = _cross(axis, vector)
    return tuple(value * cosine + perp * sine + along * dot * (1 - cosine)
                 for value, perp, along in zip(vector, perpendicular, axis, strict=True))


def _sample(index, low, high):
    value = f32(uniform_table()[index & 16383])
    return f32(f32(f32(value * f32(high - low)) * 2.0 ** -32) + low)


def directions(forward, degrees, jitter, phase, rings, base, shot):
    """Use D80310's ring angles and two independent per-pellet perturbations.

    Phase is in radians, with a negative value selecting the native
    per-shot phase sample. The launch transform and seed identities are
    supplied by the host.
    """
    forward = tuple(f32(value) for value in forward)
    right = _axis(forward, (0, 1, 0))
    radians = f32(math.pi / 180)
    tau = f32(math.tau)
    if phase < 0:
        phase = _sample(base + 7 * (shot & 65535), 0, tau)
    radial = max(0.0, f32(f32(f32(degrees) - f32(jitter)) * radians))
    jitter = f32(f32(jitter) * radians)
    rays = []
    for radius, count in rings:
        ring_angle = f32(radial * f32(radius))
        for index in range(count):
            angle = f32(f32(f32(index / count) * tau) + f32(phase))
            axis = _rotate(right, forward, angle)
            seed = seed_index(base, shot, len(rays))
            first = _rotate(forward, axis, f32(ring_angle + _sample(seed, -jitter, jitter)))
            if jitter:
                first = _rotate(first, _axis(forward, axis), _sample(seed + 1, -jitter, jitter))
            rays.append(tuple(f32(value) for value in first))
    return tuple(rays)
