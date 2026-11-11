"""Selected Heavy Assault 1408 travel and graze reconstruction."""

import math

from ow174.game import defenses, zarya_barrier
from ow174.game.script import expr, graph, play

BODY = 0x0400000000001EBB
GRAPH = 0x1408
BUTTON = 35
ABILITY = 82
BARRAGE_RESPONSE_RANGE = 12.0  # local autonomous tactic, independent of the authored contact searches
MOVEMENT = 71
WALL_RAY = 172
PHASE = 59
GRAZE_SEARCH = 32
GRAZE_HEALTH = 35
FRONT_SEARCH = 96
PIN_ACTION, VICTIM_GRAPH, CRUSH_HEALTH = 158, 0x1407, 29
IMPACT_SEARCH, IMPACT_HEALTH, IMPACT_CONTROL = 100, 102, 189
STUN_GRAPH_GUID = 0x058000000000224E


def charge(bot):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def phase(instance):
    var = instance.vars.get(PHASE)
    return expr.to_int(var.value()) if var is not None else 0


def travelling(instance):
    state = instance.states.get(instance.graph.node(MOVEMENT).state)
    return phase(instance) == 1 and state is not None and state.active


def responds_to_barrage(bot, player, distance):
    instance = charge(bot)
    if instance is None:
        return False
    if phase(instance):
        return True
    script = player.body_script
    ability = instance.states.get(instance.graph.node(ABILITY).state)
    return (distance <= BARRAGE_RESPONSE_RANGE and script is not None
            and script.hero == 0x02E0000000000008 and script.ultimate_running()
            and ability is not None and ability.active and ability.remaining() <= 0)


def stop(bot, fight):
    instance = charge(bot)
    if instance is not None and phase(instance):
        bot.velocity = (0.0, 0.0, 0.0)
        instance.write((expr.INSTANCE, PHASE), 0)
    fight.heavy_grazes.pop(bot.entity, None)
    release(fight, bot.entity)


def targets(world, state):
    from ow174.game import combat

    instance = state.instance
    if (world.match is None or instance.graph.index != GRAPH
            or state.node.pos not in (GRAZE_SEARCH, FRONT_SEARCH, IMPACT_SEARCH)):
        return NotImplemented
    fight = world.match.combat
    actor = next((bot for bot in fight.match.bots if bot.entity == instance.component.entity), None)
    if actor is None or charge(actor) is not instance or not travelling(instance):
        return ()
    frontal = state.node.pos == FRONT_SEARCH
    impact = state.node.pos == IMPACT_SEARCH
    bound = fight.heavy_pins.get(actor.entity)
    if frontal and bound is not None:
        return (bound[0].body,)
    offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    front = instance.graph.node(FRONT_SEARCH)
    front_cone = expr.to_float(instance.evaluate(front.field("m_A30B383E")))
    side_cone = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    forward = combat.aim(round(actor.yaw), 0)
    x, y, z = actor.position
    origin = (x, y + offset.y, z)
    centre = combat._along(origin, forward, offset.z)
    found = []
    for target in fight._enemies(actor):
        a, b = target.segment()
        dx, dz = a[0] - centre[0], a[2] - centre[2]
        distance = math.hypot(dx, dz)
        alignment = dx * forward[0] + dz * forward[2]
        in_front = alignment >= distance * math.cos(math.radians(front_cone))
        if not impact and (alignment < distance * math.cos(math.radians(side_cone / 2))
                           or frontal != in_front):
            continue
        if impact and bound is not None and target.owner is bound[0]:
            continue
        if frontal and (getattr(target.owner, "client", None) is None or fight.is_stunned(target.owner)
                        or defenses.phase(target.owner)[0] or defenses.fortified(target.owner)
                        or zarya_barrier.attached_to(fight, target.entity) is not None):
            continue
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius, walls=fight.walls)):
            found.append((distance, target.entity))
    if frontal:
        return (min(found)[1],) if found else ()
    return tuple(entity for _, entity in found)


