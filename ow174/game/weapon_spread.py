"""104319 spread-table generators and circular-cone math.

Tables are generated, never loaded from client assets. Their complete outputs
match the authorized capture. Seed-base argument identities belong to callers;
McCree currently supplies a declared server mapping rather than native handles.
"""

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
    for word, mask in zip(words, parity, strict=False):
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


@cache
def float_table() -> tuple[float, ...]:
    """184B280: sum three signed xorshift samples, then convert to float32.

    Seed03AF714A was inferred by reversing the captured terminal state and
    independently confirmed by regenerating all16384 captured float values.
    """
    state, mask = 0x3af714a, (1 << 64) - 1
    values = []
    for _ in range(16384):
        terms = []
        for _ in range(3):
            old = state
            state ^= state << 13 & mask
            state ^= state >> 17
            state ^= state << 5 & mask
            value = (state + old) & mask
            if value >> 63:
                value -= 1 << 64
            terms.append(float(value) * 2.0 ** -63)
        values.append(f32((terms[0] + terms[1]) + terms[2]))
    return tuple(values)


def seed_base(owner_word: int, instance_word: int, state_word: int, counter: int) -> int:
    """D5EB70 arithmetic; callers must document their four identity mappings."""
    return ((owner_word & 0xffff) + (instance_word & 0xffff) * 23
            + (state_word & 0xffff) * 7 + (counter & 63)) & 0xffff


def seed_index(base: int, shot: int, pellet: int) -> int:
    """D5F8D0/184B250, including both14-bit masks."""
    return uniform_table()[((base & 0xffff) * 131 + shot * 23 + pellet * 3) & 16383] & 16383


def _cross(a, b):
    return a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]


def launch_direction(forward, lift: float) -> tuple[float, float, float]:
    """D5ECB4-D5ECEA: add launch22CE7F30 to Y, normalize, or fall back down."""
    x, y, z = (f32(value) for value in forward)
    y = f32(y + f32(lift))
    squared = f32(f32(f32(x*x) + f32(y*y)) + f32(z*z))
    if squared < 1.1754943508222875e-36:
        return -0.0, -1.0, -0.0
    inverse = 1 / math.sqrt(squared)
    return tuple(f32(value * inverse) for value in (x, y, z))


def _rotate(vector, axis, angle):
    cos, sin = math.cos(angle), math.sin(angle)
    dot = sum(a * b for a, b in zip(axis, vector, strict=True))
    perpendicular = _cross(axis, vector)
    return tuple(vector[i] * cos + perpendicular[i] * sin + axis[i] * dot * (1 - cos)
                 for i in range(3))


def direction(forward, cone: float, base: int, shot: int, pellet: int = 0,
              *, gaussian: bool = False) -> tuple[float, float, float]:
    """D80090/884090 circular spread (aspect1), excluding input/muzzle quantization."""
    seed = seed_index(base, shot, pellet)
    values = uniform_table()
    angle = f32(f32(f32(values[seed]) * f32(math.tau)) * 2.0 ** -32)
    cone = f32(cone)
    radius = (f32(f32(float_table()[(seed + 1) & 16383] * cone) * f32(1 / 3))
              if gaussian else f32(f32(f32(values[(seed + 1) & 16383]) * cone) * 2.0 ** -32))
    forward = tuple(f32(value) for value in forward)
    right = _cross(forward, (0, 1, 0))
    # D8025A compares squared length against captured25E1860. A camera at
    # exactly90 degrees can still have cos(pi/2)~=6e-17: normalize that vector
    # rather than switching its axis to the true-zero fallback.
    squared = f32(sum(f32(value * value) for value in right))
    right = (tuple(value / math.sqrt(squared) for value in right)
             if squared >= 1.1754943508222875e-36 else (1, 0, 0))
    return tuple(f32(value) for value in _rotate(forward, _rotate(right, forward, angle), radius))
