"""Manual Heavy Assault charge input and static travel from graph 1408."""

import math

from ow174.game import fortify
from ow174.game.script import expr, graph

BODY = 0x0400000000001EBB
GRAPH = 0x1408
BUTTON = 35
PHASE = 59
ABILITY = 82
DRIVE = 71
WALL_CAST = 172
WALL_BRANCH = 173
SIDE_SEARCH, SIDE_HEALTH, FRONT_SEARCH = 32, 35, 96
IMPACT_SEARCH, IMPACT_HEALTH, IMPACT_CONTROL = 100, 102, 189
PIN_ACTION, VICTIM_GRAPH = 158, 0x1407
CRUSH_HEALTH = 29


def instance(bot):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def phase(charge):
    return expr.to_int(charge.find_var(0, PHASE).value()) if charge is not None else 0


def stop(fight, bot):
    release(fight, bot.entity)
    bot.charge_requested = False
    charge = instance(bot)
    clear_contacts(bot, charge)
    if charge is None:
        return
    charge.component.edges([(BUTTON, False)])
    ability = charge.states.get(charge.graph.node(ABILITY).state)
    if ability is not None:
        ability.presses.clear()
    if phase(charge):
        charge.write((expr.INSTANCE, PHASE), 0)
        bot.velocity = (0.0, 0.0, 0.0)


def step(fight, bot, charge, tick, *, accept_input):
    from ow174.game import combat

    if fight.is_stunned(bot):
        stop(fight, bot)
        return
    component = charge.component
    if BUTTON in component.held:
        component.edges([(BUTTON, False)])
    elif bot.charge_requested and accept_input:
        bot.charge_requested = False
        component.edges([(BUTTON, True)])
    drive = charge.states.get(charge.graph.node(DRIVE).state)
    if phase(charge) != 1 or drive is None or not drive.active:
        return
    direction = combat.aim(round(bot.yaw), 0)
    modifier = drive.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
    speed = modifier["m_expression"]["m_D99EF254"][0]
    velocity = tuple(axis * speed for axis in direction)
    ray = charge.graph.node(WALL_CAST)
    reach = expr.to_float(charge.evaluate(ray.field("m_castLength")))
    x, y, z = bot.position
    if fight.cover_ray((x, y + 1, z), direction, reach) is not None:
        crush(fight, bot, charge)
        charge.follow(charge.graph.node(WALL_BRANCH), "m_truePlug", None)
        release(fight, bot.entity)
        bot.velocity = (0.0, 0.0, 0.0)
        return
    bot.position = combat._along(bot.position, velocity, combat.TICK)
    bot.velocity = velocity
    bot.moved_tick = tick


def clear_contacts(bot, charge):
    bot.charge_contacts.clear()
    if charge is None:
        return
    slot = expr.lvalue(charge.graph.node(SIDE_SEARCH).field("m_8A2905A0"))
    value = charge.vars.get(slot[1])
    if value is not None and value.value():
        charge.write(slot, ())


def side_contacts(fight, bot, charge):
    """Resolve the active 1408 side search against live capsules."""
    drive = charge.states.get(charge.graph.node(DRIVE).state)
    if phase(charge) != 1 or drive is None or not drive.active:
        clear_contacts(bot, charge)
        return
    tracker = charge.states.get(charge.graph.node(SIDE_SEARCH).state)
    health = charge.states.get(charge.graph.node(SIDE_HEALTH).state)
    if tracker is None or not tracker.active or health is None or not health.active:
        return
    selected = search_targets(fight, bot, tracker, front=False)
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    charge.write(slot, tuple(expr.Entity(target.entity) for target in selected))
    for target in selected:
        if target.entity in bot.charge_contacts:
            continue
        bot.charge_contacts.add(target.entity)
        amount = expr.to_float(health.evaluate(health.node.field("m_modifyHealth.m_amount"), watch=False))
        loadout = health.evaluate(health.node.field("m_modifyHealth.m_loadout"), watch=False)
        dealt = fight._damage(bot, target, amount, False, loadout.guid, fight.tick, world_tick=fight.tick)
        if dealt and not fight.vitals_of(target).dead:
            graze_impulse(bot, target, health)


def graze_impulse(bot, target, state):
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


