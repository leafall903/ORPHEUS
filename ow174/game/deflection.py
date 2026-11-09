"""Scoped Genji/Bastion server reconstruction using authored Deflect boxes.

XYZ endpoint ordering matches original D5BED4..D5BF39. Eye/yaw/pitch placement
and outgoing aim are host policy; native transforms/tag/angular filters and
projectile presentation are not established by that bounded comparison.
"""

import math

from ow174.game.script import expr


def basis(yaw, pitch):
    yaw, pitch = yaw * 2 * math.pi / 65536, pitch * 2 * math.pi / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return (cy, 0.0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp)


def box_hit(origin, direction, low, high, reach):
    enter, leave = 0.0, reach
    for point, axis, first, second in zip(origin, direction, low, high, strict=True):
        if not all(math.isfinite(value) for value in (point, axis, first, second)):
            return None
        # Strict comparison preserves equal/unordered sides as in COMISS/JBE.
        if first > second:
            first, second = second, first
        if abs(axis) < 1e-12:
            if point < first or point > second:
                return None
            continue
        a, b = (first - point) / axis, (second - point) / axis
        if a > b:
            a, b = b, a
        enter, leave = max(enter, a), min(leave, b)
        if enter > leave:
            return None
    return enter


def intercept(state, eye, yaw, pitch, origin, direction, reach):
    axes = basis(yaw, pitch)
    relative = tuple(a - b for a, b in zip(origin, eye, strict=True))
    local_origin = tuple(sum(a * b for a, b in zip(relative, axis, strict=True)) for axis in axes)
    local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in axes)
    closest = None
    for row in state.node.field('m_boxes') or ():
        if not isinstance(row, dict):
            continue
        first = state.evaluate(row.get('m_CBA118B2'), watch=False)
        second = state.evaluate(row.get('m_AEF67365'), watch=False)
        if not isinstance(first, expr.Vec3) or not isinstance(second, expr.Vec3):
            continue
        hit = box_hit(local_origin, local_direction, first, second, reach)
        if hit is not None and (closest is None or hit < closest):
            closest = hit
    return closest, axes[2]


class _MeleeContext(expr.Context):
    """Declared host binding for03A3/st59's event position and facing query.

    Native parameter8 population and animated transform equivalence remain open.
    This context is temporary; it does not write a component variable or flag.
    """

    def __init__(self, owner, position, source, forward):
        super().__init__()
        self.entity = owner
        self.feet, self.source, self.forward = expr.Vec3(*position), expr.Vec3(*source), expr.Vec3(*forward)

    def owner(self):
        return expr.Entity(self.entity)

    def position(self, entity):
        return self.feet if entity == self.entity else expr.Vec3(0, 0, 0)

    def param(self, identifier):
        return self.source if identifier == 8 else None

    def native(self, cfg):
        if cfg.get('$') == 'STU_021D01CD' and expr.evaluate(cfg.get('m_entity'), self) == self.owner():
            return self.forward
        return expr.EVALUATION_FAILED


def blocks_melee(state, owner, position, source, yaw, pitch):
    """Scoped zero modifier plus its actual predicate, not generic immunity."""
    if (state.node.cls != 'STUStatescriptStateCombatModFilter'
            or state.node.field('m_41B10FF2') != 1):
        return False
    cfg = state.node.field('m_50530EAB')
    amount = state.evaluate(cfg, watch=False) if cfg is not None else None
    if not isinstance(amount, (int, float)) or amount != 0:
        return False
    condition = (state.node.field('m_6AA5D70F') or {}).get('m_condition')
    if not isinstance(condition, dict):
        return False
    context = _MeleeContext(owner, position, source, basis(yaw, pitch)[2])
    return expr.evaluate(condition, context) is True
