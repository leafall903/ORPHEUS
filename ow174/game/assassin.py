"""Scoped manual Assassin host using its actual associated13C2/1157 resources.

Local body classification, manual engagement/contact and the health clock are
server reconstructions. This is not a native actor factory or automatic AI.
"""

import math

from ow174.game import defenses, zarya_barrier
from ow174.game.script import expr, runtime, target_search

HERO = 0x02E00000000001BB
ASSOCIATED = 0x0400000000001EBE
TARGET_GRAPH, ATTACK_GRAPH = 0x13C2, 0x1157
TARGET_MESSAGE, TARGET_PARAMETER = 0x0240000000000A73, 0x05C5
BUTTON = 36
PIN_RANGE = 3.0  # manual contact policy, not native acquisition/animation reach


def owner(world, instance=None):
    match = getattr(world, 'match', None)
    if match is None or match.ended or world.hero != HERO:
        return None
    component, peer = match.script_npc_peer(world)
    if (component is None or peer is None or component.strict or component.fault is not None
            or (instance is not None and instance.component is not component)):
        return None
    for bot in match.bots:
        script = bot.body_script
        if (bot.spawner.get('manual_spawn') is True and not bot.dead and script is not None
                and not script.retired and script.associated_definition == ASSOCIATED
                and component in (script.component, script.associated_component)):
            return bot, script
    return None


def target(match, bot):
    if not bot.firing or not bot.attack_input or match.combat.is_incapacitated(bot):
        return None
    for player in match.players:
        if (player.entity == bot.attack_target and player.has_body and player.spawned
                and not player.gone and player.client is not None and player.body_script is not None
                and not player.body_script.retired and not match.combat.is_dead(player)
                and (match.game_map.free_for_all or player.team != bot.team)):
            return player
    return None


def member(world, entity, var):
    bound = owner(world)
    if bound is None:
        return NotImplemented
    _, script = bound
    if world is not script.associated_component.world:
        return NotImplemented
    for player in world.match.players:
        if player.has_body and player.body == entity and player.body_script is not None:
            # Compatibility polling reads a value, never a foreign Var/watcher.
            slot = player.body_script.component.entity_var(var, create=False)
            return slot.value() if slot is not None else None
    return NotImplemented


def health_records(world, instance, entity):
    if owner(world, instance) is None:
        return NotImplemented
    for player in world.match.players:
        if player.has_body and player.body == entity and player.body_script is not None:
            return player.body_script.world.health_pools
    return NotImplemented


def autonomous(bot):
    return bot.spawner.get('autonomous_assassin') is True


def selects_search(instance):
    if instance.graph.index != TARGET_GRAPH:
        return False
    bound = owner(instance.component.world, instance)
    return bound is not None and autonomous(bound[0]) and instance.component is bound[1].associated_component


def visible(bot, fight, candidate):
    from ow174.game import combat

    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    point = fight._eye(candidate.owner)
    delta = tuple(b - a for a, b in zip(origin, point, strict=True))
    length = math.hypot(*delta)
    if length <= 1e-9:
        return True
    direction = tuple(axis / length for axis in delta)
    wall = combat.ray_world(fight._collision(), origin, direction, length)
    return ((wall is None or wall >= length)
            and fight.walls.cast(origin, direction, length) is None)


def search(state):
    instance = state.instance
    world = instance.component.world
    bound = owner(world, instance)
    if bound is None:
        return ()
    bot, _ = bound
    fight = world.match.combat
    if not bot.firing or fight.is_incapacitated(bot):
        return ()
    return tuple(expr.Entity(candidate.entity) for candidate in _candidates(state, fight, bot)
                 if visible(bot, fight, candidate))


def _candidates(state, fight, bot):
    from ow174.game import bots

    instance = state.instance
    radius = expr.to_float(state.evaluate(state.node.field('m_radius'), watch=False))
    config = state.node.field('m_filter')
    candidates = []
    for candidate in fight._enemies(bot):
        player = candidate.owner
        if (isinstance(player, bots.Bot) or defenses.phase(player)[0]
                or not target_search.radial_capsule(bot.position, radius, 0,
                                                     candidate.feet, candidate.radius, candidate.height)
                or not target_search.candidate_masks(config, 0x40000, component97=True,
                                                      relationship=target_search.Relationship(False, True))
                or not target_search.condition(config, instance, candidate.entity)):
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda candidate: (math.dist(bot.position, candidate.feet), candidate.entity))
    return candidates


def selected_player(match, bot, body=None):
    from ow174.game import bots

    if not bot.firing or match.combat.is_incapacitated(bot):
        return None
    if body is None:
        component = bot.body_script.associated_component
        var = component.entity_var(1650, create=False) if component is not None else None
        body = expr.to_entity(var.value()) if var is not None else 0
    for candidate in match.combat._enemies(bot):
        player = candidate.owner
        if (candidate.entity == body and not isinstance(player, bots.Bot)
                and not defenses.phase(player)[0] and visible(bot, match.combat, candidate)):
            return player
    return None


