"""D.Va Fusion Cannon pellet geometry, reconstructed from build104319 rings.

The authored graph supplies count, radii and spread. Degrees, tangent-plane
projection and zero ring phase are reconstruction choices, pending native
trajectory comparison. They must not be reported as verified client behavior.
"""

import math

HERO = 0x02E000000000007A
FUSION_CANNONS = 0x050A


def pellet_directions(actor, state, direction):
    """Expand only D.Va's authored Fusion Cannon volley into independent rays."""
    if (getattr(getattr(actor, "hero", None), "guid", None) != HERO
            or state.instance.graph.index != FUSION_CANNONS):
        return (direction,)
    cfg = state.node.field("m_5EC1EB38") or {}
    if cfg.get("$") != "STU_613BDDB0":
        raise ValueError("D.Va Fusion Cannons require their authored ring pattern")
    count = state.evaluate(state.node.field("m_numProjectilesPerShot"), watch=False)
    spread = state.evaluate(cfg.get("m_spread"), watch=False)
    rings = []
    for ring in cfg.get("m_0D30E40C") or ():
        radius = state.evaluate(ring.get("m_6425FFB9"), watch=False)
        size = state.evaluate(ring.get("m_B19BD35D"), watch=False)
        if (type(radius) not in (float, int) or not math.isfinite(radius) or radius < 0
                or type(size) is not int or size <= 0):
            raise ValueError("invalid D.Va Fusion Cannon ring")
        rings.append((radius, size))
    if (type(count) is not int or not 1 <= count <= 256
            or type(spread) not in (float, int) or not math.isfinite(spread) or spread < 0
            or sum(size for _, size in rings) != count
            or any(spread * radius >= 90 for radius, _ in rings)):
        raise ValueError("invalid D.Va Fusion Cannon count/spread")
    x, y, z = direction
    right = (z, 0.0, -x) if abs(y) < 0.99 else (0.0, -z, y)
    length = math.sqrt(sum(v * v for v in right))
    right = tuple(v / length for v in right)
    rx, ry, rz = right
    up = (ry * z - rz * y, rz * x - rx * z, rx * y - ry * x)
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
