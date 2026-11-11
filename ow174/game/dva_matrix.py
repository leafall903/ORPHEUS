"""D.Va Matrix interception and Boosters contact for the prepared mech.

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

from ow174.game.script import expr, rulesets

BUILD = "1.74.0.0.104319"
HERO = 0x02E000000000007A
MECH = 0x0400000000000407
MATRIX = 0x02CC
BOOSTERS = 0x047E
BOOSTERS_LOADOUT = 0x0B900000000000D0
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


def matrix_distance(player, origin, direction, reach, anchor, forward):
    """Distance to active authored02CC/st3 boxes in a reconstructed aim frame.

    The caller supplies a live enemy player, eye anchor and normalized aim.
    Death, team, static cover and nearer body ordering belong to Combat.
    """
    hero = getattr(player, "hero", None)
    script = getattr(player, "body_script", None)
    if (hero is None or hero.guid != HERO or hero.body != MECH
            or script is None or script.retired or script.component.owner_dead
            or script.component.strict or script.component.fault is not None):
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
        if instance.graph.index != MATRIX:
            continue
        state = instance.state(3)
        if state is None or not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles":
            continue
        for box in state.node.field("m_boxes") or ():
            low = state.evaluate(box.get("m_CBA118B2"), watch=False)
            high = state.evaluate(box.get("m_AEF67365"), watch=False)
            if (not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3)
                    or any(not math.isfinite(v) for v in (*low, *high))
                    or any(a > b for a, b in zip(low, high, strict=True))):
                raise ValueError("invalid authored Matrix box")
            hit = ray_box(local_origin, local_direction, low, high, reach)
            if hit is not None and (best is None or hit < best):
                best = hit
    return best


def possession(world, cfg, instance):
    """Resolve the live possession relationships used by the prepared mech."""
    if (world.hero != HERO or world.match is None or instance.component.strict
            or instance.graph.index not in (0x0276, MATRIX, BOOSTERS)
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
    """Supply scoped authored Matrix and Boosters ruleset defaults."""
    world = instance.component.world
    if (not instance.component.strict and world.hero == HERO and entity == instance.component.entity
            and (instance.graph.index, var) in ((MATRIX, 9777), (MATRIX, 9778), (MATRIX, 9743),
                                                (BOOSTERS, 10611))):
        return rulesets.default(var, world.hero, world.mode)
    return NotImplemented


def ruleset_expression(ctx, cfg):
    """Preserve typed mech expressions before the legacy numeric lookup."""
    instance = getattr(ctx, "instance", None)
    if (instance is not None and instance.graph.index in (MATRIX, BOOSTERS) and not instance.component.strict
            and instance.component.world.hero == HERO and (cfg or {}).get("$") in expr.EXPRESSIONS):
        value = expr.evaluate(cfg, ctx)
        if value is not None:
            return value
    return NotImplemented


def booster_contact(fight, mech, command, script):
    """Resolve 047E's live tracker against bodies, once per target per activation."""
    if mech.hero.body != MECH or script.component.strict or fight.match.ended:
        return
    from ow174.game import combat, sigma

    for instance in script.component.instances.values():
        if instance.graph.index != BOOSTERS:
            continue
        track, damage = instance.states.get(6), instance.states.get(7)
        if track is None or damage is None or not (track.active and damage.active):
            continue
        offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False)
        radius = expr.to_float(track.evaluate(track.node.field("m_radius"), watch=False))
        hit = combat.hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
        if not isinstance(offset, expr.Vec3) or radius <= 0 or hit.amount <= 0:
            break
        mover = mech.mover
        direction = combat.aim(mover.yaw, mover.pitch)
        right = combat.aim(mover.yaw - 16384, 0)
        up = combat.aim(mover.yaw, mover.pitch - 16384)
        centre = tuple(p + x * offset.x + y * offset.y + z * offset.z
                       for p, x, y, z in zip(mover.position, right, up, direction, strict=True))
        activation = instance.id, damage.counter
        contacts = fight.booster_contacts.get(mech.body)
        if contacts is None or contacts[:2] != activation:
            contacts = fight.booster_contacts[mech.body] = (*activation, set())
        seen = contacts[2]
        eye, collision = fight._eye(mech), fight._collision()
        for target in fight._enemies(mech):
            if target.entity in seen:
                continue
            bottom, top = target.segment()
            if (combat.segment_distance(centre, bottom, top) > radius + target.radius
                    or not combat.capsule_visible(collision, eye, bottom, top, target.radius)):
                continue
            seen.add(target.entity)
            vitals = fight.vitals_of(target)
            before = vitals.total
            fight._damage(mech, target, hit.amount, False, hit.loadout, command.frame)
            if vitals.total < before:
                sigma.apply_impact_impulse(fight, direction, target, hit.knockback, mover.position)
        return
    fight.booster_contacts.pop(mech.body, None)
