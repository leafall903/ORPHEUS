"""Authored Genji Deflect geometry with reconstructed pose, filter and response."""

import math

from ow174.game.script import expr, runtime

GENJI = 0x02E0000000000029
GRAPH = 0x03A3
MAX_REFLECTIONS = 8  # host cycle bound
# Exact 104319 05FF.003 metadata. Unknown definitions require extracted tags.
PROJECTILE_TAGS = {0x04000000000005FF: frozenset({0x0D600000000002F9})}
EXCLUDED_TAG = 0x0D60000000000145


def admitted(definition):
    tags = PROJECTILE_TAGS.get(definition)
    return tags is not None and EXCLUDED_TAG not in tags


def basis(yaw, pitch):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return (cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp)


def box_hit(origin, direction, first, second, reach):
    enter, leave = 0.0, reach
    for point, axis, low, high in zip(origin, direction, first, second, strict=True):
        if not all(math.isfinite(v) for v in (point, axis, low, high)):
            return None
        if low > high:  # original COMISS/JBE ordering, including reversed side boxes
            low, high = high, low
        if abs(axis) < 1e-12:
            if point < low or point > high:
                return None
            continue
        a, b = (low - point) / axis, (high - point) / axis
        enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
        if enter > leave:
            return None
    return enter


def window(instance, at_tick):
    state, wait = instance.states.get(10), instance.states.get(1)
    if (state is None or not state.active or state.node.cls != 'STUStatescriptStateDeflectProjectiles'
            or wait is None or not wait.active or wait.node.pos != 7):
        return None
    now = at_tick * instance.component.frame_ms
    # One command bucket admits authored sub-frame activation. Deadline is exact;
    # no owner command is synthesized to keep a stale state alive.
    if now + instance.component.frame_ms - 1 < state.start or now >= wait.start + wait.ms:
        return None
    return state


def intercept(state, eye, yaw, pitch, origin, direction, reach):
    axes = basis(yaw, pitch)
    angle = expr.to_float(state.evaluate(state.node.field('m_025D0C39'), watch=False))
    incoming = -sum(a * b for a, b in zip(direction, axes[2], strict=True))
    if not math.isfinite(angle) or incoming <= math.cos(math.radians(angle)):
        return None
    relative = tuple(a - b for a, b in zip(origin, eye, strict=True))
    local = tuple(sum(a * b for a, b in zip(relative, axis, strict=True)) for axis in axes)
    toward = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in axes)
    closest = None
    for row in state.node.field('m_boxes') or ():
        first = state.evaluate(row.get('m_CBA118B2'), watch=False)
        second = state.evaluate(row.get('m_AEF67365'), watch=False)
        if not isinstance(first, expr.Vec3) or not isinstance(second, expr.Vec3):
            continue
        found = box_hit(local, toward, first, second, reach)
        if found is not None and (closest is None or found < closest):
            closest = found
    return (closest, axes[2]) if closest is not None else None


class MeleeContext(runtime.InstanceContext):
    """Reconstructed attack origin and defender pose services for node 107."""

    def __init__(self, instance, target, source):
        super().__init__(instance, None)
        self.target, self.source = target, source

    def parameter_result(self, var, *, initial=None):
        if var == 8:
            return True, expr.Vec3(*self.source)
        return super().parameter_result(var, initial=initial)

    def position(self, entity):
        if entity == self.target.entity:
            return expr.Vec3(*self.target.feet)
        return super().position(entity)

    def native(self, cfg):
        if (cfg.get('$') == 'STU_021D01CD'
                and expr.to_entity(expr.evaluate(cfg.get('m_entity'), self)) == self.target.entity):
            mover = self.target.owner.mover
            return expr.Vec3(*basis(mover.yaw, mover.pitch)[2])
        return super().native(cfg)


def melee_tags(attack):
    modify = attack.node.field('m_modifyHealth') or {}
    return frozenset(value.guid for row in modify.get('m_148F3152') or ()
                     if isinstance(value := attack.evaluate(row.get('m_EE3055BD'), watch=False), expr.Asset))


def blocks_melee(source, target, tags, tick):
    """Apply the authored zero multiplier to matching frontal melee."""
    owner = target.owner
    if getattr(getattr(owner, "hero", None), "guid", None) != GENJI:
        return False
    script = owner.body_script
    if script is None or script.retired:
        return False
    component = script.component
    for instance in component.instances.values():
        if instance.graph.index != GRAPH or instance.stopped:
            continue
        if window(instance, tick) is None:
            continue
        node = instance.graph.node(107)
        if node is None or node.cls != "STUStatescriptStateCombatModFilter":
            continue
        guard = instance.states.get(node.state)
        if guard is None or not guard.active:
            continue
        included = {expr.guid_of(t) for t in node.field('m_F60AA143') or ()}
        if (expr.guid_of(node.field('m_60703B1E')) == 0x0D80000000000318 and tags & included
                and guard.evaluate(node.field('m_50530EAB'), watch=False) == 0
                and expr.truthy(expr.evaluate((node.field('m_6AA5D70F') or {}).get('m_condition'),
                                             MeleeContext(instance, target, source)))):
            return True
    return False
