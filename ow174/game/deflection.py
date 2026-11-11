"""D.Va Matrix interception reconstruction; see docs/DVA_MATRIX.txt for limits.

Bounds and tags come from authored inputs. Eye attachment, aim orientation and
the tag145 exclusion are reconstructed, pending native combat comparison.
Unknown projectile metadata never silently becomes an absorbable attack.
"""

import json
import math
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType

from ow174.game.script import expr
from ow174.game.script.expr import Vec3

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145
_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
TAGS_PATH = Path(_path).resolve() if _path else None


def read_tags(path):
    return _parse_tags(json.loads(Path(path).read_bytes()))


def _parse_tags(data):
    if not isinstance(data, dict):
        raise ValueError("projectile tags must be an object")
    if data.get("version") != 1 or data.get("build") != BUILD:
        raise ValueError("projectile tags require build104319 version1")
    entities = data.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("projectile tag entities must be a mapping")
    result = {}
    for key, tags in entities.items():
        if not re.fullmatch(r"0x0400[0-9A-F]{12}", key):
            raise ValueError("projectile tag key must be a full003 GUID")
        if (not isinstance(tags, list)
                or any(not isinstance(tag, str) or not re.fullmatch(r"0x0D60[0-9A-F]{12}", tag)
                       for tag in tags)
                or len(set(tags)) != len(tags)):
            raise ValueError("invalid projectile target tags")
        result[int(key, 16)] = frozenset(int(tag, 16) for tag in tags)
    return result


@cache
def _rows(blob):
    return MappingProxyType(_parse_tags(json.loads(blob)))


@dataclass(frozen=True)
class ProjectileTags:
    """Immutable actual input bytes; stored with Combat without snapshot I/O.

    The derived lookup has only immutable values. External files/environment do
    not participate in admission after this object is constructed.
    """

    blob: bytes | None

    def __post_init__(self):
        if self.blob is not None:
            if type(self.blob) is not bytes:
                raise ValueError("projectile catalog needs immutable bytes")
            _rows(self.blob)

    def absorbable(self, definition):
        known = _rows(self.blob).get(definition) if self.blob is not None else None
        return known is not None and EXCLUDED_TAG not in known


_CATALOG = ProjectileTags(TAGS_PATH.read_bytes() if TAGS_PATH is not None else None)


def catalog():
    return _CATALOG


def tags():
    return _rows(_CATALOG.blob) if _CATALOG.blob is not None else MappingProxyType({})


def absorbable(definition):
    return _CATALOG.absorbable(definition)


def ray_box(origin, direction, lower, upper, reach):
    """First intersection of a bounded ray with a local axis-aligned box."""
    near, far = 0.0, reach
    for point, velocity, low, high in zip(origin, direction, lower, upper, strict=True):
        if abs(velocity) < 1e-12:
            if point < low or point > high:
                return None
            continue
        a, b = (low - point) / velocity, (high - point) / velocity
        near, far = max(near, min(a, b)), min(far, max(a, b))
        if near > far:
            return None
    return near


def countdown_deadline(chase):
    """Read an active authored scalar ChaseVar's remaining time without ticking it."""
    if chase is None or not chase.active or chase.node.cls != "STUStatescriptStateChaseVar":
        return None
    if chase.remaining is not None:
        remaining = max(0.0, chase.remaining / 1000)
    else:
        destination = expr.to_float(chase.evaluate(chase.node.field("m_destination"), watch=False))
        config = chase.node.field("m_9B0385B4")
        rate = expr.to_float(chase.evaluate(config, watch=False)) if config is not None else 1.0
        if not all(math.isfinite(value) for value in (chase.cur, destination, rate)):
            raise ValueError("invalid scalar defense countdown")
        if rate <= 0:
            return math.inf
        remaining = abs(chase.cur - destination) / rate
    return chase.last / 1000 + remaining


def state_deadline(state):
    """Deadline from a live native timer/countdown record; no owner-VM advancement."""
    if state is None or not state.active:
        return None
    if state.node.cls == "STUStatescriptStateWait":
        return (state.start + max(0, state.ms)) / 1000
    return countdown_deadline(state)


def world_active(state, now, deadline):
    """Current command window only: future activation cannot protect older catch-up hits."""
    if state is None or not state.active or deadline is None:
        return False
    window_end = now + (state.instance.component.frame_ms - 1) / 1000
    return window_end + 1e-9 >= state.start / 1000 and now + 1e-9 < deadline


def matrix_deadline(instance):
    chase = instance.states.get(45)
    if chase is None or chase.node.pos != 104:
        return None
    return countdown_deadline(chase)


def matrix_distance(script, origin, direction, reach, anchor, forward, *, now=None):
    """Distance to active authored02CC/st3 boxes in a reconstructed aim frame.

    Combat supplies the live enemy mech VM, eye anchor and normalized aim.
    The supplied time is in that VM's frame domain.
    """
    if script is None or getattr(script, "retired", False):
        return None
    x, y, z = forward
    length = math.hypot(x, z)
    right = (z / length, 0.0, -x / length) if length > 1e-9 else (1.0, 0.0, 0.0)
    rx, ry, rz = right
    up = (y * rz - z * ry, z * rx - x * rz, x * ry - y * rx)
    basis = (right, up, forward)
    delta = tuple(a - b for a, b in zip(origin, anchor, strict=True))
    local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
    local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
    best = None
    for instance in script.component.instances.values():
        if instance.graph.index != 0x02CC:
            continue
        state = instance.state(3)
        if state is None or not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles":
            continue
        if now is not None:
            deadline = matrix_deadline(instance)
            if not world_active(state, now, deadline):
                continue
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if (not isinstance(low, Vec3) or not isinstance(high, Vec3)
                    or any(not math.isfinite(v) for v in (*low, *high))
                    or any(a > b for a, b in zip(low, high, strict=True))):
                raise ValueError("invalid authored Matrix box")
            hit = ray_box(local_origin, local_direction, low, high, reach)
            if hit is not None and (best is None or hit < best):
                best = hit
    return best
