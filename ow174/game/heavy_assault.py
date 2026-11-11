"""Resource-backed NPC melee attacks and Heavy Assault's charge."""

import math

from ow174.game import combat, genji, sigma
from ow174.game.commands import Command
from ow174.game.mover import FELL, IN_AIR, Mover, mover_data
from ow174.game.script import expr, graph, nodes
from ow174.game.script.runtime import InstanceContext

HERO = 0x02E00000000001BA
TROOPER = 0x02E00000000001AC
GRAPH, ABILITY, QUERY, IMPACT, BUTTON = 0x140E, 9, 6, 5, 31
CHARGE_GRAPH, CHARGE_ABILITY, CHARGE_QUERY, CHARGE_IMPACT, CHARGE_BUTTON = 0x1408, 82, 100, 102, 35
VICTIM_GRAPH = 0x1407
TERRAIN_PROBES = {168: 1, 170: 1, 171: 0, 172: 0}
MELEE = {HERO: (GRAPH, ABILITY, QUERY, IMPACT), TROOPER: (0x1422, 8, 5, 4)}


class ChargeSearchContext(InstanceContext):
    def __init__(self, instance, target):
        super().__init__(instance, None)
        self.target = target

    def member(self, entity, var):
        if entity != self.target.entity:
            return super().member(entity, var)
        script = self.target.owner.body_script
        slot = script.component.entity_var(var, create=False) if script is not None else None
        if slot is None:
            return None
        if var == 2024 and slot.links:
            source = slot.links[-1].state
            child = source.instance
            if child.graph.index == VICTIM_GRAPH and child.source_entity() == self.instance.component.entity:
                return slot.value(below=source)
        return slot.value()


def native(world, cfg, instance):
    if world.hero != HERO or world.match is None or instance is None or instance.graph.index != CHARGE_GRAPH:
        return NotImplemented
    kind = cfg.get("$")
    if kind == "STU_021D01CD":
        if expr.to_entity(instance.evaluate(cfg.get("m_entity"))) == instance.component.entity:
            direction = instance.vars.get(3192)
            if direction is not None and isinstance(direction.value(), expr.Vec3):
                return direction.value()
    elif kind == "STU_AC829876" and cfg is instance.graph.node(96).field("m_filter.m_condition.m_configVars")[0]:
        # This one authored condition asks for category 1. Its manual
        # fixture supplies the player-body inventory from the match.
        entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        return any(player.has_body and player.spawned and player.body == entity for player in world.match.players)
    return NotImplemented


def ability(component, graph=None, position=None):
    hero = component.world.hero
    if component.strict or hero not in MELEE:
        return None
    if graph is None:
        graph, position, _, _ = MELEE[hero]
    elif hero != HERO:
        return None
    for instance in component.instances.values():
        if instance.graph.index == graph:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if isinstance(state, nodes.Ability):
                return state
    return None


def _melee_targets(bot, state, query_position):
    world = state.instance.component.world
    match = world.match
    if (match is None or match.ended or match.collision_world is None
            or not bot.firing or bot.dead or match.combat.is_stunned(bot)):
        return ()
    query = state.instance.graph.node(query_position)
    if query is None:
        return ()
    radius = expr.to_float(state.evaluate(query.field("m_radius"), watch=False))
    offset = state.evaluate(query.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3) or not math.isfinite(radius) or radius <= 0:
        return ()
    x, y, z = bot.position
    dx, _, dz = combat.aim(bot.yaw, 0)
    eye = (x, y + offset.y, z)
    origin = (x + dz * offset.x + dx * offset.z, eye[1], z - dx * offset.x + dz * offset.z)
    found = []
    for target in match.combat._enemies(bot):
        if target.owner not in match.players or target.owner.gone:
            continue
        a, b = target.segment()
        if (combat.segment_distance(origin, a, b) <= radius + target.radius
                and combat.capsule_visible(match.collision_world, eye, a, b, target.radius)):
            found.append(target)
    return tuple(found)


