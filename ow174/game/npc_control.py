"""Sandbox NPC input arbitration for manual commands and the Assassin controller.

Primary slot0 comes from each body's extracted AI definition. Manual Assassin
pin and the Heavy/Trooper melee actions select their own authored buttons.
Manual Sniper alternate selects the mounted sidearm before applying slot0 fire.
Targeting and contact are reconstructed sandbox policies.
"""

import math

from ow174.game import assassin, bots, combat, defenses, heavy_charge, npc_context, npc_melee
from ow174.game.script import expr

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim
SNIPER = 0x02E00000000001B8
SNIPER_PRIMARY, SNIPER_ALTERNATE = 0x1122, 0x1134
REASSIGNABLE = (npc_context.ENFORCER, SNIPER)
SNIPER_ACTIVE_SLOT = 31
SNIPER_SELECT = {1: 15, 2: 16}


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def spend_sniper_round(bot, volley):
    """Debit an emitted manual Sniper round and let the weapon graph reload."""
    weapon = volley.instance
    if (bot.hero != SNIPER or weapon.graph.index not in (SNIPER_PRIMARY, SNIPER_ALTERNATE)
            or volley.node.pos != 6 or volley.node.field("m_24E351A4") is not None):
        return True
    # These volleys omit their ammo link. The static host updates the authored
    # magazine only for accepted emission, before the projectile resolves.
    magazine = weapon.vars.get(53)
    rounds = expr.to_int(magazine.value()) if magazine is not None else 0
    if rounds <= 0:
        return False
    weapon.write((expr.INSTANCE, 53), rounds - 1)
    return True


def button(bot, action=None):
    script = bot.body_script
    if script is None:
        raise ValueError('NPC body script is unavailable')
    action = getattr(bot, 'attack_action', 'primary') if action is None else action
    if action == 'pin':
        context = getattr(script.component.world, 'manual_controller_context', None)
        if bot.hero != assassin.HERO or context is None or context.retired:
            raise ValueError('Manual Assassin pin action is unavailable')
        return assassin.PIN_BUTTON
    if action == 'melee':
        graph = npc_melee.GRAPHS.get(bot.body)
        if graph is None or not any(i.graph.index == graph for i in script.component.instances.values()):
            raise ValueError('Authored NPC melee graph is unavailable')
        return 31
    if action == 'charge' and heavy_charge.instance_of(bot) is not None:
        return 35
    if action == 'alternate' and any(i.graph.index == SNIPER_ALTERNATE
                                     for i in script.component.instances.values()):
        action = 'primary'  # both authored slots use the same fire input
    if action != 'primary':
        raise ValueError('Unknown manual NPC attack action')
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
    if wall is not None and wall < distance:
        return None
    if (not isinstance(target.owner, bots.Bot) or not manual(target.owner)
            or (not match.game_map.free_for_all and target.team == player.team)):
        return None
    return target.owner


def start(match, player, *, action="primary"):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    value = button(bot, action)  # validate before changing policy
    if getattr(bot, 'attack_action', 'primary') != action:
        if bot.attack_action == 'charge':
            heavy_charge.finish(match.combat, bot, heavy_charge.instance_of(bot))
        component = bot.body_script.component
        assassin.bind(component.world, component, None)
        component.edges([(value, False) for value in tuple(component.held)])
        _stop_target_weapon(component)
        bot.attack_input = False
    elif (bot.hero in REASSIGNABLE or action in ('melee', 'charge')) and bot.attack_target not in (None, player.entity):
        _release_target(match.combat, bot, value)
    bot.attack_action = action
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        if bot.attack_action == 'charge':
            heavy_charge.finish(match.combat, bot, heavy_charge.instance_of(bot))
        bot.attack_action = 'primary'
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            values = set(component.held)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            if any(i.graph.index == SNIPER_ALTERNATE for i in component.instances.values()):
                values.update(SNIPER_SELECT.values())
            component.edges([(value, False) for value in sorted(values)])
            _stop_target_weapon(component)
            component.shots.clear()
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [r for r in match.combat.rockets if r.shooter is not bot]
        count += 1
    return count


def _stop_enforcer_windup(component):
    for instance in component.instances.values():
        if instance.graph.index != npc_context.ENFORCER_WEAPON:
            continue
        # The weapon trigger and the sibling windup/attack scopes end separately.
        instance.exit_subgraph(instance.graph.node(2), 'm_80AF45FB')
        windup, attack, volley = (instance.node_state(instance.graph.node(pos)) for pos in (11, 15, 24))
        for state in (windup, attack):
            if state is not None:
                state.end(False)
        instance.drop_begins([state for state in (windup, attack, volley) if state is not None])


