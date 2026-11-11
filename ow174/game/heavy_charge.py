"""Authored Heavy Assault charge phases with reconstructed static world travel."""

import math

from ow174.game.script import expr, graph

BODY = 0x0400000000001EBB
GRAPH = 0x1408
BUTTON = 35
ABILITY = 82
PHASE = 59
DRIVE = 71
WINDUP = 137
WALL_CAST = 172
WALL_BRANCH = 173
SIDE_SEARCH = 32
SIDE_HEALTH = 35
FRONT_SEARCH = 96
PIN_ACTION = 158
VICTIM_GRAPH = 0x1407
CRUSH_HEALTH = 29
IMPACT_SEARCH = 100
IMPACT_HEALTH = 102
IMPACT_CONTROL = 189
COUNTER_SEARCH = 134
COUNTER_SELF = 132
COUNTER_OTHER = 140
COUNTER_RELEASE = 136
RELEASE_WAIT = 105


def charge(bot):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def recovery_animation_duration(state):
    instance = state.instance
    if instance.graph.index != 0x1254 or not any(
        i.graph.index == GRAPH for i in instance.component.instances.values()
    ):
        return None
    category = state.node.field("m_22326315.m_category")
    if category != "0x0280000000000245":
        return None
    alias = state.node.field("m_11E0A658.m_animAlias")
    # 104319 body 1EBB, blend set 07C0.021, tree 006E.020, category 0245.
    # Use the compiled clip duration as the reconstructed server end event.
    return {
        (46, "0x0C80000000000064"): 0.6666667,
        (44, "0x0C80000000000066"): 1.0666667,
    }.get((state.node.pos, alias))


def phase(instance):
    var = instance.vars.get(PHASE)
    return expr.to_int(var.value()) if var is not None else 0


def clocked(bot):
    instance = charge(bot)
    return instance is not None and (
        requested(bot) or BUTTON in instance.component.held or phase(instance) != 0
        or any(active(instance, node) for node in (WINDUP, DRIVE, IMPACT_HEALTH))
    )


def recovering(bot):
    instance = charge(bot)
    if instance is None:
        return False
    ability = instance.states.get(instance.graph.node(ABILITY).state)
    return active(instance, RELEASE_WAIT) or (ability is not None and ability.remaining() > 0.)


def requested(bot):
    return bot.pending_ability == BUTTON


def driving(instance):
    return phase(instance) == 1 and active(instance, DRIVE)


def active(instance, node):
    state = instance.states.get(instance.graph.node(node).state)
    return state is not None and state.active


def release_raycast(instance, request):
    """Reconstruct the release filter as map geometry plus Ice Wall."""
    from ow174.game import combat
    from ow174.game.script.runtime import RaycastResult

    match = getattr(instance.component.world, "match", None)
    if match is None or match.collision_world is None:
        return None
    origin, direction = tuple(request.origin), tuple(request.direction)
    static = combat.ray_world(match.collision_world, origin, direction, request.length)
    wall = match.combat.walls.cast(origin, direction, request.length)
    distance = static if wall is None or (static is not None and static <= wall[0]) else wall[0]
    if distance is None:
        return RaycastResult(False, None, None, None)
    return RaycastResult(True, None, expr.Vec3(*combat._along(origin, direction, distance)), None)


def place_released_victim(instance, node):
    """Apply 1408 placement only while its original attachment is live."""
    from ow174.game import contacts
    from ow174.game.collision import MOVER

    component = instance.component
    match = getattr(component.world, "match", None)
    if match is None:
        return
    fight = match.combat
    app = fight.heavy_charge_pins.get(component.entity)
    if app is None or app.stopped:
        return
    entity = expr.to_entity(instance.evaluate(node.field("m_target")))
    if app.component.entity != entity or fight.pins.get(component.entity) != entity:
        return
    target = next((t for t in fight._targets() if t.entity == entity and not fight.vitals_of(t).dead), None)
    if (target is None or target.owner not in match.players
            or getattr(target.owner.body_script, "component", None) is not app.component):
        return
    destination = instance.evaluate(node.field("m_AF774F7D"))
    collision = getattr(match.collision_world, "engine", None)
    if (collision is None or not isinstance(destination, expr.Vec3)
            or not all(map(math.isfinite, destination))):
        return
    player = target.owner
    mover = player.mover
    start, end = tuple(mover.position), tuple(destination)
    radius, segment = mover._capsule()
    lift = mover._lift() + radius
    points = ((0., lift, 0.), (0., lift + segment, 0.))
    if contacts.scene_sweep(collision, start, end, points, radius, MOVER) is not None:
        return
    height = mover.data.crouch_height if mover.crouched else mover.data.stand_height
    _, normals = fight.walls.constrain(start, end, radius, height)
    if not normals:
        fight.walls.place_player(player, end)