def refresh_selection(match, bot):
    player = selected_player(match, bot)
    associated = bot.body_script.associated_component
    if associated is None:
        return None
    instance = next((i for i in associated.instances.values() if i.graph.index == TARGET_GRAPH), None)
    if instance is None:
        return None
    node = instance.graph.node(10)
    config = node.field('m_condition')['m_filter']
    if player is not None and target_search.condition(config, instance, player.body):
        return player
    switch = instance.states.get(node.state)
    if switch is not None and switch.active and switch.current:
        # The authored false branch clears 1650 and restarts node 4. Changes
        # to local world visibility, team and foreign members have no VM Var.
        switch.dependency_changed()
    return None


def approach(fight, bot, tick):
    """Walk toward a selected target or route around static cover before capture."""
    from ow174.game import bots

    if (bot.dead or not bot.firing or bot.body_script is None
            or fight.is_incapacitated(bot, tick=tick)):
        if bot.combat_detour is not None:
            bot.combat_detour = None
        return 0
    player = refresh_selection(fight.match, bot)
    destination = _approach_destination(fight, bot, player)
    if destination is None:
        return 0
    dx = destination[0] - bot.position[0]
    dz = destination[2] - bot.position[2]
    bot.yaw = float(bots.heading(dx, dz))
    return bots.FORWARD


def _approach_destination(fight, bot, player):
    from ow174.game import npc_navigation

    world = fight.match.collision_world
    route = bot.combat_detour
    if player is not None:
        position = player.mover.position
        distance = math.hypot(position[0] - bot.position[0], position[2] - bot.position[2])
        if distance <= PIN_RANGE - .4:
            bot.combat_detour = None
            return None
        if world is not None and route is not None and route.target == player.body and route.path:
            return npc_navigation.waypoint(world, bot, player.body, fight._eye(player), PIN_RANGE - .2)
        bot.combat_detour = None
        return position
    associated = bot.body_script.associated_component
    if world is not None and associated is not None:
        instance = next((i for i in associated.instances.values() if i.graph.index == TARGET_GRAPH), None)
        if instance is not None and selects_search(instance):
            state = instance.states.get(instance.graph.node(4).state)
            if state is not None and state.active:
                for candidate in _candidates(state, fight, bot):
                    eye = fight._eye(candidate.owner)
                    if npc_navigation.sight(world, bot.position, eye, math.inf) is None:
                        # Movement may consider an occluded candidate. Node 4 still
                        # requires live sight before it can publish the attack target.
                        return npc_navigation.waypoint(world, bot, candidate.entity, eye, PIN_RANGE - .2)
    bot.combat_detour = None
    return None


def native(world, cfg, instance):
    bound = owner(world, instance)
    if bound is None:
        return NotImplemented
    bot, script = bound
    if (cfg.get('$') == 'STU_DB742151' and instance.graph.index == ATTACK_GRAPH
            and instance.component is script.component
            and instance.evaluate(cfg.get('m_entity')) == expr.Entity(script.component.entity)):
        # Declared associated namespace, not a synthetic data-only target Map.
        # The native ownership producer remains unproven; this binding is local.
        return expr.Entity(script.associated_component.entity)
    if (cfg.get('$') != 'STU_AC829876' or instance.graph.index != TARGET_GRAPH
            or instance.component is not script.associated_component):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    player = selected_player(world.match, bot, entity) if autonomous(bot) else target(world.match, bot)
    if player is None or entity != player.body:
        return False
    config = cfg.get('m_filter')
    if config is None:
        return True
    if (type(config) is not dict or config.get('$') != 'STUConfigVarFilter'
            or any(config.get(field) is not None for field in (
                'm_93FA1642', 'm_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3',
                'm_C1903771', 'm_F61726FE', 'm_B40362FC'))):
        instance.component.unsupported(instance, 'manual Assassin filter services', cfg['$'])
    # Declared local human-body cohort. No native component25 producer or world
    # rule stack is inferred; this manual host has neither auxiliary service.
    relationship = target_search.Relationship(
        False, world.match.game_map.free_for_all or bot.team != player.team)
    return (target_search.candidate_masks(config, 0x40000, component97=True, relationship=relationship)
            and target_search.condition(config, instance, entity))


def reset_attack(script, enabled):
    # A fresh engagement interrupts the previous target's release and wait.
    for instance in list(script.component.instances.values()):
        if instance.graph.index == ATTACK_GRAPH:
            for position in ((23, 12, 31, 37) if enabled else (23, 12)):
                node = instance.graph.node(position)
                state = instance.states.get(node.state) if node is not None else None
                if state is not None:
                    state.end(False)
    if BUTTON in script.component.held:
        script.component.edges([(BUTTON, False)])