def _stop_target_weapon(component):
    if component.world.hero == npc_context.ENFORCER:
        _stop_enforcer_windup(component)
    elif component.world.hero == SNIPER:
        for instance in component.instances.values():
            if instance.graph.index not in (SNIPER_PRIMARY, SNIPER_ALTERNATE):
                continue
            instance.exit_subgraph(instance.graph.node(4), 'm_80AF45FB')
            if instance.graph.index == SNIPER_PRIMARY:
                # Rifle aim is a separate stack started by the trigger's sibling switch.
                scope = instance.node_state(instance.graph.node(34))
                if scope is not None:
                    scope.end(False)
                    instance.drop_begins([scope])
    else:
        npc_melee.cancel_windup(component)


def _release_target(fight, bot, value):
    bot.attack_input = False
    component = bot.body_script.component
    component.edges([(value, False)])
    if bot.attack_action == 'charge':
        heavy_charge.finish(fight, bot, heavy_charge.instance_of(bot))
    else:
        _stop_target_weapon(component)


def retire_player_target(match, player):
    """Retire the target's attack scope even when its controller or body ID will be reused."""
    for bot in match.bots:
        if ((bot.hero not in REASSIGNABLE and bot.attack_action not in ('melee', 'charge'))
                or bot.attack_target != player.entity
                or not bot.firing or bot.body_script is None):
            continue
        if bot.attack_input or bot.attack_action == 'charge':
            _release_target(match.combat, bot, button(bot))
        else:
            _stop_target_weapon(bot.body_script.component)
        if player.gone:
            bot.attack_target = None


def _replacement_target(fight, bot):
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    best = None
    for player in fight.match.players:
        if (player.gone or player.client is None or not player.spawned or not player.has_body
                or fight.is_dead(player)
                or (not fight.match.game_map.free_for_all and player.team == bot.team)):
            continue
        point = fight._eye(player)
        distance = math.dist(origin, point)
        rank = (distance, player.entity)
        if not 0 < distance <= AIM_RANGE or (best is not None and rank >= best[0]):
            continue
        direction = tuple((end - start) / distance for start, end in zip(origin, point, strict=True))
        wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
        if (wall is None or wall >= distance) and not fight.walls.blocked(origin, point):
            best = (rank, player)
    return best[1] if best is not None else None


def update_input(fight, bot):
    """Refresh aim and release input on death, occlusion, range or target loss."""
    controller_target = None if bot.firing else assassin.controller_pin_target(bot)
    if controller_target is not None:
        target = next((p for p in fight.match.players if p.body == controller_target), None)
    else:
        target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    value = assassin.PIN_BUTTON if controller_target is not None else button(bot)
    if (bot.hero in REASSIGNABLE and bot.firing and not bot.dead and not fight.is_stunned(bot)
            and (target is None or target.gone)):
        if bot.attack_target is not None:
            _release_target(fight, bot, value)
        target = _replacement_target(fight, bot)
        bot.attack_target = target.entity if target is not None else None
    pin = controller_target is not None or bot.attack_action == 'pin'
    enabled = (not bot.dead and not fight.is_stunned(bot) and (bot.firing or controller_target is not None)
               and (bot.attack_action != "charge" or bot.entity not in fight.gravitons.pulled)
               and target is not None and target.spawned and target.has_body
               and not target.gone and target.client is not None and not fight.is_dead(target)
               and (not pin or not defenses.control_immune(target))
               and (fight.match.game_map.free_for_all or target.team != bot.team))
    if enabled and bot.attack_action == 'charge' and not bot.attack_input:
        # 1408 consumes the down edge even while its cooldown blocks the windup.
        enabled = heavy_charge.ready_for_input(bot)
    if enabled:
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        point = fight._eye(target)
        dx, dy, dz = (b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        limit = assassin.PIN_RANGE if pin else AIM_RANGE
        enabled = 0 < distance <= limit
        if enabled:
            direction = tuple(v / distance for v in (dx, dy, dz))
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
            enabled = (wall is None or wall >= distance) and (not pin or not fight.walls.blocked(origin, point))
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    component = bot.body_script.component
    switching = False
    if any(i.graph.index == SNIPER_ALTERNATE for i in component.instances.values()):
        slot = 2 if bot.attack_action == 'alternate' else 1
        active = component.entity_var(SNIPER_ACTIVE_SLOT).value()
        switching = enabled and active != slot
        # Let the mounted switch graph own the slot transition.
        switch = SNIPER_SELECT[slot]
        for selector in SNIPER_SELECT.values():
            if (selector in component.held) != (switching and selector == switch):
                component.edges([(selector, switching and selector == switch)])
    desired = target.body if enabled and pin else None
    context = getattr(component.world, 'manual_controller_context', None)
    if (bot.hero == assassin.HERO and context is not None
            and component.world.manual_target != desired):
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    # Give the melee ability one update with the old button released before rearming.
    melee_releasing = bot.attack_action == 'melee' and not bot.attack_input and value in component.held
    firing = bool(enabled and not switching and not melee_releasing)
    if bot.attack_input != firing or (value in component.held) != firing:
        component.edges([(value, firing)])
        if not firing:
            _stop_target_weapon(component)
    bot.attack_input = firing
    return firing