def pin(world, instance, node):
    if world.match is None or instance.graph.index != GRAPH or node.pos != PIN_ACTION:
        return
    fight = world.match.combat
    source = instance.component.entity
    actor = next((b for b in fight.match.bots if b.entity == source), None)
    if (actor is None or charge(actor) is not instance or not travelling(instance)
            or source in fight.heavy_pins):
        return
    tracker = instance.states.get(instance.graph.node(FRONT_SEARCH).state)
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(VICTIM_GRAPH)
    if (tracker is None or not tracker.active or resource is None or definition is None
            or resource.guid != 0x0580000000001407):
        return
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    if request is None:
        return
    admitted = targets(world, tracker)
    for target in fight._enemies(actor):
        if target.entity not in admitted or target.entity not in request.targets:
            continue
        script = target.owner.body_script
        if script is None or script.retired:
            return
        app = script.component.play_loaded_graph(
            definition, source=source, instigator=source, params=request.params,
            overrides=resource.overrides, policy=request.policy,
        )
        if app is not None:
            fight.heavy_pins[source] = (target.owner, app)
            fight._interrupt(target)
        return


def active(app):
    state = app.states.get(app.graph.node(1).state)
    return not app.stopped and state is not None and state.active


def controlled(fight, player):
    script = getattr(player, "body_script", None)
    return script is not None and any(
        owner is player and app.component is script.component and active(app)
        for owner, app in fight.heavy_pins.values()
    )


def place(bot, player, app):
    from ow174.game import combat
    from ow174.game.mover import mover_data

    direction = combat.aim(round(bot.yaw), 0)
    mover = player.mover
    offset = mover_data(bot.body).stand_radius + mover.data.stand_radius
    mover.position[:] = combat._along(bot.position, direction, offset)
    mover.velocity = (0.0, 0.0, 0.0)
    mover.body = None
    angle = app.graph.node(5).field("m_A53161C8.m_expression.m_D99EF254")[0]
    mover.yaw = (round(bot.yaw + angle * 65536 / 360) + 32768) % 65536 - 32768


def after_pose(fight, player):
    if controlled(fight, player):
        for bot in fight.match.bots:
            bound = fight.heavy_pins.get(bot.entity)
            if bound is not None and bound[0] is player:
                place(bot, *bound)
                return


def release(fight, source):
    entry = fight.heavy_pins.pop(source, None)
    if entry is not None:
        _, app = entry
        if app.component.instances.get(app.id) is app:
            app.component.destroy(app)


def retire_victim(fight, player):
    for source, (owner, _) in tuple(fight.heavy_pins.items()):
        if owner is player:
            release(fight, source)


def impact_control(world, instance, node):
    if (
        world.match is None or instance.graph.index != GRAPH
        or node.pos != IMPACT_CONTROL or phase(instance) != 2
    ):
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != STUN_GRAPH_GUID:
        return
    source = instance.component.entity
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    duration = expr.to_float(dict(resource.overrides).get(0x0D800000000000D7))
    if request is not None and duration > 0:
        world.match.combat.heavy_impact_requests[source] = (request.targets, duration)


def impact_health(world, state, elapsed_ms):
    from ow174.game import combat

    instance = state.instance
    if (world.match is None or instance.graph.index != GRAPH
            or state.node.pos not in (CRUSH_HEALTH, IMPACT_HEALTH)):
        return NotImplemented
    if elapsed_ms:
        return True
    fight = world.match.combat
    source = instance.component.entity
    actor = next((b for b in fight.match.bots if b.entity == source), None)
    if actor is None or fight.is_incapacitated(actor):
        return True
    victim = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    crush = state.node.pos == CRUSH_HEALTH
    if crush:
        bound = fight.heavy_pins.get(source)
        if bound is None:
            return True
        player, app = bound
        direct = instance.states.get(instance.graph.node(IMPACT_HEALTH).state)
        body_contact = phase(instance) == 2 and direct is not None and direct.active
        if (
            expr.to_entity(victim) != player.body or not active(app)
            or not (travelling(instance) or body_contact)
        ):
            return True
        victims = (victim,)
    else:
        if phase(instance) != 2 or not isinstance(victim, tuple):
            return True
        victims = victim
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    stun_targets, duration = fight.heavy_impact_requests.get(source, ((), 0.0))
    for target in fight._enemies(actor):
        if expr.Entity(target.entity) not in victims or defenses.phase(target.owner)[0]:
            continue
        bubble = zarya_barrier.attached_to(fight, target.entity)
        if bubble is not None:
            bubble.damage(amount, source)
            continue
        fight._damage(actor, target, amount, False, None, fight.tick)
        if (not crush and target.entity in stun_targets and duration > 0
                and not defenses.fortified(target.owner) and not fight.vitals_of(target).dead):
            deadline = fight.tick + math.ceil(duration / combat.TICK)
            fight.stuns[target.entity] = max(deadline, fight.stuns.get(target.entity, 0))
            fight._interrupt(target)
    return True


