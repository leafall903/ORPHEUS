"""Scoped Genji03A3/10 interception from authored boxes and angle.

Eye origin, normalized corner bounds, controller-aim frame and redirection are
explicit host reconstructions. Native filters/callbacks/history/client effects
remain unimplemented. This is not blanket immunity to splash or melee damage.
"""

import math

from ow174.game import defenses, projectile_tags
from ow174.game.script import expr

GENJI = defenses.GENJI
DEFLECTABLE = 0x0D800000000003BF
MAX_SEGMENTS = 8  # bounded host policy, not recovered native recursion limits
OFFSET = 1e-4  # outgoing segment offset, charged against distance/time
EPSILON = 1e-9


def state_of(target):
    if not hasattr(target.owner, "mover"):
        return None
    return defenses.deflect_state(target.owner)


def frontal(target, incoming, *, state=None):
    from ow174.game.combat import _dot, aim

    state = state_of(target) if state is None else state
    if state is None:
        return None
    angle = state.evaluate(state.node.field("m_025D0C39"), watch=False)
    if expr.kind(angle) not in ("float", "int") or not 0 < angle <= 180:
        return None
    forward = aim(target.owner.mover.yaw, target.owner.mover.pitch)
    return state if -_dot(forward, incoming) + EPSILON >= math.cos(math.radians(angle)) else None


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


def trace(fight, shooter, hit, origin, direction, reach, *, view_tick=None, definition=None):
    """Return nearest eligible authored box contact, even outside the body capsule."""
    tags = projectile_tags.tags_of(definition) if definition is not None else None
    # 03A3/node10 uses the same 0145 target filter as the other projectile defenses.
    # Preserve the ModifyHealth path for hitscan and projectiles with no catalog row.
    eligible = hit is not None and hit.deflectable if tags is None else projectile_tags.EXCLUDED_TAG not in tags
    if not eligible:
        return None
    best = None
    for target in fight._enemies(shooter):
        state = frontal(target, direction)
        if state is None:
            continue
        shown = target if view_tick is None else fight.seen(target, view_tick)
        if shown is None:
            continue
        distance = box_contact(target, state, origin, direction, reach, shown=shown)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, target
    return best
