"""D.Va Matrix interception reconstruction; see docs/TRACER_COMBAT.md for limits.

Bounds and tags come from authored inputs. Eye attachment, aim orientation and
the tag145 exclusion are reconstructed, pending native combat comparison.
Unknown projectile metadata never silently becomes an absorbable attack.
"""

import json
import math
import os
import re
from functools import cache
from pathlib import Path

from ow174.game.script.expr import Vec3
from ow174.game.sigma_barrier import view_basis

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145
_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
TAGS_PATH = Path(_path).resolve() if _path else None


def read_tags(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
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
def tags():
    return read_tags(TAGS_PATH) if TAGS_PATH is not None else {}


def absorbable(definition):
    known = tags().get(definition)
    return known is not None and EXCLUDED_TAG not in known


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


def matrix_boxes(player):
    """Return active authored 02CC/st3 boxes in Matrix-local coordinates."""
    hero = getattr(player, "hero", None)
    script = getattr(player, "body_script", None)
    if (hero is None or hero.guid != 0x02E000000000007A or hero.body != 0x0400000000000407
            or script is None or script.retired or script.component.owner_dead
            or script.component.fault is not None):
        return ()
    boxes = []
    for instance in script.component.instances.values():
        if instance.graph.index != 0x02CC:
            continue
        state = instance.state(3)
        if state is None or not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles":
            continue
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if (not isinstance(low, Vec3) or not isinstance(high, Vec3)
                    or any(not math.isfinite(v) for v in (*low, *high))
                    or any(a > b for a, b in zip(low, high, strict=True))):
                raise ValueError("invalid authored Matrix box")
            boxes.append((tuple(low), tuple(high)))
    return tuple(boxes)


def matrix_distance(player, origin, direction, reach, anchor, forward):
    return matrix_box_distance(matrix_boxes(player), origin, direction, reach, anchor, forward)


def matrix_box_distance(boxes, origin, direction, reach, anchor, forward):
    """Intersect the active boxes in the reconstructed eye and aim frame."""
    if not boxes:
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
    for low, high in boxes:
        hit = ray_box(local_origin, local_direction, low, high, reach)
        if hit is not None and (best is None or hit < best):
            best = hit
    return best


def genji_distance(player, origin, direction, reach, anchor, *, radius):
    """Intersect active 03A3 mode-zero boxes in the reconstructed eye frame."""
    script = player.body_script
    if script is None or script.retired or script.component.owner_dead:
        return None
    basis = view_basis(player.mover.yaw, player.mover.pitch)
    delta = tuple(a - b for a, b in zip(origin, anchor, strict=True))
    local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
    local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
    if local_direction[2] >= -1e-9:
        return None
    best = None
    for instance in script.component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        state = instance.states.get(10)
        if state is None or not state.active or state.node.field("m_8B251D53") != 0:
            continue
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if not isinstance(low, Vec3) or not isinstance(high, Vec3):
                continue
            lower = tuple(min(a, b) - radius for a, b in zip(low, high, strict=True))
            upper = tuple(max(a, b) + radius for a, b in zip(low, high, strict=True))
            distance = ray_box(local_origin, local_direction, lower, upper, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, state
    return best
