"""Original01C6/33 force/rings on the existing shared impulse mover."""

import math
from dataclasses import dataclass
from itertools import pairwise
from ow174.game.script import expr

@dataclass(frozen=True)
class Spec:
    force: float
    cap: float
    lift: float
    lift_cap: float
    rings: tuple

    def scale(self, distance):
        previous = self.rings[0]
        if distance <= previous[0]:
            return previous[1:]
        for ring in self.rings[1:]:
            if distance <= ring[0]:
                share = (distance-previous[0])/max(1e-9, ring[0]-previous[0])
                return tuple(a+(b-a)*share for a, b in zip(previous[1:], ring[1:], strict=True))
            previous = ring
        return (0.0, 0.0, 0.0)


def evaluate(state, modify):
    instance = getattr(state, 'instance', None)
    definition = getattr(instance, 'graph', None)
    identity = getattr(definition, 'index', None), getattr(getattr(state, 'node', None), 'pos', None)
    if identity != (0x1C6, 33):
        return None

    def number(config):
        value = expr.to_float(state.evaluate(config, watch=False))
        if not math.isfinite(value) or value < 0:
            raise ValueError('invalid Whole Hog force input')
        return value

    values = [number(modify[k]) for k in ('m_E33EFEE1', 'm_10399AE7', 'm_583C208B', 'm_8394CF8C')]
    rings = tuple(sorted(tuple(number(r[k]) for k in ('m_radius', 'm_9102DA3F', 'm_579D7CFF',
                                                      'm_10399AE7'))
                         for r in modify['m_knockbackRings']))
    if not rings or any(b[0] <= a[0] for a, b in pairwise(rings)):
        raise ValueError('invalid Whole Hog force rings')
    return Spec(*values, rings)



@dataclass
class Motion:
    owner: object
    body: int
    velocity: tuple
    tick: int
    npc_mover: object = None
