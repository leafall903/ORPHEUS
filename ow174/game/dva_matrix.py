"""Scoped 104319 mech 02CC resource selector and Bastion shot interception."""

import math

from ow174.game import deflection
from ow174.game.script import expr

HERO = 0x02E000000000007A
MECH = 0x0400000000000407


def controller(world, instance, cfg):
    source = cfg.get("m_801B298F") or {}
    if (instance is None or instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index not in (0x276, 0x2CC)
            or cfg.get("$") != "STU_F7262B36" or source.get("$") != "STU_B5A0CAF0"):
        return NotImplemented
    for player in world.match.players:
        if (player.has_body and not player.gone and player.body == world.body == instance.component.entity
                and player.hero.body == MECH):
            return expr.Entity(player.entity)
    return NotImplemented


def ruleset_key(cfg, instance):
    if (instance.graph.index != 0x2CC or instance.component.strict
            or instance.component.world.hero != HERO or not isinstance(cfg, dict)):
        return None
    expression = cfg.get('m_expression') or {}
    if cfg.get('$') != 'STU_919FD47C' or expression.get('m_opcodes') != [14, 0, 7, 0, 15, 0]:
        return None
    variables, configs = expression.get('m_dynamicVars') or [], cfg.get('m_configVars') or []
    if len(variables) != 1 or len(configs) != 1:
        return None
    variable = variables[0]
    if variable.get('m_60DB8F99') != 0 or variable.get('m_identifier') not in (9743, 9777, 9778):
        return None
    selector = configs[0]
    entity = selector.get('m_entity') or {}
    field = {'STU_F7262B36': 'm_801B298F', 'STU_F76C486C': 'm_D63CBEC2'}.get(entity.get('$'))
    if (selector.get('$') != 'STU_0AC15F78' or field is None
            or (entity.get(field) or {}).get('$') != 'STU_B5A0CAF0'):
        return None
    return variable['m_identifier']


def active(fight, owner):
    script = getattr(owner, 'body_script', None)
    if (script is None or script.retired or script.hero != HERO or owner.hero.body != MECH
            or fight.is_dead(owner) or fight.is_stunned(owner)):
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x2CC), None)
    state = instance.states.get(3) if instance else None
    return state if state is not None and state.active and not state.ending else None


def intercept(fight, shooter, origin, direction, reach):
    from ow174.game import bots, matrix
    from ow174.game.combat import EPSILON, aim, ray_world

    nearest = None
    for target in fight._enemies(shooter):
        owner = target.owner
        if isinstance(owner, bots.Bot):
            if owner.dva_mover is None or owner.dva_shell or fight.is_stunned(owner):
                continue
            x, y, z = target.feet
            eye = (x, y + target.eye, z)
            forward = aim(round(owner.yaw), round(owner.pitch))
            hit = matrix.matrix_distance(owner, origin, direction, reach, eye, forward)
        else:
            state = active(fight, owner)
            if state is None:
                continue
            eye = fight._eye(owner)
            hit, _ = deflection.intercept(state, eye, owner.mover.yaw, owner.mover.pitch,
                                          origin, direction, reach)
        if hit is None or (nearest is not None and hit >= nearest):
            continue
        delta = tuple(p + d * hit - e for p, d, e in zip(origin, direction, eye, strict=True))
        distance = math.sqrt(sum(axis * axis for axis in delta))
        if distance > EPSILON:
            wall = ray_world(fight._collision(), eye, tuple(axis / distance for axis in delta), distance)
            if wall is not None and wall < distance - EPSILON:
                continue
        nearest = hit
    return nearest
