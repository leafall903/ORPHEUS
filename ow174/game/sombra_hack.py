"""Sombra's authored Hack target and application bridge."""

import math

from ow174.game import bastion_repair, body_protection, tracer, ultimate, zarya_barrier
from ow174.game.script import expr, graph, nodes, play
from ow174.game.script.runtime import RaycastResult

HERO = 0x02E000000000012E
HACK = 0x0B1B
HOSTED_HACK_TARGETS = (tracer.HERO, ultimate.BASTION)


def caster(world):
    if world.match is None or world.hero != HERO:
        return None
    return next((p for p in world.match.players if p.has_body and p.body == world.body), None)


def clear(fight, player, origin, ray, length):
    from ow174.game import combat, sigma_hyperspheres

    point = combat._along(origin, ray, length)
    return (combat.capsule_visible(fight._collision(), origin, point, point, 0, walls=fight.walls)
            and sigma_hyperspheres.barrier_contact(fight, player, origin, ray, length) is None)


def targets(player, state):
    from ow174.game import combat

    fight = player.match.combat
    if fight.is_dead(player) or fight.is_stunned(player):
        return ()
    origin = fight._eye(player)
    direction = combat.aim(player.mover.yaw, player.mover.pitch)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    locked = state.instance.find_var(graph.INSTANCE, 375).value()
    locked_ids = {expr.to_entity(e) for e in locked} if isinstance(locked, tuple) else set()
    found = []
    for target in fight._enemies(player):
        if body_protection.active(target.owner):
            continue
        a, b = target.segment()
        ray = direction
        if state.node.pos == 52:
            if target.entity not in locked_ids:
                continue
            nearest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
            delta = combat._sub(nearest, origin)
            distance = math.hypot(*delta)
            if distance <= combat.EPSILON or combat._dot(delta, direction) <= 0:
                continue
            ray = tuple(axis / distance for axis in delta)
            length = max(0, distance - target.radius)
        else:
            length = combat.ray_capsule(origin, ray, a, b, target.radius)
        if length is not None and length <= radius and clear(fight, player, origin, ray, length):
            found.append((length, target.entity))
    return (min(found)[1],) if found else ()


def prepare(player, command):
    if player.hero.guid != HERO or not command.buttons & 2:
        return
    instances = player.body_script.component.instances.values()
    instance = next((i for i in instances if i.graph.index == HACK), None)
    if instance is None:
        return
    if instance.state(53).flags & nodes.Ability.RUNNING and instance.find_var(graph.INSTANCE, 375).value():
        return
    state = instance.node_state(instance.graph.node(68))
    instance.write((graph.INSTANCE, 375), tuple(expr.Entity(e) for e in targets(player, state)))


def track_ray(player, state):
    from ow174.game import combat

    fight = player.match.combat
    entity = expr.to_entity(state.instance.find_var(graph.INSTANCE, 8909).value())
    target = next((t for t in fight._enemies(player) if t.entity == entity), None)
    if target is None or body_protection.active(target.owner):
        return RaycastResult(False, None, None, None)
    origin = fight._eye(player)
    point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
    delta = combat._sub(point, origin)
    distance = math.hypot(*delta)
    if distance <= combat.EPSILON:
        return RaycastResult(True, expr.Entity(target.entity), expr.Vec3(*point), None)
    direction = tuple(v / distance for v in delta)
    hit = clear(fight, player, origin, direction, max(0, distance - target.radius))
    return RaycastResult(hit, expr.Entity(target.entity) if hit else None, expr.Vec3(*point), None)


def target_hero(target):
    return getattr(getattr(target.owner, "hero", None), "guid", None)


