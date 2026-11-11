"""Scoped Genji/Sigma projectile interception using authored states and tags.

The eye attachment, full aim frame, box endpoint normalization and mode policy
are reconstructed. Native angles and reflected-projectile presentation remain
unverified. The separate sigma_grasp host handles scoped absorption settlement.
Unknown projectile metadata is never implicitly eligible for interception.
"""

import hashlib
import json
import math
import os
import re
from functools import cache
from pathlib import Path

from ow174.game.script import expr

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145
_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
TAGS_PATH = Path(_path).resolve() if _path else None
MATRIX_HERO = 0x02E000000000007A
MATRIX_BODY = 0x0400000000000407
DEFENSES = {
    0x02E0000000000029: (0x03A3, 0),  # Genji: Deflect
    0x02E000000000023B: (0x1D02, 1),  # Sigma: Kinetic Grasp
    MATRIX_HERO: (0x02CC, 1),  # Same authored mode, with no Grasp settlement.
}
MAX_REFLECTIONS_PER_STEP = 16


def read_tags(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("build") != BUILD:
        raise ValueError("projectile tags require build104319 version1")
    entities = data.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("projectile tag entities must be a mapping")
    result = {}
    for key, values in entities.items():
        if not isinstance(key, str) or not re.fullmatch(r"0x0400[0-9A-F]{12}", key):
            raise ValueError("projectile tag key must be a full003 GUID")
        if (not isinstance(values, list)
                or any(not isinstance(tag, str) or not re.fullmatch(r"0x0D60[0-9A-F]{12}", tag)
                       for tag in values)
                or len(set(values)) != len(values)):
            raise ValueError("invalid projectile target tags")
        result[int(key, 16)] = frozenset(int(tag, 16) for tag in values)
    return result


@cache
def tags():
    return read_tags(TAGS_PATH) if TAGS_PATH is not None else {}


def runtime_identity():
    """Identify the live cached admission data and mutable gate policy, without paths.

    A file changed after loading cannot change gameplay until the cache reloads.
    Hash that same cached table rather than later bytes on disk. Canonical order
    preserves identity across relocation and irrelevant JSON formatting/order.
    """
    known = tags()
    rows = [(f"0x{definition:016X}", sorted(values)) for definition, values in sorted(known.items())]
    catalog = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    return {
        "build": BUILD, "catalog_enabled": TAGS_PATH is not None,
        "catalog_entries": len(known), "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
        "excluded_tag": EXCLUDED_TAG, "max_reflections": MAX_REFLECTIONS_PER_STEP,
        "defenses": {f"0x{hero:016X}": tuple(spec) for hero, spec in sorted(DEFENSES.items())},
    }


def eligible(definition):
    known = tags().get(definition)
    return known is not None and EXCLUDED_TAG not in known


def matrix_ruleset_key(cfg, instance):
    """Map the three authored mech Matrix selectors to local ruleset keys."""
    if (instance.graph.index != 0x02CC or instance.component.strict
            or instance.component.world.hero != MATRIX_HERO or not isinstance(cfg, dict)):
        return None
    expression = cfg.get("m_expression") or {}
    if cfg.get("$") != "STU_919FD47C" or expression.get("m_opcodes") != [14, 0, 7, 0, 15, 0]:
        return None
    variables = expression.get("m_dynamicVars") or ()
    configs = cfg.get("m_configVars") or ()
    if len(variables) != 1 or len(configs) != 1:
        return None
    variable, selector = variables[0], configs[0]
    key = variable.get("m_identifier")
    if variable.get("m_60DB8F99") != 0 or key not in (9743, 9777, 9778):
        return None
    entity = selector.get("m_entity") or {}
    field = {"STU_F7262B36": "m_801B298F", "STU_F76C486C": "m_D63CBEC2"}.get(entity.get("$"))
    if (selector.get("$") == "STU_0AC15F78" and field is not None
            and (entity.get(field) or {}).get("$") == "STU_B5A0CAF0"):
        return key
    return None


def ray_box(origin, direction, low, high, reach):
    """Authored endpoints may be reversed (03A3's third box)."""
    near, far = 0.0, reach
    for point, velocity, a, b in zip(origin, direction, low, high, strict=True):
        a, b = min(a, b), max(a, b)
        if abs(velocity) < 1e-12:
            if point < a or point > b:
                return None
            continue
        enter, leave = (a - point) / velocity, (b - point) / velocity
        near, far = max(near, min(enter, leave)), min(far, max(enter, leave))
        if near > far:
            return None
    return near


def owner_aim(owner):
    yaw = owner.mover.yaw * 2 * math.pi / 65536
    pitch = owner.mover.pitch * 2 * math.pi / 65536
    return (math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))


