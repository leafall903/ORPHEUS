"""Manual NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. This is manual targeting,
with explicit pursuit and melee policies; native autonomous acquisition is open.
"""

import math

from ow174.game import assassin, bots, combat, npc_navigation

MELEE_BUTTON = 31
CHARGE_BUTTON = 35
CHARGE_CONTACT = (0x1408, 53, 100)
MELEE_GRAPHS = {npc_navigation.TROOPER: (0x1422, 2, 5), npc_navigation.HEAVY: (0x140E, 3, 6)}
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


def attack_button(bot):
    if bot.attack_charge:
        return CHARGE_BUTTON
    return MELEE_BUTTON if bot.attack_melee else button(bot)


def start(match, player, *, melee=False, charge=False):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    old_button = attack_button(bot)  # validate before changing policy
    if melee and bot.hero not in MELEE_GRAPHS:
        raise ValueError("NPC melee input is unavailable")
    if charge and (melee or bot.hero != npc_navigation.HEAVY):
        raise ValueError("NPC charge input is unavailable")
    if (melee, charge) != (bot.attack_melee, bot.attack_charge):
        if bot.attack_charge:
            npc_navigation.cancel_charge(bot)
        bot.body_script.component.edges([(old_button, False)])
        bot.attack_input = False
    bot.autonomous = False
    bot.attack_melee, bot.attack_charge = melee, charge
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        if bot.attack_charge:
            npc_navigation.cancel_charge(bot)
        bot.autonomous = False
        bot.firing = False
        bot.attack_target = None
        npc_navigation.stop(bot)
        bot.attack_input = False
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            values = set(component.held)
            if bot.attack_melee:
                values.add(MELEE_BUTTON)
            if bot.attack_charge:
                values.add(CHARGE_BUTTON)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            component.edges([(value, False) for value in sorted(values)])
            component.shots.clear()
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [r for r in match.combat.rockets if r.shooter is not bot]
        bot.attack_melee = bot.attack_charge = False
        count += 1
    return count


def _pick_special(bot, distance):
    if bot.hero in MELEE_GRAPHS and distance <= npc_navigation.melee_reach(bot):
        return True, False
    if bot.hero == npc_navigation.HEAVY and 5 <= distance <= 8:
        instance = npc_navigation.charge_instance(bot)
        ability = instance.states.get(41) if instance is not None else None
        if ability is not None and ability.remaining() == 0:
            return False, True
    return False, False


def _set_special(bot, melee, charge):
    if (melee, charge) == (bot.attack_melee, bot.attack_charge):
        return
    bot.body_script.component.edges([(attack_button(bot), False)])
    bot.attack_input = False
    bot.attack_melee, bot.attack_charge = melee, charge


def acquire(match, bot, tick):
    """Opt-in host acquisition. Retain a valid target through cover so pursuit can route around it."""
    fight = match.combat
    if bot.dead or bot.body_script is None or fight.is_stunned(bot, tick=tick):
        return
    world = match.collision_world
    if world is None:
        return
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    specials = bot.spawner.get("autonomous_specials") is True
    candidates = []
    for target in fight._enemies(bot):
        player = target.owner
        if isinstance(player, bots.Bot) or player.gone:
            continue
        point = fight._eye(player)
        distance = math.dist(origin, point)
        if not 0 < distance <= AIM_RANGE:
            continue
        retained = player.entity == bot.attack_target
        if retained:
            if bot.attack_charge:
                instance = npc_navigation.charge_instance(bot)
                if instance is None or instance.states[41].remaining() == 0:
                    return  # let the selected windup or run finish
                _set_special(bot, False, False)
                bot.firing = True
            if not specials:
                return
        direction = tuple((b - a) / distance for a, b in zip(origin, point, strict=True))
        wall = combat.ray_world(world, origin, direction, distance)
        if retained:
            if wall is None or wall >= distance:
                _set_special(bot, *_pick_special(bot, distance))
            return
        if wall is None or wall >= distance:
            candidates.append((distance, player.entity))
    distance, target = min(candidates) if candidates else (0, None)
    if target != bot.attack_target and bot.attack_input:
        # Give an edge-triggered weapon one update with its previous button up.
        target = None
    if target is not None and specials:
        _set_special(bot, *_pick_special(bot, distance))
    bot.attack_target = target
    bot.firing = target is not None


def sniper_ready(bot):
    """Wait for the authored single-shot volley and its post-fire wait."""
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == 0x1122:
            return not any(state.active for index, state in instance.states.items() if index in (4, 39))
    return False


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
        reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else AIM_RANGE
        if bot.attack_melee:
            reach = npc_navigation.melee_reach(bot)
        enabled = 0 < distance <= reach
        if not enabled and bot.attack_melee and fight.walls.pieces and distance > 0:
            from ow174.game.mei_wall import Pillar

            graph_id, _, track_pos = MELEE_GRAPHS[bot.hero]
            instance = next((i for i in bot.body_script.component.instances.values()
                             if i.graph.index == graph_id), None)
            if instance is not None:
                enabled = any(isinstance(t, Pillar) for t in fight.npc_contact_targets(
                    bot, instance, instance.graph.node(track_pos), pillars=True, yaw=bots.heading(dx, dz),
                ))
        if enabled:
            direction = tuple(v / distance for v in (dx, dy, dz))
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
            enabled = wall is None or wall >= distance
            if bot.hero == assassin.HERO and fight.walls.blocked(origin, point):
                enabled = False
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
            npc_navigation.face_input(bot)
    enabled = enabled and not npc_navigation.repositioning(bot)
    if enabled and bot.attack_charge and fight.gravitons.npc_move(bot, fight.tick) is not None:
        enabled = False
    trigger = enabled
    if enabled and bot.autonomous and bot.hero == npc_navigation.SNIPER:
        trigger = sniper_ready(bot)
    value = attack_button(bot)
    component = bot.body_script.component
    desired = target.body if enabled else None
    if bot.hero == assassin.HERO and component.world.manual_target != desired:
        if component.world.manual_target is not None and desired is not None:
            # Let the old pin enter its release before binding another body.
            # A new press in the same update would leave that release active.
            desired = None
            enabled = trigger = False
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    if bot.attack_input != trigger or (value in component.held) != trigger:
        component.edges([(value, bool(trigger))])
    bot.attack_input = bool(trigger)
    return bool(enabled)
