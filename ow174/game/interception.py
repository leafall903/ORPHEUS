"""Reconstructed projectile admission for Sigma's authored Grasp boxes.

View-local box anchoring and eligibility are explicit approximations. The
authored absorbed-damage event is delivered; temporary shield-pool hosting and
native visual/transport behavior require separate acceptance.
"""

import math

from ow174.game.script import expr

EPSILON = 1e-9


def ray_box(origin, direction, low, high, length):
    first, last = 0.0, length
    for o, d, lo, hi in zip(origin, direction, low, high, strict=True):
        lo, hi = min(lo, hi), max(lo, hi)
        if abs(d) < EPSILON:
            if not lo <= o <= hi:
                return None
            continue
        a, b = (lo - o) / d, (hi - o) / d
        first, last = max(first, min(a, b)), min(last, max(a, b))
        if first > last:
            return None
    return first


def absorption(fight, shooter, hit, origin, direction, length):
    """Nearest eligible enemy Grasp volume before a body or wall is considered."""
    if hit is None or not hit.deflectable:
        return None
    best = None
    for target in fight._enemies(shooter):
        owner = target.owner
        mover = getattr(owner, "mover", None)
        script = getattr(owner, "body_script", None)
        if mover is None or script is None:
            continue
        yaw, pitch = mover.yaw * math.tau / 65536, mover.pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
        eye = fight._eye(owner)
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for instance in script.component.instances.values():
            if instance.graph.index != 0x1D02:
                continue
            for state in instance.states.values():
                if (not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                        or state.node.field("m_8B251D53") != 1):
                    continue
                for box in state.node.field("m_boxes") or ():
                    low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                    high = state.evaluate(box.get("m_AEF67365"), watch=False)
                    if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                        continue
                    distance = ray_box(local_origin, local_direction, low, high, length)
                    if distance is not None and (best is None or distance < best[0]):
                        best = distance, state
    return best


def absorbed(state, amount):
    """Write the per-projectile amount and run the authored accumulation plug."""
    output = expr.lvalue(state.node.field("m_F6CDC81C"))
    if output is not None:
        state.instance.write(output, expr.f32(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()
