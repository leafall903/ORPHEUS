"""NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. Autonomous weapon input
uses an opt-in local selector and the actual associated target-message graph.
"""

import math

from ow174.game import bots, combat, heavy_charge, npc_navigation
from ow174.game.script import expr, message_receive

TROOPER_BODY = 0x0400000000001D40
ENFORCER_BODY = 0x0400000000002279
TROOPER_RELOAD = 14
AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim
TARGET_GRAPHS = {
    TROOPER_BODY: 0x11E3,
    ENFORCER_BODY: 0x13E9,
    0x0400000000001E7D: 0x1138,
    heavy_charge.BODY: 0x1251,
}


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def button(bot):
    if bot.charge_input:
        return heavy_charge.BUTTON
    if bot.melee_input:
        from ow174.game import npc_melee

        return npc_melee.BUTTON
    script = bot.body_script
    if script is None:
        raise ValueError('NPC body script is unavailable')
    result = script.component.world.ai_weapon_button(0)
    if type(result) is not int:
        raise ValueError('NPC primary weapon input is unavailable')
    return result


def selected(match, player):
    """Return a living manual enemy only if it is the first visible body on the aim ray."""
    fight = match.combat
    origin = fight._eye(player)
    direction = combat.aim(player.mover.yaw, player.mover.pitch)
    best = None
    for target in fight._targets():
        if target.owner is player or fight.vitals_of(target).dead:
            continue
        a, b = target.segment()
        distance = combat.ray_capsule(origin, direction, a, b, target.radius)
        if distance is not None and distance <= AIM_RANGE and (best is None or distance < best[0]):
            best = (distance, target)
    if best is None:
        return None
    distance, target = best
    wall = combat.ray_world(match.collision_world, origin, direction, distance)
    if ((wall is not None and wall < distance)
            or fight.walls.cast(origin, direction, distance) is not None):
        return None
    if (not isinstance(target.owner, bots.Bot) or not manual(target.owner)
            or (not match.game_map.free_for_all and target.team == player.team)):
        return None
    return target.owner


def start(match, player, *, alternate=False, melee=False, charge=False):
    from ow174.game import npc_melee, sniper_reposition

    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    primary = button(bot)
    if charge and (alternate or melee or bot.body != heavy_charge.BODY):
        raise ValueError("NPC charge input is unavailable")
    if melee and (alternate or bot.body not in npc_melee.BODIES):
        raise ValueError('NPC melee input is unavailable')
    if alternate and bot.body != sniper_reposition.BODY:
        raise ValueError('NPC alternate weapon input is unavailable')
    if bot.body == sniper_reposition.BODY:
        component = bot.body_script.component
        selector = component.entity_var(31, create=False)
        if selector is None:
            raise ValueError('Sniper weapon selector is unavailable')
        slot, switch = (2, 16) if alternate else (1, 15)
        if selector.value() != slot:
            if bot.attack_input:
                component.edges([(primary, False)])
                bot.attack_input = False
            component.edges([(switch, True), (switch, False)])
            component.run_now()
            if selector.value() != slot:
                raise ValueError('Sniper weapon switch was rejected')
    if bot.charge_input and not charge:
        heavy_charge.stop(bot, match.combat)
    if bot.attack_input and (bot.melee_input != melee or bot.charge_input != charge):
        bot.body_script.component.edges([(primary, False)])
        bot.attack_input = False
    bot.melee_input = melee
    bot.charge_input = charge
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def reload(match, player):
    fight = match.combat
    bot = None if match.ended or fight.is_dead(player) else selected(match, player)
    if (bot is None or bot.body != TROOPER_BODY or bot.body_script is None
            or fight.is_incapacitated(bot)):
        raise ValueError('No eligible Trooper reload under the crosshair')
    bot.body_script.component.edges([(TROOPER_RELOAD, True), (TROOPER_RELOAD, False)])
    return bot.entity


def _reload_empty_trooper(bot):
    component = bot.body_script.component
    weapon = next((i for i in component.instances.values() if i.graph.index == 0x102E), None)
    if weapon is None:
        return
    ammo, reloading = weapon.vars.get(53), weapon.vars.get(51)
    if ammo is not None and ammo.value() == 0 and reloading is not None and not reloading.value():
        component.edges([(TROOPER_RELOAD, True), (TROOPER_RELOAD, False)])


def reposition(match, player):
    from ow174.game import sniper_reposition

    bot = selected(match, player)
    if bot is None or not sniper_reposition.request(match, bot, player):
        raise ValueError('No eligible Sniper reposition under the crosshair')
    return bot.entity


def _enforcer_walk_state(bot, *, create=False):
    script = bot.body_script
    if bot.body != ENFORCER_BODY or script is None or script.associated_component is None:
        return None
    instance = next((i for i in script.associated_component.instances.values()
                     if i.graph.index == 0x13EA), None)
    if instance is None:
        return None
    node = instance.graph.node(62)
    return instance.node_state(node) if create else instance.states.get(node.state)


