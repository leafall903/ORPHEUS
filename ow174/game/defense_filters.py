"""Scoped timing and quick-melee filters for authored defenses.

Resource facts: parameter0318, multiplier0, tags03EF/0302/20EC and the
unchanged positive-dot condition using parameter8 and owner forward.
Matching any listed tag and binding parameter8 to the authored attack origin
are reconstructed host policies, not a general native damage-filter engine.
"""

import math

from ow174.game.script import expr, runtime


def last_countdown_world_tick(state):
    """Project the fixed-rate countdowns in 017B and 1D02 onto the world clock."""
    initial = expr.to_float(state.evaluate(state.node.field("m_EB0FC261"), watch=False))
    rate = expr.to_float(state.evaluate(state.node.field("m_9B0385B4"), watch=False))
    if rate <= 0:
        return math.inf
    # World combat precedes the body command that ends the countdown.
    end_ms = state.start + initial/rate*1000
    return math.ceil(end_ms/state.instance.component.frame_ms) - 1


def last_projectile_guard_world_tick(instance):
    if instance.graph.index == 0x03A3:
        wait = instance.states.get(instance.graph.node(7).state)
        if wait is not None and wait.active:
            return (wait.start + wait.ms) // instance.component.frame_ms
    if instance.graph.index == 0x1D02:
        chase = instance.states.get(instance.graph.node(45).state)
        if chase is not None and chase.active:
            return last_countdown_world_tick(chase)
    return None


def projectile_guard_deadline_in_gap(players, previous, tick):
    for player in players:
        component = getattr(getattr(player, "body_script", None), "component", None)
        if not isinstance(component, runtime.Component):
            continue
        for instance in component.instances.values():
            end = last_projectile_guard_world_tick(instance)
            if end is not None and previous <= end < tick:
                return True
    return False


class MeleeContext(runtime.InstanceContext):
    def __init__(self, instance, target, source):
        super().__init__(instance, None)
        self.target = target
        self.source = source

    def parameter_result(self, var, *, initial=None):
        if var == 8:
            #0043 ModifyHealth.m_C0139FA6 is the source owner's position.
            return True, expr.Vec3(*self.source.mover.position)
        return super().parameter_result(var, initial=initial)

    def position(self, entity):
        if entity == self.target.entity:
            return expr.Vec3(*self.target.feet)
        return super().position(entity)

    def native(self, cfg):
        if cfg.get('$') == 'STU_021D01CD':
            entity = expr.to_entity(expr.evaluate(cfg.get('m_entity'), self))
            if entity == self.target.entity:
                from ow174.game.combat import aim

                mover = self.target.owner.mover
                return expr.Vec3(*aim(mover.yaw, mover.pitch))
        return super().native(cfg)


def blocks_melee(source, target, state):
    if state.instance.graph.index != 0x0043:
        return False
    component = getattr(getattr(target.owner, 'body_script', None), 'component', None)
    if not isinstance(component, runtime.Component) or getattr(target.owner, 'mover', None) is None:
        return False
    modify = state.node.field('m_modifyHealth') or {}
    tags = {value.guid for row in modify.get('m_148F3152') or ()
            if isinstance(value := state.evaluate(row.get('m_EE3055BD'), watch=False), expr.Asset)}
    for instance in component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        node = instance.graph.node(107)
        guard = instance.states.get(node.state) if node is not None else None
        if (guard is None or not guard.active or node.cls != 'STUStatescriptStateCombatModFilter'
                or node.field('m_60703B1E') != '0x0D80000000000318'
                or expr.to_float(guard.evaluate(node.field('m_50530EAB'), watch=False)) != 0):
            continue
        included = {int(value, 16) for value in node.field('m_F60AA143') or ()}
        condition = (node.field('m_6AA5D70F') or {}).get('m_condition')
        if tags & included and expr.truthy(expr.evaluate(condition, MeleeContext(instance, target, source))):
            return True
    return False
