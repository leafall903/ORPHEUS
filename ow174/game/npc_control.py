"""Explicit manual NPC target and input policy, reconstructed for the sandbox.

Gun slot0 comes from the extracted AI definition;1157 pin uses logical36.
Opt-in stationary acquisition is reconstructed. Native AI and navigation remain open.
"""

import math

from ow174.game import assassin, bots, combat, npc_charge

MELEE_BUTTON = 31
MELEE_GRAPHS = {0x02E00000000001AC: (0x1422, 2, 5), 0x02E00000000001BA: (0x140E, 3, 6)}
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


def _cancel_melee(bot):
    from ow174.game.script import nodes

    graph_id = MELEE_GRAPHS[bot.hero][0]
    for instance in tuple(bot.body_script.component.instances.values()):
        if instance.graph.index == graph_id:
            for state in tuple(instance.states.values()):
                if isinstance(state, nodes.Ability):
                    state.presses.clear()
                    state.stop()


def engage(match, bot, player, *, melee=False, charge=False):
    """Bind an explicit manual opponent; autonomous target acquisition is separate."""
    if match.ended or bot not in match.bots or not manual(bot) or bot.dead:
        raise ValueError('Manual NPC is not live in this match')
    if (player not in match.players or not player.has_body or not player.spawned
            or player.gone or player.client is None or match.combat.is_dead(player)
            or (not match.game_map.free_for_all and player.team == bot.team)):
        raise ValueError('Manual NPC needs a living enemy player')
    primary = button(bot)
    if type(melee) is not bool or (melee and bot.hero not in MELEE_GRAPHS):
        raise ValueError('NPC melee needs a supported authored melee graph')
    if type(charge) is not bool or (charge and (melee or bot.hero != npc_charge.HERO)):
        raise ValueError('NPC charge needs the authored Heavy Assault charge graph')
    previous_melee = bot.spawner.pop('manual_melee', False)
    previous_charge = bot.spawner.pop('manual_charge', False)
    if (previous_melee, previous_charge) != (melee, charge):
        previous_button = MELEE_BUTTON if previous_melee else primary
        if previous_charge:
            previous_button = npc_charge.BUTTON
        component = bot.body_script.component
        component.edges([(previous_button, False)])
        if previous_melee:
            _cancel_melee(bot)
        if previous_charge:
            npc_charge.stop(bot)
        component.shots.clear()
        bot.attack_input = False
    if melee:
        bot.spawner['manual_melee'] = True
    if charge:
        bot.spawner['manual_charge'] = True
    bot.spawner.pop('autonomous_targeting', None)
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def stop(match):
    """Release manual input and pin scopes; launched projectiles keep flying."""
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        bot.spawner.pop('autonomous_targeting', None)
        melee = bot.spawner.pop('manual_melee', False)
        charge = bot.spawner.pop('manual_charge', False)
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            values = set(component.held)
            if melee:
                values.add(MELEE_BUTTON)
                _cancel_melee(bot)
            if charge:
                values.add(npc_charge.BUTTON)
                npc_charge.stop(bot)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            component.edges([(value, False) for value in sorted(values)])
            component.shots.clear()
        count += 1
    return count


def _aim_sample(fight, bot, target):
    if bot.hero == assassin.HERO:
        return assassin.contact_sample(fight, bot, target)
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    x, y, z = target.feet
    delta = tuple(b - a for a, b in zip(origin, (x, y + target.eye, z), strict=True))
    distance = math.sqrt(sum(axis * axis for axis in delta))
    if not 0 < distance <= AIM_RANGE:
        return None
    direction = tuple(axis / distance for axis in delta)
    wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
    return distance, delta, wall is None or wall >= distance


def acquire(fight, bot):
    """Keep a visible enemy controller, or select the nearest visible one."""
    candidates = []
    if not fight.match.ended and not fight.is_stunned(bot):
        for target in fight._enemies(bot):
            actor = target.owner
            player = fight.match._message_player(actor.source_body) if isinstance(actor, bots.Bot) else actor
            if (player is None or player.gone
                    or (isinstance(actor, bots.Bot) and player.controlled_mech is not actor)
                    or (bot.hero == assassin.HERO and assassin.protected(fight, target))):
                continue
            sight = _aim_sample(fight, bot, target)
            if sight is None or not sight[2]:
                continue
            if player.entity == bot.attack_target:
                return
            candidates.append((sight[0], player.entity))
    # An edge-triggered weapon needs one released tick when switching controllers.
    selected = min(candidates)[1] if candidates and not bot.attack_input else None
    bot.attack_target = selected
    bot.firing = selected is not None


def update_input(fight, bot):
    """Refresh aim and release input on death, occlusion, range or target loss."""
    player = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    target = fight.player_target(player)
    sight = None
    charge = bot.spawner.get('manual_charge') is True
    if (not bot.dead and not fight.is_stunned(bot) and bot.firing
            and (not charge or (bot.impulse_mover is None and bot.entity not in fight.zarya_gravitons.pulled))
            and target is not None and not fight.vitals_of(target).dead
            and (fight.match.game_map.free_for_all or target.team != bot.team)
            and (bot.hero != assassin.HERO or not assassin.protected(fight, target))):
        sight = _aim_sample(fight, bot, target)
    enabled = sight is not None and sight[2]
    if sight is not None:
        _, (dx, dy, dz), _ = sight
        bot.yaw = float(bots.heading(dx, dz))
        bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
        bot.moved_tick = fight.tick
    if charge:
        value = npc_charge.BUTTON
    elif bot.spawner.get('manual_melee'):
        value = MELEE_BUTTON
    else:
        value = button(bot)
    component = bot.body_script.component
    desired = target.entity if enabled else None
    if (bot.hero == assassin.HERO and component.world.manual_target != desired):
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    return bool(enabled)
