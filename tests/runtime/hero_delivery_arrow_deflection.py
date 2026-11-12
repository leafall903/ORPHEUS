"""Reconstructed Genji interception using the authored 03A3 angle and boxes."""

import math

from ow174.game.script import expr

GENJI = 0x02E0000000000029
DEFLECTABLE = 0x0D800000000003BF
MAX_REFLECTIONS = 8


def box_entry(origin, direction, low, high, reach):
    enter, leave = 0.0, reach
    for point, speed, a, b in zip(origin, direction, low, high, strict=True):
        if abs(speed) < 1e-9:
            if not min(a, b) <= point <= max(a, b):
                return None
            continue
        first, last = sorted(((a - point) / speed, (b - point) / speed))
        enter, leave = max(enter, first), min(leave, last)
        if enter > leave:
            return None
    return enter


def active_volume(target, graph, slot):
    owner = target.owner
    if owner.body_script is None:
        return None
    state = next((instance.states.get(slot) for instance in owner.body_script.component.instances.values()
                  if instance.graph.index == graph), None)
    if (state is None or not state.active or state.ending
            or state.node.cls != "STUStatescriptStateDeflectProjectiles"):
        return None
    return state


def frontal(target, direction):
    from ow174.game.combat import _dot, aim

    owner = target.owner
    if getattr(getattr(owner, "hero", None), "guid", None) != GENJI:
        return None
    state = active_volume(target, 0x03A3, 10)
    if state is None:
        return None
    yaw, pitch = owner.mover.yaw, owner.mover.pitch
    forward = aim(yaw, pitch)
    angle = state.evaluate(state.node.field("m_025D0C39"), watch=False)
    if type(angle) not in (float, int) or not 0 < angle <= 180:
        return None
    if -_dot(forward, direction) + 1e-9 < math.cos(math.radians(angle)):
        return None
    return state


def trace(target, origin, direction, reach):
    state = frontal(target, direction)
    if state is None:
        return None
    return box_contact(target, state, origin, direction, reach)


def box_contact(target, state, origin, direction, reach):
    from ow174.game.combat import _dot, aim

    owner = target.owner
    mover = getattr(owner, "control_mover", None) or owner.mover
    yaw, pitch = mover.yaw, mover.pitch
    forward = aim(yaw, pitch)
    yaw_rad = yaw * math.tau / 65536
    right = (math.cos(yaw_rad), 0.0, -math.sin(yaw_rad))
    up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
          -forward[1] * right[0])
    x, y, z = target.feet
    eye_offset = (origin[0] - x, origin[1] - y - target.eye, origin[2] - z)
    axes = (right, up, forward)
    local = tuple(_dot(eye_offset, axis) for axis in axes)
    ray = tuple(_dot(direction, axis) for axis in axes)
    best = None
    for box in state.node.field("m_boxes") or ():
        low, high = (state.evaluate(box.get(key), watch=False) for key in ("m_CBA118B2", "m_AEF67365"))
        if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
            continue
        if not all(math.isfinite(value) for value in (*low, *high)):
            continue
        distance = box_entry(local, ray, tuple(low), tuple(high), reach)
        if distance is not None and (best is None or distance < best):
            best = distance
    return best
