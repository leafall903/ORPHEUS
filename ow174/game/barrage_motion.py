"""Build 104319 Barrage launch and motion from the authored weapon volley."""

import math
from functools import cache

from ow174.game.script import expr

GRAPH, STATE = 0x2B, 1
TYPE = "STU_7A5DFFF8"
FIELDS = (
    "m_startTime", "m_AD8C28BA", "m_38577C27", "m_CB529CB5",
    "m_27B17E33", "m_68CC05E5", "m_F4C89440", "m_0C405FC9",
    "m_196351F8", "m_D130A78E", "m_BB325A8F", "m_747E70DD",
)
_WORD = 0xFFFFFFFF
_BLOCK = (1 << 128) - 1
_LANES = (0, 32, 64, 96)
_MASK = 0x3FFF
_TAU = expr.f32(math.tau)
_DEGREE = expr.f32(math.pi / 180)


def _pack(words):
    return sum(word << shift for word, shift in zip(words, _LANES, strict=True))


@cache
def sample_table() -> tuple[int, ...]:
    """SFMT startup at 59DF88, with each generated block read backwards."""
    words = [1]
    for index in range(1, 624):
        previous = words[-1]
        words.append((0x6C078965 * (previous ^ (previous >> 30)) + index) & _WORD)
    if ((words[0] & 1) ^ (words[3] & 0x13C9E684)).bit_count() % 2 == 0:
        words[0] ^= 1
    blocks = [_pack(words[index:index + 4]) for index in range(0, 624, 4)]
    right = _pack((0xDFFFFFEF, 0xDDFECB7F, 0xBFFAFFFF, 0xBFFFFFF6))
    right &= _pack((_WORD >> 11,) * 4)
    left = _pack(((_WORD << 18) & _WORD,) * 4)
    result = []
    while len(result) <= _MASK:
        previous, last = blocks[-2:]
        for index, current in enumerate(blocks):
            distant = blocks[(index + 122) % len(blocks)]
            updated = (current ^ ((current << 8) & _BLOCK) ^ (previous >> 8)
                       ^ ((distant >> 11) & right) ^ ((last << 18) & left))
            blocks[index] = updated
            previous, last = last, updated
        result.extend((block >> shift) & _WORD for block in reversed(blocks) for shift in reversed(_LANES))
    return tuple(result[:_MASK + 1])


def seed_for_shot(owner_word: int, instance: int, state: int, activation: int, shot: int) -> int:
    """D60B1C then D5F8D0 for the authored single projectile per shot."""
    mixed = (owner_word + 23 * instance + 7 * state + (activation & 63)) & 0xFFFF
    return sample_table()[(131 * mixed + 23 * shot) & _MASK] & _MASK


def _sample(index, low, high):
    word = sample_table()[index & _MASK]
    return expr.f32(expr.f32(expr.f32(word) * expr.f32(high - low)) * 2**-32 + low)


def sample_motion(row, seed):
    (start, ramp, rotate_chance, reverse_chance, phase_min, phase_max,
     rate_min, rate_max, amp_min, amp_max, angle_min, angle_max) = row
    rotates = _sample(seed + 2, 0, 1) <= rotate_chance
    phase = _sample(seed + 4, phase_min, phase_max)
    rate = _sample(seed + 5, rate_min, rate_max)
    if _sample(seed + 3, 0, 1) <= reverse_chance:
        rate = -rate
    amplitude = _sample(seed + 6, amp_min, amp_max)
    angle = _sample(seed + 5, angle_min, angle_max) * _DEGREE
    return start, ramp, rotates, phase, rate, amplitude, angle


def _angle_word(angle: float, scale: float) -> int:
    scaled = expr.f32(expr.f32(angle) * expr.f32(scale))
    rounded = int(expr.f32(scaled + math.copysign(0.5, scaled)))
    return min(32767, max(-32768, rounded))


