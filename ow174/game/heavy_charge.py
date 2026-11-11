"""104319 Heavy Assault charge phases, with reconstructed world motion."""

import math

from ow174.game.script import expr

BODY = 0x0400000000001EBB
GRAPH = 0x1408
BUTTON = 35
ABILITY = 82
MOVEMENT = 71
WALL_RAY = 172
PHASE = 59
GRAZE_SEARCH = 32
GRAZE_HEALTH = 35
FRONT_SEARCH = 96
CLASH_SEARCH = 134
PIN_ACTION = 158
VICTIM_GRAPH = 0x1407
CRUSH_HEALTH = 29
IMPACT_SEARCH = 100
IMPACT_TARGET_FLAG = 961  # 1408/node 100's candidate member; 0636/node 27 writes it.
IMPACT_HEALTH = 102
IMPACT_CONTROL = 189
CLASH_CONTROL = (132, 140)
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


def stop(bot):
    instance = charge(bot)
    if instance is None or not phase(instance):
        return
    bot.velocity = (0.0, 0.0, 0.0)
    match = instance.component.world.match
    if match is not None:
        match.combat.heavy_charge_contacts.pop(bot.entity, None)
        release(match.combat, bot.entity)
    # Finish the active charge through the graph's own 59 -> cooldown path.
    instance.write((expr.INSTANCE, PHASE), 0)


def targets(world, state):
    """Reconstruct the authored capsule searches and their candidate gates."""
    if (world.match is None or state.instance.graph.index != GRAPH
            or state.node.pos not in (GRAZE_SEARCH, FRONT_SEARCH, IMPACT_SEARCH, CLASH_SEARCH)):
        return NotImplemented
    from ow174.game import combat, mei_wall, protections

    fight = world.match.combat
    actor = next((bot for bot in fight.match.bots if bot.entity == world.body), None)
    if actor is None or charge(actor) is not state.instance or not travelling(state.instance):
        return ()
    frontal = state.node.pos == FRONT_SEARCH
    impact = state.node.pos == IMPACT_SEARCH
    bound = fight.heavy_charge_pins.get(actor.entity)
    if frontal and bound is not None:
        return (bound[0].body,)
    offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    forward = combat.aim(round(actor.yaw), 0)
    x, y, z = actor.position
    origin = (x, y + offset.y, z)
    if state.node.pos == CLASH_SEARCH:
        return clash_targets(world, state, actor, origin, forward, radius)
    front = state.instance.graph.node(FRONT_SEARCH)
    cone = expr.to_float(state.instance.evaluate(front.field("m_A30B383E")))
    side_node = state.instance.graph.node(GRAZE_SEARCH)
    side = expr.to_float(state.instance.evaluate(side_node.field("m_A30B383E")))
    centre = combat._along(origin, forward, offset.z)
    cosine = math.cos(math.radians(cone))
    side_cosine = math.cos(math.radians(side / 2))
    found = []
    for target in fight._enemies(actor):
        if impact and not expr.truthy(world.entity_var(target.entity, IMPACT_TARGET_FLAG)):
            continue
        if not impact and protections.force_immune(target.owner):
            # Fortify's 9161 and 6601 stacks exclude nodes 32 and 96.
            continue
        a, b = target.segment()
        dx, dz = a[0] - centre[0], a[2] - centre[2]
        alignment = dx * forward[0] + dz * forward[2]
        distance = math.hypot(dx, dz)
        in_front = alignment >= distance * cosine
        if not impact and (alignment < distance * side_cosine or frontal != in_front):
            continue
        if impact and bound is not None and target.owner is bound[0]:
            continue
        if frontal and (not hasattr(target.owner, "mover") or controlled(fight, target.owner)
                        or fight.assassins.controlled(target.owner)
                        or fight.zarya_barriers.protects(target.owner)):
            continue
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius,
                                            walls=fight.walls)):
            found.append((distance, target.entity))
    if frontal:
        return (min(found)[1],) if found else ()
    if impact:
        for piece in fight.walls.pieces.values():
            friendly = piece.team == actor.team and not fight.match.game_map.free_for_all
            if not piece.impact_marked or friendly:
                continue
            surface = mei_wall.closest(piece, centre)
            separation = math.dist(centre, surface)
            if (separation <= radius
                    and combat.capsule_visible(fight._collision(), origin, surface, surface, 0.0)):
                found.append((separation, piece.entity))
    return tuple(entity for _, entity in found)


def clash_targets(world, state, actor, origin, forward, radius):
    """Use the graph's 9161 or opposed 596 gate with reconstructed capsule visibility."""
    from ow174.game import combat

    fight = world.match.combat
    slot = state.instance.vars.get(562)
    selected = slot.value() if slot is not None else ()
    excluded = expr.to_entity(selected[0]) if isinstance(selected, tuple) and selected else None
    angle = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    cosine = math.cos(math.radians(angle / 2))
    found = []
    for target in fight._enemies(actor):
        if target.entity == excluded:
            continue
        if not expr.truthy(world.entity_var(target.entity, 9161)):
            if not expr.truthy(world.entity_var(target.entity, 596)):
                continue
            mover = getattr(target.owner, "mover", target.owner)
            opposing = combat.aim(round(mover.yaw), 0)
            if combat._dot(forward, opposing) >= 0:
                continue
        dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[2]
        distance = math.hypot(dx, dz)
        if dx * forward[0] + dz * forward[2] < distance * cosine:
            continue
        a, b = target.segment()
        if (combat.segment_distance(origin, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius,
                                            walls=fight.walls)):
            found.append((distance, target.entity))
    return tuple(entity for _, entity in sorted(found))


