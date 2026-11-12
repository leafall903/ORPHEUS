"""Projectile consumer for active authored DeflectProjectiles states.

DATA: evaluated boxes and incoming-angle limit. RECONSTRUCTED: eye-relative
look basis, box sweep, aim-directed velocity and ownership transfer. Native
projectile filters, reflected transport and hit-scan/melee block services stay open.
"""
import math


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _box(origin, direction, low, high, length):
    enter, leave = 0.0, length
    for p, d, a, b in zip(origin, direction, low, high, strict=True):
        lo, hi = min(a, b), max(a, b)
        if abs(d) < 1e-9:
            if p < lo or p > hi:
                return None
            continue
        first, last = sorted(((lo - p) / d, (hi - p) / d))
        enter, leave = max(enter, first), min(leave, last)
        if enter > leave:
            return None
    return enter


def intercept(fight, rocket, length, excluded, *, modes=('reflect', 'absorb'), direction=None, eye_of=None):
    from ow174.game.combat import aim
    from ow174.game.script.expr import Vec3

    flight_direction = rocket.direction if direction is None else direction
    best = None
    for target in fight._enemies(rocket.shooter):
        owner = target.owner
        script = getattr(owner, 'body_script', None)
        if (target.entity in excluded or script is None or not hasattr(owner, 'mover')
                or fight.is_stunned(owner)):
            continue
        forward = aim(owner.mover.yaw, owner.mover.pitch)
        yaw = owner.mover.yaw * math.tau / 65536
        pitch = owner.mover.pitch * math.tau / 65536
        right = (math.cos(yaw), 0.0, -math.sin(yaw))
        up = (math.sin(yaw) * math.sin(pitch), math.cos(pitch), math.cos(yaw) * math.sin(pitch))
        axes = right, up, forward
        eye = fight._eye(owner) if eye_of is None else eye_of(owner)
        delta = tuple(p - e for p, e in zip(rocket.position, eye, strict=True))
        origin = tuple(_dot(delta, axis) for axis in axes)
        direction = tuple(_dot(flight_direction, axis) for axis in axes)
        for instance in script.component.instances.values():
            for state in instance.states.values():
                if not state.active or state.node.cls != 'STUStatescriptStateDeflectProjectiles':
                    continue
                key = instance.graph.index, state.index
                mode = 'reflect' if key == (0x3A3, 10) else 'absorb' if key == (0x1D02, 1) else None
                if mode not in modes:
                    continue  # Other native DeflectProjectiles modes require their own consumers.
                angle = state.evaluate(state.node.field('m_025D0C39'), watch=False)
                if not isinstance(angle, (int, float)):
                    continue
                if -_dot(forward, flight_direction) + 1e-9 < math.cos(math.radians(angle)):
                    continue
                for row in state.node.field('m_boxes') or ():
                    low = state.evaluate(row.get('m_CBA118B2'), watch=False)
                    high = state.evaluate(row.get('m_AEF67365'), watch=False)
                    if not isinstance(low, Vec3) or not isinstance(high, Vec3):
                        continue
                    distance = _box(origin, direction, (low.x, low.y, low.z),
                                    (high.x, high.y, high.z), length)
                    if distance is not None and (best is None or distance < best[0]):
                        best = distance, target, forward, state, mode
    return best


def absorb(state, amount):
    """Reconstruct Grasp's consumed-damage output and execute its authored accumulation plug."""
    from ow174.game.script import expr

    target = expr.lvalue(state.node.field('m_F6CDC81C'))
    if target is not None:
        state.instance.write(target, expr.f32(amount))
    state.instance.follow(state.node, 'm_160B5DBE', state)
    state.instance.component.run_now()


def blocks_melee(fight, attacker, target, state, modify):
    """Reconstruct Genji3A3/st59's zero scalar for matching frontal melee tags.

    DATA: active CombatModFilter zero scalar and authored matching identifiers.
    RECONSTRUCTED: its candidate-position dot-look filter evaluated from body poses.
    Other combat filters, native tag hierarchy and status stacking remain open.
    """
    from ow174.game.combat import aim
    from ow174.game.script.expr import Asset

    script = getattr(target.owner, 'body_script', None)
    if script is None or not hasattr(target.owner, 'mover') or fight.is_stunned(target.owner):
        return False
    tags = set()
    for row in modify.get('m_148F3152') or ():
        tag = state.evaluate(row.get('m_EE3055BD'), watch=False)
        if isinstance(tag, Asset):
            tags.add(tag.guid)
    position = fight._eye(attacker)
    eye = fight._eye(target.owner)
    toward = tuple(p - e for p, e in zip(position, eye, strict=True))
    forward = aim(target.owner.mover.yaw, target.owner.mover.pitch)
    if _dot(toward, forward) <= 0:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0x3A3:
            continue
        filtering = instance.states.get(59)
        if filtering is None or not filtering.active:
            continue
        authored = {int(tag, 16) for tag in filtering.node.field('m_F60AA143') or ()}
        if tags & authored and filtering.evaluate(filtering.node.field('m_50530EAB'), watch=False) == 0:
            return True
    return False
