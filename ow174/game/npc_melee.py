"""Manual contact policy for the authored NPC melee and Heavy Assault charge graphs."""

import math

from ow174.game.script import expr

BUTTON = 31
CHARGE_BUTTON = 35
HEAVY_BODY = 0x0400000000001EBB
GRAPHS = {
    0x0400000000001D40: (0x1422, 5, 4),
    HEAVY_BODY: (0x140E, 6, 5),
}
CHARGE = (0x1408, 32, 35)
CHARGE_MOVEMENT = 71
CHARGE_PIN_QUERY = 96
CHARGE_PIN_STATE = 69
CHARGE_IMPACT = 29
CHARGE_ROOT = 1


def charge_activation(source):
    if source.body_script is None:
        return None
    for instance in source.body_script.component.instances.values():
        if instance.graph.index == CHARGE[0] and not instance.component.strict:
            root = instance.states.get(instance.graph.node(CHARGE_ROOT).state)
            if root is not None and root.active:
                return instance.id, root.counter
    return None


def release_charge(fight, source, *, cancel=False):
    if source.attack_special == CHARGE_BUTTON:
        fight.pins.pop(source.entity, None)
        if cancel and source.body_script is not None:
            source.cancelled_charge = charge_activation(source)
            for instance in source.body_script.component.instances.values():
                if instance.graph.index == CHARGE[0] and not instance.component.strict:
                    instance.write((expr.INSTANCE, 536), None)


def refresh_charge_pin(fight, source):
    """Bind control to the graph's captured body while close enough to hold it."""
    if source.attack_special != CHARGE_BUTTON:
        return
    if source.cancelled_charge is not None and charge_activation(source) != source.cancelled_charge:
        source.cancelled_charge = None
    if charging(fight, source):
        for instance in source.body_script.component.instances.values():
            if instance.graph.index != CHARGE[0] or instance.component.strict:
                continue
            pin = instance.states.get(instance.graph.node(CHARGE_PIN_STATE).state)
            query = instance.states.get(instance.graph.node(CHARGE_PIN_QUERY).state)
            victim = instance.find_var(expr.INSTANCE, 536, create=False)
            if pin is None or not pin.active or query is None or not query.active or victim is None:
                continue
            entity = expr.to_entity(victim.value())
            target = next((t for t in fight._enemies(source) if t.entity == entity
                           and t.owner.entity == source.attack_target
                           and getattr(t.owner, 'client', None) is not None and not t.owner.gone), None)
            if contact(fight, source, target, query):
                if fight.pins.get(source.entity) != entity:
                    fight.pins[source.entity] = entity
                    fight._interrupt(target)
                return
    release_charge(fight, source)


def charge_carry(fight, player, seconds, tick):
    """Translate a captured player by the source's collision-resolved travel."""
    source = next((bot for bot in fight.match.bots if fight.pins.get(bot.entity) == player.body
                   and charging(fight, bot)), None)
    if source is None or source.last_tick != tick:
        return None
    velocity = source.velocity
    speed = math.hypot(*velocity)
    if speed == 0:
        return None
    destination = tuple(p + v * seconds for p, v in zip(player.mover.position, velocity, strict=True))
    return destination, speed


def charge_carry_blocked(fight, player):
    """Bridge a blocked captured-player move to the authored impact health node."""
    source = next((bot for bot in fight.match.bots if fight.pins.get(bot.entity) == player.body
                   and charging(fight, bot)), None)
    if source is None or source.body_script is None:
        return
    for instance in source.body_script.component.instances.values():
        if instance.graph.index != CHARGE[0] or instance.component.strict:
            continue
        state = instance.state(instance.graph.node(CHARGE_IMPACT).state)
        state.begin()
        state.end(True)
        break
    release_charge(fight, source, cancel=True)
    source.firing = False


