"""Sandbox NPC engagement and target reacquisition using extracted weapon input."""

import math

from ow174.game import assassin, bots, combat, heavy_assault, immunity, npc_context, npc_navigation, sniper_reposition

AIM_RANGE = 100.0  # sandbox selection/engagement policy in metres, not a native AI range claim
SNIPER_WEAPON, SNIPER_TRIGGER, SNIPER_AIM = 0x1122, 4, 34
ENFORCER_WEAPON, ENFORCER_TRIGGER, ENFORCER_WINDUP, ENFORCER_ATTACK, ENFORCER_VOLLEY = 0x13EB, 2, 11, 15, 24


def manual(bot):
    return bot.spawner.get('manual_spawn') is True


def button(bot, *, mode=None):
    script = bot.body_script
    if script is None:
        raise ValueError('NPC body script is unavailable')
    mode = bot.attack_mode if mode is None else mode
    if mode == "charge":
        if bot.hero != heavy_assault.HERO:
            raise ValueError('NPC charge input is unavailable')
        return heavy_assault.CHARGE_BUTTON
    if mode == "melee":
        if bot.hero not in heavy_assault.MELEE:
            raise ValueError('NPC melee input is unavailable')
        return heavy_assault.BUTTON
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
        owner = target.owner
        if owner is player or (isinstance(owner, bots.Bot) and owner.dead) or fight.vitals_of(target).dead:
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


def start(match, player, *, melee=False, charge=False, reposition=False):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    if sum((melee, charge, reposition)) > 1:
        raise ValueError('select one NPC attack')
    mode = "charge" if charge else "melee" if melee else "reposition" if reposition else "primary"
    value = button(bot, mode=mode)  # validate before changing policy
    if (bot.hero == assassin.HERO and getattr(bot.body_script, 'control_context', None) is not None
            and player.controller_vm is not None):
        player.controller_vm.configure_manual_pin(player.controller_filter_bits)
    if reposition:
        sniper_reposition.begin(match.combat, bot, player)
    elif bot.attack_mode == "reposition":
        sniper_reposition.stop(bot, match.tick)
    if bot.attack_input and (previous := button(bot)) != value:
        bot.body_script.component.edges([(previous, False)])
        bot.attack_input = False
    if bot.attack_mode == "charge" and mode != "charge":
        component = bot.body_script.component
        ability = heavy_assault.ability(component, heavy_assault.CHARGE_GRAPH, heavy_assault.CHARGE_ABILITY)
        if ability is not None:
            heavy_assault.stop_charge(component, ability)
    bot.attack_mode = mode
    if bot.hero == 0x02E00000000001CE:
        component = bot.body_script.component
        index = component.entity_var(9948).value()
        if index is not None and (type(index) is not int or not 0 <= index < 4):
            raise ValueError('Enforcer attack difficulty index must select one of four authored entries')
        if index is None:
            # Manual sandbox default: first authored attack-damage table entry.
            # This is not a native mission/difficulty or conditional-pool claim.
            component.set(9948, 0)
    if bot.attack_target is not None and bot.attack_target != player.entity:
        _retire_target_input(bot, value)
    if bot.navigation is not None and bot.navigation.next_plan is None:
        bot.navigation.goal = None
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def strike(match, player):
    """Request one authored melee from the selected manual NPC."""
    bot = selected(match, player)
    if bot is None or bot.hero not in (heavy_assault.TROOPER, heavy_assault.HERO) or bot.body_script is None:
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
        if bot.attack_mode == "reposition":
            sniper_reposition.stop(bot, match.tick)
        elif sniper_reposition.primary_scoped(bot):
            sniper_reposition.stop_primary(bot)
        bot.attack_mode = "primary"
        npc_navigation.stop(bot, land=True)
        reposition = npc_navigation.reposition_state(bot)
        if reposition is not None:
            reposition.instance.drop_begins([reposition])
            reposition.end(False)
        if bot.body_script is not None:
            context = getattr(bot.body_script, 'control_context', None)
            if context is not None:
                context.set_target()
                match.combat.release_pin(bot.entity)
            component = bot.body_script.component
            component.discard_button_presses()
            assassin.bind(component.world, component, None)
            heavy_assault.stop(component)
            stop_sniper_aim(component)
            values = set(component.held)
            primary = component.world.ai_weapon_button(0)
            if type(primary) is int:
                values.add(primary)  # also cancel a down edge queued before the next VM update
            component.edges([(value, False) for value in sorted(values)])
            stop_enforcer_windup(component)
            component.shots.clear()
        match.combat.cancel_npc_projectiles(bot)
        count += 1
    return count


def stop_sniper_aim(component):
    if component.world.hero != npc_navigation.SNIPER:
        return
    for instance in component.instances.values():
        if instance.graph.index != SNIPER_WEAPON:
            continue
        # The released button clears the trigger stack, but its Boolean
        # switch only has a begin link to the separate aim stack.
        instance.exit_subgraph(instance.graph.node(SNIPER_TRIGGER), 'm_80AF45FB')
        aim = instance.states.get(instance.graph.node(SNIPER_AIM).state)
        if aim is not None:
            instance.drop_begins([aim])
            aim.end(False)


def stop_enforcer_windup(component):
    if component.world.hero != npc_context.ENFORCER:
        return
    for instance in component.instances.values():
        if instance.graph.index != ENFORCER_WEAPON:
            continue
        # End the trigger callback as well as the sibling attack and
        # windup. Death can reach here without a button-release edge.
        instance.exit_subgraph(instance.graph.node(ENFORCER_TRIGGER), 'm_80AF45FB')
        windup, attack, volley = (instance.states.get(instance.graph.node(position).state)
                                 for position in (ENFORCER_WINDUP, ENFORCER_ATTACK, ENFORCER_VOLLEY))
        for state in (windup, attack):
            if state is not None:
                state.end(False)
        instance.drop_begins([state for state in (windup, attack, volley) if state is not None])