def move(bot, fight, frame):
    """Consume one authored drive frame using the current yaw and world collision."""
    from ow174.game import combat
    from ow174.game.mover import mover_data

    instance = charge(bot)
    if instance is None or not driving(instance):
        return
    direction = combat.aim(round(bot.yaw), 0)
    modifier = instance.graph.node(DRIVE).field("m_BC5E91CF")[0]["m_9B7A63EA"]
    speed = modifier["m_expression"]["m_D99EF254"][0]
    velocity = tuple(axis * speed for axis in direction)
    candidate = combat._along(bot.position, velocity, combat.TICK)

    ray = instance.graph.node(WALL_CAST)
    reach = expr.to_float(instance.evaluate(ray.field("m_castLength")))
    x, y, z = bot.position
    origin = (x, y + 1.0, z)
    blocked = combat.ray_world(fight._collision(), origin, direction, reach) is not None
    if not blocked and fight.walls.pieces:
        data = mover_data(bot.body)
        _, normals = fight.walls.constrain(bot.position, candidate, data.stand_radius, data.stand_height)
        blocked = bool(normals) or fight.walls.cast(origin, direction, reach) is not None
    if blocked:
        crush(bot, fight, instance)
        instance.follow(instance.graph.node(WALL_BRANCH), "m_truePlug", None)
        bot.velocity = (0.0, 0.0, 0.0)
        return
    bot.position, bot.velocity, bot.moved_tick = candidate, velocity, frame


def crush(bot, fight, instance):
    """Dispatch the authored victim damage state before the wall branch retires the attachment."""
    app = fight.heavy_charge_pins.get(bot.entity)
    if app is None or app.stopped:
        return
    attached = app.states.get(app.graph.node(1).state)
    if attached is None or not attached.active:
        return
    node = instance.graph.node(CRUSH_HEALTH)
    state = instance.node_state(node)
    selected = expr.to_entity(instance.evaluate(node.field("m_FA3ECC43")))
    if selected != app.component.entity:
        return
    target = next((target for target in fight._enemies(bot) if target.entity == selected), None)
    if target is None:
        return
    if not state.active:
        state.plug_enter("m_beginPlug")
        instance.component.run_now()
    if state.active:
        damage(bot, fight, state, target)
        state.request_finish()


def damage(bot, fight, state, target):
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
    return fight._damage(bot, target, amount, False, loadout.guid, fight.tick, world_tick=fight.tick)


def stun_duration(instance, node_position):
    control = instance.graph.node(node_position).field("m_69F4B52C.m_6525E98B")
    if control["m_graph"] != "0x058000000000224E":
        return 0.0
    return next(
        expr.to_float(instance.evaluate(entry["m_value"]))
        for entry in control["m_1EB5A024"] if entry["m_0D09D2D9"] == "0x0D800000000000D7"
    )


def stun(fight, target, seconds):
    from ow174.game import combat

    deadline = fight.tick + math.ceil(seconds / combat.TICK)
    fight.stuns[target.entity] = max(deadline, fight.stuns.get(target.entity, 0))
    fight._interrupt(target)


def charge_immune(actor):
    script = getattr(actor, "body_script", None)
    flag = script.component.entity_var(9161, create=False) if script is not None else None
    return flag is not None and expr.truthy(flag.value())


def counter_charge(bot, fight, instance):
    """Reconstruct 134's opposing-charge filter and paired 132/140 control."""
    from ow174.game import combat

    if instance is None or not phase(instance):
        return
    node = instance.graph.node(COUNTER_SEARCH)
    tracker = instance.states.get(node.state)
    if tracker is None or not tracker.active:
        return
    radius = expr.to_float(tracker.evaluate(node.field("m_radius"), watch=False))
    angle = math.radians(expr.to_float(tracker.evaluate(node.field("m_A30B383E"), watch=False))) / 2
    offset = tracker.evaluate(node.field("m_05BE62DD"), watch=False)
    x, y, z = bot.position
    centre = (x, y + offset.y, z)
    forward = combat.aim(round(bot.yaw), 0)
    for target in fight._enemies(bot):
        actor = target.owner
        script = getattr(actor, "body_script", None)
        if (not hasattr(actor, "mover") or script is None or fight.is_stunned(actor)
                or fight.shields.shielded(actor)):
            continue
        immune = charge_immune(actor)
        if not immune:
            opposing = next((i for i in script.component.instances.values() if i.graph.index == 0x007D), None)
            tracker_state = opposing.states.get(74) if opposing is not None else None
            flag = script.component.entity_var(596, create=False)
            if (tracker_state is None or not tracker_state.active
                    or flag is None or not expr.truthy(flag.value())):
                continue
            if combat._dot(forward, combat.aim(round(actor.mover.yaw), 0)) >= 0:
                continue
        a, b = target.segment()
        if combat.segment_distance(centre, a, b) > radius + target.radius:
            continue
        dx, dz = a[0] - centre[0], a[2] - centre[2]
        distance = math.hypot(dx, dz)
        if distance:
            alignment = (dx * forward[0] + dz * forward[2]) / distance
            padding = math.asin(min(1.0, target.radius / distance))
            if alignment < math.cos(angle + padding):
                continue
        if (not combat.capsule_visible(fight._collision(), centre, a, b, target.radius)
                or fight.walls.blocked(centre, (a[0], centre[1], a[2]))):
            continue
        slot = expr.lvalue(node.field("m_8A2905A0"))
        if immune:
            # Select the authored true branch using the live foreign entity flag.
            # The strict world query for this tracker is still unhosted.
            instance.run_node(instance.graph.node(COUNTER_RELEASE), None)
            # Consume the authored placement while the target is still bound.
            instance.component.run_now()
        else:
            own = next(item for item in fight._targets() if item.owner is bot)
            instance.write(slot, (expr.Entity(target.entity),))
            instance.component.run_now()
            stun(fight, target, stun_duration(instance, COUNTER_OTHER))
            stun(fight, own, stun_duration(instance, COUNTER_SELF))
        stop(bot, fight)
        instance.write(slot, ())
        return