def blocks_torbjorn_melee(attacker, target, attack_state, modify):
    """Host the authored 03A3/107 zero scalar for the two mapped melee attacks.

    Resolve the front-facing predicate with live feet and aim. The native
    combat callback's position operands and tag matching remain unverified.
    """
    script = getattr(target.owner, "body_script", None)
    if (attacker.hero.guid != 0x02E0000000000006
            or (attack_state.instance.graph.index, attack_state.node.pos) not in ((0x43, 10), (0x168C, 12))
            or getattr(script, "hero", None) != 0x02E0000000000029
            or script.retired or script.component.strict or script.component.owner_dead):
        return False
    delta = tuple(a - b for a, b in zip(attacker.mover.position, target.feet, strict=True))
    if sum(a * b for a, b in zip(delta, owner_aim(target.owner), strict=True)) <= 0:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0x03A3:
            continue
        for state in instance.states.values():
            node = state.node
            if not state.active or node.pos != 107 or node.cls != "STUStatescriptStateCombatModFilter":
                continue
            if node.field("m_60703B1E") != "0x0D80000000000318":
                continue
            condition = (node.field("m_6AA5D70F") or {}).get("m_condition") or {}
            expression = condition.get("m_expression") or {}
            if expression.get("m_opcodes") != [13, 0, 14, 0, 24, 14, 1, 55, 6, 0, 36, 0]:
                continue
            scalar = state.evaluate(node.field("m_50530EAB"), watch=False)
            if type(scalar) not in (int, float) or scalar != 0:
                continue
            tags = {expr.guid_of(tag) for tag in node.field("m_F60AA143") or ()}
            for row in modify.get("m_148F3152") or ():
                tag = attack_state.evaluate(row.get("m_EE3055BD"), watch=False)
                if isinstance(tag, expr.Asset) and tag.guid in tags:
                    return True
    return False


def projectile_gate(origin, direction, reach, targets, definition, *, collision=None):
    """Nearest active enemy gate; caller orders against bodies and static cover.

    Returns(distance,target,mode,defender aim,state). Live enemy/death/team eligibility
    comes from Combat._enemies. No timer or shared flag invents an active gate.
    """
    if not eligible(definition):
        return None
    best = None
    for target in targets:
        owner = target.owner
        hero = getattr(getattr(owner, "hero", None), "guid", None)
        spec = DEFENSES.get(hero)
        script = getattr(owner, "body_script", None)
        if (spec is None or script is None or script.hero != hero or script.retired or script.component.strict
                or script.component.owner_dead):
            continue
        matrix = script.hero == MATRIX_HERO
        if matrix and owner.hero.body != MATRIX_BODY:
            continue
        forward = owner_aim(owner)
        x, y, z = forward
        length = math.hypot(x, z)
        right = (z / length, 0.0, -x / length) if length > 1e-9 else (1.0, 0.0, 0.0)
        rx, ry, rz = right
        up = (y * rz - z * ry, z * rx - x * rz, x * ry - y * rx)
        basis = (right, up, forward)
        anchor = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(a - b for a, b in zip(origin, anchor, strict=True))

        def local(vector, basis=basis):
            return tuple(sum(a * b for a, b in zip(vector, axis, strict=True)) for axis in basis)

        local_origin, local_direction = local(delta), local(direction)
        for instance in script.component.instances.values():
            if instance.graph.index != spec[0]:
                continue
            for state in instance.states.values():
                if (not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles"
                        or state.node.field("m_8B251D53") != spec[1]):
                    continue
                filt = state.node.field("m_filter") or {}
                excluded = (filt.get("m_8B2A4DF3") or {}).get("m_7D7C86A1")
                if filt.get("m_27CF71D5") != 2 or excluded != f"0x{EXCLUDED_TAG:016X}":
                    continue  # never generalize this scoped adapter to another filter
                for box in state.node.field("m_boxes") or ():
                    low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                    high = state.evaluate(box.get("m_AEF67365"), watch=False)
                    if (not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3)
                            or any(not math.isfinite(v) for v in (*low, *high))):
                        raise ValueError("invalid authored projectile defense box")
                    hit = ray_box(local_origin, local_direction, low, high, reach)
                    if hit is None or (best is not None and hit >= best[0]):
                        continue
                    if matrix:
                        from ow174.game.combat import ray_world

                        offset = tuple(a + b * hit for a, b in zip(delta, direction, strict=True))
                        length = math.sqrt(sum(axis * axis for axis in offset))
                        if length > 1e-9:
                            toward_contact = tuple(axis / length for axis in offset)
                            wall = ray_world(collision, anchor, toward_contact, length)
                            if wall is not None and wall < length - 1e-5:
                                continue
                    best = (hit, target, 2 if matrix else spec[1], forward, state)
    return best
