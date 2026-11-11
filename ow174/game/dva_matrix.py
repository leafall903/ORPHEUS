"""Scoped104319 mech02CC ruleset bridge; native foreign hosting is unaccepted.

Matrix's GetGameRulesetValue uses owner/possessor entity member references
to9743/9777/9778. Reconstruct those exact selectors as the local D.Va
ruleset keys (260F enabled,2631 recovery,2632 resource scalar), preserving overrides.
This does not implement arbitrary foreign ruleset entities or mech spawning.
Active02CC/state3 deletes108B/0946 on first contact.08BE has excluded0145.
Transform/order/deletion consumers are reconstructed; native outputs,
achievements and projectile presentation remain unaccepted.
"""

HERO = 0x02E000000000007A
MECH = 0x0400000000000407


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
    if (script is None or script.hero != HERO or owner.hero.body != MECH
            or fight.is_dead(owner) or fight.frozen(owner) or fight.is_stunned(owner)):
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x2CC), None)
    state = instance.states.get(3) if instance else None
    return state if state is not None and state.active else None


def cast(fight, shooter, definition, origin, direction, length):
    from ow174.game import genji_deflect
    from ow174.game.roadhog_projectiles import SCRAP, WHOLE_HOG
    from ow174.game.symmetra_beam import ORB

    if (definition not in (genji_deflect.ICICLE, genji_deflect.BLIZZARD, ORB, WHOLE_HOG)
            and definition not in SCRAP):
        return None
    best = None
    for owner in fight.match.players:
        if owner is shooter or (not fight.match.game_map.free_for_all and owner.team == shooter.team):
            continue
        state = active(fight, owner)
        if state is None:
            continue
        hit = genji_deflect.contact(fight, owner, state, origin, direction, length)
        if hit is not None:
            from ow174.game.combat import ray_world

            point = tuple(p + d * hit[0] for p, d in zip(origin, direction, strict=True))
            eye = fight._eye(owner)
            delta = tuple(p - e for p, e in zip(point, eye, strict=True))
            reach = sum(d * d for d in delta) ** .5
            wall = ray_world(fight.match.collision_world, eye,
                             tuple(d / reach for d in delta), reach) if reach > 1e-9 else None
            # Reconstructed owner-to-contact LOS: a long matrix box cannot
            # swallow projectiles through static terrain or an Ice Wall.
            if (wall is not None and wall < reach - 1e-5) or fight.walls.blocked(eye, point):
                continue
        if hit is not None and (best is None or hit[0] < best[0]):
            best = hit
    return best


def interrupt(owner):
    script = getattr(owner, 'body_script', None)
    if script is None or getattr(script, 'hero', None) != HERO:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x2CC), None)
    ability = instance.states.get(0) if instance else None
    if ability is not None:
        ability.stop()
