"""Ana projectile eligibility and authored D.Va Matrix box intersection."""
import math

from ow174.game.script.expr import Vec3

HERO, MECH = 0x02E000000000007A, 0x0400000000000407
# Build104319 .003 target-tag components. 15E9 has the Matrix exclusion
# tag 0145. An absent component on a verified definition is an empty set.
ANA_PROJECTILE_TAGS = {
    0x04000000000015B7: (0x2F9,),
    0x0400000000001615: (0x2F9,),
    0x04000000000015E8: (),
    0x0400000000001426: (),
    0x04000000000015E9: (0x145, 0x412),
}


def absorbable(definition):
    tags = ANA_PROJECTILE_TAGS.get(definition)
    return tags is not None and 0x145 not in tags


def ray_box(origin, direction, lower, upper, reach):
    """First intersection of a bounded ray with a local axis-aligned box."""
    near, far = 0.0, reach
    for point, velocity, low, high in zip(origin, direction, lower, upper, strict=True):
        if abs(velocity) < 1e-12:
            if point < low or point > high:
                return None
            continue
        a, b = (low - point) / velocity, (high - point) / velocity
        near, far = max(near, min(a, b)), min(far, max(a, b))
        if near > far:
            return None
    return near


def matrix_distance(player, origin, direction, reach, anchor, forward):
    """Distance to active authored02CC/st3 boxes in a reconstructed aim frame.

    The caller supplies a live enemy player, eye anchor and normalized aim.
    Death, team, static cover and nearer body ordering belong to Combat.
    """
    hero = getattr(player, "hero", None)
    script = getattr(player, "body_script", None)
    if (hero is None or hero.guid != HERO or hero.body != MECH
            or script is None or script.retired or script.component.fault is not None):
        return None
    hit = active_boxes(player, origin, direction, reach, anchor, forward, 0x02CC, state_index=3)
    return hit[0] if hit is not None else None


def active_boxes(player, origin, direction, reach, anchor, forward, graph, *, state_index=None):
    """Nearest active authored box and its owning state in the current aim frame."""
    script = getattr(player, "body_script", None)
    if script is None or script.retired or script.component.fault is not None:
        return None
    x, y, z = forward
    length = math.hypot(x, z)
    right = (z / length, 0.0, -x / length) if length > 1e-9 else (1.0, 0.0, 0.0)
    rx, ry, rz = right
    up = (y * rz - z * ry, z * rx - x * rz, x * ry - y * rx)
    basis = (right, up, forward)
    delta = tuple(a - b for a, b in zip(origin, anchor, strict=True))
    local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
    local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
    best = None
    for instance in script.component.instances.values():
        if instance.graph.index != graph:
            continue
        for state in instance.states.values():
            if (not state.active or state.ending or (state_index is not None and state.index != state_index)
                    or state.node.cls != "STUStatescriptStateDeflectProjectiles"):
                continue
            for box in state.node.field("m_boxes") or ():
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if (not isinstance(low, Vec3) or not isinstance(high, Vec3)
                        or any(not math.isfinite(v) for v in (*low, *high))
                        or any(a > b for a, b in zip(low, high, strict=True))):
                    raise ValueError("invalid authored projectile defense box")
                hit = ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, state
    return best
