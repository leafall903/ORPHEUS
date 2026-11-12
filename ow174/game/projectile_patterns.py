"""Authored ring counts with an explicit unseeded radial reconstruction.

613BDDB0 contains spread and B101467F radius/count rings. We reconstruct equally
spaced angular rings in degrees. Native per-shot phase/jitter (including the
unresolved92308789 field) and muzzle offsets are not reproduced here.
"""

import math

from ow174.game.script import expr


def directions(state, forward):
    launch = state.node.field("m_5EC1EB38")
    if not isinstance(launch, dict) or launch.get("$") != "STU_613BDDB0":
        return (forward,)

    def number(cfg, default=0.0):
        return default if cfg is None else float(expr.to_float(state.evaluate(cfg, watch=False)))

    count = int(number(state.node.field("m_numProjectilesPerShot"), 1))
    spread = number(launch.get("m_spread"))
    rings = []
    for ring in launch.get("m_0D30E40C") or []:
        if not isinstance(ring, dict) or ring.get("$") != "STU_B101467F":
            raise ValueError("unsupported projectile ring definition")
        radius = number(ring.get("m_6425FFB9"))
        size = int(number(ring.get("m_B19BD35D")))
        if not math.isfinite(radius) or radius < 0.0 or not 1 <= size <= 64:
            raise ValueError("invalid projectile ring radius/count")
        rings.append((radius, size))
    if not 1 <= count <= 64 or sum(size for _, size in rings) != count:
        raise ValueError("projectile ring count disagrees with volley")
    if not math.isfinite(spread) or not 0.0 <= spread < 90.0:
        raise ValueError("invalid projectile ring spread")

    # A stable basis at vertical aim as well as ordinary horizontal aiming.
    horizontal = math.hypot(forward[0], forward[2])
    right = ((forward[2] / horizontal, 0.0, -forward[0] / horizontal)
             if horizontal > 1e-9 else (1.0, 0.0, 0.0))
    up = (forward[1] * right[2] - forward[2] * right[1],
          forward[2] * right[0] - forward[0] * right[2],
          forward[0] * right[1] - forward[1] * right[0])
    found = []
    for radius, size in rings:
        angle = math.radians(spread * radius)
        if angle >= math.pi / 2:
            raise ValueError("projectile ring exceeds forward hemisphere")
        slope = math.tan(angle)
        for index in range(size):
            phase = 2 * math.pi * index / size
            vector = tuple(forward[k] + slope * (math.cos(phase) * right[k] + math.sin(phase) * up[k])
                           for k in range(3))
            length = math.sqrt(sum(axis * axis for axis in vector))
            found.append(tuple(axis / length for axis in vector))
    return tuple(found)
