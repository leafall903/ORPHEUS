"""104319 C8E660 capsule/volume math, independent of candidate and filter services.

DC8720 finds closest segment points; DC8B30 moves those onto their capsule
surfaces. Inputs and arithmetic round to float32. Normalization uses sqrt and
division rather than the native rsqrt estimate/Newton sequence, so comparisons
at floating-point boundaries still need the original operator oracle.
"""

import math
from dataclasses import dataclass

from ow174.game.script.expr import f32

_TINY = 1.1754943508222875e-35
_FULL_OUTER = -0.9998999834060669
_EMPTY_INNER = 0.9998999834060669


def _add(a, b):
    return tuple(f32(x + y) for x, y in zip(a, b, strict=True))


def _sub(a, b):
    return tuple(f32(x - y) for x, y in zip(a, b, strict=True))


def _scale(a, value):
    return tuple(f32(x * value) for x in a)


def _dot(a, b):
    x, y, z = (f32(x * y) for x, y in zip(a, b, strict=True))
    return f32(f32(x + y) + z)


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _nonzero_squared(value):
    squared = f32(value * value)
    return f32(f32(squared + squared) + squared) > _TINY


def _capsule_surface(a, b, c, d, first_radius, second_radius):
    """DC8720/DC8B30: closest points on both capsule surfaces and their gap."""
    u, v, w = _sub(b, a), _sub(d, c), _sub(a, c)
    aa, ee = _dot(u, u), _dot(v, v)
    valid_a, valid_e = _nonzero_squared(aa), _nonzero_squared(ee)
    if not valid_a and not valid_e:
        s = t = 0.0
    else:
        ff = _dot(v, w)
        if not valid_a:
            s, t = 0.0, _clamp(f32(ff / ee))
        else:
            cc = _dot(u, w)
            if not valid_e:
                t, s = 0.0, _clamp(f32(-cc / aa))
            else:
                bb = _dot(u, v)
                denominator = f32(f32(aa * ee) - f32(bb * bb))
                s = (_clamp(f32(f32(f32(bb * ff) - f32(cc * ee)) / denominator))
                     if _nonzero_squared(denominator) else 0.0)
                t = f32(f32(f32(bb * s) + ff) / ee)
                if t < 0:
                    t, s = 0.0, _clamp(f32(-cc / aa))
                elif t > 1:
                    t, s = 1.0, _clamp(f32(f32(bb - cc) / aa))
    p, q = _add(a, _scale(u, s)), _add(c, _scale(v, t))
    delta = _sub(q, p)
    squared = _dot(delta, delta)
    if f32(f32(squared + squared) + squared) <= _TINY:
        return p, p, 0.0
    distance = f32(math.sqrt(squared))
    direction = _scale(delta, f32(1.0 / distance))
    radii = f32(first_radius + second_radius)
    if squared > f32(radii * radii):
        return (_add(p, _scale(direction, first_radius)),
                _sub(q, _scale(direction, second_radius)), f32(distance - radii))
    point = _add(_add(p, q), _scale(direction, f32(first_radius - second_radius)))
    point = _scale(point, 0.5)
    return point, point, 0.0


@dataclass(frozen=True)
class Volume:
    """The C9A430 result consumed at C8E660; axes must come from the caller.

    This value object supplies no candidate enumeration, team/tag filters,
    entity transforms, LOS, lifecycle, replication or target effects.
    """

    origin: tuple
    axis: tuple
    outer_cos: tuple
    outer_extent: tuple
    inner_extent: tuple
    inner_cos: tuple
    depth: float
    near: float = 0.0
    margin: float = 0.0
    parallel: bool = False
    ignore_height: bool = False

    def capsule(self, feet, radius, height):
        """Return (accepted, native distance metric); early radial rejects give -1."""
        origin, axis = tuple(map(f32, self.origin)), tuple(map(f32, self.axis))
        feet, radius, height = tuple(map(f32, feet)), f32(radius), f32(height)
        half = f32(height * 0.5)
        low = f32(feet[1] + min(radius, half))
        high = f32(feet[1] + max(f32(height - radius), half))
        if self.ignore_height:
            low = high = origin[1]
        picked = (feet[0], _clamp(origin[1], low, high), feet[2])
        delta = _sub(picked, origin)
        squared = _dot(delta, delta)
        depth, near, margin = f32(self.depth), f32(self.near), f32(self.margin)
        far_radius = f32(depth + radius)
        near_radius = max(f32(near - max(radius, half)), 0.0)
        if squared > f32(far_radius * far_radius) or squared < f32(near_radius * near_radius):
            return False, -1.0
        point, other, gap = _capsule_surface(
            (feet[0], low, feet[2]), (feet[0], high, feet[2]),
            _add(origin, _scale(axis, depth)), _sub(origin, _scale(axis, depth)),
            f32(radius + margin), 0.0,
        )
        metric = max(f32(f32(math.sqrt(squared)) - radius), 0.0)
        outer = any(f32(value) > _FULL_OUTER for value in self.outer_cos)
        inner = any(f32(value) < _EMPTY_INNER for value in self.inner_cos)
        if not outer and not inner:
            return True, metric
        delta = _sub(point, origin)
        along = _dot(delta, axis)
        if gap == 0 and along > 0 and outer:
            return not inner, metric
        if self.parallel and along < 0:
            return False, metric
        direction_squared = _dot(delta, delta)
        length = f32(math.sqrt(direction_squared))
        direction = (_scale(delta, f32(1.0 / length))
                     if direction_squared >= 1.1754943508222875e-36 else (0.0, 0.0, 1.0))
        cosine = _dot(direction, axis)
        mix = _clamp(abs(f32(point[1] - other[1])) if self.parallel else abs(direction[1]))

        def lerp(values):
            first, second = map(f32, values)
            return f32(f32(f32(second - first) * mix) + first)

        if self.parallel:
            outer_bound = lerp(self.outer_extent) if outer else -1.0
            inner_bound = lerp(self.inner_extent) if inner else -1.0
            return gap <= outer_bound and gap > inner_bound, metric
        return ((not outer or cosine >= lerp(self.outer_cos))
                and (not inner or cosine < lerp(self.inner_cos))), metric
