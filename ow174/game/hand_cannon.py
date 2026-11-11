"""Hand Cannon's selected 0D0A volley pattern for build 104319."""

import math

from ow174.game import weapon_spread
from ow174.game.script import expr

GRAPH = 0x0D0A
HERO = 0x02E000000000012F
LOADOUT = 0x0B90000000000109


def selected(state):
    return state.instance.graph.index == GRAPH and state.index == 2


def directions(state, forward, body, shot, activation):
    config = state.node.field("m_5EC1EB38") or {}
    if config.get("$") != "STU_613BDDB0":
        return (forward,)

    def number(cfg):
        return expr.to_float(state.evaluate(cfg, watch=False))

    spread = number(config.get("m_spread"))
    jitter = number(config.get("m_92308789"))
    count = number(state.node.field("m_numProjectilesPerShot"))
    rings = []
    for ring in config.get("m_0D30E40C") or ():
        radius = number(ring.get("m_6425FFB9"))
        pellets = number(ring.get("m_B19BD35D"))
        if not math.isfinite(radius) or radius < 0 or not 0 < pellets <= 64 or not pellets.is_integer():
            return (forward,)
        rings.append((radius, int(pellets)))
    if not (0 <= spread < 90 and 0 <= jitter < 90) or sum(n for _, n in rings) != count:
        return (forward,)
    # Reconstructed server binding of the native identity operands.
    base = weapon_spread.seed_base(body, state.instance.id, state.node.pos, activation[0])
    return weapon_spread.radial_directions(forward, spread, jitter, rings, base, shot)