def body_impact(bot, fight, instance):
    """Resolve the authored close-body impact, pairing control with admitted damage."""
    from ow174.game import combat

    if instance is None or not driving(instance):
        return
    tracker = instance.states.get(instance.graph.node(IMPACT_SEARCH).state)
    if tracker is None or not tracker.active:
        return
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    centre = tuple(a + b for a, b in zip(bot.position, (offset.x, offset.y, offset.z), strict=True))
    targets = []
    pinned = fight.pins.get(bot.entity)
    for target in fight._enemies(bot):
        if target.entity == pinned:
            continue
        a, b = target.segment()
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), centre, a, b, target.radius)
                and not fight.walls.blocked(centre, (a[0], centre[1], a[2]))):
            targets.append(target)
    if not targets:
        return
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    instance.write(slot, tuple(expr.Entity(target.entity) for target in targets))
    instance.component.run_now()
    state = instance.states.get(instance.graph.node(IMPACT_HEALTH).state)
    if phase(instance) != 2 or state is None or not state.active:
        return
    duration = stun_duration(instance, IMPACT_CONTROL)
    for target in targets:
        if damage(bot, fight, state, target) and duration > 0.0 and not fight.vitals_of(target).dead:
            stun(fight, target, duration)
    crush(bot, fight, instance)
    state.request_finish()
    stop(bot, fight)


def search(bot, fight, tracker, *, front):
    """Reconstruct the side and frontal target cones around the authored search spheres."""
    from ow174.game import combat

    instance = tracker.instance
    forward = combat.aim(round(bot.yaw), 0)
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    side_angle = expr.to_float(instance.evaluate(instance.graph.node(SIDE_SEARCH).field("m_A30B383E")))
    front_angle = expr.to_float(instance.evaluate(instance.graph.node(FRONT_SEARCH).field("m_A30B383E")))
    side_cos = math.cos(math.radians(side_angle / 2))
    front_cos = math.cos(math.radians(front_angle))
    x, y, z = bot.position
    origin = (x, y + offset.y, z)
    centre = combat._along(origin, forward, offset.z)
    selected = []
    for target in fight._enemies(bot):
        a, b = target.segment()
        dx, dz = a[0] - centre[0], a[2] - centre[2]
        alignment = dx * forward[0] + dz * forward[2]
        distance = math.hypot(dx, dz)
        if alignment < distance * side_cos or (alignment >= distance * front_cos) != front:
            continue
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius)
                and not fight.walls.blocked(origin, (a[0], origin[1], a[2]))):
            selected.append((distance, target))
    return sorted(selected, key=lambda item: (item[0], item[1].entity))


def side_contacts(bot, fight, instance):
    """Route the active side search and health state through the combat damage service."""
    if instance is None or not driving(instance):
        clear_contacts(bot, fight, instance)
        return
    tracker = instance.states.get(instance.graph.node(SIDE_SEARCH).state)
    health = instance.states.get(instance.graph.node(SIDE_HEALTH).state)
    if tracker is None or not tracker.active or health is None or not health.active:
        return
    pinned = fight.pins.get(bot.entity)
    selected = [target for _, target in search(bot, fight, tracker, front=False) if target.entity != pinned]
    target_slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    instance.write(target_slot, tuple(expr.Entity(target.entity) for target in selected))
    if not selected:
        return
    seen = fight.heavy_charge_contacts.setdefault(bot.entity, set())
    for target in selected:
        if target.entity not in seen:
            seen.add(target.entity)
            if damage(bot, fight, health, target) and not fight.vitals_of(target).dead:
                graze_impulse(bot, target, health)


