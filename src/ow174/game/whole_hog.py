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
        if config is None:
            raise ValueError('missing Whole Hog force expression')
        value = state.evaluate(config, watch=False)
        if type(value) not in (int, float):
            raise ValueError('unresolved Whole Hog force expression')
        value = expr.to_float(value)
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


def volley_impulse(direction, distance, spec, *, current=(0.0, 0.0, 0.0)):
    """Bound the live recipient velocity, submitting only its additive delta.

    The shared receiver owns decay, landing and replay. Reading its actual
    velocity prevents repeated volleys from replacing the old capped channel
    with an unbounded sum, without creating a second motion integrator.
    """
    size = math.hypot(direction[0], direction[2])
    if size <= 1e-9:
        return None
    horizontal, vertical, cap = spec.scale(distance)
    if not horizontal and not vertical:
        return None
    x, y, z = current
    x += direction[0] / size * spec.force * horizontal
    z += direction[2] / size * spec.force * horizontal
    flat = math.hypot(x, z)
    limit = spec.cap * cap
    if flat > limit:
        x, z = x / flat * limit, z / flat * limit
    y = min(spec.lift_cap * cap, y + spec.lift * vertical)
    return expr.Vec3(*(expr.f32(after - before) for before, after in
                       zip(current, (x, y, z), strict=True)))