def _begin_enforcer_walk(state):
    from ow174.game.script import runtime

    if not state.active:
        state.request(runtime.BEGIN)
        state.instance.component.run_now()
    return state.active


def enforcer_walk(match, player):
    """Admit the authored walk branch for the selected manual Enforcer."""
    fight = match.combat
    bot = None if match.ended or fight.is_dead(player) else selected(match, player)
    state = _enforcer_walk_state(bot, create=True) if bot is not None else None
    if state is None or fight.is_incapacitated(bot):
        raise ValueError('No eligible Enforcer walk under the crosshair')
    if not _begin_enforcer_walk(state):
        raise ValueError('Enforcer walk was rejected')
    bot.attack_target = player.entity
    return bot.entity


def stop_enforcer_walk(bot):
    state = _enforcer_walk_state(bot)
    if state is not None and state.active:
        state.end(False)
        state.instance.component.run_now()


def cancel_combat_movement(bot):
    if bot.combat_detour is not None:
        bot.combat_detour = None
    if bot.body == ENFORCER_BODY:
        stop_enforcer_walk(bot)


def combat_forward(fight, bot):
    if bot.body == ENFORCER_BODY:
        return enforcer_forward(fight, bot)
    if (bot.body not in TARGET_GRAPHS or bot.dead or not manual(bot)
            or bot.spawner.get('autonomous_weapon') is not True):
        return 0
    if fight.is_incapacitated(bot):
        cancel_combat_movement(bot)
        return 0
    point = _weapon_detour(fight, bot)
    if point is None:
        return 0
    bot.yaw = float(bots.heading(point[0] - bot.position[0], point[2] - bot.position[2]))
    return bots.FORWARD


def enforcer_forward(fight, bot):
    if bot.body != ENFORCER_BODY or bot.dead or not manual(bot):
        return 0
    if fight.is_incapacitated(bot):
        cancel_combat_movement(bot)
        return 0
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    detouring = False
    if bot.spawner.get('autonomous_weapon') is True:
        if _eligible(fight, bot, target) and _visible_aim(fight, bot, target, AIM_RANGE) is not None:
            bot.combat_detour = None
            destination = target.mover.position
        else:
            destination = _weapon_detour(fight, bot)
            detouring = destination is not None
            if detouring:
                state = _enforcer_walk_state(bot, create=True)
                if state is None or not _begin_enforcer_walk(state):
                    return 0
    else:
        destination = target.mover.position if _eligible(fight, bot, target) else None
    if destination is None:
        stop_enforcer_walk(bot)
        return 0
    state = _enforcer_walk_state(bot)
    if state is None or not state.active:
        return 0
    # The body polls its controller relation on the next VM frame. Wait for
    # the authored speed modifier before supplying the first movement input.
    if not any(instance.graph.index == 0x13EF and modifier.active and modifier.node.pos == 0
               for instance in bot.body_script.component.instances.values()
               for modifier in instance.states.values()):
        return 0
    dx = destination[0] - bot.position[0]
    dz = destination[2] - bot.position[2]
    bot.yaw = float(bots.heading(dx, dz))
    # The sandbox only supplies held forward input, with room for both bodies.
    return bots.FORWARD if detouring or math.hypot(dx, dz) > 2.0 else 0


def _weapon_detour(fight, bot):
    world = fight.match.collision_world
    script = bot.body_script
    controller = script.associated_component if script is not None else None
    if world is None or not bot.firing or controller is None or controller.strict or controller.fault:
        bot.combat_detour = None
        return None
    candidates = []
    for player in fight.match.players:
        if _eligible(fight, bot, player):
            if _visible_aim(fight, bot, player, AIM_RANGE) is not None:
                bot.combat_detour = None
                return None
            eye = fight._eye(player)
            distance = math.dist(bot.position, eye)
            if distance <= AIM_RANGE and npc_navigation.sight(world, bot.position, eye, AIM_RANGE) is None:
                candidates.append((distance, player.body, eye))
    if not candidates:
        bot.combat_detour = None
        return None
    _, target, eye = min(candidates)
    return npc_navigation.waypoint(world, bot, target, eye, AIM_RANGE)


def stop(match):
    from ow174.game import assassin, sniper_reposition

    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        sniper_reposition.cancel(bot)
        cancel_combat_movement(bot)
        heavy_charge.stop(bot, match.combat)
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        assassin.bind(match, bot, False)
        if bot.body_script is not None:
            component = bot.body_script.component
            values = set(component.held)
            if bot.melee_input or bot.charge_input:
                values.add(button(bot))
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            component.edges([(value, False) for value in sorted(values)])
            component.shots.clear()
        bot.melee_input = False
        bot.charge_input = False
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [r for r in match.combat.rockets if r.shooter is not bot]
        count += 1
    return count


def _eligible(fight, bot, target):
    return (target is not None and target.spawned and target.has_body
            and not target.gone and target.client is not None and not fight.is_dead(target)
            and (fight.match.game_map.free_for_all or target.team != bot.team))


