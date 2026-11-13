"""1259/32 provider and native pre-profile arithmetic on resolved host inputs.

The host binds the impact snapshot once. Native actor snapshot capture and final
D73830 conversion remain receiver work; this module never reads consume-time
velocity, writes target417, applies resistance, or starts a second mover.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr
from ow174.game.script.runtime import InstanceContext

FLOAT_MAX = 3.4028234663852886e38


class WhipContext(InstanceContext):
    def __init__(self, instance, target, airborne):
        super().__init__(instance, None)
        self.target = target
        self.airborne = airborne

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 375):
            return (expr.Entity(self.target),)
        return super().var(scope, var)

    def member(self, entity, var):
        if (entity, var) == (self.target, 417):
            return self.airborne
        return super().member(entity, var)


@dataclass(frozen=True)
class WhipSpec:
    explicit: expr.Vec3
    horizontal: float
    vertical: float
    minimum_y: float
    application_parameter: float
    rings: tuple

    def impulse(self, incoming):
        """D6DA7F..DB01: explicit overwrite, ring scale, then minimum Y.

        Whip has one flat999 ring. Contact distance lies inside that ring.
        Overall scale is1 at this unresisted producer boundary.
        """
        _, horizontal, vertical = self.rings[0]
        x, y, z = self.explicit
        y = expr.f32(y * vertical)
        if vertical > 0:
            y = max(y, expr.f32(self.minimum_y - incoming[1]))
        return expr.Vec3(expr.f32(x * horizontal), y, expr.f32(z * horizontal))


def evaluate_whip(instance, target, airborne):
    """Evaluate the unchanged authored bytecode with a resolved contact subject."""
    if instance.graph.index != 0x1259 or type(airborne) is not bool:
        return None
    modify = instance.graph.node(32).field("m_modifyHealth") or {}
    context = WhipContext(instance, target, airborne)

    def number(config, default):
        value = default if config is None else expr.evaluate(config, context)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("unresolved Whip force scalar")
        return expr.f32(value)

    vector = expr.evaluate(modify.get("m_1376B4DF"), context)
    if not isinstance(vector, expr.Vec3) or not all(map(math.isfinite, vector)):
        return None
    values = [number(modify.get(key), default) for key, default in (
        ("m_E33EFEE1", 0.), ("m_10399AE7", 0.),
        ("m_583C208B", -FLOAT_MAX), ("m_8394CF8C", FLOAT_MAX),
    )]
    rings = tuple(tuple(number(ring.get(key), default) for key, default in (
        ("m_radius", 0.), ("m_579D7CFF", 1.), ("m_10399AE7", 1.),
    )) for ring in modify.get("m_knockbackRings") or ())
    if len(rings) != 1 or rings[0][0] != expr.f32(999.):
        return None  # broader ring interpolation belongs to the shared consumer
    return WhipSpec(vector, *values, rings)
