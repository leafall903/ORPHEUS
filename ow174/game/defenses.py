"""Scoped defensive admission from live build104319 state graphs.

Damage scaling, displacement admission and the Zen entry callback are host
bindings for these authored states. Native filter and force services remain open.
"""

import math

from ow174.game import phase
from ow174.game.script import expr

ORISA = 0x02E000000000013E
ZENYATTA = 0x02E0000000000020


def active_state(owner, hero, graph, number, *, tick=None):
    identity = getattr(owner, "hero", None)
    if getattr(identity, "guid", identity) != hero:
        return None
    script = getattr(owner, "body_script", None)
    if (script is None or getattr(script, "retired", True) or script.entity != owner.body or owner.gone
            or script.component.owner_dead or script.component.fault is not None
            or not phase.window_at(script.world, tick)):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == graph:
            state = instance.states.get(number)
            if state is not None and (script.world.phase_started is not None
                                      or state.active and not state.ending):
                return state
    return None


def fortify_state(owner, *, tick=None):
    return active_state(owner, ORISA, 0x0CF4, 4, tick=tick)


def fortify_scale(owner, *, tick=None):
    state = fortify_state(owner, tick=tick)
    if state is None:
        return None
    value = state.evaluate(state.node.field("m_50530EAB"), watch=False)
    if expr.kind(value) not in ("int", "float") or not math.isfinite(value):
        return None
    return max(0.0, min(1.0, 1.0 + value))


def knockback_scale(owner, *, tick=None):
    """Live authored movement modifier26, using the mover's ordered operations.

    Use the known world window for Fortify. The remaining live modifier
    ordering and conversion to impulse resistance are host policies.
    """
    from ow174.game.mover import apply_op, statescript_mods

    script = getattr(owner, "body_script", None)
    if script is None or getattr(script, "retired", False):
        return 1.0
    component = script.component
    entries = []
    for key, (index, op, value, priority) in statescript_mods(component).items():
        if index != 26:
            continue
        instance_id, state, _ = key
        instance = component.instances[instance_id]
        if instance.graph.index == 0x0CF4 and not phase.window_at(script.world, tick):
            continue
        entries.append((priority, instance.states[state].start, key, op, value))
    result = 1.0
    for _, _, _, op, value in sorted(entries):
        result = apply_op(op, result, value)
    return max(0.0, result) if math.isfinite(result) else 0.0


def invulnerable(owner, *, tick=None):
    # Transcendence remains a solid projectile target; it does not phase bodies.
    return active_state(owner, ZENYATTA, 0x017B, 3, tick=tick) is not None


def begin_compatible_state(world, state):
    """Drive the scoped Zen entry and use both authored ChaseVars on the world clock."""
    if state.instance.component.entity != world.body:
        return
    graph = state.instance.graph.index
    key = (world.hero, graph, state.node.pos)
    if key == (ZENYATTA, 0x017B, 26) and state.node.cls == "STU_54FEB21C":
        if state.evaluate(state.node.field("m_ED97A97C"), watch=False) != 0:
            raise ValueError("unsupported Transcendence entry duration")
        state.request_finish()
        chase = state.instance.graph.node(8)
    elif key == (ORISA, 0x0CF4, 10) and state.node.cls == "STUStatescriptStateCombatModFilter":
        chase = state.instance.graph.node(6)
    else:
        return
    if world.match is not None:
        from ow174.game.combat import TICK

        start = expr.to_float(state.evaluate(chase.field("m_EB0FC261"), watch=False))
        end = expr.to_float(state.evaluate(chase.field("m_destination"), watch=False))
        world.phase_started = world.match.tick
        world.phase_deadline = world.phase_started + math.ceil(max(0.0, start - end) / TICK)


def end_compatible_state(world, state, finished: bool) -> None:
    key = (world.hero, state.instance.graph.index, state.node.pos)
    # Fortify's filter is a child of node 6; the timer owns its natural end.
    if key in ((ORISA, 0x0CF4, 6), (ZENYATTA, 0x017B, 8)):
        phase.end_world_window(world, state, finished)
