"""Resource-backed default Mei spray rules for the combat host."""

from ow174.game.script import expr

SPRAY_LOADOUT = 0x0B900000000000D3
INITIAL = expr.f32(0.2)
STEP = expr.f32(0.0285)
GAP_MS = 1000
FREEZE_MS = 1300


def immune(actor, predicates=(6604, 8440, 9161)):
    """Read the body predicates used by 0B77 and its area caller."""
    component = getattr(getattr(actor, "body_script", None), "component", None)
    return component is not None and any(
        (var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
        for key in predicates
    )


def projectile_target_limit(state):
    if state.instance.graph.index != 0x05E6 or state.index != 13:
        return 1
    value = state.node.field("m_6396149F.m_B4454CD7")
    return max(1, min(99, expr.to_int(state.evaluate(value, watch=False)))) if value else 1
