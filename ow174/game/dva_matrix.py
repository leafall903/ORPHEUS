"""Scoped bindings for the authored 104319 mech Matrix."""

import json
import math
import os
import re
from functools import cache
from pathlib import Path

from ow174.game import defense_boxes
from ow174.game.mover import REPEAT
from ow174.game.script import expr, nodes, rulesets

HERO = 0x02E000000000007A
MECH = 0x0400000000000407
MATRIX = 0x02CC
BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145
_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
TAGS_PATH = Path(_path).resolve() if _path else None


def possession(world, cfg, instance):
    """Resolve the live possession relationships used by the prepared mech."""
    if (world.hero != HERO or world.match is None or instance.component.strict
            or instance.graph.index not in (0x0276, MATRIX)
            or instance.component.entity != world.body):
        return NotImplemented
    key = {"STU_0AC15F78": "m_entity", "STU_F76C486C": "m_D63CBEC2",
           "STU_F7262B36": "m_801B298F"}.get(cfg.get("$"))
    if key is None:
        return NotImplemented
    source = expr.to_entity(instance.evaluate(cfg.get(key)))
    owner = next((actor for actor in world.match.players if actor.has_body and not actor.gone
                  and actor.body == world.body and actor.hero.body == MECH), None)
    if owner is not None:
        if key == "m_entity" and source == owner.entity:
            return expr.Entity(owner.body)
        if key != "m_entity" and source == owner.body:
            return expr.Entity(owner.entity)
    return expr.Entity(0)


def ruleset_member(instance, entity, var):
    """Supply scoped authored Matrix ruleset defaults."""
    world = instance.component.world
    if (not instance.component.strict and world.hero == HERO and entity == instance.component.entity
            and (instance.graph.index, var) in ((MATRIX, 9777), (MATRIX, 9778), (MATRIX, 9743))):
        return rulesets.default(var, world.hero, world.mode)
    return NotImplemented


def ruleset_expression(ctx, cfg):
    """Preserve typed mech expressions before the legacy numeric lookup."""
    from ow174.game.script.runtime import Instance

    instance = getattr(ctx, "instance", None)
    if (isinstance(instance, Instance) and instance.graph.index == MATRIX and not instance.component.strict
            and instance.component.world.hero == HERO and (cfg or {}).get("$") in expr.EXPRESSIONS):
        value = expr.evaluate(cfg, ctx)
        if value is not None:
            return value
    return NotImplemented


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


def interrupt(component, tick):
    """Use the authored cooldown-and-stop path when forced control ends a live box."""
    if component.world.hero != HERO:
        return
    for instance in component.instances.values():
        if instance.graph.index == MATRIX:
            volume = instance.states.get(3)
            if volume is not None and volume.active and not volume.ending:
                ability = instance.state(0)
                # A world hit can precede the body command for the same frame.
                frame_ms = component.frame_ms
                impact = max(component.now, (tick + 1) * frame_ms - 1)
                last_frame = max(component.now, tick * frame_ms - 1)
                drain = instance.states.get(45)
                if isinstance(drain, nodes.ChaseVar) and drain.active:
                    drain.advance_to(last_frame)
                instance.run_node(instance.graph.node(97), None)
                ability.restart(at=impact)
                if component.now < last_frame:
                    component.run_now()
                    recharge = instance.states.get(21)
                    if isinstance(recharge, nodes.Wait) and recharge.active and recharge.start == component.now:
                        recharge.reschedule(last_frame)
            return


def closed_at(instance, tick):
    """Project expiry at contact without changing the mech's command VM."""
    component = instance.component
    at = (tick + 1) * component.frame_ms - 1
    if at <= component.now:
        return False
    ability = instance.states.get(0)
    if isinstance(ability, nodes.Ability) and ability.held(29):
        release = component.now + REPEAT * component.frame_ms + 1
        tap = instance.states.get(26)
        if isinstance(tap, nodes.Wait) and tap.active:
            release = max(release, tap.start + tap.ms)
        if at >= release:
            return True
    drain = instance.states.get(45)
    return isinstance(drain, nodes.ChaseVar) and drain.active and drain.reaches_at(at)


def settle_natural_close(component, tick):
    """Advance an omitted natural close before a world hit or resumed command."""
    if component.world.hero != HERO or component.strict or component.fault is not None:
        return
    instance = next((i for i in component.instances.values() if i.graph.index == MATRIX), None)
    volume = instance.states.get(3) if instance is not None else None
    if volume is None or not volume.active or not closed_at(instance, tick - 1):
        return
    previous, previous_frame = component.now, component.frame
    held = list(component.held)
    frame_ms = component.frame_ms
    deadline = tick * frame_ms - 1
    release = previous + REPEAT * frame_ms + 1
    try:
        with component.operation():
            while component.now < deadline:
                next_frame = (component.now // frame_ms + 1) * frame_ms
                frame_end = ((component.now + 1) // frame_ms + 1) * frame_ms - 1
                sample = min(frame_end, deadline) if volume.active else deadline
                drain = instance.states.get(45)
                if (volume.active and next_frame <= sample and isinstance(drain, nodes.ChaseVar)
                        and drain.active and drain.reaches_at(next_frame)):
                    sample = next_frame
                if instance.queue:
                    sample = min(sample, max(component.now, instance.queue[0].time))
                if component.now < release <= sample and 29 in component.held:
                    component.now = release
                    component.frame = release // frame_ms
                    instance.button_edge(29, False)
                else:
                    component.now = sample
                    component.frame = sample // frame_ms
                instance.update()
    finally:
        component.now, component.frame = previous, previous_frame
        component.held[:] = held


def intercept(fight, shooter, definition, origin, direction, reach):
    """First visible active Matrix box for a known eligible projectile."""
    if definition is None or not absorbable(definition):
        return None
    from ow174.game.combat import EPSILON, ray_world

    nearest = None
    for target in fight._enemies(shooter):
        owner = target.owner
        hero = getattr(owner, "hero", None)
        if (target.construct is not None or getattr(hero, "guid", None) != HERO
                or hero.body != MECH or fight.is_stunned(owner)):
            continue
        script = owner.body_script
        if (script is None or script.retired or script.component.entity != target.entity
                or script.component.owner_dead or script.component.strict or script.component.fault is not None):
            continue
        instance = next((i for i in script.component.instances.values() if i.graph.index == MATRIX), None)
        state = instance.states.get(3) if instance is not None else None
        if (state is None or not state.active or state.ending
                or state.node.cls != "STUStatescriptStateDeflectProjectiles" or closed_at(instance, fight.tick)):
            continue
        eye = fight._eye(owner)
        bound = reach if nearest is None else nearest
        hit = defense_boxes.intersect(state, eye, owner.mover.yaw, owner.mover.pitch, origin, direction, bound)
        if hit is None or (nearest is not None and hit >= nearest):
            continue
        delta = tuple(p + d * hit - e for p, d, e in zip(origin, direction, eye, strict=True))
        distance = math.hypot(*delta)
        if distance > EPSILON:
            wall = ray_world(fight._collision(), eye, tuple(axis / distance for axis in delta), distance)
            if wall is not None and wall < distance - EPSILON:
                continue
        nearest = hit
    return nearest
