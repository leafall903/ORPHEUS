"""Manual NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. This is manual targeting,
not restored native autonomous acquisition, navigation or enemy special behavior.
Manual Assassin engagement uses1157's logical36, separately from AI slot0.
"""

import math

from ow174.game import assassin, bots, combat, heavy_charge
from ow174.game.script.bot import TROOPER, BotScript

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def button(bot):
    script = bot.body_script
    if script is None:
        raise ValueError('NPC body script is unavailable')
    if bot.hero == assassin.HERO and script.component.world.manual_control:
        # Explicit manual action from1157/node3; do not rewrite AI slot0 metadata.
        return 36
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


def start(match, player):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    button(bot)  # validate before changing policy
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def strike(match, player):
    """Request one authored melee from the selected manual NPC."""
    bot = selected(match, player)
    if bot is None or bot.hero not in (TROOPER, heavy_charge.HERO) or bot.body_script is None:
        raise ValueError('Select a living manual Trooper or Heavy Assault for melee')
    fight = match.combat
    if match.ended or fight.is_dead(player) or fight.is_stunned(bot):
        raise ValueError('NPC melee requires an eligible attacker and player')
    x, _, z = player.mover.position
    bot.yaw = float(bots.heading(x - bot.position[0], z - bot.position[2]))
    bot.body_script.component.edges([(31, True), (31, False)])
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
            values = set(component.held)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            if bot.hero == assassin.HERO and component.world.manual_control:
                values.add(36)  # cancel the separately queued manual special too
            component.edges([(value, False) for value in sorted(values)])
            if isinstance(bot.body_script, BotScript):
                bot.body_script.cancel_primary_windup()
            heavy_charge.interrupt(match.combat, bot)
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
    if enabled:
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        point = fight._eye(target)
        dx, dy, dz = (b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        enabled = 0 < distance <= AIM_RANGE
        if enabled:
            direction = tuple(v / distance for v in (dx, dy, dz))
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
            enabled = wall is None or wall >= distance
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    if bot.hero == assassin.HERO:
        enabled = enabled and distance <= assassin.PIN_RANGE
    script = bot.body_script
    component = script.component
    world = component.world
    desired = target.body if enabled else None
    if bot.hero == assassin.HERO and world.manual_target != desired:
        assassin.bind(world, component, desired)
        bot.attack_input = False
    value = button(bot)
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    if not enabled and isinstance(script, BotScript):
        script.cancel_primary_windup()
    bot.attack_input = bool(enabled)
    return bool(enabled)
