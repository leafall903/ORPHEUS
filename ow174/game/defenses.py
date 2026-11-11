"""Combat consumers of authored defensive ability states."""

import math

from ow174.game.script import expr

ORISA = 0x02E000000000013E
ZENYATTA = 0x02E0000000000020
MEI = 0x02E00000000000DD
REAPER = 0x02E0000000000002
MOIRA = 0x02E00000000001A2
PHASES = {REAPER: (0x0020, 17, 8440), MOIRA: (0x0F7C, 28, 6604)}


def active_state(owner, hero, graph, index):
    identity = getattr(owner, "hero", None)
    if getattr(identity, "guid", identity) != hero:
        return None
    script = getattr(owner, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == graph:
            state = instance.states.get(index)
            if state is not None and state.active:
                return state
    return None


def _fortify(owner):
    return active_state(owner, ORISA, 0x0CF4, 4)


def _transcendence(owner):
    return active_state(owner, ZENYATTA, 0x017B, 3)


def _cryo(owner):
    return active_state(owner, MEI, 0x05FE, 6)


def phased(owner):
    """Live Wraith/Fade stack, distinct from the longer action-recovery flag."""
    identity = getattr(owner, "hero", None)
    hero = getattr(identity, "guid", identity)
    spec = PHASES.get(hero)
    if spec is None:
        return False
    graph, index, variable = spec
    state = active_state(owner, hero, graph, index)
    if state is None:
        return False
    flag = state.instance.component.entity_var(variable, create=False)
    return flag is not None and expr.truthy(flag.value())


def damage_scale(owner):
    if phased(owner) or _transcendence(owner) is not None or _cryo(owner) is not None:
        return 0.0
    fortify = _fortify(owner)
    if fortify is not None:
        value = fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False)
        if expr.kind(value) in ("int", "float") and math.isfinite(value):
            return max(0.0, min(1.0, 1.0 + value))
    return 1.0


def control_immune(owner):
    return (phased(owner) or _fortify(owner) is not None
            or _transcendence(owner) is not None or _cryo(owner) is not None)
