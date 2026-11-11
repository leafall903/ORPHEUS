"""Authored Heavy Assault charge phases with reconstructed static world travel."""

import math

from ow174.game.script import expr, graph

BODY = 0x0400000000001EBB
GRAPH = 0x1408
BUTTON = 35
ABILITY = 82
PHASE = 59
DRIVE = 71
WALL_CAST = 172
WALL_BRANCH = 173
SIDE_SEARCH = 32
SIDE_HEALTH = 35
FRONT_SEARCH = 96
FRONT_IMPULSE = 120
PIN_ACTION = 158
VICTIM_GRAPH = 0x1407
CRUSH_HEALTH = 29
RELEASE_HEALTH = 88
IMPACT_SEARCH = 100
IMPACT_HEALTH = 102
IMPACT_CONTROL = 189
AUTO_CHARGE_MIN = 6.0  # reconstructed distances for choosing a forward charge
AUTO_CHARGE_MAX = 24.0


def charge(bot):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def phase(instance):
    var = instance.vars.get(PHASE)
    return expr.to_int(var.value()) if var is not None else 0


def clocked(bot):
    instance = charge(bot)
    return instance is not None and (
        requested(bot) or BUTTON in instance.component.held or phase(instance) != 0
    )


def requested(bot):
    return not bot.firing and bot.attack_target is not None


def ability(instance):
    return instance.states.get(instance.graph.node(ABILITY).state)


def auto_charge(bot, fight, distance):
    """Request the existing charge when the selected opponent has a clear approach."""
    from ow174.game import combat

    if bot.body != BODY or not AUTO_CHARGE_MIN <= distance <= AUTO_CHARGE_MAX:
        return False
    instance = charge(bot)
    if instance is None or fight.junkrat.rooted(bot.entity, fight.tick):
        return False
    state = ability(instance)
    if state is not None and state.remaining() > 0:
        return False
    melee = melee_instance(bot)
    melee_state = melee_ability(melee) if melee is not None else None
    if melee_state is not None and melee_state.remaining() > 0:
        return False
    x, y, z = bot.position
    direction = combat.aim(round(bot.yaw), 0)
    if combat.ray_world(fight._collision(), (x, y + 1.0, z), direction, distance) is not None:
        return False
    bot.firing = False
    return True


def driving(instance):
    state = instance.states.get(instance.graph.node(DRIVE).state)
    return phase(instance) == 1 and state is not None and state.active


def move(bot, fight, frame):
    """Advance one unobstructed authored drive frame, returning whether it moved."""
    from ow174.game import combat

    instance = charge(bot)
    if instance is None or not driving(instance):
        return False
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
    if blocked:
        crush(bot, fight, instance)
        instance.follow(instance.graph.node(WALL_BRANCH), "m_truePlug", None)
        bot.velocity = (0.0, 0.0, 0.0)
        return False
    bot.position, bot.velocity, bot.moved_tick = candidate, velocity, frame
    return True


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


def shield(fight, bot, target):
    return fight.barriers.attached_enemy(bot, target.entity)


def damage(bot, fight, state, target, *, blocked=False):
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    loadout = state.evaluate(state.node.field("m_modifyHealth.m_loadout"), watch=False)
    bubble = shield(fight, bot, target)
    if bubble is not None:
        fight.barriers.damage(bubble, amount, bot)
        return 0.0
    if blocked:
        return 0.0
    before = fight.vitals_of(target).total
    fight._damage(bot, target, amount, False, getattr(loadout, "guid", None), fight.tick, world_tick=fight.tick)
    return before - fight.vitals_of(target).total


def body_impact(bot, fight, instance):
    """Resolve the authored close-body impact, pairing control with admitted damage."""
    from ow174.game import combat, immunity

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
                and combat.capsule_visible(fight._collision(), centre, a, b, target.radius)):
            targets.append(target)
    if not targets:
        return
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    instance.write(slot, tuple(expr.Entity(target.entity) for target in targets))
    instance.component.run_now()
    state = instance.states.get(instance.graph.node(IMPACT_HEALTH).state)
    if phase(instance) != 2 or state is None or not state.active:
        return
    node = instance.graph.node(IMPACT_CONTROL)
    control = node.field("m_69F4B52C.m_6525E98B")
    duration = 0.0
    if control["m_graph"] == "0x058000000000224E":
        duration = next(
            expr.to_float(instance.evaluate(entry["m_value"]))
            for entry in control["m_1EB5A024"] if entry["m_0D09D2D9"] == "0x0D800000000000D7"
        )
    for target in targets:
        if (damage(bot, fight, state, target) and duration > 0.0 and target.construct is None
                and not immunity.control_immune(target.owner, target.entity) and not fight.vitals_of(target).dead):
            deadline = fight.tick + math.ceil(duration / combat.TICK)
            fight.stuns[target.entity] = max(deadline, fight.stuns.get(target.entity, 0))
            fight._interrupt(target)
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
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius)):
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
                apply_impulse(target, health, bot.position)


