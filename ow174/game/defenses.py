"""Scoped reconstructed consumers of authored defensive ability lifetimes.

The ordinary VM owns activation, expiry and retirement. These combat policies
are not a general native CombatModFilter implementation or client acceptance.
"""

import math

from ow174.game.script import expr

ORISA = 0x02E000000000013E
ZENYATTA = 0x02E0000000000020


def active_state(owner, hero, graph, number):
    identity = getattr(owner, "hero", None)
    if getattr(identity, "guid", identity) != hero:
        return None
    script = getattr(owner, "body_script", None)
    if script is None or getattr(script, "retired", False) or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == graph:
            state = instance.states.get(number)
            if state is not None and state.active:
                return state
    return None


def fortify_scale(owner):
    state = active_state(owner, ORISA, 0x0CF4, 4)
    if state is None:
        return None
    value = state.evaluate(state.node.field("m_50530EAB"), watch=False)
    if expr.kind(value) not in ("int", "float") or not math.isfinite(value):
        return None
    return max(0.0, min(1.0, 1.0 + value))


def invulnerable(owner):
    # Transcendence remains a solid projectile target; it does not phase bodies.
    return active_state(owner, ZENYATTA, 0x017B, 3) is not None


def advance_entry(owner):
    """Reconstruct only Zen017B/st13's zero-duration, output-free callback.

    Native54FEB21C also has engine outputs; this authored node uses none. Its
    end plug enters the ordinary six-second Wait. No other callback is enabled.
    """
    state = active_state(owner, ZENYATTA, 0x017B, 13)
    if state is None or state.node.cls != "STU_54FEB21C":
        return
    if state.evaluate(state.node.field("m_ED97A97C"), watch=False) != 0:
        raise ValueError("unsupported Transcendence entry duration")
    state.request_finish()
    state.instance.update()
