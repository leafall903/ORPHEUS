"""Authored collision radii and ricochet coefficients for Sigma 187F."""
from dataclasses import dataclass

from ow174.game.script import expr

HERO = 0x02E000000000023B


@dataclass(frozen=True)
class Motion:
    body_radius: float
    wall_radius: float
    bounces_left: int
    normal: float
    tangent: float


def motion(state):
    node = state.node
    if state.instance.graph.index != 0x187F or node.pos not in (45, 56):
        return None
    bounce = node.field("m_E365DB20") or {}
    if bounce.get("$") != "STU_3FE1A93B":
        return None
    hit = node.field("m_6396149F")

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))

    return Motion(
        value(hit, "m_57DAE1B5"), value(hit, "m_1FFD8312"),
        expr.to_int(state.evaluate(bounce.get("m_360294F0"), watch=False)),
        value(bounce, "m_0D20DAFD"), value(bounce, "m_C29D35E8"),
    )