def apply_impulse(target, state, origin):
    """Reconstruct a radial player impulse from the authored health-state magnitudes."""
    from ow174.game import immunity
    from ow174.game.mover import FELL

    mover = getattr(target.owner, "mover", None)
    if (mover is None or target.construct is not None or immunity.control_immune(target.owner, target.entity)
            or target.owner.match.combat.junkrat.rooted(target.entity, target.owner.match.tick)):
        return
    horizontal = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_E33EFEE1"), watch=False))
    vertical = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_583C208B"), watch=False))
    dx, dz = target.feet[0] - origin[0], target.feet[2] - origin[2]
    distance = math.hypot(dx, dz)
    scale = horizontal / distance if distance else 0.0
    delta = (dx * scale, vertical, dz * scale)
    mover.velocity = tuple(a + b for a, b in zip(mover.velocity, delta, strict=True))
    if vertical > 0.0:
        mover.flags_word |= FELL


def front_contact(bot, fight, instance, *, drive_step):
    """Resolve the 1408 target and attach the authored victim graph on its body."""
    from ow174.game import immunity

    app = fight.heavy_charge_pins.get(bot.entity)
    if instance is None or not driving(instance):
        release(fight, bot.entity, natural=drive_step and instance is not None and phase(instance) == 0)
        return
    tracker = instance.states.get(instance.graph.node(FRONT_SEARCH).state)
    if tracker is None or not tracker.active:
        return
    if app is not None:
        victim_id = app.component.entity
        victim = next((target for target in fight._enemies(bot) if target.entity == victim_id), None)
        attachment = app.states.get(app.graph.node(1).state)
        if (victim is None or app.stopped or attachment is None or not attachment.active
                or immunity.control_immune(victim.owner, victim.entity)
                or fight.junkrat.rooted(victim.entity, fight.tick) or shield(fight, bot, victim) is not None):
            stop(bot, fight)
        else:
            transport(bot, victim.owner, app)
        return

    candidates = (
        target for _, target in search(bot, fight, tracker, front=True)
        if target.construct is None and hasattr(target.owner, "mover") and not fight.is_stunned(target.owner)
        and not immunity.control_immune(target.owner, target.entity)
        and not fight.junkrat.rooted(target.entity, fight.tick) and shield(fight, bot, target) is None
    )
    target = next(candidates, None)
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    tracked = (expr.Entity(target.entity),) if target is not None else ()
    previous = instance.vars.get(slot[1])
    newly_tracked = bool(tracked) and (previous is None or previous.value() != tracked)
    instance.write(slot, tracked)
    if target is None:
        return
    if newly_tracked:
        node = instance.graph.node(FRONT_IMPULSE)
        state = instance.states.get(node.state)
        if state is not None and state.active and state.evaluate(node.field("m_FA3ECC43")) == tracked:
            # Give the contact impulse one world step before capsule transport owns the pose.
            if apply_charge_impulse(bot, target, state):
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
    player.mover.position[:] = combat._along(bot.position, direction, offset)
    player.mover.body = None
    player.mover.velocity = (0.0, 0.0, 0.0)
    angle = app.graph.node(5).field("m_A53161C8.m_expression.m_D99EF254")[0]
    player.mover.yaw = (round(bot.yaw + angle * 65536 / 360) + 32768) % 65536 - 32768


class _ImpulsePose(expr.Context):
    """Supply the caster pose for the authored charge impulse origins."""

    def __init__(self, bot):
        from ow174.game.combat import aim

        super().__init__()
        self.bot = bot
        self.forward = expr.Vec3(*aim(round(bot.yaw), 0))

    def owner(self):
        return expr.Entity(self.bot.entity)

    def position(self, entity):
        return expr.Vec3(*self.bot.position) if entity == self.bot.entity else super().position(entity)

    def native(self, cfg):
        return self.forward if cfg.get("$") == "STU_021D01CD" else super().native(cfg)


def apply_charge_impulse(bot, target, state):
    origin = expr.evaluate(state.node.field("m_modifyHealth.m_C0139FA6"), _ImpulsePose(bot))
    if isinstance(origin, expr.Vec3):
        apply_impulse(target, state, (origin.x, origin.y, origin.z))
        return True
    return False


def release(fight, source, *, natural=False):
    app = fight.heavy_charge_pins.pop(source, None)
    if app is None:
        return
    fight.pins.pop(source, None)
    victim_id = app.component.entity
    if app.component.instances.get(app.id) is app:
        app.component.destroy(app)

    bot = next((candidate for candidate in fight.match.bots if candidate.entity == source), None)
    instance = charge(bot) if bot is not None else None
    if instance is None:
        return
    node = instance.graph.node(RELEASE_HEALTH)
    if expr.to_entity(instance.evaluate(node.field("m_FA3ECC43"))) != victim_id:
        return
    state = instance.node_state(node)
    # The sandbox consumes the authored release on its unobstructed drive-to-idle edge.
    if natural:
        target = next((target for target in fight._enemies(bot) if target.entity == victim_id), None)
        if target is not None and shield(fight, bot, target) is None:
            if not state.active:
                state.plug_enter("m_beginPlug")
                instance.component.run_now()
            if state.active:
                apply_charge_impulse(bot, target, state)
    if state.active:
        state.request_finish()
    else:
        instance.follow(node, "m_onEndPlug", None)
    instance.component.run_now()


