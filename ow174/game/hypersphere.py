"""Authored Sigma Hypersphere launch settings."""

from ow174.game.script import expr


def hypersphere_motion(state):
    """Read both stock Hypersphere launch rows for the scoped sphere host."""
    node = state.node
    if state.instance.graph.index != 0x187F or node.pos not in (45, 56):
        return None
    bounce = node.field("m_E365DB20") or {}
    motions = node.field("m_projectileMotions") or ()
    if (bounce.get("$") != "STU_3FE1A93B" or len(motions) != 1
            or motions[0].get("$") != "STU_E1A5B579"):
        return None

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))

    hit = node.field("m_6396149F")
    return (value(motions[0], "m_599AB8D7"),
            value(hit, "m_57DAE1B5"), value(hit, "m_1FFD8312"),
            expr.to_int(state.evaluate(bounce.get("m_360294F0"), watch=False)),
            value(bounce, "m_0D20DAFD"), value(bounce, "m_C29D35E8"))
