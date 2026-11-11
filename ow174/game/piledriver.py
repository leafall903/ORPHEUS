"""Selected 160A Piledriver mover and landing services for build 104319."""

import math

from ow174.game.script import expr, nodes

HERO = 0x02E00000000001CA
GRAPH = 0x160A
AIR_LOCK_SECONDS = .5


def instance_of(player):
    if player.hero.guid != HERO or player.body_script is None:
        return None
    component = player.body_script.component
    if component.strict or component.owner_dead:
        return None
    return next((i for i in component.instances.values()
                 if i.graph.index == GRAPH and not i.stopped and not i.teardown), None)


def native(world, cfg, instance):
    cls = cfg.get("$")
    if (cls not in ("STU_8CBDD81C", "STU_71A87E92") or instance is None
            or world.hero != HERO or instance.graph.index != GRAPH or world.match is None):
        return NotImplemented
    entity = instance.evaluate(cfg.get("m_entity"))
    if entity != expr.Entity(world.body):
        return NotImplemented
    return world.match.script_body_position(world.body, world.body)


def observe(player):
    instance = instance_of(player)
    if instance is None:
        return
    mover = player.mover
    component = instance.component
    component.set(417, mover.airborne)
    component.set(545, not mover.airborne)
    node = instance.graph.state(38)
    origin = instance.evaluate(node.field("m_startPosWS"))
    direction = instance.evaluate(node.field("m_dirWS"))
    length = expr.to_float(instance.evaluate(node.field("m_castLength")))
    if not isinstance(origin, expr.Vec3) or not isinstance(direction, expr.Vec3):
        return
    magnitude = math.hypot(*direction)
    if magnitude <= 0 or length <= 0:
        return
    unit = tuple(axis / magnitude for axis in direction)
    hit = mover.world.cast_ray(tuple(origin), unit, length, mover.category)
    component.set(2413, hit is not None)


def movement(player, seconds):
    instance = instance_of(player)
    if instance is None or not instance.state(24).flags & nodes.Ability.RUNNING:
        return None
    windup = instance.states.get(18)
    if windup is not None and windup.active:
        player.mover.velocity = (0., 0., 0.)
        return tuple(player.mover.position), 1.0
    descent = instance.states.get(3)
    if descent is None or not descent.active or not player.mover.airborne:
        return None
    rows = descent.node.field("m_BC5E91CF") or ()
    acceleration = next((abs(expr.to_float(descent.evaluate(row.get("m_9B7A63EA"), watch=False)))
                         for row in rows if row.get("m_A9561CA0") == 34), 0.0)
    cap = expr.to_float(instance.evaluate(instance.graph.state(25).field("m_answer")))
    if acceleration <= 0 or cap <= 0:
        return None
    velocity = max(-cap, min(-5.0, player.mover.velocity[1]) - acceleration * seconds)
    x, y, z = player.mover.position
    return (x, y + 2 * velocity * seconds, z), abs(velocity)


def command(fight, player, command):
    from ow174.game.combat import TICK, capsule_visible, segment_distance

    instance = instance_of(player)
    state = instance.states.get(7) if instance is not None else None
    if state is None:
        return
    counters = fight.melee_seen.setdefault(player.body, {})
    key = instance.id, state.index
    seen = counters.get(key, 0)
    counters[key] = state.counter
    if (command is None or state.counter == seen
            or state.start // instance.component.frame_ms != command.frame):
        return
    anchor = instance.find_var(expr.INSTANCE, 1175, create=False)
    centre = anchor.value() if anchor is not None else None
    if not isinstance(centre, expr.Vec3):
        return
    centre = tuple(centre)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    damage_node, pull_node = instance.graph.node(11), instance.graph.node(12)
    modify = instance.graph.state(5).field("m_modifyHealth")
    loadout = instance.evaluate(modify["m_loadout"])
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    lift = expr.to_float(instance.evaluate(modify["m_583C208B"]))
    for target in fight._enemies(player):
        a, b = target.segment()
        distance = max(0.0, segment_distance(centre, a, b) - target.radius)
        if distance > radius or not capsule_visible(fight._collision(), centre, a, b, target.radius,
                                                    walls=fight.walls):
            continue
        instance.write((expr.INSTANCE, 680), distance)
        amount = expr.to_float(instance.evaluate(damage_node.field("m_value")))
        bubble = fight.zarya_bubbles.for_target(target.owner)
        fight._damage(player, target, amount, False, loadout, command.frame)
        if bubble is not None or lift <= 0:
            continue
        pull = expr.to_float(instance.evaluate(pull_node.field("m_value")))
        dx, dz = centre[0] - target.feet[0], centre[2] - target.feet[2]
        planar = math.hypot(dx, dz)
        scale = pull / planar if planar > 0 else 0.0
        moved = fight.impulses.vector(target, expr.Vec3(dx * scale, lift, dz * scale))
        if moved and target.owner in fight.match.players:
            end = fight.tick + math.ceil(AIR_LOCK_SECONDS / TICK)
            fight.pile_air_locks[target.entity] = (fight.tick, end)


def steering_locked(fight, player, tick):
    start, deadline = fight.pile_air_locks.get(player.body, (0, 0))
    return (start <= tick < deadline
            and (player.mover.airborne or player.body in fight.impulses.live))
