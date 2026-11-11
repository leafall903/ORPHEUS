"""Manual NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. This is manual targeting,
not restored native autonomous acquisition, navigation or enemy special behavior.
"""

import math

from ow174.game import assassin, bots, combat, heavy_charge, npc_context, npc_melee, sniper_reposition

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def button(bot, action=None):
    script = bot.body_script
    if script is None:
        raise ValueError('NPC body script is unavailable')
    action = bot.attack_action if action is None else action
    if action == "charge" and heavy_charge.instance_of(bot) is not None:
        return 35
    if action == "melee" and npc_melee.instance_of(bot) is not None:
        return npc_melee.BUTTON
    if action != "primary":
        raise ValueError("Manual NPC action is unavailable for this body")
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
    if fight.walls.cast(origin, direction, distance) is not None:
        return None
    if (not isinstance(target.owner, bots.Bot) or not manual(target.owner)
            or (not match.game_map.free_for_all and target.team == player.team)):
        return None
    return target.owner


def start(match, player, *, action="primary"):
    if action not in ("primary", "charge", "melee", "reposition"):
        raise ValueError("Invalid manual NPC action")
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    if action == "reposition":
        if not sniper_reposition.request(match, bot, player):
            raise ValueError("The selected NPC cannot reposition")
        return bot.entity
    button(bot, action)  # validate before changing policy
    if action != bot.attack_action:
        component = bot.body_script.component
        component.edges([(button(bot), False)])
        if bot.attack_action == "primary":
            _cancel_pending_fire(bot)
        elif bot.attack_action == "charge":
            heavy_charge.finish(match.combat, bot, heavy_charge.instance_of(bot))
        elif bot.attack_action == "melee":
            npc_melee.cancel(bot)
        bot.attack_input = False
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
        sniper_reposition.cancel(match.combat, bot)
        if bot.body_script is not None:
            _cancel_pending_fire(bot)
            if bot.attack_action == "charge":
                bot.body_script.component.edges([(button(bot), False)])
                heavy_charge.finish(match.combat, bot, heavy_charge.instance_of(bot))
            elif bot.attack_action == "melee":
                bot.body_script.component.edges([(npc_melee.BUTTON, False)])
                npc_melee.cancel(bot)
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            values = set(component.held)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            component.edges([(value, False) for value in sorted(values)])
            component.shots.clear()
        bot.attack_action = "primary"
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [r for r in match.combat.rockets if r.shooter is not bot]
        count += 1
    return count


def _cancel_pending_fire(bot):
    if bot.hero == npc_context.ENFORCER:
        graph, windup_index = npc_context.WEAPON, 9
    elif bot.body == 0x0400000000001D40:
        graph, windup_index = 0x102E, 33
    else:
        return
    component = bot.body_script.component
    weapon = next((i for i in component.instances.values() if i.graph.index == graph), None)
    if weapon is None:
        return
    windup = weapon.states.get(windup_index)
    if windup is not None and windup.active:
        # Trooper's wait belongs to the firing stack. End that scope so the
        # next press can start a new windup. Reload and recovery are separate.
        owner = weapon.states[16] if graph == 0x102E else windup
        owner.end(False)
    if graph == 0x102E:
        volley = weapon.states.get(23)
        if volley is not None and volley.active:
            volley.cancel_pending()


def update_input(fight, bot):
    """Refresh manual aim and release input on death, occlusion, range or target loss."""
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    enabled = (not bot.dead and not fight.is_stunned(bot) and bot.firing
               and target is not None and target.spawned and target.has_body
               and not target.gone and target.client is not None and not fight.is_dead(target)
               and not fight.hooks.controlled(bot.entity)
               and bot.entity not in fight.sniper_repositions
               and (bot.attack_action != "charge"
                    or (bot.entity not in fight.gravitons.pulled
                        and not fight.traps.rooted(bot.entity, fight.tick)))
               and (fight.match.game_map.free_for_all or target.team != bot.team))
    if enabled and bot.hero == assassin.HERO and assassin.releasing(bot.body_script.component):
        enabled = False
    if enabled:
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        point = fight._eye(target)
        dx, dy, dz = (b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else AIM_RANGE
        enabled = 0 < distance <= reach
        if enabled:
            direction = tuple(v / distance for v in (dx, dy, dz))
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
            enabled = ((wall is None or wall >= distance)
                       and fight.walls.cast(origin, direction, distance) is None)
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    value = button(bot)
    component = bot.body_script.component
    if not enabled:
        _cancel_pending_fire(bot)
    desired = target.body if enabled else None
    if bot.hero == assassin.HERO and component.world.manual_target != desired:
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    return bool(enabled)
