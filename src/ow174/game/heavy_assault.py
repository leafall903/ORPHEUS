"""Resource-backed NPC melee attacks and Heavy Assault's charge."""

import math
from copy import copy

from ow174.game import combat, correction, voicelines
from ow174.game.mover import FELL, IN_AIR, mover_data
from ow174.game.script import expr, nodes, runtime

HERO = 0x02E00000000001BA
GRAPH, ABILITY, QUERY, IMPACT, BUTTON = 0x140E, 9, 6, 5, 31
TROOPER = 0x02E00000000001AC
MELEE = {HERO: (GRAPH, ABILITY, QUERY, IMPACT), TROOPER: (0x1422, 8, 5, 4)}
CHARGE_GRAPH, CHARGE_ABILITY, CHARGE_QUERY, CHARGE_IMPACT, CHARGE_BUTTON = 0x1408, 82, 100, 102, 35
CHARGE_VOICE = 155
CHARGE_FLOOR_TOLERANCE = 0.8
MINIGUN_GRAPH, MINIGUN_PRESS, MINIGUN_SCOPE = 0x114F, 54, 28


def skip_sandbox_intro(instance):
    """Keep Heavy Assault visible when spawned directly in the Rialto sandbox."""
    if instance.graph.index != 0x14EB:
        return
    # 14EB/4 also starts the ordinary body logic at node 14. Preserve
    # that link and the original graph layout used by observers.
    for event in instance.queue:
        if event.kind == runtime.ENTRY and event.node.pos == 4:
            entry = copy(event.node)
            plug = entry.field("m_outPlug")
            entry.fields = {**entry.fields, "m_outPlug": {
                **plug, "links": [link for link in plug["links"] if link[0] != 23],
            }}
            event.node = entry


def ability(component, graph=None, position=None):
    melee = MELEE.get(component.world.hero)
    if melee is None:
        return None
    if graph is None:
        graph, position, _, _ = melee
    elif component.world.hero != HERO:
        return None
    for instance in component.instances.values():
        if instance.graph.index == graph:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if isinstance(state, nodes.Ability):
                return state
    return None


def emit_charge_voice(world, voice):
    """Bridge the local-only charge bark on its authored activation."""
    if (world.hero != HERO or not world.manual_nameplate or world.match is None
            or voice.instance.graph.index != CHARGE_GRAPH or voice.node.pos != CHARGE_VOICE):
        return
    entity = voice.instance.component.entity
    bot = next(
        (bot for bot in world.match.bots if bot.entity == entity and bot.firing and not bot.dead), None
    )
    if bot is None:
        return
    for viewer in world.match.players:
        if viewer.spawned and not viewer.gone:
            voicelines.heavy_charge(viewer, bot, voice.counter)


def _target(bot, state, query_position):
    world = state.instance.component.world
    match = world.match
    if (
        match is None
        or match.ended
        or match.collision_world is None
        or not bot.firing
        or bot.dead
        or match.combat.is_stunned(bot)
    ):
        return None
    if query_position == CHARGE_QUERY and match.combat.junkrat.rooted(bot.entity, match.tick):
        return None
    target = next((target for target in match.combat._enemies(bot)
                   if getattr(target.owner, "client", None) is not None
                   and not target.owner.gone and target.owner.entity == bot.attack_target), None)
    if target is None:
        return None
    query = state.instance.graph.node(query_position)
    if query is None:
        return None
    radius = expr.to_float(state.evaluate(query.field("m_radius"), watch=False))
    offset = state.evaluate(query.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3) or not math.isfinite(radius) or radius <= 0:
        return None
    x, y, z = bot.position
    dx, _, dz = combat.aim(bot.yaw, 0)
    eye = (x, y + offset.y, z)
    origin = (x + dz * offset.x + dx * offset.z, eye[1], z - dx * offset.x + dz * offset.z)
    a, b = target.segment()
    # The swept mover can touch the victim before the scripted sphere overlaps.
    contact = (
        query_position == CHARGE_QUERY
        and bot.navigation is not None
        and getattr(bot.navigation, "charge_contact", None) == (match.tick, target.entity)
    )
    if (
        contact or combat.segment_distance(origin, a, b) <= radius + target.radius
    ) and combat.capsule_visible(match.collision_world, eye, a, b, target.radius):
        return target
    return None


