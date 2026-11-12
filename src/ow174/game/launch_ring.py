"""104319 ring launch math with explicit resolved context inputs.

Scalar selection is bitwise verified;vector reconstruction permits SSE rsqrt
rounding differences. Engine random-table/seed ownership remains separate.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr
from ow174.game.script.expr import f32

TAU = f32(6.2831854820251465)
DEGREES = f32(0.01745329238474369)
MAYHEM_VOLLEYS = (10, 11, 13, 14, 15, 16)


@dataclass(frozen=True)
class Inputs:
    """Resolved native-context inputs;no engine table or key policy is assumed."""

    azimuth_radians: float
    noise_words: tuple[tuple[int, int], ...]


def projectile_directions(state, forward, number, activation):
    """Resolve authored multi-pellet ring launches with an explicit world service.

    Return None when the required launch/context is unsupported. A missing
    service must not turn an authored25-pellet volley into one or25 coincident
    pellets. The service resolves native global azimuth and per-pellet table
    keys,including any authored fixed azimuth,for this exact volley activation.
    """
    def value(cfg, default):
        return default if cfg is None else state.evaluate(cfg, watch=False)

    count = expr.to_int(value(state.node.field('m_numProjectilesPerShot'), 1))
    launch = state.node.field('m_5EC1EB38')
    mayhem = state.instance.graph.index == 0x022A and state.node.pos in MAYHEM_VOLLEYS
    if count == 1 and not mayhem:
        return (forward,)
    if count <= 0 or not isinstance(launch, dict) or launch.get('$') != 'STU_613BDDB0':
        return None
    if mayhem:
        axis = value(launch.get('m_E2FD6F04'), None)
        if count != 1 or not isinstance(axis, expr.Vec3):
            return None
        forward = (axis.x, axis.y, axis.z)
        index = MAYHEM_VOLLEYS.index(state.node.pos)
        # Sandbox launch context until the native seed and azimuth ownership is recovered.
        azimuth = index * TAU / len(MAYHEM_VOLLEYS)
        words = ((index * 2654435761) & 0xFFFFFFFF, ((index + 7) * 2246822519) & 0xFFFFFFFF)
        inputs = Inputs(azimuth, (words,))
    else:
        provider = getattr(state.instance.component.world, 'weapon_launch_inputs', None)
        inputs = provider(state, number, activation, count) if provider is not None else None
    if inputs is None:
        return None
    if not isinstance(inputs, Inputs) or len(inputs.noise_words) != count:
        raise ValueError('launch context must provide one explicit word pair per authored pellet')
    rings = tuple((expr.to_float(value(ring.get('m_6425FFB9'), 0)),
                   expr.to_int(value(ring.get('m_B19BD35D'), 0)))
                  for ring in launch.get('m_0D30E40C') or ())
    spread = expr.to_float(value(launch.get('m_spread'), 0))
    noise = expr.to_float(value(launch.get('m_92308789'), 0))
    aspect = expr.to_float(value(launch.get('m_6425FFB9'), 1))
    return tuple(direction(rings, index, forward, spread, noise,
                           azimuth_radians=inputs.azimuth_radians,
                           noise_words=inputs.noise_words[index], aspect=aspect)
                 for index in range(count))


def angles(rings, index, spread_degrees, noise_degrees):
    """Return (ring azimuth, radial angle) for explicit compiled ring entries.

    Entries are (radius multiplier, positive int32 pellet count). The native
    caller supplies a nonnegative int32 pellet index. An index beyond all
    counts stays in the final ring, with its residual index left unclamped.
    Empty rings produce zero angles. Global/random azimuth, angular noise,
    axis construction and the later aspect adjustment are separate stages.
    """
    if type(rings) is not tuple:
        raise ValueError('explicit compiled ring tuple required')
    if type(index) is not int or not 0 <= index <= 0x7FFFFFFF:
        raise ValueError('pellet index must be a nonnegative int32')
    if any(type(count) is not int or not 0 < count <= 0x7FFFFFFF for _, count in rings):
        raise ValueError('compiled ring counts must be positive int32 values')
    if not rings:
        return 0.0, 0.0
    for slot, (radius, count) in enumerate(rings):
        if index < count or slot == len(rings) - 1:
            azimuth = f32(f32(f32(index) / f32(count)) * TAU)
            width = f32(f32(f32(spread_degrees) - f32(noise_degrees)) * DEGREES)
            # MAXSS returns its second operand on equality or unordered: this
            # preserves negative zero and NaN, unlike Python's max(0, width).
            width = 0.0 if width < 0.0 else width
            return azimuth, f32(width * f32(radius))
        index -= count
    raise AssertionError('nonempty ring selection must terminate')


def uniform(word, low, high):
    """184B1F0 uint32-table sample;the caller owns the native table/key lookup."""
    if type(word) is not int or not 0 <= word <= 0xFFFFFFFF:
        raise ValueError('native random-table word must be a uint32')
    width = f32(f32(high) - f32(low))
    return f32(f32(f32(f32(word) * width) * f32(2 ** -32)) + f32(low))


def _cross(a, b):
    return tuple(f32(f32(a[i] * b[j]) - f32(a[j] * b[i]))
                 for i, j in ((1, 2), (2, 0), (0, 1)))


def _dot(a, b):
    return f32(f32(f32(a[0] * b[0]) + f32(a[1] * b[1])) + f32(a[2] * b[2]))


def _normal(value, fallback):
    square = _dot(value, value)
    if square < f32(1.1754943508222875e-36):
        return fallback
    # Native uses RSQRTPS plus one Newton step. A correctly rounded inverse
    # square root changes a few final float32 ULPs;the shape/order is retained.
    reciprocal = f32(1.0 / math.sqrt(square))
    return tuple(f32(axis * reciprocal) for axis in value)


def _rotate(value, axis, angle):
    sine, cosine = f32(math.sin(angle)), f32(math.cos(angle))
    projection = f32(f32(1.0 - cosine) * _dot(axis, value))
    cross = _cross(axis, value)
    return tuple(f32(f32(f32(projection * axis[i]) + f32(cosine * value[i]))
                     + f32(cross[i] * sine)) for i in range(3))


def direction(rings, index, forward, spread_degrees, noise_degrees, *,
              azimuth_radians, noise_words, aspect):
    """Reconstruct D80310 with resolved azimuth,two table words and aspect.

    Inputs are explicit:there is no invented random table or seed generator.
    Forward must be a finite unit vector;azimuth has already passed the native
    fixed-versus-random selection. Final aspect scaling is not renormalized,
    as in the native evaluator. Combat integration/engine context is separate.
    """
    if len(forward) != 3 or not all(math.isfinite(v) for v in forward):
        raise ValueError('forward must be a finite unit vector')
    forward = tuple(f32(v) for v in forward)
    if abs(_dot(forward, forward) - 1.0) > 1e-5:
        raise ValueError('forward must be a finite unit vector')
    if not math.isfinite(azimuth_radians) or azimuth_radians < 0:
        raise ValueError('azimuth must be resolved before vector sampling')
    if len(noise_words) != 2:
        raise ValueError('two explicit native random-table words required')
    azimuth, radial = angles(rings, index, spread_degrees, noise_degrees)
    azimuth = f32(azimuth + f32(azimuth_radians))
    noise = f32(f32(noise_degrees) * DEGREES)
    first = uniform(noise_words[0], -noise, noise)
    second = uniform(noise_words[1], -noise, noise)
    basis = _normal(_cross(forward, (0.0, 1.0, 0.0)), (1.0, 0.0, 0.0))
    basis = _rotate(basis, forward, azimuth)
    result = _rotate(forward, basis, f32(first + radial))
    if noise > f32(1.1920928955078125e-5):
        second_axis = _normal(_cross(forward, basis), (0.0, 0.0, 1.0))
        result = _rotate(result, second_axis, second)
    aspect = f32(aspect)
    aspect = f32(1.1920928955078125e-7) if aspect < f32(1.1920928955078125e-7) else aspect
    if aspect != 1.0 and aspect > 0.0:
        u = _normal(_cross(forward, (0.0, 1.0, 0.0)), (1.0, 0.0, 0.0))
        v = _cross(forward, u)
        scale_u, scale_v = (f32(1.0 / aspect), 1.0) if aspect > 1.0 else (1.0, aspect)
        pu = f32(_dot(result, u) * scale_u)
        pv = f32(_dot(result, v) * scale_v)
        pf = _dot(result, forward)
        result = tuple(f32(f32(f32(pv * v[i]) + f32(pu * u[i])) + f32(pf * forward[i]))
                       for i in range(3))
    return result