def _source(world, state, position):
    if state.instance.component.strict or state.node.pos != position:
        return NotImplemented
    if world.match is None:
        return NotImplemented
    component = state.instance.component
    return next((bot for bot in world.match.bots if bot.entity == component.entity
                 and bot.spawner.get("manual_spawn") is True and bot.body_script is not None
                 and bot.body_script.component is component), None)


def terrain_probe(world, state):
    kind = TERRAIN_PROBES.get(state.node.pos)
    if world.hero != HERO or state.instance.graph.index != CHARGE_GRAPH or kind is None:
        return NotImplemented
    source = _source(world, state, state.node.pos)
    if source is None or source is NotImplemented or world.match.collision_world is None:
        return NotImplemented
    request = nodes.raycast_request(state.instance, state.node, kind=kind)
    if request is None:
        return False  # The windup has not published its direction yet.
    # Use the static mover mesh for both authored probes. The native
    # type-1 shape and the A1 filter still need a runtime binding.
    collision = world.match.collision_world
    return collision.cast_ray(tuple(request.origin), tuple(request.direction), request.length) is not None


def _contact_nodes(world, state):
    graph = state.instance.graph.index
    melee = MELEE.get(world.hero)
    if melee is not None and graph == melee[0]:
        return melee[2], melee[3]
    return None, None


def tracked_targets(world, state):
    if world.hero == HERO and state.instance.graph.index == CHARGE_GRAPH:
        source = _source(world, state, state.node.pos)
        if source is not NotImplemented and state.node.pos in (32, 85, 96, CHARGE_QUERY):
            return _charge_targets(world.match.combat, source, state) if source is not None else ()
    query, _ = _contact_nodes(world, state)
    source = _source(world, state, query)
    if source is NotImplemented:
        return NotImplemented
    return tuple(target.entity for target in _melee_targets(source, state, query)) if source is not None else ()


def _charge_targets(fight, source, state):
    instance = state.instance
    slot = instance.vars.get(3192)
    forward = slot.value() if slot is not None else None
    offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    if (source.dead or fight.is_stunned(source) or not isinstance(forward, expr.Vec3)
            or not any(forward) or not isinstance(offset, expr.Vec3)):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    angle = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    minimum_dot = math.cos(math.radians(angle))
    x, y, z = source.position
    origin = (x, y + offset.y, z)
    centre = (x + forward.z * offset.x + forward.x * offset.z, origin[1],
              z - forward.x * offset.x + forward.z * offset.z)
    condition = state.node.field("m_filter.m_condition")
    found = []
    carried = fight.pins.get(source.entity)
    for target in fight._enemies(source):
        if target.owner not in fight.match.players or sigma.body_protection(target.owner):
            continue
        if state.node.pos == 32 and target.entity == carried:
            continue
        if state.node.pos == 96 and (carried != target.entity
                and (sigma.particle_barrier(target.owner) or sigma.fortify(target.owner)
                     or target.entity in fight.pins.values())):
            continue
        dx, dz = target.feet[0] - x, target.feet[2] - z
        if dx * forward.x + dz * forward.z < minimum_dot * math.hypot(dx, dz):
            continue
        a, b = target.segment()
        distance = combat.segment_distance(centre, a, b) - target.radius
        if distance > radius or not combat.capsule_visible(fight.match.collision_world, origin, a, b, target.radius):
            continue
        with instance.component.parameter_scope({8: expr.Entity(target.entity)}):
            if expr.truthy(expr.evaluate(condition, ChargeSearchContext(instance, target))):
                found.append((distance, target.entity))
    limit = state.node.field("m_C39691D9")
    maximum = expr.to_int(state.evaluate(limit, watch=False)) if limit is not None else len(found)
    return tuple(entity for _, entity in sorted(found)[:maximum])