def recovering(bot):
    return any(instance.graph.index == ATTACK_GRAPH
               and (state := instance.states.get(instance.graph.node(37).state)) is not None
               and state.active for instance in bot.body_script.component.instances.values())


def melee_release(match, bot):
    if autonomous(bot):
        reset_attack(bot.body_script, False)
        bot.body_script.component.run_now()
        bot.attack_input = False
        match.combat.clear_pins(bot.entity)
    else:
        cancel(match, bot)


def bind(match, bot, enabled):
    script = bot.body_script
    if script is None or owner(script.component.world) is None:
        return False
    player = target(match, bot) if enabled else None
    desired = expr.Entity(player.body if player is not None else 0)
    associated = script.associated_component
    current = associated.entity_var(14365, create=False)
    if not enabled:
        match.combat.clear_pins(bot.entity)
    if autonomous(bot):
        return False
    if expr.to_entity(current.value() if current is not None else None) == desired.id:
        return False
    reset_attack(script, enabled)
    delivered = runtime.send_message(associated, TARGET_MESSAGE, {TARGET_PARAMETER: desired},
                                     player.entity if player is not None else bot.entity)
    if not delivered:
        raise ValueError('Assassin associated target-message entry is unavailable')
    associated.run_now()
    return True


def victim(world, state):
    bound = owner(world, state.instance)
    if bound is None or state.instance.graph.index != ATTACK_GRAPH:
        return None
    bot, script = bound
    if state.instance.component is not script.component:
        return None
    player = target(world.match, bot)
    entity = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if player is None or player.body != entity:
        return None
    from ow174.game import combat

    if defenses.phase(player)[0]:
        return None
    fight = world.match.combat
    target_body = next((candidate for candidate in fight._enemies(bot) if candidate.entity == entity), None)
    if target_body is None:
        return None
    origin = (bot.position[0], bot.position[1]+combat.EYE, bot.position[2])
    if math.dist(origin, fight._eye(player)) > PIN_RANGE:
        return None
    a, b = target_body.segment()
    if not combat.capsule_visible(world.match.collision_world, origin, a, b, target_body.radius):
        return None
    return bot, target_body


def modify_health(world, state, elapsed_ms):
    if world.hero != HERO or state.instance.graph.index != ATTACK_GRAPH or state.node.pos not in (15, 25):
        return NotImplemented
    if state.instance.component.strict:
        return NotImplemented
    actors = victim(world, state)
    if actors is None:
        end_health(world, state, force=True)
        return True
    bot, target_body = actors
    fight = world.match.combat
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
    pinned = fight.pins.get(bot.entity) == target_body.entity
    if (mode == 1 and not pinned) or (mode != 1 and elapsed_ms):
        return True
    # Explicit local clock reconstruction: mode1 is per second; the default
    # applies once on entry. Amounts and scope exits remain actual1157 data.
    damage = amount*elapsed_ms/1000.0 if mode == 1 else amount
    bubble = zarya_barrier.attached_to(fight, target_body.entity)
    if not pinned and bubble is None and not defenses.fortified(target_body.owner):
        fight.pins[bot.entity] = target_body.entity
        fight._interrupt(target_body)
    if math.isfinite(damage) and damage > 0:
        if bubble is not None:
            bubble.damage(expr.f32(damage), bot.entity)
        else:
            fight._damage(bot, target_body, expr.f32(damage), False, None, fight.tick)
    return True


def end_health(world, state, *, force=False):
    if (world.hero != HERO or world.match is None or state.instance.graph.index != ATTACK_GRAPH
            or state.instance.component.strict):
        return
    node = state.instance.graph.node(23)
    pin = state.instance.states.get(node.state) if node is not None else None
    if force or state.instance.component.fault is not None or pin is None or not pin.active or pin.ending:
        world.match.combat.clear_pins(state.instance.component.entity)


def refresh_control(world, component):
    if world.hero != HERO or world.match is None or component.entity not in world.match.combat.pins:
        return
    for instance in component.instances.values():
        if instance.graph.index == ATTACK_GRAPH:
            node = instance.graph.node(23)
            pin = instance.states.get(node.state) if node is not None else None
            if pin is not None and pin.active:
                return
    world.match.combat.clear_pins(component.entity)


def cancel(match, bot):
    script = bot.body_script
    if script is None or owner(script.component.world) is None:
        return
    if bot.combat_detour is not None:
        bot.combat_detour = None
    bot.firing = bot.attack_input = False
    bot.attack_target = None
    match.combat.clear_pins(bot.entity)
    bind(match, bot, False)
