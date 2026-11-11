"""Scoped damage and control consumers of Orisa's authored Fortify state."""

import math

from ow174.game.script import expr
from ow174.game.transcendence import active_state

HERO = 0x02E000000000013E


def scale(owner):
    state = active_state(owner, HERO, 0x0CF4, 4)
    if state is None:
        return None
    value = state.evaluate(state.node.field("m_50530EAB"), watch=False)
    if expr.kind(value) not in ("int", "float") or not math.isfinite(value):
        return None
    return max(0.0, min(1.0, 1.0 + value))