def _visible_aim(fight, bot, player, reach):
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    dx, dy, dz = (b - a for a, b in zip(origin, fight._eye(player), strict=True))
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not 0 < distance <= reach:
        return None
    direction = dx / distance, dy / distance, dz / distance
    wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
    if (wall is not None and wall < distance) or fight.walls.cast(origin, direction, distance) is not None:
        return None
    return distance, (dx, dy, dz)


def _autonomous_target(fight, bot, *, locked=False):
    controller = bot.body_script.associated_component
    if controller is None or controller.strict or controller.fault is not None:
        return None, None
    publication = next((i for i in controller.instances.values()
                        if i.graph.index == TARGET_GRAPHS[bot.body]), None)
    if publication is None:
        return None, None
    candidates = []
    if bot.firing and not fight.is_incapacitated(bot):
        for player in fight.match.players:
            if locked and player.entity != bot.attack_target:
                continue
            if _eligible(fight, bot, player) and (sight := _visible_aim(fight, bot, player, AIM_RANGE)):
                candidates.append((sight[0], player.entity, player, sight))
    _, _, target, sight = min(candidates) if candidates else (None, None, None, None)
    desired = expr.Entity(target.body if target is not None else 0)
    if expr.to_entity(publication.find_var(0, 536).value()) != desired.id:
        packet = message_receive.Packet(0x0240000000000079, bot.entity, bot.entity, (
            (0x0A40000000000057, 0), (0x0A40000000000059, desired),
        ))
        message_receive.receive(publication, packet)
        controller.run_now()
    # A zero message clears 536. The authored nonzero-only writer leaves 1650 latched.
    if (target is not None and controller.fault is None
            and publication.find_var(0, 536).value() == desired
            and controller.entity_var(1650).value() == desired):
        return target, sight
    return None, None


def update_input(fight, bot):
    """Refresh aim and release input on death, occlusion, range or target loss."""
    from ow174.game import assassin, npc_melee, sniper_reposition

    sight = None
    autonomous_weapon = bot.spawner.get('autonomous_weapon') is True
    repositioning = sniper_reposition.busy(bot)
    moving = repositioning and sniper_reposition.moving(bot)
    if assassin.autonomous(bot):
        target = assassin.refresh_selection(fight.match, bot)
        desired = target.entity if target is not None else None
        if desired != bot.attack_target:
            assassin.reset_attack(bot.body_script, target is not None)
            bot.attack_target = desired
    elif autonomous_weapon:
        target, sight = _autonomous_target(fight, bot, locked=bot.charge_input or repositioning)
        bot.attack_target = target.entity if target is not None else None
    else:
        target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    if autonomous_weapon and bot.body == sniper_reposition.BODY:
        if repositioning and target is None:
            sniper_reposition.cancel(bot, tick=fight.tick)
            moving = False
        elif not repositioning and target is not None and fight.tick == fight.match.tick:
            sniper_reposition.request(fight.match, bot, target)
            moving = sniper_reposition.moving(bot)
    enabled = (not bot.dead and not fight.is_incapacitated(bot) and bot.firing
               and not (assassin.autonomous(bot) and assassin.recovering(bot))
               and not moving
               and _eligible(fight, bot, target))
    if autonomous_weapon and bot.body == heavy_charge.BODY:
        charge = enabled and sight is not None and heavy_charge.responds_to_barrage(bot, target, sight[0])
        if bot.charge_input != charge:
            bot.body_script.component.edges([(button(bot), False)])
            bot.attack_input = False
            if bot.charge_input:
                heavy_charge.stop(bot, fight)
            bot.charge_input = charge
    instance = heavy_charge.charge(bot) if bot.charge_input else None
    if enabled and not (instance is not None and heavy_charge.travelling(instance)):
        reach = assassin.PIN_RANGE if assassin.autonomous(bot) else AIM_RANGE
        if sight is None:
            sight = _visible_aim(fight, bot, target, reach)
        enabled = sight is not None
        if sight is not None:
            _, (dx, dy, dz) = sight
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    if autonomous_weapon and bot.body == TROOPER_BODY:
        ability = npc_melee.trooper_ability(fight, bot, target) if enabled else None
        melee = ability is not None
        if bot.melee_input != melee:
            bot.body_script.component.edges([(button(bot), False)])
            bot.attack_input = False
            bot.melee_input = melee
        if melee:
            enabled = ability.remaining() <= 0 and not bot.attack_input
        elif enabled:
            _reload_empty_trooper(bot)
    if autonomous_weapon and bot.body == ENFORCER_BODY:
        if enabled:
            state = _enforcer_walk_state(bot, create=True)
            if state is not None:
                _begin_enforcer_walk(state)
        elif fight.is_incapacitated(bot) or not (bot.combat_detour and bot.combat_detour.path):
            stop_enforcer_walk(bot)
    value = button(bot)
    component = bot.body_script.component
    previous = bot.attack_input
    if enabled and not previous and assassin.autonomous(bot):
        assassin.reset_attack(bot.body_script, True)
    bot.attack_input = bool(enabled)
    assassin.bind(fight.match, bot, bool(enabled))
    if previous != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    return bool(enabled)
