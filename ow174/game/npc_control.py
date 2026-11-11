"""Sandbox NPC attack input and optional automatic target acquisition.

Slot0 comes from each body's extracted AI definition. The sandbox policy uses
reconstructed ranges and does not model native perception or navigation.
"""

import math

from ow174.game import assassin, bots, combat, dva, mei, phase_defenses, sniper_special

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def engaged(bot):
    return (manual(bot) and bot.firing and not bot.dead
            and (bot.attack_target is not None or bot.spawner.get("autonomous_targeting", False)))


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
    wall = combat.ray_world(fight._line_collision(), origin, direction, distance)
    if wall is not None and wall < distance:
        return None
    if (not isinstance(target.owner, bots.Bot) or not manual(target.owner)
            or (not match.game_map.free_for_all and target.team == player.team)):
        return None
    return target.owner


def start(match, player):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    button(bot)  # validate before changing policy
    bot.attack_target = player.entity
    bot.firing = True
    sniper_special.engagement_changed(bot)
    return bot.entity


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            sniper_special.interrupt(bot)
            sniper_special.engagement_changed(bot)
            values = set(component.held)
            primary = component.world.ai_weapon_button(0)
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


def _attack_line(fight, bot, point):
    x, y, z = bot.position
    origin = (x, y + combat.EYE, z)
    dx, dy, dz = (b - a for a, b in zip(origin, point, strict=True))
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else AIM_RANGE
    if not 0 < distance <= reach:
        return None
    direction = tuple(v / distance for v in (dx, dy, dz))
    wall = combat.ray_world(fight._line_collision(), origin, direction, distance)
    return dx, dy, dz, distance, wall is None or wall >= distance


def target_player(fight, target):
    """Resolve the controller of a player body or an actively driven mech."""
    owner = target.owner
    if isinstance(owner, bots.Bot) and owner.dva_mover is not None:
        return dva.pilot_for_mech(fight.match, owner)
    return owner if owner in fight.match.players else None


def _acquire(fight, bot):
    closest = None
    pinned = fight.pins.get(bot.entity) if bot.hero == assassin.HERO else None
    for target in fight._enemies(bot):
        player = target_player(fight, target)
        if (player is None or player.gone
                or (bot.hero == assassin.HERO and (target.owner is not player or mei.protected(player)))
                or not fight.visible_for_acquisition(target, bot.position)):
            continue
        if target.owner is player:
            point = fight._eye(player)
        else:
            x, y, z = target.feet
            point = (x, y + combat.eye_height(target.owner.body), z)
        line = _attack_line(fight, bot, point)
        if line is None or not line[4]:
            continue
        if target.entity == pinned:
            return player, line
        order = (line[3], player.entity)
        if closest is None or order < closest[0]:
            closest = (order, player, line)
    return (None, None) if closest is None else (closest[1], closest[2])


def update_input(fight, bot):
    """Refresh sandbox aim and release input when engagement is unavailable."""
    ready = (not bot.dead and not fight.is_stunned(bot) and bot.firing
             and not (bot.hero == assassin.HERO and bot.entity in fight.hacks))
    if bot.spawner.get("autonomous_targeting"):
        target, line = _acquire(fight, bot) if ready else (None, None)
        controller = target.entity if target is not None else None
        if controller != bot.attack_target:
            if bot.attack_target is not None:
                sniper_special.interrupt(bot)
            bot.attack_target = controller
            sniper_special.engagement_changed(bot)
    else:
        target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
        valid = (ready and target is not None and target.spawned and target.has_body
                 and not target.gone and target.client is not None and not fight.is_dead(target)
                 and (fight.match.game_map.free_for_all or target.team != bot.team))
        if valid and bot.hero == assassin.HERO:
            # Protection must defer the opening contact and its damage loop together.
            valid = not (phase_defenses.active(target) or mei.protected(target))
        line = _attack_line(fight, bot, fight._eye(target)) if valid else None

    enabled = False
    if line is not None:
        dx, dy, dz, _, enabled = line
        bot.yaw = float(bots.heading(dx, dz))
        bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
        bot.moved_tick = fight.tick
    value = button(bot)
    component = bot.body_script.component
    desired = target.body if enabled else None
    if (bot.hero == assassin.HERO and component.world.manual_target != desired):
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    return bool(enabled)