def handles(instance, node):
    return instance.graph.index == GRAPH and (
        (node.pos == PIN_ACTION and node.field("m_script.m_graph") == "0x0580000000001407")
        or (node.pos in (*CLASH_CONTROL, IMPACT_CONTROL)
            and node.field("m_69F4B52C.m_6525E98B.m_graph") == "0x058000000000224E")
    )


def pin(world, instance, request):
    from ow174.game.script import graph

    fight = world.match.combat
    bot = next((actor for actor in fight.match.bots if actor.entity == world.body), None)
    if (bot is None or charge(bot) is not instance or not travelling(instance)
            or bot.entity in fight.heavy_charge_pins or fight.actions_blocked(bot)
            or request.resource.guid != 0x0580000000001407):
        return
    tracker = instance.states.get(instance.graph.node(FRONT_SEARCH).state)
    if tracker is None or not tracker.active:
        return
    admitted = targets(world, tracker)
    for target in fight._enemies(bot):
        if target.entity not in admitted or target.entity not in request.targets:
            continue
        player = target.owner
        script = player.body_script
        if script is None or script.component.owner_dead:
            return
        application = script.component.play_loaded_graph(
            graph.graph(VICTIM_GRAPH), source=bot.entity, instigator=bot.entity,
            params=request.params, overrides=request.resource.overrides, policy=request.policy,
        )
        if application is not None:
            fight.heavy_charge_pins[bot.entity] = (player, application)
            fight.interrupt_actions(player)
        return


def controlled(fight, player):
    script = getattr(player, "body_script", None)
    return script is not None and any(
        owner is player and app.component is script.component and active_attachment(app)
        for owner, app in fight.heavy_charge_pins.values()
    )


def active_attachment(app):
    state = app.states.get(app.graph.node(1).state)
    return not app.stopped and state is not None and state.active


def place(bot, player, app):
    """Project the live 1407 attachment onto server poses.

    Bone 006C's animated transform is unavailable. Use the bodies' capsule
    separation and the authored opposite-facing angle for the static host.
    """
    from ow174.game import combat

    direction = combat.aim(round(bot.yaw), 0)
    mover = player.mover
    offset = bot.movement_data.stand_radius + mover.data.stand_radius
    mover.position[:] = combat._along(bot.position, direction, offset)
    mover.velocity = (0.0, 0.0, 0.0)
    mover.body = None
    angle = app.graph.node(5).field("m_A53161C8.m_expression.m_D99EF254")[0]
    mover.yaw = (round(bot.yaw + angle * 65536 / 360) + 32768) % 65536 - 32768


def after_pose(fight, player):
    if not controlled(fight, player):
        return
    for bot in fight.match.bots:
        binding = fight.heavy_charge_pins.get(bot.entity)
        if binding is not None and binding[0] is player:
            place(bot, *binding)
            return


def release(fight, source):
    entry = fight.heavy_charge_pins.pop(source, None)
    if entry is not None:
        player, app = entry
        if app.component.instances.get(app.id) is app:
            app.component.destroy(app)
        if player.body_script is not None and player.body_script.component is app.component:
            player._track_movement_mods(player.body_script.frame)


def retire_victim(fight, player):
    for source, (owner, _) in tuple(fight.heavy_charge_pins.items()):
        if owner is player:
            release(fight, source)
            bot = next((actor for actor in fight.match.bots if actor.entity == source), None)
            if bot is not None:
                stop(bot)


def wall_near(bot, fight, instance, direction):
    from ow174.game import combat

    ray = instance.graph.node(WALL_RAY)
    reach = expr.to_float(instance.evaluate(ray.field("m_castLength")))
    x, y, z = bot.position
    origin = (x, y + 1, z)
    if combat.ray_world(fight._collision(), origin, direction, reach) is not None:
        return True
    if fight.match.game_map.free_for_all:
        return False
    # Allied pillars are solid and excluded from the authored hostile impact search.
    allied = (piece for piece in fight.walls.pieces.values() if piece.team == bot.team)
    return fight.walls.cast(origin, direction, reach, pieces=allied) is not None


