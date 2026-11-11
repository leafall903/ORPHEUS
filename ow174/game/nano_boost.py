"""Scoped 104319 0B9E target search and B9F combat effects."""

import math

from ow174.game import ana_grenade, mei, stats
from ow174.game.script import expr

HERO = 0x02E000000000013B
GRAPH = 0x0B9E
HEALING, OUTGOING, INCOMING = 250., 1.5, .5


def tracks(state):
    return (state.instance.component.world.hero == HERO
            and state.instance.graph.index == GRAPH and state.index in (3, 6))


def team_size(world, cfg, instance):
    selector = cfg.get("m_team") or {}
    if (cfg.get("$") != "STU_D289FA32" or instance.graph.index != GRAPH or world.match is None
            or selector.get("$") != "STU_65ABFCCC"
            or (selector.get("m_target") or {}).get("$") != "STU_B5A0CAF0"):
        return NotImplemented
    match = world.match
    caster = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if caster is None:
        return 0
    return sum(target.team == caster.team for target in match.combat._targets())


def candidates(state):
    from ow174.game.combat import aim, ray_world

    match = getattr(state.instance.component.world, "match", None)
    if match is None or match.collision_world is None or match.game_map.free_for_all:
        return ()
    fight = match.combat
    body = state.instance.component.entity
    caster = next((p for p in match.players if p.has_body and p.body == body), None)
    if caster is None or fight.is_dead(caster) or fight.is_stunned(caster) or fight.frozen(caster):
        return ()
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin, direction = fight._eye(caster), aim(caster.mover.yaw, caster.mover.pitch)
    found = []
    for target in fight._targets():
        if target.owner is caster or target.team != caster.team or fight.vitals_of(target).dead:
            continue
        centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = tuple(p - o for p, o in zip(centre, origin, strict=True))
        distance = math.hypot(*delta)
        if not 0 < distance <= reach:
            continue
        ray = tuple(axis / distance for axis in delta)
        alignment = sum(a * b for a, b in zip(direction, ray, strict=True))
        if alignment < math.cos(math.radians(10.)):
            continue
        wall = ray_world(match.collision_world, origin, ray, distance)
        if (wall is None or wall >= distance) and not fight.walls.blocked(origin, centre):
            found.append((-alignment, distance, target.entity))
    return (expr.Entity(min(found)[2]),) if found else ()


def release(fight, caster, state):
    if fight.match.game_map.free_for_all or fight.is_dead(caster):
        return
    target_var = state.instance.find_var(expr.INSTANCE, 8070, create=False)
    duration_var = state.instance.find_var(expr.INSTANCE, 215, create=False)
    if target_var is None or duration_var is None:
        return
    selected, duration = target_var.value(), expr.to_float(duration_var.value())
    if not math.isfinite(duration) or duration <= 0.:
        return
    if isinstance(selected, expr.Entity):
        selected = (selected,)
    if not isinstance(selected, tuple):
        return
    for target in fight._targets():
        if (expr.Entity(target.entity) not in selected or target.owner is caster
                or target.team != caster.team):
            continue
        vitals = fight.vitals_of(target)
        if vitals.dead:
            continue
        if mei.protected(target.owner):
            fight._cryo_healing(target.owner)
        given = vitals.heal(ana_grenade.healing_amount(vitals, HEALING, fight.now))
        if given:
            caster.stats.add(stats.HEALING, given)
            fight._sync_body_health(target.owner, vitals)
        vitals.nano_until = max(vitals.nano_until, fight.now + duration)
