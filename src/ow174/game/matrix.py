"""Ana projectile eligibility and authored D.Va Matrix box intersection."""
import json
import math
import os
import re
from pathlib import Path

from ow174.game import deflection

from ow174.game.script.expr import Vec3

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = deflection.EXCLUDED_TAG
_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
TAGS_PATH = Path(_path).resolve() if _path else None


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


def tags():
    """Read the combined immutable catalog; no second process-global cache."""
    catalog = deflection.catalog()
    if catalog.blob is not None:
        return deflection._rows(catalog.blob)
    return {guid: frozenset(0x0D60000000000000 | tag for tag in values)
            for guid, values in ANA_PROJECTILE_TAGS.items()}


def loaded_tags():
    return tags()


HERO, MECH = 0x02E000000000007A, 0x0400000000000407
DVA = HERO  # retained delivery name for the same mounted hero identity
# Build104319 .003 target-tag components. 15E9 has the Matrix exclusion
# tag 0145. An absent component on a verified definition is an empty set.
ANA_PROJECTILE_TAGS = {
    0x04000000000015B7: (0x2F9,),
    0x0400000000001615: (0x2F9,),
    0x04000000000015E8: (),
    0x0400000000001426: (),
    0x04000000000015E9: (0x145, 0x412),
}


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


def matrix_distance(player, origin, direction, reach, anchor, forward):
    """Distance to active authored02CC/st3 boxes in a reconstructed aim frame.

    The caller supplies a live enemy player, eye anchor and normalized aim.
    Death, team, static cover and nearer body ordering belong to Combat.
    """
    hero = getattr(player, "hero", None)
    script = getattr(player, "body_script", None)
    if (hero is None or hero.guid != HERO or hero.body != MECH
            or script is None or script.retired or script.component.fault is not None):
        return None
    hit = active_boxes(player, origin, direction, reach, anchor, forward, 0x02CC, state_index=3)
    return hit[0] if hit is not None else None


def active_boxes(player, origin, direction, reach, anchor, forward, graph, *, state_index=None):
    """Nearest active authored box and its owning state in the current aim frame."""
    script = getattr(player, "body_script", None)
    if script is None or script.retired or script.component.fault is not None:
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
        if instance.graph.index != graph:
            continue
        for state in instance.states.values():
            if (not state.active or state.ending or (state_index is not None and state.index != state_index)
                    or state.node.cls != "STUStatescriptStateDeflectProjectiles"):
                continue
            for box in state.node.field("m_boxes") or ():
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if (not isinstance(low, Vec3) or not isinstance(high, Vec3)
                        or any(not math.isfinite(v) for v in (*low, *high))
                        or any(a > b for a, b in zip(low, high, strict=True))):
                    raise ValueError("invalid authored projectile defense box")
                hit = ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, state
    return best


def load_admitted_definitions(path):
    catalog = json.loads(path.read_bytes())
    if (not isinstance(catalog, dict) or type(catalog.get("version")) is not int
            or catalog["version"] != 1 or catalog.get("build") != "1.74.0.0.104319"
            or not isinstance(catalog.get("entities"), dict)):
        raise ValueError("Matrix requires projectile tag catalog version 1 for build 104319")
    admitted = set()
    for definition, tags in catalog["entities"].items():
        if (not re.fullmatch(r"0x0400[0-9A-F]{12}", definition) or not isinstance(tags, list)
                or any(not isinstance(tag, str) or not re.fullmatch(r"0x0D60[0-9A-F]{12}", tag)
                       for tag in tags)):
            raise ValueError("invalid projectile definition or target tags")
        if EXCLUDED_TAG not in {int(tag, 16) for tag in tags}:
            admitted.add(int(definition, 16))
    return frozenset(admitted)


def _owned_closed(state, last_pose, tick):
    from ow174.game.mover import REPEAT
    from ow174.game.script import nodes

    instance = state.instance
    component = instance.component
    at = component.now + (tick - last_pose) * component.frame_ms
    if at < state.start:
        return True
    ability, tap = instance.states.get(0), instance.states.get(26)
    release = None
    if isinstance(ability, nodes.Ability) and ability.held(29):
        release = component.now + REPEAT * component.frame_ms + 1
    if isinstance(tap, nodes.Wait) and tap.active:
        release = max(release or tap.start, tap.start + tap.ms)
    drain = instance.states.get(45)
    return ((release is not None and at >= release)
            or (isinstance(drain, nodes.ChaseVar) and drain.active and drain.reaches_at(at)))


def trace(target, origin, direction, reach, *, tick=None):
    """Delivered Matrix contact for selected players and their actual0407 actor."""
    from ow174.game import bots
    from ow174.game.interception import box_contact

    owner = target.owner
    hero = getattr(owner, "hero", None)
    if getattr(hero, "guid", hero) != HERO:
        return None
    script = getattr(owner, "body_script", None)
    if script is None:
        return None
    if isinstance(owner, bots.Bot):
        match = script.component.world.match
        pilot = match._message_player(owner.source_body) if match is not None else None
        if (owner.body != MECH or pilot is None or pilot.controlled_mech is not owner
                or owner.owner != pilot.entity or owner.dead or owner.control_mover is None):
            return None
    elif hero.body != MECH:
        return None
    state = next((state for state in deflection._active_states(script, 0x02CC, 3)
                  if not state.ending), None)
    if state is None:
        return None
    if isinstance(owner, bots.Bot) and tick is not None and owner.moved_tick is not None:
        if _owned_closed(state, owner.moved_tick, tick):
            return None
    elif tick is not None:
        # Delivered selected-player Matrix countdown: read the retained native
        # chase record at world hit time without advancing the owner's VM.
        from ow174.game.combat import TICK

        if not deflection.world_active(state, tick * TICK,
                                       deflection.matrix_deadline(state.instance)):
            return None
    return box_contact(target, state, origin, direction, reach)