def sync_captures(fight):
    """Attach the authored victim graph while a live 1408 carry is active."""
    wanted = {}
    for bot in fight.match.bots:
        if bot.hero != HERO or bot.dead or bot.body_script is None or fight.is_stunned(bot):
            continue
        charge = ability(bot.body_script.component, CHARGE_GRAPH, CHARGE_ABILITY)
        phase = charge.instance.vars.get(59) if charge is not None else None
        if phase is None or expr.to_int(phase.value()) != 1:
            continue
        instance = charge.instance
        state = instance.states.get(instance.graph.node(158).state)
        if state is not None and state.active:
            entity = expr.to_entity(state.evaluate(state.node.field("m_targets"), watch=False))
            target = next((t for t in fight._enemies(bot) if t.entity == entity and t.owner in fight.match.players
                           and not sigma.body_protection(t.owner)), None)
            if target is not None:
                wanted.setdefault(entity, (bot.entity, target))

    for player in fight.match.players:
        if player.body_script is None:
            continue
        component = player.body_script.component
        desired = wanted.get(component.entity)
        for child in tuple(component.instances.values()):
            if child.graph.index != VICTIM_GRAPH:
                continue
            source = child.source_entity()
            if desired is not None and desired[0] == source and fight.pins.get(source) == component.entity:
                desired = None
                continue
            if fight.pins.get(source) == component.entity:
                del fight.pins[source]
            component.destroy(child)
        if desired is None or component.entity in fight.pins.values():
            continue
        definition = graph.graph(VICTIM_GRAPH)
        if definition is not None:
            source, target = desired
            child = component.create(definition, values={(expr.INSTANCE, 27): expr.Entity(source)}, init=False)
            child.set_source_entity(source)
            child.init()
            fight.pins[source] = component.entity
            fight._interrupt(target)


def release_capture(fight, source, victim):
    player = next((p for p in fight.match.players if p.body == victim and p.body_script is not None), None)
    if player is not None:
        component = player.body_script.component
        for child in tuple(component.instances.values()):
            if child.graph.index == VICTIM_GRAPH and child.source_entity() == source:
                component.destroy(child)


def _push(source, target, state, settings):
    x, _, z = source.position
    _push_from(source, target, state, settings, x, z, "m_10399AE7")


def _push_from(source, target, state, settings, x, z, upward_field):
    # Resource scalars 1257/1258 supply the horizontal and upward parts.
    # Their application as a velocity impulse is the sandbox reconstruction.
    horizontal = expr.to_float(state.evaluate(settings.get("m_E33EFEE1"), watch=False))
    upward = expr.to_float(state.evaluate(settings.get(upward_field), watch=False))
    if not all(math.isfinite(value) and value >= 0 for value in (horizontal, upward)):
        return
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


def _charge_damage(world, state, elapsed_ms):
    source = _source(world, state, state.node.pos)
    if source is NotImplemented:
        return NotImplemented
    secondary = state.node.pos == 35
    if secondary and elapsed_ms == 0:
        state.charge_contacts = set()
    if source is None or (elapsed_ms and not secondary):
        return True
    fight = world.match.combat
    if source.dead or fight.is_stunned(source):
        return True
    value = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    selected = value if isinstance(value, tuple) else (value,)
    settings = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    loadout = expr.guid_of(state.evaluate(settings["m_loadout"], watch=False))
    for target in fight._enemies(source):
        if expr.Entity(target.entity) not in selected:
            continue
        if secondary:
            if target.entity == fight.pins.get(source.entity) or target.entity in state.charge_contacts:
                continue
            state.charge_contacts.add(target.entity)
        protected = (sigma.body_protection(target.owner) or sigma.particle_barrier(target.owner)
                     or sigma.fortify(target.owner))
        fight._damage(source, target, amount, False, loadout, fight.tick)
        if secondary and not protected and not fight.vitals_of(target).dead:
            origin = state.evaluate(settings["m_C0139FA6"], watch=False)
            if isinstance(origin, expr.Vec3):
                _push_from(source, target, state, settings, origin.x, origin.z, "m_583C208B")
    return True


