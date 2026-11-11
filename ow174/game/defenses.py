"""Static combat responses to authored defensive cast scopes."""

from ow174.game.script import expr

REAPER = 0x02E0000000000002
ZENYATTA = 0x02E0000000000020
MEI = 0x02E00000000000DD
ORISA = 0x02E000000000013E

# Hero -> graph, zero-duration entry, protection state.
SCOPES = {REAPER: (0x0020, 1, 3), ZENYATTA: (0x017B, 13, 8), MEI: (0x05FE, 41, 17)}


def _scope(script):
    hero = getattr(script, "hero", None)
    spec = SCOPES.get(hero)
    if (spec is None or script.retired or script.component.owner_dead
            or script.component.fault is not None):
        return None, None
    instance = next((i for i in script.component.instances.values() if i.graph.index == spec[0]), None)
    return instance, spec


def advance_entry(script):
    """Complete only the authored zero-delay server cast entries."""
    instance, spec = _scope(script)
    if instance is None:
        return
    state = instance.states.get(spec[1])
    if state is None or not state.active or state.ending or state.node.cls != "STU_54FEB21C":
        return
    auxiliary = state.node.field("m_E5790891") or {}
    if (expr.to_float(state.evaluate(state.node.field("m_ED97A97C"), watch=False)) != 0
            or auxiliary.get("links") or auxiliary.get("m_FF3DAF1E")
            or any(state.node.field(name) is not None
                   for name in ("m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5"))):
        raise ValueError("unsupported defensive entry callback configuration")
    state.request_finish()
    instance.update()


def active_fortify(actor):
    script = getattr(actor, "body_script", None)
    if getattr(script, "hero", None) != ORISA:
        return None
    component = script.component
    if script.retired or component.owner_dead or component.fault is not None:
        return None
    for instance in component.instances.values():
        if instance.graph.index == 0x0CF4:
            state = instance.states.get(4)
            if state is not None and state.active and not state.ending:
                return state
    return None


def protection(actor):
    """Reconstruct Wraith pass-through and the two owner-only immunity scopes."""
    script = getattr(actor, "body_script", None)
    from ow174.game import dva

    if getattr(script, "hero", None) == dva.HERO:
        component = script.component
        if script.retired or component.owner_dead or component.fault is not None:
            return None
        for instance in component.instances.values():
            if instance.graph.index != 0x026A:
                continue
            entry = instance.states.get(12)
            state = instance.states.get(24)
            if (entry is not None and entry.active and not entry.ending
                    and state is not None and state.active and not state.ending
                    and state.node.cls == "STU_D07EBF01"
                    and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0}):
                return "immune"
        return None
    instance, spec = _scope(script)
    if instance is None:
        return None
    state = instance.states.get(spec[2])
    if state is None or not state.active or state.ending:
        return None
    if script.hero == REAPER and state.node.cls == "STUStatescriptStateWait":
        return "intangible"
    if (state.node.cls == "STU_D07EBF01"
            and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0}):
        return "immune"
    return None
