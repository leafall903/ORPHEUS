"""Resource ring sampler; tangent projection and zero phase are reconstructed.

Callers restrict this to reviewed profiles. Native spread seed/phase and muzzle
transforms are not accepted by this sampler.
"""

import math


def ring_directions(state, direction):
    cfg = state.node.field("m_5EC1EB38") or {}
    if cfg.get("$") != "STU_613BDDB0":
        return (direction,)
    spread = state.evaluate(cfg.get("m_spread"), watch=False)
    count = state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)
    rings = []
    for ring in cfg.get("m_0D30E40C") or ():
        radius = state.evaluate(ring.get("m_6425FFB9"), watch=False)
        size = state.evaluate(ring.get("m_B19BD35D"), watch=False)
        if (type(radius) not in (float, int) or not math.isfinite(radius) or radius < 0
                or type(size) is not int or size < 1):
            raise ValueError("invalid authored spread ring")
        rings.append((radius, size))
    if (type(spread) not in (float, int) or not 0 <= spread < 90
            or type(count) is not int or not 1 <= count <= 256
            or sum(size for _, size in rings) != count
            or any(spread * radius >= 90 for radius, _ in rings)):
        raise ValueError("invalid authored pellet count or spread")
    # Cross the forward axis with world-up, switching reference near the poles.
    x, y, z = direction
    right = (z, 0.0, -x) if abs(y) < 0.99 else (0.0, -z, y)
    length = math.sqrt(sum(v * v for v in right))
    right = tuple(v / length for v in right)
    rx, ry, rz = right
    up = (ry * z - rz * y, rz * x - rx * z, rx * y - ry * x)
    result = []
    for radius, size in rings:
        offset = math.tan(math.radians(spread * radius))
        for pellet in range(size):
            angle = 2 * math.pi * pellet / size
            a, b = offset * math.cos(angle), offset * math.sin(angle)
            ray = tuple(d + a * r + b * u for d, r, u in zip(direction, right, up, strict=True))
            length = math.sqrt(sum(v * v for v in ray))
            result.append(tuple(v / length for v in ray))
    return tuple(result)
