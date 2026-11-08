"""Scoped defensive casts adapted from Reaper/Pharah lanes.

Native server filters and phase collision are not recovered. Immune solid
bodies intercept shots; mapped ghost phases let them pass. These reconstructed
policies assign no generic meaning to D07EBF01 or10467 outside four casts.
"""

from ow174.game.script import expr

# hero -> (graph, entry state, protection state or10467 stack, intangible)
PROTECTIONS = {
    0x02E0000000000002: (0x0020, 1, None, True),
    0x02E00000000001A2: (0x0F7C, 1, None, True),
    0x02E00000000000DD: (0x05FE, 41, 17, False),
    0x02E0000000000020: (0x017B, 13, 8, False),
}


def _scope(actor):
    hero = getattr(getattr(actor, "hero", None), "guid", None)
    spec = PROTECTIONS.get(hero)
    script = getattr(actor, "body_script", None)
    if (spec is None or script is None or script.retired or script.component.owner_dead
            or script.hero != hero):
        return None, None
    return next((i for i in script.component.instances.values() if i.graph.index == spec[0]), None), spec


def advance_entry(actor) -> None:
    """Bridge four zero-delay entries without auxiliary/output scopes."""
    instance, spec = _scope(actor)
    if instance is None or instance.component.strict:
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


def protection(actor) -> tuple[bool, bool]:
    """(immune, intangible) from the exact live authored protection scope."""
    instance, spec = _scope(actor)
    if instance is None:
        return False, False
    if spec[2] is not None:
        state = instance.states.get(spec[2])
        active = bool(state is not None and state.active and state.node.cls == "STU_D07EBF01"
                      and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0})
    else:
        slot = instance.component.entity_var(10467, create=False)
        active = bool(slot is not None and expr.truthy(slot.value()) and any(
            state.active and state.node.cls == "STUStatescriptStateStack"
            and expr.lvalue(state.node.field("m_out_Var")) == (1, 10467)
            for state in instance.states.values()
        ))
    return active, active and spec[3]