def quantize_direction(forward) -> tuple[float, float, float]:
    """DB1050/DB1360 quantize launch yaw and pitch, including pole snapping."""
    x, y, z = map(expr.f32, forward)
    yaw = _angle_word(math.atan2(x, z) if x or z else 0.0, 65536 / math.tau)
    pitch = (math.copysign(math.pi / 2, y) if abs(y) > 0.9998455047607422 else math.asin(y))
    pitch = _angle_word(pitch, 65536 / math.pi)
    yaw = expr.f32(yaw * expr.f32(math.tau / 65536))
    pitch = expr.f32(pitch * expr.f32(math.pi / 65536))
    horizontal = expr.f32(math.cos(pitch))
    return (expr.f32(expr.f32(math.sin(yaw)) * horizontal), expr.f32(math.sin(pitch)),
            expr.f32(expr.f32(math.cos(yaw)) * horizontal))


def _cross(a, b) -> tuple[float, float, float]:
    x, y, z = a
    u, v, w = b
    return y*w-z*v, z*u-x*w, x*v-y*u


def spread_direction(forward, cone: float, seed: int) -> tuple[float, float, float]:
    """D80090/884090, unit aspect and uniform sampling."""
    azimuth = _sample(seed, 0, _TAU)
    radius = _sample(seed + 1, 0, cone)
    forward = tuple(map(expr.f32, forward))
    right = _cross(forward, (0, 1, 0))
    squared = expr.f32(sum(expr.f32(axis * axis) for axis in right))
    right = (tuple(axis / math.sqrt(squared) for axis in right)
             if squared >= 1.1754943508222875e-36 else (1.0, 0.0, 0.0))
    ca, sa = math.cos(azimuth), math.sin(azimuth)
    axis = tuple(a * ca + b * sa for a, b in zip(right, _cross(forward, right), strict=True))
    cr, sr = math.cos(radius), math.sin(radius)
    return tuple(expr.f32(a * cr + b * sr)
                 for a, b in zip(forward, _cross(axis, forward), strict=True))


def launch(state, activation: int, shot: int, forward) -> tuple[tuple, tuple]:
    # Pharah's WeaponComponent constructor initializes this word to zero.
    # Runtime writes before the volley are still unverified.
    seed = seed_for_shot(0, state.instance.id, state.index, activation, shot)

    def value(cfg):
        return expr.f32(expr.to_float(state.evaluate(cfg, watch=False)))

    # Both authored angle bounds are eight degrees, independent of normalized spread.
    cone = expr.f32(value(state.node.field("m_5EC1EB38.m_7F0FED02")) * _DEGREE)
    direction = spread_direction(quantize_direction(forward), cone, seed)
    motions = tuple(
        sample_motion(tuple(value(row.get(field)) for field in FIELDS), seed)
        for row in state.node.field("m_projectileMotions") or () if row.get("$") == TYPE
    )
    return direction, motions


def _offset(motion, elapsed):
    start, ramp, rotates, phase, rate, amplitude, angle = motion
    elapsed -= start
    if elapsed <= 0:
        return 0.0, 0.0
    radius = amplitude * (elapsed / ramp if elapsed < ramp else 1.0)
    phase = (elapsed * rate + phase) * _TAU
    if rotates:
        angle = phase
    else:
        radius *= math.sin(phase)
    return radius * math.sin(angle), radius * math.cos(angle)


def displacement(motions, forward, elapsed: float, duration: float) -> tuple[float, float, float]:
    """Lateral increment between two original motion samples."""
    x, y, z = forward
    horizontal = math.hypot(x, z)
    a, b = (-z / horizontal, x / horizontal) if horizontal else (1.0, 0.0)
    lateral = vertical = 0.0
    for motion in motions:
        old_x, old_y = _offset(motion, elapsed)
        new_x, new_y = _offset(motion, elapsed + duration)
        lateral += new_x - old_x
        vertical += new_y - old_y
    return a * lateral - b * y * vertical, (b * x - a * z) * vertical, b * lateral + a * y * vertical
