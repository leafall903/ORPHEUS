"""Original01C6/33 force/rings on the existing shared impulse mover."""

import math
from dataclasses import dataclass
from itertools import pairwise
from ow174.game.script import expr

@dataclass(frozen=True)
class Spec:
    horizontal: float
    vertical: float
    minimum_y: float
    application_parameter: float  # m_8394CF8C, passed to native D73830; receiver-owned.
    rings: tuple

    def scale(self, distance):
        # D6D87C..D6DA6A compares float32 distance against exclusive radii.
        distance = expr.f32(distance)
        previous = self.rings[0]
        if distance < previous[0]:
            return previous[1:]
        for ring in self.rings[1:]:
            if distance < ring[0]:
                share = expr.f32(expr.f32(distance - previous[0]) /
                                 max(expr.f32(ring[0] - previous[0]), expr.f32(.0001)))
                return tuple(expr.f32(expr.f32(expr.f32(b - a) * share) + a)
                             for a, b in zip(previous[1:], ring[1:], strict=True))
            previous = ring
        return (0.0, 0.0)


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
    rings = tuple(sorted(tuple(number(r[k]) for k in ('m_radius', 'm_579D7CFF', 'm_10399AE7'))
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
    """Original pre-application ring/vector/Y-floor arithmetic on resolved inputs.

    D6D87C..D6DA6A scales horizontal direction and vertical displacement
    independently. D6DADE..D6DAFB then enforces minimum resulting Y; it is
    not an additive lift or a horizontal cap. Native D73830 receives the
    separate application_parameter and target context; the shared receiver
    owns that unresolved final conversion, limits, integration and landing.
    """
    horizontal, vertical = spec.scale(distance)
    if not horizontal and not vertical:
        return None
    size = math.hypot(direction[0], direction[2])
    scale = spec.horizontal * horizontal / size if size else 0.0
    lift = expr.f32(expr.f32(direction[1] * spec.vertical) * vertical)
    if vertical > 0:
        lift = max(lift, expr.f32(spec.minimum_y - current[1]))
    return expr.Vec3(expr.f32(direction[0] * scale), lift, expr.f32(direction[2] * scale))
