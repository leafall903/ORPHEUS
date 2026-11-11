"""Manual NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. This is manual targeting,
not restored native autonomous acquisition, navigation or enemy special behavior.
"""

import math

from ow174.game import assassin, bots, combat, enforcer_movement, heavy_charge, npc_melee, sniper_reposition

TROOPER_BODY = 0x0400000000001D40
TROOPER_RELOAD = 14

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim


def manual(bot):
    # Scene-owned transports retain spawned-body cleanup without sandbox weapon input.
    return (bot.spawner.get('manual_spawn') is True and not bot.spawner.get('scripted_transport')
            and not bot.spawner.get('owned_pet'))


def button(bot, *, mode=None):
    script = bot.body_script
    if script is None:
        raise ValueError('NPC body script is unavailable')
    mode = bot.attack_mode if mode is None else mode
    if mode == "charge":
        if bot.body != heavy_charge.BODY:
            raise ValueError('NPC charge input is unavailable')
        return heavy_charge.BUTTON
    if mode == "melee":
        if bot.body not in npc_melee.BODIES:
            raise ValueError('NPC melee input is unavailable')
        return npc_melee.BUTTON
    if mode == "alternate" and bot.body != sniper_reposition.BODY:
        raise ValueError('NPC alternate weapon input is unavailable')
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


SNIPER_ACTIVE_SLOT = 31
SNIPER_WEAPONS = {"primary": (1, 15), "alternate": (2, 16)}


def start(match, player, *, melee=False, charge=False, alternate=False):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    if sum((melee, charge, alternate)) > 1:
        raise ValueError('select one NPC attack')
    mode = "charge" if charge else "melee" if melee else "primary"
    if alternate:
        mode = "alternate"
    new_button = button(bot, mode=mode)
    component = bot.body_script.component
    selector = None
    if bot.body == sniper_reposition.BODY:
        slot, switch_button = SNIPER_WEAPONS[mode]
        active_slot = component.entity_var(SNIPER_ACTIVE_SLOT, create=False)
        if active_slot is None:
            raise ValueError('NPC weapon selector is unavailable')
        if active_slot.value() != slot:
            selector = switch_button
    if bot.attack_input:
        previous_button = button(bot)
        if previous_button != new_button or selector is not None:
            component.edges([(previous_button, False)])
            bot.attack_input = False
    if bot.attack_mode == "charge" and mode != "charge":
        heavy_charge.stop(bot, match.combat)
    if selector is not None:
        component.edges([(selector, True), (selector, False)])
        component.run_now()
    bot.attack_mode = mode
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def reload(match, player):
    bot = None if match.ended or match.combat.is_dead(player) else selected(match, player)
    if (bot is None or bot.body != TROOPER_BODY or bot.body_script is None
            or match.combat.is_stunned(bot)):
        raise ValueError('No eligible manual Trooper reload under the crosshair')
    bot.body_script.component.edges([(TROOPER_RELOAD, True), (TROOPER_RELOAD, False)])
    return bot.entity


def reposition(match, player):
    bot = selected(match, player)
    if bot is None or not sniper_reposition.request(match, bot, player):
        raise ValueError('No eligible manual Sniper reposition under the crosshair')
    return bot.entity


def enforcer_walk(match, player, *, enabled=True):
    bot = None if match.ended else selected(match, player)
    if bot is None or not enforcer_movement.set_movement(bot, enabled):
        raise ValueError('No eligible manual Enforcer movement under the crosshair')
    return bot.entity


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        sniper_reposition.cancel(match.combat, bot)
        enforcer_movement.set_movement(bot, False)
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            values = set(component.held)
            primary = (button(bot) if bot.attack_mode != "primary"
                       else component.world.ai_weapon_button(0))
            heavy_charge.stop(bot, match.combat)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
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
               and bot.entity not in fight.sniper_repositions
               and target is not None and target.spawned and target.has_body
               and not target.gone and target.client is not None and not fight.is_dead(target)
               and (fight.match.game_map.free_for_all or target.team != bot.team))
    active_charge = heavy_charge.charge(bot) if bot.attack_mode == "charge" else None
    if enabled and not (active_charge is not None and heavy_charge.travelling(active_charge)):
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        point = fight._eye(target)
        dx, dy, dz = (b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else AIM_RANGE
        enabled = 0 < distance <= reach
        if enabled:
            direction = tuple(v / distance for v in (dx, dy, dz))
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
            enabled = wall is None or wall >= distance
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    value = button(bot)
    component = bot.body_script.component
    if bot.hero == assassin.HERO:
        enabled = enabled and not assassin.recovering(component)
        assassin.bind(component.world, component, target.body if enabled else None)
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    return bool(enabled)