def modify_health(world, state, elapsed_ms):
    if world.hero == HERO and state.instance.graph.index == CHARGE_GRAPH and state.node.pos in (29, 35, CHARGE_IMPACT):
        return _charge_damage(world, state, elapsed_ms)
    query, impact = _contact_nodes(world, state)
    source = _source(world, state, impact)
    if source is NotImplemented:
        return NotImplemented
    if source is not None and elapsed_ms == 0:
        chosen = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        if isinstance(chosen, tuple) and chosen:
            settings = state.node.field("m_modifyHealth")
            amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
            if math.isfinite(amount) and amount > 0:
                fight = world.match.combat
                for target in _melee_targets(source, state, query):
                    if (expr.Entity(target.entity) not in chosen
                            or genji.parries_melee(fight, target, source.position, settings)):
                        continue
                    protected = (sigma.body_protection(target.owner) or sigma.particle_barrier(target.owner)
                                 or sigma.fortify(target.owner))
                    fight._damage(source, target, expr.f32(amount), False, None, fight.tick)
                    if not protected and not fight.vitals_of(target).dead:
                        _push(source, target, state, settings)
    return True


def update(bot, component, enabled, target):
    """Prefer the authored melee when the manual target is within reach."""
    state = ability(component)
    if state is None:
        return False
    if bot.hero == TROOPER and bot.effect_mover is not None and bot.effect_mover.airborne:
        if state.flags & nodes.Ability.RUNNING:
            stop(component)
        return False
    if update_charge(bot, component, enabled, target, state):
        return True
    if BUTTON in component.held:
        component.edges([(BUTTON, False)])
    if state.flags & nodes.Ability.RUNNING:
        if not enabled:
            stop(component)
        return True
    _, _, query, _ = MELEE[component.world.hero]
    if enabled and state.remaining() <= 0 and any(item.owner is target for item in _melee_targets(bot, state, query)):
        component.edges([(BUTTON, True)])
        return True
    return False


def stop(component, bot=None):
    state = ability(component)
    if state is not None:
        state.presses.clear()
        state.stop()
        component.edges([(BUTTON, False)])
    charge = ability(component, CHARGE_GRAPH, CHARGE_ABILITY)
    if charge is not None:
        stop_charge(component, charge)
        if bot is not None and bot.effect_mover is None:
            bot.velocity = (0.0, 0.0, 0.0)


def update_charge(bot, component, enabled, target, melee):
    charge = ability(component, CHARGE_GRAPH, CHARGE_ABILITY)
    if charge is None:
        return False
    if CHARGE_BUTTON in component.held:
        component.edges([(CHARGE_BUTTON, False)])
    phase = charge.instance.vars.get(59)
    current_phase = expr.to_int(phase.value()) if phase is not None else 0
    if current_phase == 2:
        bot.velocity = (0.0, 0.0, 0.0)
        finish_impact_recovery(charge)
        return True
    if current_phase != 0:
        if (not enabled or abs(bot.position[1] - target.mover.position[1]) >= 0.8
                or bot.effect_mover is not None):
            stop_charge(component, charge)
            if bot.effect_mover is None:
                bot.velocity = (0.0, 0.0, 0.0)
        else:
            step_charge(bot, component, charge)
        return True
    if (enabled and bot.effect_mover is None
            and not melee.flags & nodes.Ability.RUNNING and charge.remaining() <= 0):
        distance = math.dist(bot.position, target.mover.position)
        if 8 <= distance <= 16 and abs(bot.position[1] - target.mover.position[1]) < 0.8:
            component.edges([(CHARGE_BUTTON, True)])
            return True
    return False


def finish_impact_recovery(charge):
    instance = charge.instance
    impact = instance.states.get(instance.graph.node(10).state)
    if impact is None or not impact.active:
        return
    transition = instance.graph.node(15)
    duration = expr.to_float(impact.evaluate(transition.field("m_AA6E611E"), watch=False))
    if impact.now - impact.start >= duration * 1000:
        instance.follow(transition, "m_truePlug", impact)


