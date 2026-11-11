"""Build 104319 Barrage launch and motion from the authored weapon volley."""

import math
from dataclasses import dataclass

from ow174.game import weapon_spread
from ow174.game.script import expr

GRAPH, STATE = 0x2B, 1
TYPE = "STU_7A5DFFF8"
FIELDS = (
    "m_startTime", "m_AD8C28BA", "m_38577C27", "m_CB529CB5",
    "m_27B17E33", "m_68CC05E5", "m_F4C89440", "m_0C405FC9",
    "m_196351F8", "m_D130A78E", "m_BB325A8F", "m_747E70DD",
)
_MASK = 0x3FFF
_TAU = expr.f32(math.tau)
_DEGREE = expr.f32(math.pi / 180)


def _sample(index, low, high):
    word = weapon_spread.uniform_table()[index & _MASK]
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


def shot_direction(state, forward, seed: int) -> tuple[float, float, float]:
    # Both authored angle bounds are eight degrees, independent of normalized spread.
    cfg = state.node.field("m_5EC1EB38.m_7F0FED02")
    degrees = expr.f32(expr.to_float(state.evaluate(cfg, watch=False)))
    return spread_direction(quantize_direction(forward), expr.f32(degrees * _DEGREE), seed)


def shot_seed(state, activation: int, shot: int) -> int:
    # Pharah's WeaponComponent constructor initializes this word to zero.
    # Runtime writes before the volley are still unverified.
    base = weapon_spread.seed_base(0, state.instance.id, state.index, activation)
    return weapon_spread.seed_index(base, shot, 0)


def launch(state, activation: int, shot: int, forward) -> tuple[tuple, tuple]:
    seed = shot_seed(state, activation, shot)

    def value(cfg):
        return expr.f32(expr.to_float(state.evaluate(cfg, watch=False)))

    direction = shot_direction(state, forward, seed)
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


@dataclass(frozen=True)
class LaunchSource:
    body: int
    instance: int
    state: int
    shot: int
    lifetime: float
    reach: float | None

    def reflected_direction(self, match, forward):
        owner = next((player for player in match.players
                      if player.body == self.body and not player.gone), None)
        script = owner.body_script if owner is not None else None
        instance = script.component.instances.get(self.instance) if script is not None else None
        if instance is None or instance.graph.index != GRAPH:
            return forward
        state = instance.states.get(self.state)
        if state is None:
            return forward
        return shot_direction(state, forward, shot_seed(state, state.counter, self.shot))


def reflected_origin(genji):
    # The 0225 FirstPerson component supplies the eye above the sprung body pose.
    mover = genji.mover
    x, y, z = mover.position
    height = 0.84 if mover.crouched else 1.20
    return x, expr.f32(y + expr.f32(height + mover.spring)), z
