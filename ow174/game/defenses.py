"""Bounded defensive-cast consumers; native damage/geometry fidelity stays open.

017B (Transcendence) and05FE (Cryo-Freeze) enter a D07EBF01 owner scope.
The client capture supplies only that state's passive parent implementation,
not its authoritative damage consumer. Self damage immunity in those exact
scopes is a server reconstruction. It is not a generic meaning for D07EBF01.
"""

import math

from ow174.game.script import expr

ZENYATTA = 0x02E0000000000020
GENJI = 0x02E0000000000029
DEFLECT_GRAPH = 0x03A3
MEI = 0x02E00000000000DD
ORISA = 0x02E000000000013E
# hero -> graph, zero-duration entry, owner protection state
SELF_IMMUNITY = {ZENYATTA: (0x017B, 13, 8), MEI: (0x05FE, 41, 17)}


def deflect_state(actor):
    if getattr(getattr(actor, "hero", None), "guid", None) != GENJI:
        return None
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == DEFLECT_GRAPH:
            state = instance.states.get(10)
            return state if state is not None and state.active and not state.ending else None
    return None


def _scope(actor):
    hero = getattr(getattr(actor, "hero", None), "guid", None)
    spec = SELF_IMMUNITY.get(hero)
    script = getattr(actor, "body_script", None)
    if (spec is None or script is None or script.retired or script.component.owner_dead
            or script.hero != hero):
        return None, None
    instance = next((i for i in script.component.instances.values() if i.graph.index == spec[0]), None)
    return instance, spec


def advance_entry(actor) -> None:
    """Reconstruct the server's zero-delay callback for two explicit cast entries.

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


def invulnerable(actor) -> bool:
    """The protected body still collides and intercepts shots; only damage rejects."""
    from ow174.game import meteor_strike

    if meteor_strike.protected(actor):
        return True
    instance, spec = _scope(actor)
    if instance is None:
        return False
    state = instance.states.get(spec[2])
    return bool(state is not None and state.active and state.node.cls == "STU_D07EBF01"
                and state.node.field("m_target") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0})


def healing(actor):
    """Read the two authored healing profiles while their owner scope is active.

    The server interprets amount as total healing over duration. Native rate
    integration, filter flags, alternate Zen modes and activation restoration
    remain unverified. Zen's unimplemented TrackTargets uses a declared living
    friendly-body/capsule/LOS search in Combat; its VM target list is not forged.
    """
    instance, spec = _scope(actor)
    if instance is None or not invulnerable(actor):
        return None
    owner_scope = instance.states[spec[2]]
    mei = actor.hero.guid == MEI
    node = instance.graph.node(27 if mei else 12)
    if mei:
        state = instance.states.get(node.state)
        if state is None or not state.active:
            return None
        radius, offset = 0.0, (0.0, 0.0, 0.0)
    else:
        tracker = instance.states.get(0)
        if tracker is None or not tracker.active:
            return None
        radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        position = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
        if not isinstance(position, expr.Vec3):
            raise ValueError("unsupported Transcendence healing offset")
        offset = (position.x, position.y, position.z)
    amount = expr.to_float(instance.evaluate(node.field("m_modifyHealth")["m_amount"]))
    duration = expr.to_float(instance.evaluate(node.field("m_95F1FB0B")))
    if (not all(math.isfinite(value) for value in (amount, duration, radius, *offset))
            or amount < 0 or duration <= 0 or radius < 0):
        raise ValueError("invalid defensive healing profile")
    return {
        "activation": (instance.id, owner_scope.counter, owner_scope.start),
        "duration": duration, "rate": amount / duration,
        "self_only": mei, "radius": radius, "offset": offset,
    }
