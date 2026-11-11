"""Reconstructed Matrix absorption using authored mech volumes and projectile tags."""

import json
import re

from ow174.game.arrow_deflection import active_volume, box_contact

DVA = 0x02E000000000007A
MECH = 0x0400000000000407
EXCLUDED_TAG = "0x0D60000000000145"


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
        if EXCLUDED_TAG not in tags:
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
    owner = target.owner
    hero = getattr(owner, "hero", None)
    if getattr(hero, "guid", hero) != DVA:
        return None
    if isinstance(hero, int):
        pilot = owner.control_owner
        if owner.body != MECH or pilot is None or pilot.controlled_mech is not owner or owner.dead:
            return None
    elif hero.body != MECH:
        return None
    state = active_volume(target, 0x02CC, 3)
    if state is None:
        return None
    if isinstance(hero, int) and tick is not None and owner.moved_tick is not None:
        if _owned_closed(state, owner.moved_tick, tick):
            return None
    return box_contact(target, state, origin, direction, reach)
