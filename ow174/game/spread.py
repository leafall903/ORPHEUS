"""Reconstructed Roadhog ring-pattern directions from the target-build volley.

Ring radii/counts and spread come from the authored graph. The tangent cone and
even ring phases are inferred; native random jitter/phase and client agreement
remain open. Keep the scope explicit rather than guessing other pattern classes.
"""

import math

from ow174.game.script import expr


def directions(state, forward, *, fields=None):
    if fields is None and (state.instance.graph.index != 0x01C6 or state.node.pos not in (26, 33)):
        return (forward,)
    fields = state.node.fields if fields is None else fields
    config = fields.get("m_5EC1EB38") or {}
    if config.get("$") != "STU_613BDDB0":
        return (forward,)

    def number(cfg):
        return expr.to_float(state.evaluate(cfg, watch=False))

    rings = [(number(r["m_6425FFB9"]), int(number(r["m_B19BD35D"])))
             for r in config.get("m_0D30E40C") or ()]
    count = int(number(fields.get("m_numProjectilesPerShot")))
    if count < 1 or count > 256 or sum(n for _, n in rings) != count:
        raise ValueError("Roadhog ring counts do not match authored projectile count")
    width = math.tan(math.radians(number(config.get("m_spread"))))
    # A stable basis even at vertical aim: right = up cross forward.
    length = math.hypot(forward[0], forward[2])
    right = (forward[2] / length, 0.0, -forward[0] / length) if length > 1e-9 else (1.0, 0.0, 0.0)
    up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
          -forward[1] * right[0])
    result = []
    for radius, count in rings:
        for index in range(count):
            angle = 2 * math.pi * index / count
            x, y = radius * width * math.cos(angle), radius * width * math.sin(angle)
            vector = tuple(f + x * r + y * u for f, r, u in zip(forward, right, up, strict=True))
            norm = math.sqrt(sum(v * v for v in vector))
            result.append(tuple(v / norm for v in vector))
    return tuple(result)
