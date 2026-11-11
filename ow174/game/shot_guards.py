"""Authored Genji and Sigma guard windows with reconstructed eye attachment."""

import math

from ow174.game.script import expr, runtime

GENJI = 0x02E0000000000029
SIGMA = 0x02E000000000023B
DVA = 0x02E000000000007A
MECH = 0x0400000000000407
MAX_RETURNS = 8
# Target-build 03DB and 08CE carry projectile tags and lack exclusion tag 0145.
ZARYA_GUARDED_PROJECTILES = frozenset((0x04000000000003DB, 0x04000000000008CE))
# Actual Enforcer 23F0 and Sniper 1E80 carry target tag 02F9 without 0145.
NPC_GUARDED_PROJECTILES = frozenset((0x04000000000023F0, 0x0400000000001E80))
SLEEP_DART = 0x04000000000015E8  # Ana 0BDD/14 projectile entity
BIOTIC_GRENADE = 0x0400000000001426  # Ana 0B97/1 projectile entity
ICICLE = 0x040000000000108B  # Mei 05E6/40 carries projectile tag 02F9
GUARDED_PROJECTILES = (
    ZARYA_GUARDED_PROJECTILES | NPC_GUARDED_PROJECTILES | {SLEEP_DART, BIOTIC_GRENADE, ICICLE}
)


def guard_hero(actor):
    hero = getattr(actor, "hero", None)
    return getattr(hero, "guid", hero)


def active_guard(player, now):
    hero = guard_hero(player)
    script = getattr(player, "body_script", None)
    if (hero not in (GENJI, SIGMA, DVA) or script is None
            or getattr(script, "retired", False) or script.component.owner_dead):
        return None
    if hero == DVA:
        if hasattr(player.hero, "body"):
            if player.hero.body != MECH:
                return None
        elif player.body != MECH or player.dva_mover is None:
            return None
    graph_index, state_index, clock_index = {
        GENJI: (0x03A3, 10, 1), SIGMA: (0x1D02, 1, 20), DVA: (0x02CC, 3, None),
    }[hero]
    for instance in script.component.instances.values():
        if instance.graph.index != graph_index:
            continue
        guard, clock = instance.states.get(state_index), instance.states.get(clock_index)
        if (guard is None or not guard.active or guard.ending
                or guard.node.cls != "STUStatescriptStateDeflectProjectiles"
                or guard.node.field("m_8B251D53") != int(hero != GENJI)
                or now + script.component.frame_ms - 1 < guard.start):
            continue
        if hero == DVA:
            return guard
        if clock is None or not clock.active:
            continue
        if hero == GENJI and now < clock.start + clock.ms:
            return guard
        if hero == SIGMA:
            remaining = clock.remaining
            if remaining is None:
                destination = expr.to_float(clock.evaluate(clock.node.field("m_destination"), watch=False))
                rate = expr.to_float(clock.evaluate(clock.node.field("m_9B0385B4"), watch=False))
                remaining = abs(clock.cur - destination) * 1000 / rate if rate > 0 else math.inf
            if now < clock.last + max(0, remaining):
                return guard
    return None


def intercept(state, eye, yaw, pitch, origin, direction, reach, *, margin=0.):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    axes = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
    if state.instance.graph.index != 0x02CC:
        angle = expr.to_float(state.evaluate(state.node.field("m_025D0C39"), watch=False))
        incoming = -sum(a * b for a, b in zip(direction, axes[2], strict=True))
        if not math.isfinite(angle) or incoming <= math.cos(math.radians(angle)):
            return None
    relative = tuple(a - b for a, b in zip(origin, eye, strict=True))
    local = tuple(sum(a * b for a, b in zip(relative, axis, strict=True)) for axis in axes)
    toward = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in axes)
    closest = None
    for box in state.node.field("m_boxes") or ():
        first = state.evaluate(box.get("m_CBA118B2"), watch=False)
        second = state.evaluate(box.get("m_AEF67365"), watch=False)
        if not isinstance(first, expr.Vec3) or not isinstance(second, expr.Vec3):
            continue
        enter, leave = 0.0, reach
        for point, axis, a, b in zip(local, toward, first, second, strict=True):
            if not all(math.isfinite(value) for value in (point, axis, a, b)):
                break
            low, high = min(a, b) - margin, max(a, b) + margin
            if abs(axis) < 1e-12:
                if not low <= point <= high:
                    break
                continue
            near, far = sorted(((low - point) / axis, (high - point) / axis))
            enter, leave = max(enter, near), min(leave, far)
            if enter > leave:
                break
        else:
            if closest is None or enter < closest:
                closest = enter
    return (closest, axes[2]) if closest is not None else None


def absorb(state, amount, source):
    instance = state.instance
    instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), expr.f32(amount))
    instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(source))
    instance.follow(state.node, "m_160B5DBE", state)
    instance.component.run_now()


class _MeleeContext(runtime.InstanceContext):
    def __init__(self, instance, target, source, forward):
        super().__init__(instance, None)
        self.target = target
        self.source = source
        self.forward = forward

    def parameter_result(self, var, *, initial=None):
        if var == 8:
            return True, expr.Vec3(*self.source)
        return super().parameter_result(var, initial=initial)

    def position(self, entity):
        if entity == self.target.entity:
            return expr.Vec3(*self.target.feet)
        return super().position(entity)

    def native(self, cfg):
        if (cfg.get("$") == "STU_021D01CD"
                and expr.to_entity(expr.evaluate(cfg.get("m_entity"), self)) == self.target.entity):
            return expr.Vec3(*self.forward)
        return super().native(cfg)


def parries_melee(fight, target, source, tags):
    """Evaluate Genji's authored 03A3/node107 melee filter."""
    from ow174.game.combat import TICK, aim

    defender = target.owner
    hero = getattr(defender, "hero", None)
    if getattr(hero, "guid", None) != GENJI or target.entity in fight.hacks or fight.is_stunned(defender):
        return False
    guard = active_guard(defender, fight.tick * TICK * 1000)
    if guard is None:
        return False
    instance = guard.instance
    node = instance.graph.node(107)
    state = instance.states.get(node.state)
    if state is None or not state.active or state.ending:
        return False
    included = {expr.guid_of(tag) for tag in node.field("m_F60AA143") or ()}
    if (expr.guid_of(node.field("m_60703B1E")) != 0x0D80000000000318
            or not included.intersection(tags)
            or expr.to_float(state.evaluate(node.field("m_50530EAB"), watch=False)) != 0):
        return False
    condition = (node.field("m_6AA5D70F") or {}).get("m_condition")
    forward = aim(defender.mover.yaw, defender.mover.pitch)
    return expr.truthy(expr.evaluate(condition, _MeleeContext(instance, target, source, forward)))