def clear_contacts(bot, fight, instance):
    fight.heavy_charge_contacts.pop(bot.entity, None)
    if instance is not None:
        for search_node in (SIDE_SEARCH, FRONT_SEARCH):
            node = instance.graph.node(search_node)
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
        bot.attack_target = None
        bot.firing = bool(bot.spawner.get("auto_engage"))
    if BUTTON in component.held:
        component.edges([(BUTTON, False)])
    state = ability(instance)
    if state is not None:
        state.presses.clear()
    if phase(instance):
        bot.velocity = (0.0, 0.0, 0.0)
        instance.write((expr.INSTANCE, PHASE), 0)

MELEE_GRAPH = 0x140E
TROOPER_BODY = 0x0400000000001D40
TROOPER_MELEE = 0x1422
MELEE_BUTTON = 31


def melee_instance(bot):
    graph = {BODY: MELEE_GRAPH, TROOPER_BODY: TROOPER_MELEE}.get(bot.body)
    if graph is None or bot.dead or bot.body_script is None:
        return None
    return next((instance for instance in bot.body_script.component.instances.values()
                 if instance.graph.index == graph), None)


def melee_ability(instance):
    position = 8 if instance.graph.index == TROOPER_MELEE else 9
    return instance.states.get(instance.graph.node(position).state)


def melee_targets(bot, fight, instance, node):
    from ow174.game import combat

    forward = combat.aim(round(bot.yaw), 0)
    offset = instance.evaluate(node.field("m_05BE62DD"))
    radius = expr.to_float(instance.evaluate(node.field("m_radius")))
    x, y, z = bot.position
    origin = (x, y + offset.y, z)
    centre = combat._along(origin, forward, offset.z)
    selected = []
    for target in fight._enemies(bot):
        a, b = target.segment()
        if (combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius)):
            selected.append(target)
    return selected


def auto_melee(bot, fight, opponent):
    """Prefer a ready melee against the engaged player and suspend fire during its swing."""
    instance = melee_instance(bot)
    if instance is None:
        return False
    ability = melee_ability(instance)
    if ability is None or not ability.active:
        return False
    if ability.flags & (ability.RUNNING | ability.PUSHED):
        return True
    if ability.remaining() > 0:
        return False
    search = instance.graph.node(5 if instance.graph.index == TROOPER_MELEE else 6)
    if any(target.owner is opponent for target in melee_targets(bot, fight, instance, search)):
        fight.npc_melee_pending.add(bot.entity)
        return True
    return False


def trooper_track(world, tracker):
    if (world.match is None or tracker.instance.graph.index != TROOPER_MELEE
            or tracker.node.pos != 5):
        return NotImplemented
    fight = world.match.combat
    bot = next((bot for bot in world.match.bots
                if bot.entity == tracker.instance.component.entity and bot.body == TROOPER_BODY), None)
    if bot is None or bot.dead or fight.is_stunned(bot):
        return ()
    return tuple(expr.Entity(target.entity) for target in melee_targets(bot, fight, tracker.instance, tracker.node))


def melee(bot, fight):
    """Consume the authored health state once after each supported NPC strike."""
    from ow174.game import combat

    instance = melee_instance(bot)
    if instance is None:
        return
    trooper = instance.graph.index == TROOPER_MELEE
    search_pos, damage_pos = (5, 4) if trooper else (6, 5)
    tracker = instance.states.get(instance.graph.node(search_pos).state)
    state = instance.states.get(instance.graph.node(damage_pos).state)
    if tracker is None or not tracker.active or state is None or not state.active:
        return
    stamp = instance.id, state.counter
    if fight.npc_melee_seen.get(bot.entity) == stamp:
        return
    fight.npc_melee_seen[bot.entity] = stamp
    if trooper:
        bodies = {target.id for target in tracker.targets}
        selected = [target for target in fight._enemies(bot) if target.entity in bodies]
    else:
        selected = melee_targets(bot, fight, instance, tracker.node)
        instance.write(expr.lvalue(tracker.node.field("m_8A2905A0")),
                       tuple(expr.Entity(target.entity) for target in selected))
    incoming = combat.aim(round(bot.yaw), 0)
    for target in selected:
        blocked = fight._genji_deflects(target, incoming)
        if damage(bot, fight, state, target, blocked=blocked) and not fight.vitals_of(target).dead:
            apply_impulse(target, state, bot.position)


def cancel_melee(bot):
    instance = melee_instance(bot)
    if instance is None:
        return
    ability = melee_ability(instance)
    if ability is not None:
        ability.presses.clear()
        ability.stop()
    instance.component.edges([(MELEE_BUTTON, False)])