def impact_health(world, state):
    """Consume the authored victim-specific health state on a bound wall contact."""
    if (world.match is None or state.instance.graph.index != GRAPH
            or state.node.pos not in (CRUSH_HEALTH, IMPACT_HEALTH)):
        return NotImplemented
    from ow174.game import combat

    fight = world.match.combat
    source = next((bot for bot in fight.match.bots if bot.entity == world.body), None)
    if (source is None or charge(source) is not state.instance or fight.actions_blocked(source)
            or fight.hacks.active(source.entity)):
        return True
    victim = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    crush = state.node.pos == CRUSH_HEALTH
    if crush:
        binding = fight.heavy_charge_pins.get(world.body)
        if binding is None:
            return True
        player, app = binding
        direct = state.instance.states.get(state.instance.graph.node(IMPACT_HEALTH).state)
        body_impact = phase(state.instance) == 2 and direct is not None and direct.active
        wall_impact = travelling(state.instance) and wall_near(
            source, fight, state.instance, combat.aim(round(source.yaw), 0)
        )
        if (expr.to_entity(victim) != player.body or not controlled(fight, player)
                or app.source_entity() != source.entity
                or not (wall_impact or body_impact)):
            return True
        selected = (victim,)
    else:
        if phase(state.instance) != 2 or not isinstance(victim, tuple):
            return True
        selected = victim
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    if not crush:
        for entity in selected:
            piece = fight.walls.pieces.get(expr.to_entity(entity))
            if piece is not None and piece.impact_marked:
                fight.walls.damage(piece, source, amount)
    loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    stun_targets, duration = fight.heavy_charge_impact_requests.get(source.entity, ((), 0.0))
    for target in fight._enemies(source):
        if expr.Entity(target.entity) not in selected:
            continue
        dealt = fight._damage(source, target, amount, False, loadout, fight.tick, world_tick=fight.tick)
        if (not crush and dealt and not fight.vitals_of(target).dead
                and target.entity in stun_targets and duration > 0):
            fight._apply_control(target, fight.stuns, duration)
    return True


def impact_control(world, instance, request):
    fight = world.match.combat
    if phase(instance) != 2 or request.resource.guid != STUN_GRAPH_GUID:
        return
    duration = expr.to_float(dict(request.resource.overrides).get(0x0D800000000000D7))
    if duration <= 0:
        return
    # The action runs before the queued ModifyHealth entry. Match its
    # targets against the later admitted hit before applying the control.
    fight.heavy_charge_impact_requests[world.body] = (request.targets, duration)


def clash_control(world, request):
    """Apply a clash request to its resolved live body recipients."""
    if request.resource.guid != STUN_GRAPH_GUID:
        return
    duration = expr.to_float(dict(request.resource.overrides).get(0x0D800000000000D7))
    if duration <= 0:
        return
    fight = world.match.combat
    for target in fight._targets():
        if target.entity in request.targets and not fight.vitals_of(target).dead:
            fight._apply_control(target, fight.stuns, duration)


def finish_frame(bot, fight, moved):
    instance = charge(bot)
    fight.heavy_charge_impact_requests.pop(bot.entity, None)
    if instance is not None and phase(instance) == 2:
        impact = instance.states.get(instance.graph.node(IMPACT_HEALTH).state)
        if impact is not None and impact.active:
            stop(bot)
            impact.request_finish()
            instance.component.run_now()
    if instance is None or not travelling(instance):
        fight.heavy_charge_contacts.pop(bot.entity, None)
        release(fight, bot.entity)
        if moved is not None:
            bot.velocity = (0.0, 0.0, 0.0)
        return
    binding = fight.heavy_charge_pins.get(bot.entity)
    if binding is not None:
        player, app = binding
        if controlled(fight, player):
            place(bot, player, app)
        else:
            stop(bot)
            return
    tracker = instance.states.get(instance.graph.node(GRAZE_SEARCH).state)
    health = instance.states.get(instance.graph.node(GRAZE_HEALTH).state)
    if tracker is None or health is None or not tracker.active or not health.active:
        return
    selected = {entity for entity, _ in tracker.targets}
    if not selected:
        return
    seen = fight.heavy_charge_contacts.setdefault(bot.entity, set())
    amount = expr.to_float(health.evaluate(health.node.field("m_modifyHealth.m_amount"), watch=False))
    loadout = health.evaluate(health.node.field("m_modifyHealth.m_loadout"), watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    for target in fight._enemies(bot):
        if target.entity in selected and target.entity not in seen:
            seen.add(target.entity)
            affected = fight._damage(bot, target, amount, False, loadout, fight.tick, world_tick=fight.tick)
            if affected and not fight.vitals_of(target).dead:
                fight.impulses.radial_health(target, bot.position, health)


def move(bot, fight, frame):
    """Advance a graph-owned travel frame before the VM's contact searches."""
    from ow174.game import combat

    instance = charge(bot)
    if instance is None or not phase(instance):
        return None
    if fight.actions_blocked(bot) or fight.hacks.active(bot.entity):
        stop(bot)
        return instance
    if not travelling(instance):
        return None
    direction = combat.aim(round(bot.yaw), 0)
    # The ray origin is the authored local (0, 1, 0) transform. Follow the
    # original phase-1 wall branch when the reconstructed world query hits.
    if wall_near(bot, fight, instance, direction):
        if bot.entity in fight.heavy_charge_pins:
            # Reconstructed dispatch of 1408's victim-specific wall damage.
            # Consume it before the wall exit destroys the victim binding.
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
