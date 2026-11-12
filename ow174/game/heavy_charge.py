"""Host authored 1408 charge travel on the server world clock."""

import math

from ow174.game import mei_wall
from ow174.game.script import expr, runtime

BODY = 0x0400000000001EBB
GRAPH = 0x1408
PHASE = 59
GRAZE_SEARCH = 32
GRAZE_HEALTH = 35
FRONT_SEARCH = 96
PIN_ACTION = 158
VICTIM_GRAPH = 0x1407
CRUSH_HEALTH = 29
IMPACT_SEARCH = 100
IMPACT_HEALTH = 102
IMPACT_CONTROL = 189


class _TravelContext(runtime.InstanceContext):
    def __init__(self, instance, direction):
        super().__init__(instance, None)
        self.direction = expr.Vec3(*direction)

    def var(self, scope, identifier):
        if scope == expr.INSTANCE and identifier == 3192:
            return self.direction
        return super().var(scope, identifier)


def active(bot, *, include_cooldown=False):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == GRAPH:
            phase = instance.vars.get(PHASE)
            if phase is not None and expr.to_int(phase.value()):
                return instance
            ability = instance.states.get(instance.graph.node(82).state)
            if include_cooldown and ability is not None and ability.remaining() > 0:
                return instance
    return None


def travelling(instance):
    phase = instance.vars.get(PHASE)
    state = instance.states.get(instance.graph.node(71).state)
    return phase is not None and phase.value() == 1 and state is not None and state.active


def tracked_contacts(fight, state):
    """Use the authored sphere and angles to separate graze from frontal contact."""
    if state.instance.graph.index != GRAPH or state.node.pos not in (GRAZE_SEARCH, FRONT_SEARCH, IMPACT_SEARCH):
        return NotImplemented
    from ow174.game import combat

    actor = next((bot for bot in fight.match.bots
                  if bot.entity == state.instance.component.entity and active(bot) is state.instance), None)
    if actor is None or not travelling(state.instance) or fight.is_stunned(actor):
        return ()
    frontal = state.node.pos == FRONT_SEARCH
    impact = state.node.pos == IMPACT_SEARCH
    if frontal and actor.entity in fight.heavy_charge_pins:
        return (expr.Entity(fight.pins[actor.entity]),)
    offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    front = state.instance.graph.node(FRONT_SEARCH)
    front_angle = expr.to_float(state.instance.evaluate(front.field("m_A30B383E")))
    side = state.instance.graph.node(GRAZE_SEARCH)
    side_angle = expr.to_float(state.instance.evaluate(side.field("m_A30B383E")))
    front_cos = math.cos(math.radians(front_angle))
    side_cos = math.cos(math.radians(side_angle / 2))
    forward = combat.aim(round(actor.yaw), 0)
    x, y, z = actor.position
    origin = (x, y + offset.y, z)
    centre = combat._along(origin, forward, offset.z)
    collision = fight._collision()
    found = []
    for target in fight._enemies(actor):
        if impact:
            script = getattr(target.owner, "body_script", None)
            marker = script.component.entity_var(961, create=False) if script is not None else None
            if (marker is None or not expr.truthy(marker.value()) or target.entity == fight.pins.get(actor.entity)
                    or fight.damage_immune(target.owner)):
                continue
        if frontal and (getattr(target.owner, "mover", None) is None
                        or fight.damage_immune(target.owner) or target.entity in fight.pins.values()
                        or fight.zarya_bubbles.for_target(target.owner) is not None):
            continue
        a, b = target.segment()
        dx, dz = a[0] - centre[0], a[2] - centre[2]
        distance = math.hypot(dx, dz)
        alignment = dx * forward[0] + dz * forward[2]
        in_front = alignment >= distance * front_cos
        if ((impact or (alignment >= distance * side_cos and frontal == in_front))
                and combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(collision, origin, a, b, target.radius)):
            found.append((distance, target.entity))
    if frontal:
        return (expr.Entity(min(found)[1]),) if found else ()
    if impact:
        for piece in fight.walls.pieces.values():
            if not piece.impact_marked or (piece.team == actor.team and not fight.match.game_map.free_for_all):
                continue
            surface = mei_wall.closest(piece, centre)
            separation = math.dist(centre, surface)
            if (separation <= radius
                    and combat.capsule_visible(collision, origin, surface, surface, 0.0)):
                found.append((separation, piece.entity))
    return tuple(expr.Entity(entity) for _, entity in found)


def handles_request(instance, node):
    return instance.graph.index == GRAPH and node.pos in (PIN_ACTION, IMPACT_CONTROL)


