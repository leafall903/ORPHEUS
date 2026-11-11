"""NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. The opt-in autonomous
policy is stationary and uses the same authored weapon input as manual control.
"""

import math

from ow174.game import assassin, bots, combat

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def autonomous(bot):
    return manual(bot) and bot.spawner.get('autonomous_targeting') is True


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
    if (wall is not None and wall < distance) or fight.walls.cast(origin, direction, distance) is not None:
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
    bot.spawner.pop('autonomous_targeting', None)
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        bot.spawner.pop('autonomous_targeting', None)
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            values = set(component.held)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            component.edges([(value, False) for value in sorted(values)])
            # Start authored exit/recovery at the actual stop clock, before an
            # idle NPC ceases advancing. Reengagement must not restart that wait.
            component.run_now()
            component.shots.clear()
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [r for r in match.combat.rockets if r.shooter is not bot]
        count += 1
    return count


def _aim_sample(fight, bot, target):
    if (target is None or not target.spawned or not target.has_body or target.gone
            or target.client is None or fight.is_dead(target)
            or (not fight.match.game_map.free_for_all and target.team == bot.team)):
        return None
    if bot.hero == assassin.HERO and fight.zarya_bubbles.for_target(target) is not None:
        return None
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    delta = tuple(b - a for a, b in zip(origin, fight._eye(target), strict=True))
    distance = math.sqrt(sum(axis * axis for axis in delta))
    reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else AIM_RANGE
    if not 0 < distance <= reach:
        return None
    direction = tuple(axis / distance for axis in delta)
    wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
    return distance, delta, (wall is None or wall >= distance) and (
        fight.walls.cast(origin, direction, distance) is None)


def acquire(fight, bot):
    """Keep a visible opponent, or acquire the nearest after releasing old input."""
    candidates = []
    if not fight.match.ended and not fight.is_stunned(bot):
        for player in fight.match.players:
            sight = _aim_sample(fight, bot, player)
            if sight is None or not sight[2]:
                continue
            if player.entity == bot.attack_target:
                bot.firing = True
                return
            candidates.append((sight[0], player.entity))
    bot.attack_target = min(candidates)[1] if candidates and not bot.attack_input else None
    bot.firing = bot.attack_target is not None


def update_input(fight, bot):
    """Refresh aim and release input on death, occlusion, range or target loss."""
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    sight = None
    if not bot.dead and not fight.is_stunned(bot) and bot.firing:
        sight = _aim_sample(fight, bot, target)
    enabled = sight is not None and sight[2]
    if sight is not None:
        _, (dx, dy, dz), _ = sight
        bot.yaw = float(bots.heading(dx, dz))
        bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
        bot.moved_tick = fight.tick
    value = button(bot)
    component = bot.body_script.component
    if bot.hero == assassin.HERO and assassin.recovering(component.world, component):
        # Recovery's authored end clears target536. A fresh down edge must follow
        # that cleanup rather than letting it overwrite a newly selected target.
        enabled = False
    desired = target.body if enabled else None
    if (bot.hero == assassin.HERO and component.world.manual_target != desired):
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    return bool(enabled)
