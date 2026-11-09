"""Bounded defensive-cast consumers; native damage/geometry fidelity stays open.

017B (Transcendence) and05FE (Cryo-Freeze) enter a D07EBF01 owner scope.
The client capture supplies only that state's passive parent implementation,
not its authoritative damage consumer. Self damage immunity in those exact
scopes is a server reconstruction. Reaper 0020 uses its authored wait as the
intangible window, with a separate reconstructed target filter.
"""

import math

from ow174.game import reaper_shadow
from ow174.game.script import expr, nodes

REAPER = 0x02E0000000000002
ZENYATTA = 0x02E0000000000020
MEI = 0x02E00000000000DD
ORISA = 0x02E000000000013E
# hero -> graph, zero-duration entry, owner protection state
CAST_SCOPES = {REAPER: (0x0020, 1, 3), ZENYATTA: (0x017B, 13, 8), MEI: (0x05FE, 41, 17)}


def _scope(actor):
    hero = getattr(getattr(actor, "hero", None), "guid", None)
    spec = CAST_SCOPES.get(hero)
    script = getattr(actor, "body_script", None)
    if (spec is None or script is None or script.retired or script.component.owner_dead
            or script.hero != hero):
        return None, None
    instance = next((i for i in script.component.instances.values() if i.graph.index == spec[0]), None)
    return instance, spec


def advance_entry(actor) -> None:
    """Reconstruct the server's zero-delay callback for explicit cast entries.

    Native8E5A90 defaults absent duration to0, rounds seconds to milliseconds,
    exports random/context values and queues timer1. Its client timer8E5F50
    does not unconditionally finish. This is an authoritative server callback
    bridge, not an implementation of that complete native class/interface.
    Only these no-output, no-auxiliary-scope entries are accepted.
    """
    instance, spec = _scope(actor)
    if instance is None:
        return
    state = instance.states.get(spec[1])
    if state is None or not state.active or state.node.cls != "STU_54FEB21C":
        return
    if (expr.to_float(state.evaluate(state.node.field("m_ED97A97C"), watch=False)) != 0
            or any(state.node.field(name) is not None
                   for name in ("m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5"))):
        raise ValueError("unsupported defensive entry callback configuration")
    auxiliary = state.node.field("m_E5790891")
    if auxiliary and (auxiliary.get("links") or auxiliary.get("m_FF3DAF1E")):
        raise ValueError("unsupported defensive entry auxiliary scope")
    state.request_finish()
    instance.update()


def _wraith(state) -> bool:
    return bool(state is not None and state.active and state.node.cls == "STUStatescriptStateWait")


def intangible(actor) -> bool:
    if getattr(getattr(actor, "hero", None), "guid", None) != REAPER:
        return False
    instance, spec = _scope(actor)
    return (instance is not None and _wraith(instance.states.get(spec[2]))) or reaper_shadow.protected(actor)


def reaper_protection_states(actor):
    instance, spec = _scope(actor)
    wraith = instance.states.get(spec[2]) if instance is not None else None
    return (wraith, reaper_shadow.protection_state(actor))


def reaper_cast_needs_clock(actor) -> bool:
    if getattr(getattr(actor, "hero", None), "guid", None) != REAPER:
        return False
    instance, _ = _scope(actor)
    ability = instance.states.get(10) if instance is not None else None
    if ability is not None and (ability.flags & nodes.Ability.RUNNING or ability.remaining() > 0):
        return True
    return reaper_shadow.needs_clock(actor)


def invulnerable(actor) -> bool:
    """Reject damage during an active supported protection scope."""
    instance, spec = _scope(actor)
    if instance is None:
        return False
    state = instance.states.get(spec[2])
    if spec[0] == 0x0020:
        return _wraith(state) or reaper_shadow.protected(actor)
    return bool(state is not None and state.active and state.node.cls == "STU_D07EBF01"
                and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0})


def _fortify_component(actor, index):
    if getattr(getattr(actor, "hero", None), "guid", None) != ORISA:
        return None
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == 0x0CF4:
            state = instance.states.get(index)
            if state is not None and state.active:
                return script.component
    return None


def fortify_scale(actor) -> float | None:
    """Read the active 0CF4 damage stack. The host applies it before armor."""
    component = _fortify_component(actor, 5)
    if component is None:
        return None
    scale = component.entity_var(8243).value()
    if type(scale) not in (int, float):
        return None
    return scale if math.isfinite(scale) and 0 <= scale <= 1 else None


def fortify_force_immune(actor) -> bool:
    component = _fortify_component(actor, 9)
    return component is not None and expr.truthy(component.entity_var(9161).value())
