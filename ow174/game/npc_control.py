"""Manual NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. This is manual targeting,
not restored native autonomous acquisition, navigation or enemy special behavior.
"""

import math

from ow174.game import assassin, bots, combat, npc_melee, sombra_hack

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
    wall = combat.ray_world(match.collision_world, origin, direction, distance)
    if wall is not None and wall < distance:
        return None
    if (not isinstance(target.owner, bots.Bot) or not manual(target.owner)
            or (not match.game_map.free_for_all and target.team == player.team)):
        return None
    return target.owner


def start(match, player, *, melee=False, charge=False):
    if melee and charge:
        raise ValueError('Select one NPC attack')
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    primary = button(bot)  # validate before changing policy
    if melee and bot.body not in npc_melee.GRAPHS:
        raise ValueError('NPC melee input is unavailable')
    if charge and bot.body != npc_melee.HEAVY_BODY:
        raise ValueError('NPC charge input is unavailable')
    special = npc_melee.CHARGE_BUTTON if charge else npc_melee.BUTTON if melee else None
    component = bot.body_script.component
    if bot.attack_special != special:
        npc_melee.release_charge(match.combat, bot, cancel=True)
        component.edges([(bot.attack_special or primary, False)])
        bot.attack_input = False
    assassin.bind(component.world, component, None)
    bot.attack_special = special
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        npc_melee.release_charge(match.combat, bot, cancel=True)
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            values = set(component.held)
            if bot.attack_special is not None:
                values.add(bot.attack_special)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            component.edges([(value, False) for value in sorted(values)])
            component.shots.clear()
        bot.attack_special = None
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [r for r in match.combat.rockets if r.shooter is not bot]
        count += 1
    return count


def update_input(fight, bot):
    """Refresh manual aim and release input on death, occlusion, range or target loss."""
    component = bot.body_script.component
    pin = bot.hero == assassin.HERO and getattr(component.world, 'controller_components', None) is not None
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    enabled = (not bot.dead and not fight.is_stunned(bot) and not bot.controlled and bot.firing
               and target is not None and target.spawned and target.has_body
               and not target.gone and target.client is not None and not fight.is_dead(target)
               and (fight.match.game_map.free_for_all or target.team != bot.team))
    if pin or bot.attack_special:
        enabled = enabled and not fight.punches.controlled(bot.entity)
    if pin or bot.attack_special == npc_melee.CHARGE_BUTTON:
        enabled = enabled and not sombra_hack.locked(bot, fight.tick)
    if enabled:
        height = combat.eye_height(bot.body) if pin or bot.attack_special else combat.EYE
        origin = (bot.position[0], bot.position[1] + height, bot.position[2])
        point = fight._eye(target)
        dx, dy, dz = (b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        enabled = 0 < distance <= (assassin.PIN_RANGE if pin else AIM_RANGE)
        if pin:
            enabled = enabled and not fight.protected(target.body)
        if enabled:
            direction = tuple(v / distance for v in (dx, dy, dz))
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
            enabled = wall is None or wall >= distance
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    value = bot.attack_special or button(bot)
    if pin:
        assassin.bind(component.world, component, target.body if enabled else None)
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    return bool(enabled)