def charge_lane(match, bot, target):
    from ow174.game import npc_navigation

    world = match.collision_world
    x, y, z = target.mover.position
    if (world is None or match.combat.junkrat.rooted(bot.entity, match.tick)
            or not 8 <= math.dist(bot.position, target.mover.position) <= 16
            or abs(bot.position[1] - y) >= CHARGE_FLOOR_TOLERANCE):
        return False
    data = mover_data(bot.body)
    if not npc_navigation.teammate_clear(match, bot, bot.position, target.mover.position, data):
        return False
    # The runtime mover sweeps the actual capsule during the charge.
    end = npc_navigation.edge(world, bot.position, x, z, data, side_clearance=False)
    return end is not None and abs(end[1] - y) <= data.step_down


def update(bot, component, enabled):
    """Start the authored close attack and pause primary input during its scope."""
    state = ability(component)
    if state is None:
        return False
    charge = ability(component, CHARGE_GRAPH, CHARGE_ABILITY)
    if charge is not None:
        if CHARGE_BUTTON in component.held:
            component.edges([(CHARGE_BUTTON, False)])
        phase = charge.instance.vars.get(59)
        if phase is not None and expr.to_int(phase.value()) != 0:
            fight = component.world.match.combat
            if (not bot.firing or bot.attack_target is None or fight.is_stunned(bot)
                    or fight.junkrat.rooted(bot.entity, fight.tick)):
                stop_charge(component, charge)
            return True
        if enabled and not state.flags & nodes.Ability.RUNNING and charge.remaining() <= 0:
            match = component.world.match
            target = next((player for player in match.players if player.entity == bot.attack_target), None)
            if target is not None and charge_lane(match, bot, target):
                component.edges([(CHARGE_BUTTON, True)])
                return True
    if BUTTON in component.held:
        component.edges([(BUTTON, False)])
    if state.flags & nodes.Ability.RUNNING:
        return True
    _, _, query, _ = MELEE[component.world.hero]
    if enabled and state.remaining() <= 0 and _target(bot, state, query) is not None:
        component.edges([(BUTTON, True)])
        return True
    return False


def stop(component):
    stop_miniguns(component)
    state = ability(component)
    if state is not None:
        state.presses.clear()
        state.stop()
        component.edges([(BUTTON, False)])
    charge = ability(component, CHARGE_GRAPH, CHARGE_ABILITY)
    if charge is not None:
        stop_charge(component, charge)


def stop_miniguns(component):
    if component.world.hero != HERO:
        return
    for instance in component.instances.values():
        if instance.graph.index != MINIGUN_GRAPH:
            continue
        # The button's release ends its callback (54). Its callback started
        # the sibling stack (28), which owns both windup and the live volley.
        states = [
            instance.states.get(instance.graph.node(pos).state) for pos in (MINIGUN_PRESS, MINIGUN_SCOPE)
        ]
        pending = [state for state in states if state is not None]
        instance.drop_begins(pending)
        for state in pending:
            state.end(False)


def stop_charge(component, ability_state):
    instance = ability_state.instance
    phase = instance.vars.get(59)
    was_charging = phase is not None and expr.to_int(phase.value()) != 0
    cooldown = instance.vars.get(472)
    duration = expr.to_float(cooldown.value()) if cooldown is not None else 0.0
    ability_state.presses.clear()
    ability_state.stop()
    component.edges([(CHARGE_BUTTON, False)])
    instance.write((expr.INSTANCE, 59), 0)
    match = getattr(component.world, "match", None)
    if match is not None:
        match.combat.release_heavy_capture(component.entity)
    pending = [instance.states.get(instance.graph.node(pos).state) for pos in (137, 71)]
    for state in pending:
        if state is not None and state.active:
            state.end(False)
    instance.drop_begins([state for state in pending if state is not None])
    if was_charging and ability_state.remaining() <= 0 and math.isfinite(duration) and duration > 0:
        # Host-driven interruption can bypass the authored stack exit's
        # cooldown write. Use the same resource value in that case.
        instance.write((expr.INSTANCE, 61), duration)


