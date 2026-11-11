"""Authored spread rings with reconstructed tangent projection and zero phase."""

import math


def _basis(direction):
    x, y, z = direction
    right = (z, 0.0, -x) if abs(y) < 0.99 else (0.0, -z, y)
    length = math.sqrt(sum(v * v for v in right))
    right = tuple(v / length for v in right)
    rx, ry, rz = right
    return right, (ry * z - rz * y, rz * x - rx * z, rx * y - ry * x)


def orient_forward(rays, direction):
    """Rotate a cone sampled in +Z to the projectile's final heading."""
    right, up = _basis(direction)
    return tuple(tuple(x * r - y * u + z * f for r, u, f in zip(right, up, direction, strict=True))
                 for x, y, z in rays)


def ring_directions(state, direction, *, fields=None):
    fields = state.node.fields if fields is None else fields
    cfg = fields.get("m_5EC1EB38") or {}
    if cfg.get("$") != "STU_613BDDB0":
        raise ValueError("weapon requires its authored ring pattern")
    count = state.evaluate(fields.get("m_numProjectilesPerShot"), watch=False)
    spread = state.evaluate(cfg.get("m_spread"), watch=False)
    rings = []
    for ring in cfg.get("m_0D30E40C") or ():
        radius = state.evaluate(ring.get("m_6425FFB9"), watch=False)
        size = state.evaluate(ring.get("m_B19BD35D"), watch=False)
        if (type(radius) not in (float, int) or not math.isfinite(radius) or radius < 0
                or type(size) is not int or size <= 0):
            raise ValueError("invalid weapon ring")
        rings.append((radius, size))
    if (type(count) is not int or not 1 <= count <= 256
            or type(spread) not in (float, int) or not math.isfinite(spread) or spread < 0
            or sum(size for _, size in rings) != count
            or any(spread * radius >= 90 for radius, _ in rings)):
        raise ValueError("invalid weapon count/spread")
    right, up = _basis(direction)
    rays = []
    for radius, size in rings:
        offset = math.tan(math.radians(spread * radius))
        for pellet in range(size):
            phase = 2 * math.pi * pellet / size
            horizontal, vertical = offset * math.cos(phase), offset * math.sin(phase)
            ray = tuple(d + horizontal * r + vertical * u
                        for d, r, u in zip(direction, right, up, strict=True))
            length = math.sqrt(sum(v * v for v in ray))
            rays.append(tuple(v / length for v in ray))
    return tuple(rays)
