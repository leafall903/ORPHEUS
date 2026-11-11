"""Ray intersection with authored interception boxes in a reconstructed aim frame."""

import math

from ow174.game.script import expr

EPSILON = 1e-9


def ray_box(origin, direction, low, high, reach):
    enter, leave = 0.0, reach
    for position, velocity, a, b in zip(origin, direction, low, high, strict=True):
        a, b = min(a, b), max(a, b)
        if abs(velocity) <= EPSILON:
            if position < a or position > b:
                return None
            continue
        first, last = (a - position) / velocity, (b - position) / velocity
        enter, leave = max(enter, min(first, last)), min(leave, max(first, last))
        if enter > leave + EPSILON:
            return None
    return enter if leave >= 0 and enter <= reach else None


def box_contact(target, state, origin, direction, reach, *, shown=None):
    """Shared reconstructed eye/aim frame for one authored interception state."""
    from ow174.game.combat import _dot, _sub, aim

    shown = target if shown is None else shown
    yaw, pitch = target.owner.mover.yaw, target.owner.mover.pitch
    forward = aim(yaw, pitch)
    angle = yaw * 2 * math.pi / 65536
    right = (math.cos(angle), 0.0, -math.sin(angle))
    up = (-forward[1] * math.sin(angle),
          forward[2] * right[0] - forward[0] * right[2],
          -forward[1] * math.cos(angle))
    eye = (shown.feet[0], shown.feet[1] + shown.eye, shown.feet[2])
    offset = _sub(origin, eye)
    local = tuple(_dot(offset, axis) for axis in (right, up, forward))
    ray = tuple(_dot(direction, axis) for axis in (right, up, forward))
    best = None
    for box in state.node.field("m_boxes") or ():
        low = state.evaluate(box.get("m_CBA118B2"), watch=False)
        high = state.evaluate(box.get("m_AEF67365"), watch=False)
        if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
            continue
        if not all(math.isfinite(v) for v in (*low, *high)):
            continue
        distance = ray_box(local, ray, tuple(low), tuple(high), reach)
        if distance is not None and (best is None or distance < best):
            best = distance
    return best
