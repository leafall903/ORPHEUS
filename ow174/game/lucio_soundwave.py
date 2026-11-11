"""Scoped 0776 Soundwave direction queries from the Lucio movement analysis."""

import math

from ow174.game import bots
from ow174.game.mover import TURN, f32
from ow174.game.script import expr

HERO = 0x02E0000000000079
GRAPH = 0x0776
QUERIES = {"STU_2200A66D", "STU_021D01CD", "STU_0762B33A"}


def query(world, cfg, instance):
    cls = cfg.get("$")
    if (cls not in QUERIES or world.hero != HERO or instance is None or instance.graph.index != GRAPH
            or world.match is None or instance.component.strict or instance.stopped or instance.teardown):
        return NotImplemented
    player = next((actor for actor in world.match.players
                   if actor.has_body and actor.body == world.body
                   and actor.body_script is not None and not actor.body_script.retired
                   and actor.body_script.component is instance.component), None)
    if player is None:
        return NotImplemented
    if cls == "STU_0762B33A":
        return soundwave_direction(instance, cfg)
    target = cfg.get("m_entity") or {}
    if (target.get("$") != "STU_B5A0CAF0"
            or instance.evaluate(target) != expr.Entity(world.body)):
        return NotImplemented
    yaw = player.mover.yaw * TURN
    sine, cosine = f32(math.sin(yaw)), f32(math.cos(yaw))
    if cls == "STU_021D01CD":
        return expr.Vec3(sine, 0., cosine)
    pitch = player.mover.pitch * TURN
    horizontal = f32(math.cos(pitch))
    return expr.Vec3(f32(sine * horizontal), f32(-math.sin(pitch)), f32(cosine * horizontal))


def soundwave_direction(instance, cfg):
    """CEB820's actual0776 family: desired yaw/pitch around its forward+up basis.

    This computes the resource's vector only. It does not apply health knockback
    to another body, choose resistance, or implement the general limiter API.
    """
    yaw_limit = instance.evaluate(cfg.get("m_5023B9B0"))
    pitch_limit = instance.evaluate(cfg.get("m_E26E5E09"))
    if yaw_limit != 90 or pitch_limit != 25:
        return NotImplemented
    desired = instance.evaluate(cfg.get("m_32B00625"))
    basis = instance.evaluate(cfg.get("m_3661BC45"))
    return limit_direction(desired, basis, yaw_limit, pitch_limit)


def limit_direction(desired, basis, yaw_limit, pitch_limit):
    """Clamp desired angles around the basis for selected native query families."""
    if (not isinstance(desired, expr.Vec3) or not isinstance(basis, expr.Vec3)
            or not all(map(math.isfinite, (*desired, *basis)))):
        return NotImplemented
    angles = []
    for value in (desired, basis):
        square = f32(sum(f32(axis * axis) for axis in value))
        if square < expr.EPSILON:
            return expr.EVALUATION_FAILED
        length = math.sqrt(square)
        x, y, z = (f32(axis / length) for axis in value)
        angles.append((math.atan2(x, z), math.asin(max(-1., min(1., -y)))))
    result = []
    for wanted, centre, limit in zip(angles[0], angles[1], (yaw_limit, pitch_limit), strict=True):
        difference = (wanted - centre + math.pi) % math.tau - math.pi
        limit = math.radians(limit)
        result.append(centre + max(-limit, min(limit, difference)))
    yaw, pitch = result
    horizontal = math.cos(pitch)
    return expr.Vec3(f32(math.sin(yaw) * horizontal), f32(-math.sin(pitch)),
                     f32(math.cos(yaw) * horizontal))


def contact(fight, player, command, script):
    """Apply actual 0776 st11 once, using reconstructed target selection."""
    from ow174.game.combat import (
        EPSILON,
        _dot,
        _number,
        _sub,
        aim,
        capsule_visible,
        hit_def,
        segment_distance,
    )

    if player.hero.guid != HERO or script.component.strict:
        return
    for instance in script.component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        state, search = instance.states.get(11), instance.states.get(10)
        if state is None or search is None:
            continue
        counters = fight.melee_seen.setdefault(script.entity, {})
        key = (instance.id, state.index)
        seen = counters.get(key, 0)
        counters[key] = state.counter
        if state.counter == seen or not state.active or command is None:
            continue
        hit = hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
        radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
        angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
        if hit is None or hit.amount <= 0 or radius <= 0 or not 0 < angle <= 360:
            continue
        vector = state.evaluate(state.node.field("m_modifyHealth.m_1376B4DF"), watch=False)
        origin, direction = fight._eye(player), aim(*command.aim())
        threshold = math.cos(math.radians(angle / 2))
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        for target in fight._enemies(player):
            shown = fight.seen(target, view_tick)
            if shown is None:
                continue
            a, b = shown.segment()
            if segment_distance(origin, a, b) - shown.radius > radius:
                continue
            centre = (shown.feet[0], shown.feet[1] + shown.height / 2, shown.feet[2])
            offset = _sub(centre, origin)
            length = math.sqrt(_dot(offset, offset))
            if length > EPSILON and _dot(offset, direction) < threshold * length:
                continue
            if (not capsule_visible(fight._collision(), origin, a, b, shown.radius)
                    or fight.walls.blocked(origin, centre)):
                continue
            health = fight.vitals_of(target)
            before = health.total
            fight._damage(player, target, hit.amount, False, hit.loadout, command.frame)
            if (health.total < before and not health.dead and not isinstance(target.owner, bots.Bot)
                    and not fight.fortify_control_immune(target)
                    and isinstance(vector, expr.Vec3) and all(map(math.isfinite, vector))):
                fight.npc_impulses[target.entity] = (fight.tick, tuple(vector))