def charge_motion(bot):
    """Consume 1408's movement phase with the shared collision mover."""
    script = bot.body_script
    if script is None or bot.hero != HERO:
        return None
    component = script.component
    charge = ability(component, CHARGE_GRAPH, CHARGE_ABILITY)
    if charge is None:
        return None
    fight = component.world.match.combat
    if fight.junkrat.rooted(bot.entity, fight.tick):
        stop_charge(component, charge)
        return None
    instance = charge.instance
    phase = instance.vars.get(59)
    if phase is None or expr.to_int(phase.value()) == 0:
        return None
    if expr.to_int(phase.value()) == 2:
        recovery = instance.states.get(instance.graph.node(50).state)
        if recovery is not None and recovery.active and recovery.now - recovery.start >= 500:
            stop_charge(component, charge)
        return charge, None
    state = instance.states.get(instance.graph.node(71).state)
    if state is None or not state.active:
        return charge, None
    if state.now - state.start >= 2000:  # bounded sandbox charge window
        stop_charge(component, charge)
        return charge, None
    slot = instance.vars.get(3192)
    direction = slot.value() if slot is not None else None
    if not isinstance(direction, expr.Vec3) or math.hypot(direction.x, direction.z) < 0.01:
        dx, _, dz = combat.aim(bot.yaw, 0)
        direction = expr.Vec3(dx, 0.0, dz)
        instance.write((expr.INSTANCE, 3192), direction)
    rows = state.node.field("m_BC5E91CF")
    if not rows or rows[0].get("$") != "STU_09F9B1FE":
        return charge, None
    velocity = state.evaluate(rows[0].get("m_9B7A63EA"), watch=False)
    if not isinstance(velocity, expr.Vec3):
        return charge, None
    speed = math.hypot(velocity.x, velocity.z)
    if not math.isfinite(speed) or speed <= 0:
        return charge, None
    x, y, z = bot.position
    return charge, ((x + velocity.x / speed, y, z + velocity.z / speed), speed)


def _source(world, state, position):
    if state.node.pos != position:
        return NotImplemented
    if world.match is None:
        return NotImplemented
    component = state.instance.component
    return next(
        (
            bot
            for bot in world.match.bots
            if bot.entity == component.entity
            and bot.spawner.get("manual_spawn") is True
            and bot.body_script is not None
            and bot.body_script.component is component
        ),
        None,
    )


def _attack_nodes(world, state):
    graph = state.instance.graph.index
    if world.hero == HERO and graph == CHARGE_GRAPH:
        return CHARGE_QUERY, CHARGE_IMPACT
    melee = MELEE.get(world.hero)
    if melee is not None and graph == melee[0]:
        return melee[2], melee[3]
    return None


def tracked_targets(world, state):
    attack = _attack_nodes(world, state)
    if attack is None:
        return NotImplemented
    query, _ = attack
    source = _source(world, state, query)
    if source is NotImplemented:
        return NotImplemented
    target = _target(source, state, query) if source is not None else None
    return (target.entity,) if target is not None else ()


def _push(source, target, state, settings, *, defer=False):
    # Resource scalars 1257/1258 supply the horizontal and upward parts.
    # Their application as a velocity impulse is the sandbox reconstruction.
    horizontal = expr.to_float(state.evaluate(settings.get("m_E33EFEE1"), watch=False))
    upward = expr.to_float(state.evaluate(settings.get("m_10399AE7"), watch=False))
    if not all(math.isfinite(value) and value >= 0 for value in (horizontal, upward)):
        return
    x, _, z = source.position
    dx, dz = target.feet[0] - x, target.feet[2] - z
    distance = math.hypot(dx, dz)
    if distance:
        dx, dz = dx / distance, dz / distance
    else:
        dx, _, dz = combat.aim(source.yaw, 0)
    mover = target.owner.mover
    if defer:
        # The original configured-Sentry force must join the next physical
        # action phase after its persistent movement constraints.
        fight = state.instance.component.world.match.combat
        accepted = fight.knockbacks.apply(target, expr.Vec3(horizontal * dx, upward, horizontal * dz),
                                          impact_frame=fight.tick)
        if accepted and upward > 0 and state.instance.graph.index in (GRAPH, 0x1422):
            fight.knockbacks.record_launch(target, source_entity=source.entity)
    else:
        vx, vy, vz = mover.velocity
        mover.velocity = (expr.f32(vx + horizontal * dx), expr.f32(vy + upward),
                          expr.f32(vz + horizontal * dz))
        if upward > 0 and not mover.flags_word & IN_AIR:
            mover.flags_word |= FELL
            mover.air_ticks = 0
        if upward > 0 and state.instance.graph.index in (GRAPH, 0x1422):
            state.instance.component.world.match.combat.knockbacks.record_launch(
                target, source_entity=source.entity)
    if source.hero == HERO and (horizontal > 0 or upward > 0):
        correction.force_impulse(target.owner)


def modify_health(world, state, elapsed_ms):
    attack = _attack_nodes(world, state)
    if attack is None:
        return NotImplemented
    query, impact = attack
    source = _source(world, state, impact)
    if source is NotImplemented:
        return NotImplemented
    if source is not None and elapsed_ms == 0:
        target = _target(source, state, query)
        chosen = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if target is not None and isinstance(chosen, tuple) and expr.Entity(target.entity) in chosen:
            settings = state.node.field("m_modifyHealth")
            amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
            if math.isfinite(amount) and amount > 0:
                fight = world.match.combat
                fight._damage(source, target, expr.f32(amount), False, None, fight.tick)
                if not fight.vitals_of(target).dead:
                    _push(source, target, state, settings)
    return True