def step_charge(bot, component, charge):
    match = component.world.match
    fight = match.combat
    motion = charge_motion(bot, charge)
    if motion is None:
        bot.velocity = (0.0, 0.0, 0.0)
        return
    destination, speed = motion
    mover = Mover(bot.position, round(bot.yaw), match.collision_world, mover_data(bot.body))
    command = Command(fight.tick, yaw=round(bot.yaw), pitch=round(bot.pitch))
    result = mover.step_pose(command, combat.TICK, fight.tick, move_to=(destination, speed))
    entity = fight.pins.get(bot.entity)
    victim = next((player for player in match.players if player.body == entity), None)
    if victim is not None:
        _carry(bot, charge, mover, victim.mover, destination, speed, command, result)
        return
    bot.position = tuple(mover.position)
    bot.velocity = mover.velocity
    bot.moved_tick = fight.tick
    if result is False or mover.airborne:
        stop_charge(component, charge)
        if mover.airborne:
            bot.effect_mover = mover
            bot.last_tick = fight.tick
        else:
            bot.velocity = (0.0, 0.0, 0.0)


def _carry(bot, charge, carrier, pose, destination, speed, command, carrier_result):
    start = bot.position
    direction = tuple(end - begin for end, begin in zip(destination, start, strict=True))
    probe = Mover(pose.position, pose.yaw, carrier.world, pose.data, pose.category)
    goal = tuple(p + axis for p, axis in zip(pose.position, direction, strict=True))
    victim_result = probe.step_pose(command, combat.TICK, command.frame, move_to=(goal, speed))

    def progress(before, after):
        return max(0.0, sum((end - begin) * axis for begin, end, axis in zip(before, after, direction, strict=True)))

    stride = min(speed * combat.TICK, progress(start, carrier.position), progress(pose.position, probe.position))
    shift = tuple(axis * stride for axis in direction)
    bot.position = tuple(p + delta for p, delta in zip(start, shift, strict=True))
    pose.position = [p + delta for p, delta in zip(pose.position, shift, strict=True)]
    pose.velocity = (0.0, 0.0, 0.0)
    pose.body = None
    bot.moved_tick = command.frame
    bot.velocity = tuple(delta / combat.TICK for delta in shift)
    if carrier.airborne or probe.airborne:
        stop_charge(charge.instance.component, charge)
    elif carrier_result is False or victim_result is False:
        charge.instance.follow(charge.instance.graph.node(11), "m_truePlug")
        bot.velocity = (0.0, 0.0, 0.0)


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


def charge_motion(bot, charge):
    """Consume 1408's movement phase with the shared collision mover."""
    instance = charge.instance
    component = instance.component
    phase = instance.vars.get(59)
    if phase is None or expr.to_int(phase.value()) == 0:
        return None
    state = instance.states.get(instance.graph.node(71).state)
    if state is None or not state.active:
        return None
    if state.now - state.start >= 2000:  # bounded sandbox charge window
        stop_charge(component, charge)
        return None
    slot = instance.vars.get(3192)
    direction = slot.value() if slot is not None else None
    if not isinstance(direction, expr.Vec3) or math.hypot(direction.x, direction.z) < 0.01:
        dx, _, dz = combat.aim(bot.yaw, 0)
        direction = expr.Vec3(dx, 0.0, dz)
        instance.write((expr.INSTANCE, 3192), direction)
    rows = state.node.field("m_BC5E91CF")
    if not rows or rows[0].get("$") != "STU_09F9B1FE":
        return None
    velocity = state.evaluate(rows[0].get("m_9B7A63EA"), watch=False)
    if not isinstance(velocity, expr.Vec3):
        return None
    speed = math.hypot(velocity.x, velocity.z)
    if not math.isfinite(speed) or speed <= 0:
        return None
    x, y, z = bot.position
    return (x + velocity.x / speed, y, z + velocity.z / speed), speed
