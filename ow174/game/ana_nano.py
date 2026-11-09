"""104319 0B9E targeting and 0B9F effect values for Nano on Bastion."""

import math

from ow174.game import ana_grenade
from ow174.game.script import expr
from ow174.game.script.driver import BASTION

GRAPH = 0x0B9E
HEAL = 250.0
OUTGOING, INCOMING = 1.5, 0.5


def targets(world, state):
    if (world.hero != ana_grenade.HERO or state.instance.graph.index != GRAPH
            or state.index not in (3, 6)):
        return NotImplemented
    match = world.match
    if match is None or match.game_map.free_for_all or match.collision_world is None:
        return ()
    from ow174.game.combat import EPSILON, _dot, _sub, aim, ray_world

    fight = match.combat
    shooter = next((p for p in match.players if p.has_body and not p.gone and p.body == world.body), None)
    if shooter is None or fight.is_dead(shooter) or fight.is_stunned(shooter):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    origin, direction = fight._eye(shooter), aim(shooter.mover.yaw, shooter.mover.pitch)
    candidates = []
    for target in _allies(fight, shooter):
        centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = _sub(centre, origin)
        distance = math.sqrt(_dot(delta, delta))
        if not EPSILON < distance <= radius:
            continue
        alignment = _dot(direction, delta) / distance
        if alignment < math.cos(math.radians(10)):
            continue
        wall = ray_world(fight._collision(), origin, tuple(axis / distance for axis in delta), distance)
        if wall is None or wall >= distance - EPSILON:
            candidates.append((-alignment, distance, target.entity))
    return (min(candidates)[2],) if candidates else ()


def _allies(fight, shooter):
    return (t for t in fight._targets() if t.owner is not shooter and t.team == shooter.team
            and getattr(getattr(t.owner, "hero", None), "guid", None) == BASTION
            and not fight.vitals_of(t).dead)


def apply(fight, shooter, state):
    if fight.match.game_map.free_for_all or fight.is_dead(shooter) or fight.is_stunned(shooter):
        return
    instance = state.instance
    selected = instance.find_var(expr.INSTANCE, 8070, create=False)
    duration = instance.find_var(expr.INSTANCE, 215, create=False)
    values = selected.value() if selected is not None else None
    if isinstance(values, expr.Entity):
        values = (values,)
    duration = expr.to_float(duration.value()) if duration is not None else 0.0
    if not isinstance(values, tuple) or not math.isfinite(duration) or duration <= 0.0:
        return
    for target in _allies(fight, shooter):
        if expr.Entity(target.entity) in values:
            vitals = fight.vitals_of(target)
            fight._heal(shooter, target, vitals, HEAL)
            vitals.nano_until = max(vitals.nano_until, fight.now + duration)


def native(world, cfg, instance):
    """Bind the owner-team queries used by the authored cast branch."""
    if (world.match is None or world.hero != ana_grenade.HERO
            or instance is None or instance.graph.index != GRAPH):
        return NotImplemented
    team_queries = instance.graph.node(51).field("m_value.m_configVars")
    if not any(cfg is authored for authored in team_queries[:2]):
        return NotImplemented
    query = cfg.get("$")
    match = world.match
    caster = next((p for p in match.players if p.has_body and not p.gone and p.body == world.body), None)
    if caster is None:
        return NotImplemented
    if query == "STU_65ABFCCC":
        return caster.team
    if match.game_map.free_for_all:
        return 1
    return sum(p.has_body and not p.gone and p.team == caster.team for p in match.players)
