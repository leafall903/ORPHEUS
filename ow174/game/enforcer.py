"""104319 manual Enforcer rings with a reconstructed server seed."""

import hashlib
import math
import os
import struct
from functools import lru_cache
from pathlib import Path

from ow174.game.script import expr

HERO, GRAPH, NODE = 0x02E00000000001CE, 0x13EB, 24
MAX_PELLETS = 256
TABLE_ENV = "ORPHEUS_ENFORCER_DISPERSION_TABLE"
TABLE_SHA256 = "4f99ff210621528fff6737582c39f0ed5464cdf1c9980f03d503dcf311b356f6"
TABLE_MASK = 0x3FFF
TABLE_SCALE = 2.0**-32
TAU = expr.f32(math.tau)
DEGREES = expr.f32(math.pi / 180.0)


@lru_cache
def load_dispersion_table(path):
    payload = Path(path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != TABLE_SHA256:
        raise ValueError("Enforcer dispersion table does not match the captured 104319 table")
    return struct.unpack("<16384I", payload)


def dispersion_fingerprint():
    path = os.environ.get(TABLE_ENV)
    if not path:
        return None
    load_dispersion_table(path)
    return TABLE_SHA256


def directions(shooter, state, forward, *, activation, number):
    if (
        getattr(shooter, "hero", None) != HERO
        or getattr(shooter, "spawner", {}).get("manual_spawn") is not True
        or state.instance.graph.index != GRAPH
        or state.node.pos != NODE
    ):
        return (forward,)
    path = os.environ.get(TABLE_ENV)
    if path:
        # A replay-stable seed from the admitted shot. Live native input
        # operands are unresolved; only the mixer coefficients are native.
        counter, start = activation
        seed = (shooter.entity + 23 * counter + 7 * start) & 0xFFFF
        return ring_directions(
            state, forward, state.node.fields, sampling=(load_dispersion_table(path), seed, number)
        )
    return ring_directions(state, forward, state.node.fields)


def ring_directions(state, forward, fields, *, sampling=None):
    config = fields.get("m_5EC1EB38") or {}
    if config.get("$") != "STU_613BDDB0":
        raise ValueError("manual Enforcer requires its declared ring pattern")

    def number(cfg):
        value = expr.to_float(state.evaluate(cfg, watch=False))
        if not math.isfinite(value):
            raise ValueError("nonfinite Enforcer pattern value")
        return value

    count = int(number(fields.get("m_numProjectilesPerShot")))
    rings = [(number(r["m_6425FFB9"]), int(number(r["m_B19BD35D"]))) for r in config.get("m_0D30E40C") or ()]
    if (
        not 1 <= count <= MAX_PELLETS
        or sum(n for _, n in rings) != count
        or any(radius < 0 or n < 1 for radius, n in rings)
    ):
        raise ValueError("Enforcer rings do not match declared projectile count")
    spread = number(config.get("m_spread"))
    if not 0 <= spread < 90:
        raise ValueError("unsupported Enforcer cone angle")
    if sampling is not None:
        jitter = number(config.get("m_92308789"))
        fixed = config.get("m_391F15AF")
        return sampled_rings(
            forward, rings, spread, jitter, None if fixed is None else number(fixed), *sampling
        )
    # Existing zero-phase reconstruction for other ring users and older runtime data.
    width = math.tan(math.radians(spread))
    horizontal = math.hypot(forward[0], forward[2])
    right = (forward[2] / horizontal, 0.0, -forward[0] / horizontal) if horizontal > 1e-9 else (1.0, 0.0, 0.0)
    up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2], -forward[1] * right[0])
    result = []
    for radius, pellets in rings:
        for index in range(pellets):
            angle = 2 * math.pi * index / pellets
            x, y = radius * width * math.cos(angle), radius * width * math.sin(angle)
            vector = tuple(f + x * r + y * u for f, r, u in zip(forward, right, up, strict=True))
            length = math.sqrt(sum(v * v for v in vector))
            result.append(tuple(v / length for v in vector))
    return tuple(result)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def unit(vector):
    square = sum(axis * axis for axis in vector)
    if square < 1.1754943508222875e-36:
        return (1.0, 0.0, 0.0)
    length = math.sqrt(square)
    return tuple(axis / length for axis in vector)


def rotate(vector, axis, angle):
    cosine, sine = math.cos(angle), math.sin(angle)
    projection = sum(a * b for a, b in zip(vector, axis, strict=True)) * (1.0 - cosine)
    return tuple(
        a * cosine + b * sine + c * projection
        for a, b, c in zip(vector, cross(axis, vector), axis, strict=True)
    )


def sample(table, index, low, high):
    product = expr.f32(expr.f32(table[index & TABLE_MASK]) * expr.f32(high - low))
    return expr.f32(expr.f32(product * TABLE_SCALE) + low)


def sampled_rings(forward, rings, spread, jitter, fixed_phase, table, seed, shot):
    """Rebuild native ring phase and sequential rotations from admitted inputs."""
    basis = unit(cross(forward, (0.0, 1.0, 0.0)))
    phase = (
        sample(table, seed + 7 * (shot & 0xFFFF), 0.0, TAU)
        if fixed_phase is None
        else expr.f32(math.radians(fixed_phase) % TAU)
    )
    jitter_angle = expr.f32(jitter * DEGREES)
    ring_angle = expr.f32(expr.f32(spread - jitter) * DEGREES)
    result = []
    for radius, pellets in rings:
        offset = expr.f32(radius * max(0.0, ring_angle))
        for index in range(pellets):
            angle = expr.f32(expr.f32(expr.f32(index / pellets) * TAU) + phase)
            axis = rotate(basis, forward, angle)
            key = table[(131 * seed + 23 * shot + 3 * len(result)) & TABLE_MASK] & TABLE_MASK
            first = rotate(forward, axis, expr.f32(offset + sample(table, key, -jitter_angle, jitter_angle)))
            second = rotate(
                first, unit(cross(forward, axis)), sample(table, key + 1, -jitter_angle, jitter_angle)
            )
            result.append(unit(second))
    return tuple(result)
