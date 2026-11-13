"""104319 Helix's independent authored force rings; no receiver/transport policy."""

import math

from ow174.game.script.expr import f32


def helix_force_axes(blast, distance, *, own=False):
    """D6D87C..D6DA6A ring interpolation with an exclusive outer boundary.

    Empty native force-ring arrays retain the unit scale. The caller supplies
    its existing capsule distance and radial/up direction policy separately.
    """
    if not math.isfinite(distance):
        raise ValueError('invalid Helix force distance')
    distance = f32(max(0, distance))
    horizontal = vertical = 1.0
    if blast.impulse_rings:
        horizontal = vertical = 0.0
        previous = None
        for radius, h, v in blast.impulse_rings:
            radius, h, v = f32(radius), f32(h), f32(v)
            if not all(math.isfinite(value) for value in (radius, h, v)):
                raise ValueError('invalid authored Helix force ring')
            if distance < radius:
                if previous is None:
                    horizontal, vertical = h, v
                else:
                    before, last_h, last_v = previous
                    share = f32(f32(distance - before) / max(f32(radius - before), f32(.0001)))
                    horizontal = f32(f32(f32(h - last_h) * share) + last_h)
                    vertical = f32(f32(f32(v - last_v) * share) + last_v)
                break
            previous = radius, h, v
    source = blast.source_impulse_scale if own else 1.0
    return (f32(f32(blast.horizontal_impulse * horizontal) * source),
            f32(f32(blast.vertical_impulse * vertical) * source))