def request(fight, instance, node, value):
    if node.pos == PIN_ACTION:
        bind(fight, instance, value)
        return
    if instance.vars[PHASE].value() != 2 or value.resource.guid != 0x058000000000224E:
        return
    duration = expr.to_float(dict(value.resource.overrides).get(0x0D800000000000D7))
    if duration > 0:
        fight.heavy_charge_impacts[instance.component.entity] = (value.targets, duration)


def impact(fight, state):
    instance = state.instance
    if instance.graph.index != GRAPH or state.node.pos != IMPACT_HEALTH:
        return NotImplemented
    from ow174.game import combat

    source = instance.component.entity
    actor = next((bot for bot in fight.match.bots if bot.entity == source), None)
    if actor is None or active(actor) is not instance or instance.vars[PHASE].value() != 2:
        return True
    victims = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if not isinstance(victims, tuple):
        return True
    targets, duration = fight.heavy_charge_impacts.pop(source, ((), 0.0))
    modify = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
    loadout = state.evaluate(modify.get("m_loadout"), watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    tags = combat.health_tags(state, modify)
    for victim in victims:
        piece = fight.walls.pieces.get(expr.to_entity(victim))
        if piece is not None and piece.impact_marked:
            fight.walls.damage(piece, actor, amount)
    for target in fight._enemies(actor):
        if expr.Entity(target.entity) not in victims:
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(actor, target, amount, False, loadout, fight.tick,
                      world_tick=fight.tick, hit_tags=tags)
        if not vitals.dead and vitals.total < before and target.entity in targets:
            fight._apply_stun(target, duration)
    return True


def bind(fight, instance, request):
    from ow174.game.script import graph

    source = instance.component.entity
    if (source in fight.heavy_charge_pins or not travelling(instance)
            or request.resource.guid != 0x0580000000001407):
        return
    actor = next((bot for bot in fight.match.bots if bot.entity == source and active(bot) is instance), None)
    tracker = instance.node_state(instance.graph.node(FRONT_SEARCH))
    if actor is None or tracker is None or not tracker.active:
        return
    admitted = tracked_contacts(fight, tracker)
    for target in fight._enemies(actor):
        if target.entity not in request.targets or expr.Entity(target.entity) not in admitted:
            continue
        script = target.owner.body_script
        if script is None or script.retired:
            return
        definition = graph.graph(VICTIM_GRAPH)
        if definition is None:
            return
        application = script.component.play_loaded_graph(
            definition, source=source, instigator=source, params=request.params,
            overrides=request.resource.overrides, policy=request.policy,
        )
        if application is not None:
            fight.heavy_charge_pins[source] = application
            fight.pins[source] = target.entity
            fight._interrupt(target)
            place(actor, target.owner, application)
        return


def place(bot, player, application):
    from ow174.game import combat

    mover = player.mover
    direction = combat.aim(round(bot.yaw), 0)
    offset = combat.mover_data(bot.body).stand_radius + mover.data.stand_radius
    mover.position[:] = combat._along(bot.position, direction, offset)
    mover.velocity = (0.0, 0.0, 0.0)
    mover.body = None
    angle = application.graph.node(5).field("m_A53161C8.m_expression.m_D99EF254")[0]
    mover.yaw = (round(bot.yaw + angle * 65536 / 360) + 32768) % 65536 - 32768


def after_pose(fight, player):
    for source, application in fight.heavy_charge_pins.items():
        if application.component.entity == player.body:
            bot = next((actor for actor in fight.match.bots if actor.entity == source), None)
            if bot is not None:
                place(bot, player, application)
            return


def release(fight, source, *, cancel=False):
    application = fight.heavy_charge_pins.pop(source, None)
    if application is None:
        return
    fight.pins.pop(source, None)
    component = application.component
    if component.instances.get(application.id) is application:
        component.destroy(application)
    if cancel:
        actor = next((bot for bot in fight.match.bots if bot.entity == source), None)
        if actor is not None:
            stop(fight, actor)


def stop(fight, bot):
    instance = active(bot)
    if instance is None:
        return
    release(fight, bot.entity)
    fight.heavy_charge_contacts.pop(bot.entity, None)
    instance.write((expr.INSTANCE, PHASE), 0)
    bot.velocity = (0.0, 0.0, 0.0)


def crush(fight, state):
    """Apply the loaded wall-contact health operation to its bound victim."""
    instance = state.instance
    if instance.graph.index != GRAPH or state.node.pos != CRUSH_HEALTH:
        return NotImplemented
    from ow174.game import combat

    source = instance.component.entity
    application = fight.heavy_charge_pins.get(source)
    actor = next((bot for bot in fight.match.bots if bot.entity == source), None)
    if application is None or actor is None or active(actor) is not instance:
        return True
    direct = instance.states.get(instance.graph.node(IMPACT_HEALTH).state)
    body_impact = instance.vars[PHASE].value() == 2 and direct is not None and direct.active
    direction = combat.aim(round(actor.yaw), 0)
    if not body_impact and not (travelling(instance) and wall_near(fight, actor, instance, direction)):
        return True
    victim = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if victim != expr.Entity(application.component.entity):
        return True
    target = next((item for item in fight._enemies(actor) if item.entity == victim.id), None)
    if target is None or target.owner.body_script is None or target.owner.body_script.component is not application.component:
        return True
    modify = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
    loadout = state.evaluate(modify.get("m_loadout"), watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    fight._damage(actor, target, amount, False, loadout, fight.tick, world_tick=fight.tick,
                  hit_tags=combat.health_tags(state, modify))
    return True


def wall_near(fight, bot, instance, direction):
    from ow174.game import combat

    ray = instance.graph.node(172)
    reach = expr.to_float(instance.evaluate(ray.field("m_castLength")))
    x, y, z = bot.position
    return combat.ray_world(fight._collision(), (x, y + 1, z), direction, reach) is not None


def finish_frame(fight, bot, instance):
    fight.heavy_charge_impacts.pop(bot.entity, None)
    if active(bot) is instance and instance.vars[PHASE].value() == 2:
        health = instance.node_state(instance.graph.node(IMPACT_HEALTH))
        if health is not None and health.active:
            stop(fight, bot)
            health.request_finish()
            instance.component.run_now()
    if active(bot) is not instance:
        fight.heavy_charge_contacts.pop(bot.entity, None)
        release(fight, bot.entity)
        bot.velocity = (0.0, 0.0, 0.0)
        return
    if not travelling(instance):
        return
    application = fight.heavy_charge_pins.get(bot.entity)
    if application is not None:
        player = next((player for player in fight.match.players
                       if player.body == application.component.entity), None)
        state = application.states.get(application.graph.node(1).state)
        if (player is None or player.body_script is None
                or player.body_script.component is not application.component
                or state is None or not state.active):
            release(fight, bot.entity)
            instance.write((expr.INSTANCE, PHASE), 0)
            return
        place(bot, player, application)
    tracker = instance.node_state(instance.graph.node(GRAZE_SEARCH))
    health = instance.node_state(instance.graph.node(GRAZE_HEALTH))
    if tracker is None or health is None or not tracker.active or not health.active or not tracker.targets:
        return
    from ow174.game import combat

    modify = health.node.field("m_modifyHealth")
    amount = expr.to_float(health.evaluate(modify.get("m_amount"), watch=False))
    loadout = health.evaluate(modify.get("m_loadout"), watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    tags = combat.health_tags(health, modify)
    seen = fight.heavy_charge_contacts.setdefault(bot.entity, set())
    for target in fight._enemies(bot):
        if (target.entity in seen or target.entity == fight.pins.get(bot.entity)
                or expr.Entity(target.entity) not in tracker.targets):
            continue
        seen.add(target.entity)
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(bot, target, amount, False, loadout, fight.tick,
                      world_tick=fight.tick, hit_tags=tags)
        if not vitals.dead and vitals.total < before:
            fight._melee_impulse(target, bot.position, health)


def advance(fight, bot, instance, tick):
    """Reconstruct yaw motion and feed wall hits into the loaded exit branch."""
    from ow174.game import combat

    if fight.is_stunned(bot, tick=tick):
        stop(fight, bot)
        return
    node = instance.graph.node(71)
    if not travelling(instance):
        return
    direction = combat.aim(round(bot.yaw), 0)
    if wall_near(fight, bot, instance, direction):
        if bot.entity in fight.heavy_charge_pins:
            state = instance.node_state(instance.graph.node(CRUSH_HEALTH))
            state.plug_enter("m_beginPlug")
            instance.component.run_now()
            state.request_finish()
        instance.follow(instance.graph.node(173), "m_truePlug")
        bot.velocity = (0.0, 0.0, 0.0)
        return
    row = node.field("m_BC5E91CF")[0]
    velocity = expr.evaluate(row["m_9B7A63EA"], _TravelContext(instance, direction))
    bot.velocity = (velocity.x, velocity.y, velocity.z)
    bot.position = combat._along(bot.position, bot.velocity, combat.TICK)
    bot.moved_tick = tick
