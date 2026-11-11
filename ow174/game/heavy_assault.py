"""Resource-backed NPC melee attacks and Heavy Assault's charge."""

import math

from ow174.game import combat
from ow174.game.mover import FELL, IN_AIR
from ow174.game.script import expr, nodes

HERO = 0x02E00000000001BA
GRAPH, ABILITY, QUERY, IMPACT, BUTTON = 0x140E, 9, 6, 5, 31
TROOPER = 0x02E00000000001AC
MELEE = {HERO: (GRAPH, ABILITY, QUERY, IMPACT), TROOPER: (0x1422, 8, 5, 4)}
CHARGE_GRAPH, CHARGE_ABILITY, CHARGE_QUERY, CHARGE_IMPACT, CHARGE_BUTTON = 0x1408, 82, 100, 102, 35


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


def _target(bot, state, query_position, entity=None):
    world = state.instance.component.world
    match = world.match
    if (match is None or match.ended or match.collision_world is None
            or not bot.firing or bot.dead or match.combat.is_stunned(bot)):
        return None
    target = next((target for target in match.combat._enemies(bot)
                   if getattr(target.owner, "client", None) is not None
                   and not target.owner.gone
                   and (target.owner.entity == bot.attack_target if entity is None
                        else target.entity == entity)), None)
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
    if query_position == 96:
        # The authored 30-degree field narrows the first group. Using it as
        # a half-angle for a horizontal cone is a reconstructed policy.
        tx, tz = target.feet[0]-x, target.feet[2]-z
        distance = math.hypot(tx, tz)
        angle = expr.to_float(state.evaluate(query.field("m_A30B383E"), watch=False))
        if distance and tx*dx+tz*dz < distance*math.cos(math.radians(angle)):
            return None
    a, b = target.segment()
    if (combat.segment_distance(origin, a, b) <= radius + target.radius
            and combat.capsule_visible(match.collision_world, eye, a, b, target.radius)):
        return target
    return None


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
            if not bot.firing or bot.attack_target is None or component.world.match.combat.is_stunned(bot):
                stop_charge(component, charge)
            return True
        if enabled and not state.flags & nodes.Ability.RUNNING and charge.remaining() <= 0:
            match = component.world.match
            target = next((player for player in match.players if player.entity == bot.attack_target), None)
            if target is not None:
                distance = math.dist(bot.position, target.mover.position)
                if 8 <= distance <= 16 and abs(bot.position[1] - target.mover.position[1]) < 0.8:
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
    state = ability(component)
    if state is not None:
        state.presses.clear()
        state.stop()
        component.edges([(BUTTON, False)])
    charge = ability(component, CHARGE_GRAPH, CHARGE_ABILITY)
    if charge is not None:
        stop_charge(component, charge)


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
    return next((bot for bot in world.match.bots if bot.entity == component.entity
                 and bot.spawner.get("manual_spawn") is True and bot.body_script is not None
                 and bot.body_script.component is component), None)


def _attack_nodes(world, state):
    graph = state.instance.graph.index
    if world.hero == HERO and graph == CHARGE_GRAPH:
        if state.node.pos in (32, 35):
            return 32, 35
        if state.node.pos in (96, 120):
            return 96, 120
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
    if source is not None and query == 32:
        primary = state.instance.vars.get(562)
        excluded = primary.value() if primary is not None else ()
        return tuple(target.entity for target in world.match.combat._enemies(source)
                     if expr.Entity(target.entity) not in (excluded or ())
                     and _target(source, state, query, target.entity) is not None)
    target = _target(source, state, query) if source is not None else None
    return (target.entity,) if target is not None else ()


def _push(source, target, state, settings):
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
    vx, vy, vz = mover.velocity
    mover.velocity = (expr.f32(vx + horizontal * dx), expr.f32(vy + upward),
                      expr.f32(vz + horizontal * dz))
    if upward > 0 and not mover.flags_word & IN_AIR:
        mover.flags_word |= FELL
        mover.air_ticks = 0


def modify_health(world, state, elapsed_ms):
    attack = _attack_nodes(world, state)
    if attack is None:
        return NotImplemented
    query, impact = attack
    source = _source(world, state, impact)
    if source is NotImplemented:
        return NotImplemented
    if source is not None and elapsed_ms == 0:
        chosen = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        settings = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(settings.get("m_amount"), watch=False))
        loadout = state.evaluate(settings.get("m_loadout"), watch=False)
        fight = world.match.combat
        for entity in chosen if isinstance(chosen, tuple) else ():
            target = _target(source, state, query, expr.to_entity(entity) if query == 32 else None)
            if target is None or expr.Entity(target.entity) != entity:
                continue
            shielded = fight.barriers.bubble(target) is not None
            if math.isfinite(amount) and amount > 0:
                fight._damage(source, target, expr.f32(amount), False,
                              loadout.guid if isinstance(loadout, expr.Asset) else None, fight.tick)
            if not shielded and not fight.vitals_of(target).dead:
                _push(source, target, state, settings)
    return True


def advance_charge(fight, bot, tick):
    """Drive the retained authored phase with the common swept mover."""
    from ow174.game.commands import Command
    from ow174.game.mover import Mover, mover_data

    charge = charge_motion(bot)
    if charge is None:
        bot.special_mover = None
        return
    ability_state, motion = charge
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    collision = fight._collision()
    if (target is None or target.gone or fight.is_dead(target) or collision is None
            or abs(bot.position[1] - target.mover.position[1]) >= .8):
        stop_charge(bot.body_script.component, ability_state)
        bot.special_mover = None
        return
    if motion is None:
        return
    mover = bot.special_mover
    if mover is None or mover.world is not collision:
        mover = bot.special_mover = Mover(bot.position, round(bot.yaw), collision, mover_data(bot.body))
    destination, speed = motion
    before = bot.position
    outcome = mover.step_pose(Command(tick, yaw=round(bot.yaw)), combat.TICK, tick,
                              move_to=(destination, speed))
    bot.position, bot.velocity = tuple(mover.position), mover.velocity
    bot.moved_tick = tick
    if outcome is False or math.dist(before, bot.position) < .001:
        stop_charge(bot.body_script.component, ability_state)
        bot.special_mover = None
