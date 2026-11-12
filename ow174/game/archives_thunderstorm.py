"""Live combat adapter for the per-target scopes in Archives graph 2535."""

from math import dist, isfinite

from ow174.game import immunity
from ow174.game.script import expr
from ow174.game.script.bot import DEATH_VOLLEY_GRAPH


def active_tracker(component):
    instance = next((i for i in component.instances.values() if i.graph.index == DEATH_VOLLEY_GRAPH), None)
    if instance is None:
        return None
    track_node, dispatch_node = instance.graph.node(70), instance.graph.node(74)
    if track_node is None or dispatch_node is None:
        return None
    tracker = instance.states.get(track_node.state)
    dispatch = instance.states.get(dispatch_node.state)
    if tracker is None or not tracker.active or dispatch is None or not dispatch.active:
        return None
    return tracker


def advance(fight, bot, component):
    """Run the authored 74 -> 77 -> 76 -> 68 chain on one retained combat frame."""
    tracker = active_tracker(component)
    if tracker is None or not tracker.targets or bot.dead:
        return
    instance = tracker.instance
    ray, health = instance.graph.node(77), instance.graph.node(68)
    if ray is None or health is None:
        return
    reach = expr.to_float(instance.evaluate(ray.field("m_castLength")))
    amount = expr.to_float(instance.evaluate(health.field("m_modifyHealth.m_amount")))
    mode = expr.to_int(instance.evaluate(health.field("m_95F1FB0B")))
    if mode != 1 or not isfinite(reach) or not isfinite(amount) or reach <= 0 or amount <= 0:
        return
    # The mode-1 clock follows the existing ModifyHealth damage/second convention.
    damage = expr.f32(amount * component.frame_ms / 1000.0)
    selected = set(tracker.targets)  # Current TrackTargets stores body IDs, not expr.Entity values.
    # Cast against live hostile body capsules. The barrier provider handles shields.
    bodies = tuple(target for target in fight._enemies(bot) if target.construct is None
                   and not immunity.phased_out(target.owner, target.entity))
    for target in bodies:
        if target.entity in selected and not fight.vitals_of(target).dead:
            strike(fight, bot, target, bodies, reach, damage)


def strike(fight, bot, aim_target, bodies, reach, damage):
    from ow174.game import combat

    # Reconstruct hardpoint 1 with the existing body-eye anchors.
    x, y, z = bot.position
    origin = x, y + combat.EYE, z
    x, y, z = aim_target.feet
    endpoint = x, y + aim_target.eye, z
    length = dist(origin, endpoint)
    if length <= combat.EPSILON:
        return
    direction = tuple((b - a) / length for a, b in zip(origin, endpoint, strict=True))
    target, hit = None, reach
    for candidate in bodies:
        if fight.vitals_of(candidate).dead:
            continue
        a, b = candidate.segment()
        distance = combat.ray_capsule(origin, direction, a, b, candidate.radius)
        if distance is not None and distance <= reach and (target is None or distance < hit):
            target, hit = candidate, distance
    if target is None:
        return
    barrier = fight.barriers.intercept(bot, origin, direction, hit)
    contact = hit if barrier is None else barrier[0]
    wall = combat.ray_world(fight._collision(), origin, direction, contact)
    if wall is not None and wall < contact:
        return
    if barrier is not None:
        fight.barriers.damage(barrier[1], damage, bot)
        return
    fight._damage(bot, target, damage, False, None, fight.tick)
