"""104319 radial spread with a deterministic server seed binding."""

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
    return a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]


def _rotate(vector, axis, angle):
    cos, sin = math.cos(angle), math.sin(angle)
    dot = sum(a * b for a, b in zip(axis, vector, strict=True))
    perpendicular = _cross(axis, vector)
    return tuple(vector[i] * cos + perpendicular[i] * sin + axis[i] * dot * (1 - cos)
                 for i in range(3))


def radial_directions(forward, spread, jitter, rings, base, shot):
    """D80310 ring and jitter rotations for Doomfist's zero phase, unit aspect."""
    right = _cross(forward, (0, 1, 0))
    squared = sum(value * value for value in right)
    if squared < 1.1754943508222875e-36:
        right = (1.0, 0.0, 0.0)
    else:
        right = tuple(value / math.sqrt(squared) for value in right)
    jitter_radians = f32(f32(jitter) * f32(math.pi / 180))
    ring_radians = f32(max(f32(f32(spread) - f32(jitter)), 0.0) * f32(math.pi / 180))
    width = f32(2 * jitter_radians)
    table = uniform_table()
    directions = []
    for radius, count in rings:
        for index in range(count):
            azimuth = f32(f32(index / count) * f32(math.tau))
            axis = _rotate(right, forward, azimuth)
            seed = seed_index(base, shot, len(directions))
            first = f32(f32(f32(f32(table[seed]) * width) * 2.0 ** -32) - jitter_radians)
            second = f32(f32(f32(f32(table[(seed + 1) & 16383]) * width) * 2.0 ** -32) - jitter_radians)
            vector = _rotate(forward, axis, f32(f32(ring_radians * radius) + first))
            directions.append(_rotate(vector, _cross(forward, axis), second))
    return tuple(directions)
