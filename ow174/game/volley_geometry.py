"""Explicit reconstructed ring/paired geometry for authored multi-pellet volleys.

Counts/radii come from STU_613BDDB0. Degrees as cone half-angle, fixed zero
phase, equal azimuth spacing and muzzle-at-eye are reconstruction choices.
Paired STU_239B571E uses opposite rectangle corners and collocated eye muzzles;
its two extents are interpreted as degree half-angles. Native launch parameters,
sampling, rotation, seeding and animated muzzle transforms are unproven.
"""

import math


class UnsupportedGeometry(ValueError):
    pass


def _number(state, cfg):
    value = state.evaluate(cfg, watch=False)
    if type(value) not in (int, float) or not math.isfinite(value):
        raise UnsupportedGeometry("nonfinite or nonnumeric launch parameter")
    return value


def _count(state, cfg):
    value = _number(state, cfg)
    if int(value) != value or not 0 <= value <= 512:
        raise UnsupportedGeometry("pellet count outside integral0..512")
    return int(value)


def _axes(forward):
    if len(forward) != 3 or any(not math.isfinite(v) for v in forward):
        raise UnsupportedGeometry("invalid forward vector")
    length = math.sqrt(sum(v * v for v in forward))
    if not length:
        raise UnsupportedGeometry("zero forward vector")
    axis = tuple(v / length for v in forward)
    reference = (0.0, 1.0, 0.0) if abs(axis[1]) < 0.99 else (0.0, 0.0, 1.0)
    right = (reference[1] * axis[2] - reference[2] * axis[1],
             reference[2] * axis[0] - reference[0] * axis[2],
             reference[0] * axis[1] - reference[1] * axis[0])
    length = math.sqrt(sum(v * v for v in right))
    right = tuple(v / length for v in right)
    up = (axis[1] * right[2] - axis[2] * right[1],
          axis[2] * right[0] - axis[0] * right[2],
          axis[0] * right[1] - axis[1] * right[0])
    return axis, right, up


def _paired(state, forward, launch, count):
    if count != 2 or len(launch.get("m_muzzles") or ()) != 2:
        raise UnsupportedGeometry("paired reconstruction requires two pellets and two muzzles")
    if any(launch.get(key) is not None for key in ("m_23C72C36", "m_6D992E59")):
        raise UnsupportedGeometry("additional paired launch fields are not reconstructed")
    if _number(state, launch.get("m_AC20B1C1")) != 10:
        raise UnsupportedGeometry("paired reconstruction only covers observed parameter10")
    width, height = (_number(state, launch.get(key)) for key in ("m_7F0FED02", "m_429A6C85"))
    if not (0 <= width < 90 and 0 <= height < 90):
        raise UnsupportedGeometry("paired extents outside0..90 degrees")
    axis, right, up = _axes(forward)
    x, y = math.tan(math.radians(width)), math.tan(math.radians(height))
    length = math.sqrt(1 + x * x + y * y)
    return tuple(tuple((a + sign * (x * r + y * u)) / length
                       for a, r, u in zip(axis, right, up, strict=True)) for sign in (-1, 1))


def directions(state, forward, *, reconstructed_patterns=False):
    cfg = state.node.field("m_numProjectilesPerShot")
    count = 1 if cfg is None else _count(state, cfg)
    if count <= 1:
        return (forward,) if count else ()
    if not reconstructed_patterns:
        raise UnsupportedGeometry("multiple pellets require explicit reconstructed pattern geometry")
    launch = state.node.field("m_5EC1EB38")
    if not isinstance(launch, dict) or launch.get("$") not in ("STU_613BDDB0", "STU_239B571E"):
        raise UnsupportedGeometry("unknown multiple-pellet launch class")
    if any(launch.get(key) is not None for key in
           ("m_22CE7F30", "m_E2FD6F04", "m_1C78B91C", "m_6425FFB9", "m_391F15AF")):
        raise UnsupportedGeometry("additional launch transforms are not reconstructed")
    if any(row.get("m_entityHandle") is not None for row in launch.get("m_muzzles") or ()):
        raise UnsupportedGeometry("foreign muzzle entity is not reconstructed")
    if launch["$"] == "STU_239B571E":
        return _paired(state, forward, launch, count)
    if len(launch.get("m_muzzles") or ()) != 1:
        raise UnsupportedGeometry("ring reconstruction requires one muzzle")
    if _number(state, launch.get("m_92308789")) != 2:
        raise UnsupportedGeometry("ring reconstruction only covers observed launch parameter2")
    spread = _number(state, launch.get("m_spread"))
    if not 0 <= spread < 90:
        raise UnsupportedGeometry("ring cone half-angle outside0..90 degrees")
    rings = []
    for row in launch.get("m_0D30E40C") or ():
        if not isinstance(row, dict) or row.get("$") != "STU_B101467F":
            raise UnsupportedGeometry("unknown ring row")
        radius = _number(state, row.get("m_6425FFB9"))
        if not 0 <= radius <= 1:
            raise UnsupportedGeometry("ring radius outside0..1")
        rings.append((radius, _count(state, row.get("m_B19BD35D"))))
    if sum(size for _, size in rings) != count:
        raise UnsupportedGeometry("ring sizes disagree with authored pellet count")
    axis, right, up = _axes(forward)
    result = []
    for radius, size in rings:
        angle = math.radians(spread) * radius
        for index in range(size):
            phase = 2 * math.pi * index / size
            result.append(tuple(math.cos(angle) * a + math.sin(angle) *
                                (math.cos(phase) * r + math.sin(phase) * u)
                                for a, r, u in zip(axis, right, up, strict=True)))
    return tuple(result)
