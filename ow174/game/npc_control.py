"""Manual NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. This policy also selects
authored close-range actions for supported manual NPCs.
"""

import math

from ow174.game import assassin, bots, combat, heavy_charge, npc_melee

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def button(bot):
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
    if (not isinstance(target.owner, bots.Bot) or not manual(target.owner)
            or (not match.game_map.free_for_all and target.team == player.team)):
        return None
    walls = fight.ice_walls if target.owner.hero == assassin.HERO else None
    wall = combat.ray_world(match.collision_world, origin, direction, distance, ice_walls=walls)
    if wall is not None and wall < distance:
        return None
    return target.owner


def start(match, player):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    button(bot)  # validate before changing policy
    if player.controller_vm is not None and assassin.pin_button(bot) is not None:
        player.controller_vm.configure_manual_pin(player.controller_filter_bits)
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
        heavy_charge.stop(match, bot)
        if bot.body_script is not None:
            context = getattr(bot.body_script, 'control_context', None)
            if context is not None:
                context.set_target()
            component = bot.body_script.component
            npc_melee.stop(component)
            values = set(component.held)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            pin = assassin.pin_button(bot)
            if pin is not None:
                values.add(pin)
            component.edges([(value, False) for value in sorted(values)])
            component.shots.clear()
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [r for r in match.combat.rockets if r.shooter is not bot]
        count += 1
    return count


def update_input(fight, bot):
    """Refresh manual aim and release input on death, occlusion, range or target loss."""
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    enabled = (not bot.dead and not fight.is_stunned(bot) and bot.firing
               and target is not None and target.spawned and target.has_body
               and not target.gone and target.client is not None and not fight.is_dead(target)
               and (fight.match.game_map.free_for_all or target.team != bot.team))
    engaged = enabled
    if enabled:
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        point = fight._eye(target)
        dx, dy, dz = (b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        enabled = 0 < distance <= AIM_RANGE
        if enabled:
            direction = tuple(v / distance for v in (dx, dy, dz))
            walls = fight.ice_walls if bot.hero == assassin.HERO else None
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance, ice_walls=walls)
            enabled = wall is None or wall >= distance
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    value = button(bot)
    context = getattr(bot.body_script, 'control_context', None)
    if context is not None:
        host = target.controller_vm if enabled else None
        if host is not None and (host.retired or host.component.fault is not None):
            host = None
        context.set_target(target.entity if enabled else None, host=host)
    component = bot.body_script.component
    special = heavy_charge.update(fight, bot, component, target, engaged, enabled)
    special = special or npc_melee.update(fight, bot, component, enabled)
    primary = bool(enabled and not special)
    if bot.attack_input != primary or (value in component.held) != primary:
        component.edges([(value, primary)])
    pin = assassin.pin_button(bot)
    if pin is not None:
        # Selection admits contact. An active pin uses its authored local
        # target and the body hit at contact for reconstructed input holding.
        pin_enabled = bool(enabled and distance <= assassin.PIN_RANGE
                           and (assassin.selected_controller(context) == target.entity
                                or assassin.holds_pin(bot, target)))
        if (pin in component.held) != pin_enabled:
            component.edges([(pin, pin_enabled)])
        if not pin_enabled and context is not None:
            assassin.retire_pin_effect(context.target_host, bot.entity)
    bot.attack_input = primary
    return bool(enabled and not special)
