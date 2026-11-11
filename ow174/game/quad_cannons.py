"""Build 104319 Quad Cannons cone and selected damage identity.

Use the authored 1514/24 spread ramp with the captured uniform table.
The server supplies aim, unit aspect and body/graph/state/activation seed words.
"""

import math

from ow174.game import weapon_spread
from ow174.game.piledriver import HERO
from ow174.game.script import expr

GRAPH, VOLLEY = 0x1514, 24
DEFINITION = 0x0400000000002584
LOADOUT = 0x0B90000000000189
GENJI = 0x02E0000000000029


def selected(state):
    return (state.instance.graph.index, state.node.pos) == (GRAPH, VOLLEY)


def direction(shooter, state, forward, number, time, activation):
    launch = state.node.field("m_5EC1EB38")
    if launch["$"] != "STU_239B571E" or state.spread_ramp is None:
        return forward
    low, high = (expr.f32(expr.to_float(state.evaluate(launch[name], watch=False)))
                 for name in ("m_7F0FED02", "m_429A6C85"))
    normalized = state.spread_at(time - activation[1] - state.offset)
    degrees = expr.f32(expr.f32(expr.f32(high - low) * normalized) + low)
    cone = expr.f32(degrees * expr.f32(math.pi / 180))
    base = weapon_spread.seed_base(shooter.body, state.instance.id, state.node.pos, activation[0])
    return weapon_spread.circular_direction(forward, cone, base, number)