def _retire_target_input(bot, value):
    component = bot.body_script.component
    component.discard_button_presses()
    heavy_assault.stop(component)
    stop_sniper_aim(component)
    if component.world.hero in (npc_context.ENFORCER, npc_navigation.SNIPER):
        # The new target needs a fresh trigger after the old weapon scope retires.
        component.edges([(value, False)])
        stop_enforcer_windup(component)
        bot.attack_input = False


def target_player(fight, bot):
    """Keep a valid target through cover; after target loss, acquire a visible opponent."""
    candidates = []
    for target in fight._targets():
        player = target.owner
        if isinstance(player, bots.Bot):
            continue
        if (not player.spawned or not player.has_body or player.gone or player.client is None
                or (not fight.match.game_map.free_for_all and player.team == bot.team)
                or fight.is_dead(player)):
            continue
        if player.entity == bot.attack_target:
            return target
        candidates.append(target)
    nearest = None
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    for target in candidates:
        point = fight._eye(target.owner)
        distance = math.dist(origin, point)
        if not 0 < distance <= AIM_RANGE:
            continue
        rank = (distance, target.owner.entity)
        if nearest is not None and rank >= nearest[0]:
            continue
        if npc_navigation.visible_aim(fight.match.collision_world, bot.position, target) is not None:
            nearest = (rank, target)
    target = nearest[1] if nearest is not None else None
    bot.attack_target = target.owner.entity if target is not None else None
    return target


def update_input(fight, bot):
    """Refresh aim and release input on death, occlusion, range or target loss."""
    if bot.reposition_body is not None:
        owner = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
        if (owner is None or owner.body != bot.reposition_body or not owner.spawned
                or not owner.has_body or owner.gone or owner.client is None or fight.is_dead(owner)
                or (not fight.match.game_map.free_for_all and owner.team == bot.team)):
            sniper_reposition.stop(bot, fight.tick)
    active = not bot.dead and bot.firing and not fight.is_stunned(bot)
    previous_target = bot.attack_target
    target = target_player(fight, bot) if active else None
    enabled = target is not None
    if fight.junkrat.rooted(bot.entity, fight.tick) and (bot.attack_mode == "charge" or bot.hero == assassin.HERO):
        enabled = False  # movement/contact attacks stop; ranged and melee input remains live.
    if bot.attack_mode == "reposition":
        if not enabled:
            sniper_reposition.stop(bot, fight.tick)
        elif bot.reposition_body is None and sniper_reposition.repositioning(bot):
            enabled = False
    if enabled and bot.hero == assassin.HERO and immunity.control_immune(target.owner, target.entity):
        enabled = False
    if enabled:
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        distance = math.dist(origin, fight._eye(target.owner))
        reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else AIM_RANGE
        enabled = 0 < distance <= reach
        if enabled:
            direction = npc_navigation.visible_aim(fight.match.collision_world, bot.position, target)
            enabled = direction is not None
        if enabled and bot.hero == assassin.HERO:
            enabled = assassin.contact(fight.match, bot, target)
        if enabled:
            dx, dy, dz = direction
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            npc_navigation.face_input(bot)
            bot.moved_tick = fight.tick
    value = button(bot)
    component = bot.body_script.component
    if bot.hero == assassin.HERO and enabled:
        enabled = not fight._control_immune(target)
    if previous_target is not None and previous_target != bot.attack_target:
        _retire_target_input(bot, value)
    if bot.attack_mode == "primary" and (
            heavy_assault.update(bot, component, enabled)
            or sniper_reposition.update_primary(fight, bot, enabled)):
        enabled = False
    context = getattr(bot.body_script, 'control_context', None)
    if context is not None and bot.hero == assassin.HERO:
        player = target.owner if enabled else None
        host = player.controller_vm if player is not None else None
        if host is not None and (host.retired or host.component.fault is not None):
            host = None
        if context.target_host is not host:
            fight.release_pin(bot.entity)
        context.set_target(player.entity if player is not None else None, host=host)
    else:
        desired = target.entity if enabled else None
        if (bot.hero == assassin.HERO and component.world.manual_target != desired):
            if component.world.manual_target is not None:
                # Let the old pin's exits run before binding a replacement. Those
                # exits can start the release animation after bind returns.
                desired = None
                enabled = False
            assassin.bind(component.world, component, desired)
            bot.attack_input = False
    if bot.attack_input != enabled or (value in component.held) != enabled:
        if not enabled:
            heavy_assault.stop_miniguns(component)
            stop_sniper_aim(component)
        component.edges([(value, bool(enabled))])
        if not enabled:
            stop_enforcer_windup(component)
    if context is not None and bot.hero == assassin.HERO:
        pin = assassin.pin_button(bot)
        player = target.owner if enabled else None
        pin_enabled = bool(enabled and player is not None
                           and (assassin.selected_controller(context) == player.entity
                                or assassin.holds_pin(bot, player)))
        if pin is not None and (pin in component.held) != pin_enabled:
            component.edges([(pin, pin_enabled)])
        if not pin_enabled:
            assassin.retire_pin_effect(context.target_host, bot.entity)
    bot.attack_input = bool(enabled)
    return bool(enabled)


def autonomous(bot):
    return manual(bot) and bot.spawner.get('autonomous_targeting') is True

def _aim_sample(fight, bot, target):
    if (target is None or not target.spawned or not target.has_body or target.gone
            or target.client is None or fight.is_dead(target)
            or (not fight.match.game_map.free_for_all and target.team == bot.team)):
        return None
    if bot.hero == assassin.HERO and fight.barriers.for_target(target) is not None:
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