def charge_impact(world, state, elapsed_ms):
    instance = state.instance
    if instance.graph.index != CHARGE[0] or state.node.pos != CHARGE_IMPACT or instance.component.strict:
        return NotImplemented
    if elapsed_ms or world.match is None:
        return True
    fight = world.match.combat
    source = next((bot for bot in world.match.bots if bot.entity == instance.component.entity), None)
    if source is None or not charging(fight, source):
        return True
    victim = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if fight.pins.get(source.entity) != victim:
        return True
    target = next((target for target in fight._enemies(source) if target.entity == victim), None)
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    if target is not None and math.isfinite(amount) and amount > 0:
        fight._damage(source, target, amount, False, None, fight.tick)
    return True


def charge_ray_hit(world, state):
    """Reconstruct the paired charge probes using static mover collision."""
    instance = state.instance
    if (world.match is None or instance.component.strict or instance.graph.index != CHARGE[0]
            or state.node.pos not in (168, 170, 171, 172)):
        return None
    source = next((b for b in world.match.bots if b.entity == instance.component.entity), None)
    if source is None or not charging(world.match.combat, source):
        return False
    start = state.evaluate(state.node.field('m_startPosWS'), watch=False)
    if isinstance(start, expr.Entity) and start.id == source.entity:
        start = expr.Vec3(*source.position)
    direction = state.evaluate(state.node.field('m_dirWS'), watch=False)
    length = expr.to_float(state.evaluate(state.node.field('m_castLength'), watch=False))
    if not isinstance(start, expr.Vec3) or not isinstance(direction, expr.Vec3) or length <= 0:
        return False
    magnitude = math.hypot(*direction)
    if not math.isfinite(magnitude) or magnitude == 0:
        return False
    from ow174.game import combat

    return combat.ray_world(world.match.collision_world, tuple(start),
                            tuple(v / magnitude for v in direction), length) is not None


def charging(fight, source):
    return (source.body == HEAVY_BODY and source.attack_special == CHARGE_BUTTON
            and source.firing and not source.dead and not source.controlled
            and not fight.is_stunned(source) and not fight.punches.controlled(source.entity)
            and (source.cancelled_charge is None or charge_activation(source) != source.cancelled_charge))


def charge_query(world, cfg, instance):
    """Supply the manual engagement's poses to the authored direction setters."""
    cls = cfg.get('$')
    if (cls not in ('STU_2200A66D', 'STU_71A87E92') or world.match is None
            or instance is None or instance.component.strict or instance.graph.index != CHARGE[0]):
        return NotImplemented
    source = next((bot for bot in world.match.bots if bot.entity == instance.component.entity), None)
    if source is None or not charging(world.match.combat, source):
        return NotImplemented
    entity = cfg.get('m_entity')
    if cls == 'STU_71A87E92' and isinstance(entity, dict) and entity.get('$') == 'STU_A37A910D':
        target = next((p for p in world.match.players if p.entity == source.attack_target
                       and p.has_body and p.spawned and not p.gone), None)
        return expr.Vec3(*target.mover.position) if target is not None else NotImplemented
    if expr.to_entity(instance.evaluate(entity)) != source.entity:
        return NotImplemented
    if cls == 'STU_71A87E92':
        return expr.Vec3(*source.position)
    from ow174.game import combat

    return expr.Vec3(*combat.aim(source.yaw, 0))


def charge_velocity(fight, source):
    script = source.body_script
    if fight.match.ended or script is None or not charging(fight, source):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != CHARGE[0] or instance.component.strict:
            continue
        node = instance.graph.node(CHARGE_MOVEMENT)
        state = instance.states.get(node.state)
        if state is not None and state.active:
            row = next((item for item in node.field('m_BC5E91CF') or ()
                        if item.get('$') == 'STU_09F9B1FE'), None)
            velocity = instance.evaluate(row['m_9B7A63EA']) if row else None
            if isinstance(velocity, expr.Vec3) and all(math.isfinite(v) for v in velocity):
                return tuple(velocity)
    return None


