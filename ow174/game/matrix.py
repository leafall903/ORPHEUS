"""D.Va Matrix interception reconstruction; see docs/ZENYATTA_COMBAT.txt for limits.

Bounds and tags come from authored inputs. Eye attachment, aim orientation and
the tag145 exclusion are reconstructed, pending native combat comparison.
Unknown projectile metadata never silently becomes an absorbable attack.
"""

import json
import math
import os
import re
from pathlib import Path

from ow174.game.script.expr import EPSILON, Vec3, f32, to_float

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145
_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
TAGS_PATH = Path(_path).resolve() if _path else None
_tag_table = None


def read_tags(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(data.get('version')) is not int or data['version'] != 1 or data.get("build") != BUILD:
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


# Bind the immutable configured input before the first controlled server root.
# A process-global cold->loaded transition inside combat cannot be reproduced
# by a second server in the same process without restoring hidden cache state.
_tag_table = read_tags(TAGS_PATH) if TAGS_PATH is not None else {}


def tags():
    global _tag_table
    if _tag_table is None:
        _tag_table = read_tags(TAGS_PATH) if TAGS_PATH is not None else {}
    return _tag_table


def loaded_tags():
    """Observe the cold or loaded canonical catalog without reading or initializing it."""
    return _tag_table


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


def active_matrix_states(player):
    hero = getattr(player, "hero", None)
    script = getattr(player, "body_script", None)
    hero_guid, body_guid = ((hero, player.body) if type(hero) is int else
                            (getattr(hero, "guid", None), getattr(hero, "body", None)))
    if (hero_guid != 0x02E000000000007A or body_guid != 0x0400000000000407
            or script is None or getattr(script, "retired", False)):
        return
    for instance in script.component.instances.values():
        if instance.graph.index == 0x02CC:
            state = instance.states.get(3)
            if state is not None and state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles":
                yield state


def depleted_before(state, frame):
    instance = state.instance
    component = instance.component
    if frame is None or frame <= component.frame + 1:
        return False
    chase = instance.states.get(instance.graph.node(104).state)
    if chase is None or not chase.active:
        return False
    rate = to_float(chase.evaluate(chase.node.field("m_9B0385B4"), watch=False))
    if rate <= 0:
        return False
    current = chase.cur
    dest = to_float(chase.evaluate(chase.node.field("m_destination"), watch=False))
    last = chase.last
    # Project the held drain through earlier body frames without advancing the
    # VM or spending fuel. The current world update still precedes its command.
    for body_frame in range(component.frame + 1, frame):
        end_ms = (body_frame + 1)*component.frame_ms - 1
        step = f32(f32(f32(rate * .001) * (end_ms-last)) + EPSILON)
        current, reached = chase._step(current, dest, step)
        if reached:
            return True
        last = end_ms
    return False


def active_matrix_depleted_before(player, frame):
    return any(depleted_before(state, frame) for state in active_matrix_states(player))


def matrix_distance(player, origin, direction, reach, anchor, forward, *, frame=None):
    """Distance to active authored02CC/st3 boxes in a reconstructed aim frame."""
    states = tuple(active_matrix_states(player))
    if not states:
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
    for state in states:
        if depleted_before(state, frame):
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