def finish_frame(bot, fight, moved):
    instance = charge(bot)
    fight.heavy_impact_requests.pop(bot.entity, None)
    if instance is not None and phase(instance) == 2:
        impact = instance.states.get(instance.graph.node(IMPACT_HEALTH).state)
        if impact is not None and impact.active:
            stop(bot, fight)
            impact.request_finish()
            instance.component.run_now()
    if instance is None or not travelling(instance):
        fight.heavy_grazes.pop(bot.entity, None)
        release(fight, bot.entity)
        if moved is not None:
            bot.velocity = (0.0, 0.0, 0.0)
        return
    action = instance.states.get(instance.graph.node(PIN_ACTION).state)
    if action is not None and action.active and bot.entity not in fight.heavy_pins:
        pin(instance.component.world, instance, action.node)
    bound = fight.heavy_pins.get(bot.entity)
    if bound is not None:
        if active(bound[1]):
            place(bot, *bound)
        else:
            stop(bot, fight)
            return
    tracker = instance.states.get(instance.graph.node(GRAZE_SEARCH).state)
    health = instance.states.get(instance.graph.node(GRAZE_HEALTH).state)
    if tracker is None or health is None or not tracker.active or not health.active or not tracker.targets:
        return
    seen = fight.heavy_grazes.setdefault(bot.entity, set())
    amount = expr.to_float(health.evaluate(health.node.field("m_modifyHealth.m_amount"), watch=False))
    horizontal = expr.to_float(health.evaluate(health.node.field("m_modifyHealth.m_E33EFEE1"), watch=False))
    vertical = expr.to_float(health.evaluate(health.node.field("m_modifyHealth.m_583C208B"), watch=False))
    for target in fight._enemies(bot):
        if target.entity not in tracker.targets or target.entity in seen or defenses.phase(target.owner)[0]:
            continue
        seen.add(target.entity)
        bubble = zarya_barrier.attached_to(fight, target.entity)
        if bubble is not None:
            bubble.damage(amount, bot.entity)
            continue
        fight._damage(bot, target, amount, False, None, fight.tick)
        if not fight.vitals_of(target).dead:
            fight._blast_impulse(target, bot.position, horizontal, vertical)


def wall_near(bot, fight, instance, direction):
    from ow174.game import combat

    ray = instance.graph.node(WALL_RAY)
    reach = expr.to_float(instance.evaluate(ray.field("m_castLength")))
    x, y, z = bot.position
    origin = (x, y + 1, z)
    return (combat.ray_world(fight._collision(), origin, direction, reach) is not None
            or fight.walls.cast(origin, direction, reach) is not None)


def move(bot, fight, frame):
    """Advance a graph-owned travel frame before the VM's contact searches."""
    from ow174.game import combat

    instance = charge(bot)
    if instance is None or not phase(instance):
        return None
    if fight.is_incapacitated(bot) or not bot.firing:
        stop(bot, fight)
        return instance
    if not travelling(instance):
        return None
    direction = combat.aim(round(bot.yaw), 0)
    # The ray origin is the authored local (0, 1, 0) transform. Follow the
    # original phase-1 wall branch when the reconstructed map query hits.
    if wall_near(bot, fight, instance, direction):
        if bot.entity in fight.heavy_pins:
            health = instance.node_state(instance.graph.node(CRUSH_HEALTH))
            health.plug_enter("m_beginPlug")
            instance.component.run_now()
            health.request_finish()
        branch = instance.graph.node(173)
        instance.follow(branch, "m_truePlug", None)
        bot.velocity = (0.0, 0.0, 0.0)
        return instance
    row = instance.graph.node(MOVEMENT).field("m_BC5E91CF")[0]
    speed = row["m_9B7A63EA"]["m_expression"]["m_D99EF254"][0]
    bot.velocity = tuple(axis * speed for axis in direction)
    bot.position = combat._along(bot.position, bot.velocity, combat.TICK)
    bot.moved_tick = frame
    return instance
