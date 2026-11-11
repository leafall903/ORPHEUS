"""Selected 0DD8 Rising Uppercut resource services for build 104319."""

import math

from ow174.game import doomfist_passive
from ow174.game.script import expr

GRAPH = 0x0DD8
TRACK = (GRAPH, 16)
HIT = (GRAPH, 17)


def targets(fight, state):
    from ow174.game.combat import capsule_visible, segment_distance

    caster = fight._cast_owner(state.instance)
    if caster is None:
        return ()
    centre = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    if (not isinstance(centre, expr.Vec3) or not all(map(math.isfinite, centre))
            or not math.isfinite(radius) or radius <= 0):
        return ()
    origin = fight._eye(caster)
    selected = []
    for target in fight._enemies(caster):
        a, b = target.segment()
        if (segment_distance(tuple(centre), a, b) <= radius + target.radius
                and capsule_visible(fight._collision(), origin, a, b, target.radius, walls=fight.walls)):
            selected.append(expr.Entity(target.entity))
    return tuple(selected)


def hit(fight, state):
    from ow174.game.combat import aim, hit_def

    caster = fight._cast_owner(state.instance)
    if caster is None:
        return
    selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if not isinstance(selected, tuple) or not selected:
        return
    definition = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
    if definition is None or definition.amount <= 0:
        return
    horizontal = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1"), watch=False))
    vertical = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_583C208B"), watch=False))
    dx, _, dz = aim(caster.mover.yaw, 0)
    vector = expr.Vec3(dx * horizontal, vertical, dz * horizontal)
    for target in fight._enemies(caster):
        if expr.Entity(target.entity) not in selected:
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        immune = fight.zarya_bubbles.for_target(target.owner) is not None
        fight._damage(caster, target, definition.amount, False, definition.loadout, fight.tick)
        if vitals.total < before:
            doomfist_passive.grant(fight, caster)
        if not immune:
            fight.impulses.vector(target, vector)