def context(world, state, node_index):
    match = world.match
    component = state.instance.component
    if match is None or match.ended or component.strict:
        return None
    fight = match.combat
    source = next((bot for bot in match.bots if bot.entity == component.entity), None)
    if source is None or source.body_script is None or source.body_script.component is not component:
        return None
    charge = source.body == HEAVY_BODY and state.instance.graph.index == CHARGE[0]
    record = CHARGE if charge else GRAPHS.get(source.body)
    pin_query = charge and node_index == 1 and state.node.pos == CHARGE_PIN_QUERY
    if (record is None or state.instance.graph.index != record[0]
            or (state.node.pos != record[node_index] and not pin_query)):
        return None
    if (source.attack_special != (CHARGE_BUTTON if charge else BUTTON)
            or not source.firing or source.dead or source.controlled
            or fight.is_stunned(source) or fight.punches.controlled(source.entity)
            or (charge and not charging(fight, source))):
        return fight, source, None, record
    target = next((t for t in fight._enemies(source) if t.owner.entity == source.attack_target
                   and getattr(t.owner, 'client', None) is not None and not t.owner.gone), None)
    return fight, source, target, record


def contact(fight, source, target, query):
    if target is None:
        return False
    from ow174.game import combat

    offset = query.evaluate(query.node.field('m_05BE62DD'), watch=False)
    radius = expr.to_float(query.evaluate(query.node.field('m_radius'), watch=False))
    if not isinstance(offset, expr.Vec3) or not math.isfinite(radius) or radius <= 0:
        return False
    x, y, z = source.position
    forward = combat.aim(source.yaw, 0)
    right = combat.aim(source.yaw + 16384, 0)
    center = (x + offset.z * forward[0] + offset.x * right[0], y + offset.y,
              z + offset.z * forward[2] + offset.x * right[2])
    origin = x, y + combat.eye_height(source.body), z
    a, b = target.segment()
    return (combat.segment_distance(center, a, b) <= radius + target.radius
            and combat.capsule_visible(fight.match.collision_world, origin, a, b, target.radius))


def track(world, state):
    pair = context(world, state, 1)
    if pair is None:
        return None
    fight, source, target, _ = pair
    if not contact(fight, source, target, state):
        return ()
    if state.node.pos == CHARGE_PIN_QUERY:
        from ow174.game import combat

        # Reconstruct the narrow pin query as a forward cone. Its angle
        # comes from the authored query and is treated as a full width.
        angle = expr.to_float(state.evaluate(state.node.field('m_A30B383E'), watch=False))
        forward = combat.aim(source.yaw, 0)
        point, _ = target.segment()
        dx, dz = point[0] - source.position[0], point[2] - source.position[2]
        if dx * forward[0] + dz * forward[2] < math.hypot(dx, dz) * math.cos(math.radians(angle / 2)):
            return ()
    return (target.entity,)


def modify_health(world, state, elapsed_ms):
    impact = charge_impact(world, state, elapsed_ms)
    if impact is not NotImplemented:
        return impact
    pair = context(world, state, 2)
    if pair is None:
        return NotImplemented
    fight, source, target, record = pair
    charge = record == CHARGE
    if target is None or state.contact_applied or (elapsed_ms and not charge):
        return True
    settings = state.node.field('m_modifyHealth') or {}
    targets = state.evaluate(state.node.field('m_FA3ECC43'), watch=False)
    query_node = state.instance.graph.node(record[1])
    query = state.instance.states.get(query_node.state)
    if query is None or not query.active or not contact(fight, source, target, query):
        return True
    # The charge's target volume persists while the NPC moves. Recheck that
    # volume on VM ticks after the normal collision-aware movement step.
    if not (charge and elapsed_ms) and (
            not isinstance(targets, tuple) or expr.Entity(target.entity) not in targets):
        return True
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    if math.isfinite(amount) and amount > 0:
        state.contact_applied = True
        fight._damage(source, target, amount, False, None, fight.tick)
    return True