def emp_targets(player, state):
    from ow174.game import combat

    fight = player.match.combat
    if fight.is_dead(player) or fight.is_stunned(player):
        return ()
    origin = fight._eye(player)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    found = []
    for target in fight._enemies(player):
        if body_protection.active(target.owner):
            continue
        if target_hero(target) not in HOSTED_HACK_TARGETS and zarya_barrier.active(target.owner) is None:
            continue
        a, b = target.segment()
        if combat.segment_distance(origin, a, b) - target.radius > radius:
            continue
        if combat.capsule_visible(fight._collision(), origin, a, b, target.radius, walls=fight.walls):
            found.append(target.entity)
    manager = fight.reinhardt_barriers
    if manager.geometry is not None:
        for owner, _ in manager.enemies(player):
            anchor, _ = manager.pose(owner)
            if (math.dist(origin, anchor) <= radius and combat.capsule_visible(
                fight._collision(), origin, anchor, anchor, 0, walls=fight.walls,
            )):
                # The shield is stored under the owner body in this host.
                found.append(owner.body)
    return tuple(found)


def apply(world, instance, node):
    from ow174.game import combat

    if world.match is None:
        return
    parent = instance.graph.index
    source = instance.source_entity() if parent == 0x0B32 else world.body
    player = next((p for p in world.match.players if p.has_body and p.body == source
                   and p.hero.guid == HERO), None)
    if player is None:
        return
    child = 0x0B32 if parent == 0x0B31 else 0x0B1F
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != (0x0580000000000000 | child):
        return
    request = play.resolve_request(
        instance, node, resource, context_provider=lambda: play.InvocationContext(source, source, source),
    )
    definition = graph.graph(child)
    if request is None or definition is None:
        return
    fight = world.match.combat
    if child == 0x0B32:
        config = definition.node(3).field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(config.get("m_amount")))
        manager = fight.reinhardt_barriers
        for owner, barrier in tuple(manager.enemies(player)):
            if owner.body in request.targets:
                manager.damage(owner, barrier, amount)
    for target in fight._enemies(player):
        if target.entity not in request.targets or body_protection.active(target.owner):
            continue
        if child == 0x0B32:
            for pet in tuple(zarya_barrier.particle_barriers(target.owner)):
                # EMP's shield branch supplies no attacker for energy credit.
                zarya_barrier.damage_particle_barrier(pet, fight.vitals_of(target), amount, attacker=0)
        hero = target_hero(target)
        if hero not in HOSTED_HACK_TARGETS:
            continue
        script = target.owner.body_script
        if script is None or script.retired:
            continue
        application = script.component.play_loaded_graph(
            definition, source=request.context.caller, params=request.params,
            overrides=resource.overrides, policy=request.policy, instigator=request.context.instigator,
        )
        if application is None or child != 0x0B1F:
            continue
        duration = expr.to_float(application.evaluate(definition.node(96).field("m_value")))
        if duration <= 0:
            continue
        vitals = fight.vitals_of(target)
        vitals.hack_until = max(vitals.hack_until, fight.tick + math.ceil(duration / combat.TICK))
        if hero == ultimate.BASTION:
            bastion_repair.interrupt(script.component)
            continue
        for current in script.component.instances.values():
            if current.graph.index == 0x0034:
                for state in tuple(current.states.values()):
                    if isinstance(state, nodes.Ability):
                        state.presses.clear()
                        if state.flags & nodes.Ability.RUNNING:
                            state.stop()

def drain_shields(world, state):
    from ow174.game import combat

    if world.match is None:
        return
    fight = world.match.combat
    source = state.instance.source_entity()
    player = next((p for p in world.match.players if p.has_body and p.body == source), None)
    if player is None:
        return
    target = next((t for t in fight._enemies(player) if t.entity == world.body), None)
    if target is None:
        return
    config = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(config.get("m_amount"), watch=False))
    vitals = fight.vitals_of(target)
    shields = vitals.now[combat.SHIELDS_POOL] + sum(p.now for p in vitals.temporary_shields.values())
    fight._damage(player, target, min(shields, amount), False, None, fight.tick,
                  world_tick=fight.tick, tags=combat.damage_tags(state, config), source_scaled=True)