def graze_impulse(bot, target, state):
    """Reconstruct a radial player impulse from the authored health-state magnitudes."""
    from ow174.game.mover import FELL

    mover = getattr(target.owner, "mover", None)
    if mover is None:
        return
    horizontal = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1"), watch=False))
    vertical = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_583C208B"), watch=False))
    dx, dz = target.feet[0] - bot.position[0], target.feet[2] - bot.position[2]
    distance = math.hypot(dx, dz)
    scale = horizontal / distance if distance else 0.0
    delta = (dx * scale, vertical, dz * scale)
    mover.velocity = tuple(a + b for a, b in zip(mover.velocity, delta, strict=True))
    if vertical > 0.0:
        mover.flags_word |= FELL


def front_contact(bot, fight, instance):
    """Resolve the 1408 target and attach the authored victim graph on its body."""
    from ow174.game import mei

    app = fight.heavy_charge_pins.get(bot.entity)
    if instance is None or not driving(instance):
        release(fight, bot.entity)
        return
    tracker = instance.states.get(instance.graph.node(FRONT_SEARCH).state)
    if tracker is None or not tracker.active:
        return
    if app is not None:
        victim_id = app.component.entity
        victim = next((target for target in fight._enemies(bot) if target.entity == victim_id), None)
        attachment = app.states.get(app.graph.node(1).state)
        if (victim is None or app.stopped or attachment is None or not attachment.active
                or mei.protected(victim.owner) or fight.shields.shielded(victim.owner)
                or charge_immune(victim.owner)):
            stop(bot, fight)
        else:
            transport(bot, victim.owner, app)
        return

    candidates = (
        target for _, target in search(bot, fight, tracker, front=True)
        if hasattr(target.owner, "mover") and not fight.is_stunned(target.owner)
        and not mei.protected(target.owner) and not fight.shields.shielded(target.owner)
        and not charge_immune(target.owner)
    )
    target = next(candidates, None)
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    instance.write(slot, (expr.Entity(target.entity),) if target is not None else ())
    if target is None:
        return
    action = instance.graph.node(PIN_ACTION)
    selected = instance.evaluate(action.field("m_targets"))
    active = instance.states.get(action.state)
    if active is None or not active.active or selected != expr.Entity(target.entity):
        return
    definition = graph.graph(VICTIM_GRAPH)
    script = target.owner.body_script
    if definition is None or script is None or script.component.owner_dead:
        return
    app = script.component.play_loaded_graph(definition, source=bot.entity, instigator=bot.entity)
    if app is None:
        return
    script.component.run_now()
    attach = app.states.get(definition.node(1).state)
    if attach is None or not attach.active:
        script.component.destroy(app)
        return
    fight.heavy_charge_pins[bot.entity] = app
    fight.pins[bot.entity] = target.entity
    fight._interrupt(target)
    transport(bot, target.owner, app)


def transport(bot, player, app):
    """Project the unavailable animated attachment bone onto the two capsules."""
    from ow174.game import combat
    from ow174.game.mover import mover_data

    direction = combat.aim(round(bot.yaw), 0)
    offset = mover_data(bot.body).stand_radius + player.mover.data.stand_radius
    player.match.combat.walls.place_player(player, combat._along(bot.position, direction, offset))
    player.mover.velocity = (0.0, 0.0, 0.0)
    angle = app.graph.node(5).field("m_A53161C8.m_expression.m_D99EF254")[0]
    player.mover.yaw = (round(bot.yaw + angle * 65536 / 360) + 32768) % 65536 - 32768


def release(fight, source):
    app = fight.heavy_charge_pins.pop(source, None)
    if app is not None:
        fight.pins.pop(source, None)
        if app.component.instances.get(app.id) is app:
            app.component.destroy(app)


def clear_contacts(bot, fight, instance):
    fight.heavy_charge_contacts.pop(bot.entity, None)
    if instance is not None:
        node = instance.graph.node(SIDE_SEARCH)
        slot = expr.lvalue(node.field("m_8A2905A0"))
        var = instance.vars.get(slot[1])
        if var is not None and var.value():
            instance.write(slot, ())


def stop(bot, fight):
    instance = charge(bot)
    if instance is None:
        return
    component = instance.component
    release(fight, bot.entity)
    clear_contacts(bot, fight, instance)
    if requested(bot):
        bot.pending_ability = None
    if BUTTON in component.held:
        component.edges([(BUTTON, False)])
    state = instance.states.get(instance.graph.node(ABILITY).state)
    if state is not None:
        state.presses.clear()
    if phase(instance):
        bot.velocity = (0.0, 0.0, 0.0)
        instance.write((expr.INSTANCE, PHASE), 0)
