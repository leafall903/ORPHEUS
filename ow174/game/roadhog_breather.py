"""Hosted self-heal and incoming damage for the 104319 Breather graph."""

import math

from ow174.game import ultimate
from ow174.game.script import expr

HERO = 0x02E0000000000040
GRAPH = 0x0240


def selected(state):
    return (state.instance.graph.index, state.node.pos) == (GRAPH, 18)


def heal(world, state, elapsed_ms):
    from ow174.game.combat import damage_tags

    if world.match is None or elapsed_ms <= 0 or state.instance.component.owner_dead:
        return
    target_cfg = state.node.field("m_FA3ECC43") or {}
    modify = state.node.field("m_modifyHealth") or {}
    if target_cfg.get("$") != "STU_B5A0CAF0" or modify.get("m_93CDFD2F") != 0:
        return
    duration = round(1000 * expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False)))
    amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
    already = max(0, state.now - state.start - elapsed_ms)
    portion = min(elapsed_ms, duration - already)
    if portion <= 0 or amount <= 0:
        return
    fight = world.match.combat
    target = next((item for item in fight._targets() if item.entity == state.instance.component.entity), None)
    if target is not None:
        given = fight._heal(target.owner, target, amount * portion / duration)
        ultimate.credit_healing(target.owner, target, given, damage_tags(state, modify))


def deadline(player, tick, tick_ms):
    component = player.body_script.component
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            wait = instance.states.get(1)
            if wait is not None and wait.active:
                remaining = max(0, wait.start + wait.ms - component.now)
                return tick + math.ceil(remaining / tick_ms)
    return None


def damage_scale(owner):
    script = getattr(owner, "body_script", None)
    if script is not None:
        scale = script.component.entity_var(8243, create=False)
        if scale is not None and scale.value() == 0.5:
            return 0.5
    return 1.0