def body_impact(fight, bot, charge):
    """Use the close-body tracker to enter the authored impact branch."""
    from ow174.game import combat

    drive = charge.states.get(charge.graph.node(DRIVE).state)
    tracker = charge.states.get(charge.graph.node(IMPACT_SEARCH).state)
    if phase(charge) != 1 or drive is None or not drive.active or tracker is None or not tracker.active:
        return
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    centre = tuple(a + b for a, b in zip(bot.position, (offset.x, offset.y, offset.z), strict=True))
    targets = []
    for target in fight._enemies(bot):
        if target.entity == fight.pins.get(bot.entity):
            continue
        a, b = target.segment()
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and fight.visible_target(centre, target)):
            targets.append(target)
    if not targets:
        return
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    charge.write(slot, tuple(expr.Entity(target.entity) for target in targets))
    charge.component.run_now()
    state = charge.states.get(charge.graph.node(IMPACT_HEALTH).state)
    if phase(charge) != 2 or state is None or not state.active:
        return
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
    control = charge.graph.node(IMPACT_CONTROL).field("m_69F4B52C.m_6525E98B")
    duration = next(
        expr.to_float(charge.evaluate(entry["m_value"]))
        for entry in control["m_1EB5A024"] if entry["m_0D09D2D9"] == "0x0D800000000000D7"
    )
    for target in targets:
        dealt = fight._damage(bot, target, amount, False, loadout.guid, fight.tick, world_tick=fight.tick)
        if dealt and duration > 0 and not fight.vitals_of(target).dead:
            deadline = fight.tick + math.ceil(duration / combat.TICK)
            fight.stuns[target.entity] = max(deadline, fight.stuns.get(target.entity, 0))
            fight._interrupt(target)
    state.request_finish()
    stop(fight, bot)


def search_targets(fight, bot, tracker, *, front):
    from ow174.game import combat

    charge = tracker.instance
    forward = combat.aim(round(bot.yaw), 0)
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    side_angle = expr.to_float(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False))
    front_angle = expr.to_float(charge.evaluate(charge.graph.node(FRONT_SEARCH).field("m_A30B383E")))
    side_cos = math.cos(math.radians(side_angle / 2))
    front_cos = math.cos(math.radians(front_angle))
    x, y, z = bot.position
    origin = (x, y + offset.y, z)
    centre = combat._along(origin, forward, offset.z)
    selected = []
    pinned = fight.pins.get(bot.entity)
    for target in fight._enemies(bot):
        if target.entity == pinned:
            continue
        a, b = target.segment()
        dx, dz = a[0] - centre[0], a[2] - centre[2]
        alignment = dx * forward[0] + dz * forward[2]
        distance = math.hypot(dx, dz)
        if alignment < distance * side_cos or (alignment >= distance * front_cos) != front:
            continue
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and fight.visible_target(origin, target)):
            selected.append(target)
    return selected


def front_contact(fight, bot, instance):
    """Resolve the 1408 target and attach the authored victim graph on its body."""
    app = fight.heavy_charge_pins.get(bot.entity)
    if phase(instance) != 1 or not instance.node_state(instance.graph.node(DRIVE)).active:
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
                or fight.zarya_barriers.contact(victim.owner) is not None
                or fortify.scale(victim.owner) is not None):
            stop(fight, bot)
        else:
            transport(bot, victim.owner, app)
        return

    candidates = (
        target for target in search_targets(fight, bot, tracker, front=True)
        if hasattr(target.owner, "mover") and not fight.is_stunned(target.owner)
        and fight.zarya_barriers.contact(target.owner) is None
        and fortify.scale(target.owner) is None
    )
    target = min(candidates, key=lambda t: (math.dist(t.feet, bot.position), t.entity), default=None)
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    instance.write(slot, (expr.Entity(target.entity),) if target is not None else ())
    if target is None:
        return
    instance.component.run_now()
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
    x, y, z = combat._along(bot.position, direction, offset)
    player.mover.position[:] = (x, y, z)
    if player.mover.body is not None:
        player.mover.body.teleport((x, y + player.mover._lift(), z))
    player.mover.velocity = (0.0, 0.0, 0.0)
    angle = app.graph.node(5).field("m_A53161C8.m_expression.m_D99EF254")[0]
    player.mover.yaw = (round(bot.yaw + angle * 65536 / 360) + 32768) % 65536 - 32768


def release(fight, source):
    app = fight.heavy_charge_pins.pop(source, None)
    if app is not None:
        fight.pins.pop(source, None)
        if app.component.instances.get(app.id) is app:
            app.component.destroy(app)


def crush(fight, bot, instance):
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
    if (target is None or fight.zarya_barriers.contact(target.owner) is not None
            or fortify.scale(target.owner) is not None):
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
