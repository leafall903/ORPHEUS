"""Ray tests against authored State boxes in a reconstructed eye-relative frame."""

import math

from ow174.game.script import expr


def ray_box(origin, direction, first, second, reach, radius):
    """First nonnegative intersection with a local box expanded by the cast radius."""
    start, stop = 0.0, reach
    for point, vector, a, b in zip(origin, direction, first, second, strict=True):
        lower, upper = min(a, b) - radius, max(a, b) + radius
        if abs(vector) < 1e-9:
            if point < lower or point > upper:
                return None
            continue
        enter, leave = (lower - point) / vector, (upper - point) / vector
        start, stop = max(start, min(enter, leave)), min(stop, max(enter, leave))
        if start > stop:
            return None
    return start


def intersect(state, eye, yaw, pitch, origin, direction, reach, *, radius=0.0):
    """Evaluate the active state's bounds and cast in its reconstructed local frame."""
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
    delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
    local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
    local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
    nearest = None
    for box in state.node.field("m_boxes") or ():
        first = state.evaluate(box.get("m_CBA118B2"), watch=False)
        second = state.evaluate(box.get("m_AEF67365"), watch=False)
        if not isinstance(first, expr.Vec3) or not isinstance(second, expr.Vec3):
            continue
        hit = ray_box(local_origin, local_direction, first, second, reach, radius)
        if hit is not None and (nearest is None or hit < nearest):
            nearest = hit
    return nearest
