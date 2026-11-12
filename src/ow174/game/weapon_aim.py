"""Build 104319 direction round trip used before weapon spread."""

import math

from ow174.game.script.expr import f32

POLE = 0.9998455047607422


def _angle_word(angle, scale):
    scaled = f32(f32(angle) * scale)
    rounded = math.trunc(f32(scaled + math.copysign(.5, scaled)))
    return min(65535, max(0, rounded + 32768))


def direction_words(forward):
    """DB1050 encodes unsigned yaw and elevation from a unit vector."""
    x, y, z = map(f32, forward)
    yaw = math.atan2(x, z) if x or z else 0.0
    elevation = math.copysign(math.pi / 2, y) if abs(y) > POLE else math.asin(y)
    return _angle_word(yaw, 10430.3779296875), _angle_word(elevation, 20860.755859375)


def quantized_direction(forward):
    """DB1360 decodes the launch vector before the spread callback."""
    yaw, elevation = direction_words(forward)
    yaw = f32((yaw - 32768) * 9.58738019107841e-5)
    elevation = f32((elevation - 32768) * 4.793690095539205e-5)
    horizontal = f32(math.cos(elevation))
    return (f32(f32(math.sin(yaw)) * horizontal), f32(math.sin(elevation)),
            f32(f32(math.cos(yaw)) * horizontal))


def normalize_direction(forward):
    """D5ECD7 normalizes the direction after the launch callback."""
    x, y, z = forward
    squared = f32(f32(f32(x * x) + f32(y * y)) + f32(z * z))
    if squared < 1.1754943508222875e-36:
        return (0.0, -1.0, 0.0)
    inverse = f32(1 / math.sqrt(squared))
    return tuple(f32(value * inverse) for value in forward)
