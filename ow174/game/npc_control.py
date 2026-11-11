"""Manual NPC attack selection and input policy, reconstructed for the sandbox.

Slot0 comes from each body's extracted AI definition. Target selection after a
manual engagement loses its opponent is reconstructed sandbox policy.
"""

import math

from ow174.game import assassin, bots, combat, enforcer, heavy_charge, sniper_reposition

SNIPER_BODY = 0x0400000000001E7D
SNIPER_SLOT_INPUT = {1: 15, 2: 16}  # authored direct-slot inputs in weapon manager 0015
SNIPER_SIDEARM_DISTANCE = 6.0  # reconstructed preference for the faster close-range weapon
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


def validate_engagement(bot):
    button(bot)
    if bot.body == enforcer.BODY and bot.body_script.component.world.enforcer_controller is None:
        raise ValueError("Enforcer 227A controller is unavailable")


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


def start(match, player, *, action="primary"):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    if action == "reload":
        if bot.body != heavy_charge.TROOPER_BODY or match.combat.is_stunned(bot):
            raise ValueError("Manual reload requires a living, uncontrolled Trooper")
        primary = button(bot)
        bot.firing = False
        bot.spawner.pop("auto_engage", None)
        bot.attack_target = None
        bot.body_script.component.edges([(primary, False)])
        match.combat.npc_melee_pending.discard(bot.entity)
        heavy_charge.cancel_melee(bot)
        bot.reload_requested = True
        return bot.entity
    if action == "melee":
        instance = heavy_charge.melee_instance(bot)
        if instance is None or match.combat.is_stunned(bot) or heavy_charge.clocked(bot):
            raise ValueError("Manual melee requires a living, uncontrolled Trooper or Heavy Assault")
        ability = heavy_charge.melee_ability(instance)
        if ability is not None and ability.remaining() > 0:
            raise ValueError("NPC melee is cooling down")
        bot.reload_requested = False
        bot.firing = False
        bot.attack_target = None
        x, _, z = player.mover.position
        bot.yaw = float(bots.heading(x - bot.position[0], z - bot.position[2]))
        instance.component.edges([(button(bot), False)])
        match.combat.npc_melee_pending.add(bot.entity)
        return bot.entity
    if action == "charge":
        instance = heavy_charge.charge(bot)
        if instance is None or match.combat.is_stunned(bot) or match.combat.junkrat.rooted(bot.entity, match.tick):
            raise ValueError("Manual charge requires a living, uncontrolled Heavy Assault")
        ability = heavy_charge.ability(instance)
        if ability is not None and ability.remaining() > 0:
            raise ValueError("Heavy Assault charge is cooling down")
        if heavy_charge.requested(bot):
            return bot.entity
        bot.body_script.component.edges([(button(bot), False)])
        match.combat.npc_melee_pending.discard(bot.entity)
        heavy_charge.cancel_melee(bot)
        bot.spawner.pop("auto_engage", None)
        bot.firing = False
        bot.attack_target = player.entity
        x, _, z = player.mover.position
        bot.yaw = float(bots.heading(x - bot.position[0], z - bot.position[2]))
        return bot.entity
    if action == "reposition":
        validate_engagement(bot)
        sniper_reposition.begin(match.combat, bot, player)
        bot.body_script.component.edges([(button(bot), False)])
        bot.attack_input = False
        begin_engagement(bot, target=player.entity)
        return bot.entity
    if action == "secondary":
        if bot.body != SNIPER_BODY:
            raise ValueError("Manual secondary requires a living Sniper")
    elif action not in ("primary", "auto"):
        raise ValueError("Unknown manual NPC action")
    validate_engagement(bot)
    sniper_reposition.stop(bot, match.tick)
    bot.reload_requested = False
    match.combat.npc_melee_pending.discard(bot.entity)
    heavy_charge.cancel_melee(bot)
    if heavy_charge.clocked(bot):
        heavy_charge.stop(bot, match.combat)
    if action != "auto" and bot.hero == assassin.HERO and bot.spawner.get("auto_engage"):
        assassin.stop_approach(bot, match.tick)
        assassin.cancel_lunge(bot)
        bot.pursuit = None
    begin_engagement(bot, target=None if action == "auto" else player.entity, secondary=action == "secondary")
    return bot.entity


def begin_engagement(bot, *, target=None, secondary=False):
    bot.attack_slot = (2 if secondary else 1) if bot.body == SNIPER_BODY else None
    if target is None:
        # Manual NPCs own this per-spawn dictionary, which survives a snapshot.
        bot.spawner["auto_engage"] = True
    else:
        bot.spawner.pop("auto_engage", None)
    bot.attack_target = target
    bot.firing = True


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        if heavy_charge.clocked(bot):
            heavy_charge.stop(bot, match.combat)
        match.combat.npc_melee_pending.discard(bot.entity)
        heavy_charge.cancel_melee(bot)
        sniper_reposition.stop(bot, match.tick)
        bot.firing = False
        automatic = bot.spawner.pop("auto_engage", False)
        if automatic and bot.hero == assassin.HERO:
            assassin.stop_approach(bot, match.tick)
            assassin.cancel_lunge(bot)
            bot.pursuit = None
        bot.attack_target = None
        bot.attack_slot = None
        bot.reload_requested = False
        bot.attack_input = False
        if bot.body_script is not None:
            if automatic and bot.body == heavy_charge.TROOPER_BODY:
                bot.body_script.abort_trooper_reload()
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
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


def _valid_opponent(fight, bot, player):
    return (player.spawned and player.has_body and not player.gone and player.client is not None
            and not fight.is_dead(player)
            and (fight.match.game_map.free_for_all or player.team != bot.team))


def _aim_at(fight, bot, player):
    origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
    delta = tuple(b - a for a, b in zip(origin, fight._eye(player), strict=True))
    distance = math.sqrt(sum(axis * axis for axis in delta))
    reach = AIM_RANGE
    if bot.hero == assassin.HERO:
        reach = assassin.ACQUIRE_RANGE if bot.spawner.get("auto_engage") else assassin.PIN_RANGE
    if not 0 < distance <= reach:
        return None
    direction = tuple(axis / distance for axis in delta)
    if bot.hero == assassin.HERO and bot.spawner.get("auto_engage"):
        return distance, direction
    wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
    return (distance, direction) if wall is None or wall >= distance else None


def _engagement_aim(fight, bot):
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    if target is not None and _valid_opponent(fight, bot, target):
        aim = _aim_at(fight, bot, target)
        if aim is not None or not bot.spawner.get("auto_engage"):
            return target, aim
    candidates = []
    for player in fight.match.players:
        if _valid_opponent(fight, bot, player):
            aim = _aim_at(fight, bot, player)
            if aim is not None:
                candidates.append((aim[0], player.entity, player, aim))
    if not candidates:
        bot.attack_target = None
        return None, None
    _, bot.attack_target, target, aim = min(candidates, key=lambda row: row[:2])
    return target, aim


def _auto_reload(bot, component):
    """Queue the authored reload input once the Trooper's magazine is empty."""
    if bot.body != heavy_charge.TROOPER_BODY:
        return False
    weapon = next((i for i in component.instances.values() if i.graph.index == 0x102E), None)
    if weapon is None:
        return False
    ammo = weapon.vars.get(53)
    reloading = weapon.vars.get(51)
    if reloading is not None and reloading.value():
        return True
    if ammo is not None and ammo.value() == 0:
        bot.reload_requested = True
        return True
    return False


def update_input(fight, bot):
    """Hold a manual target through cover, or reacquire a visible one in auto mode."""
    previous = bot.attack_target
    automatic_assassin = bot.hero == assassin.HERO and bot.spawner.get("auto_engage")
    if automatic_assassin:
        assassin.stop_approach(bot, fight.tick)
    active = not bot.dead and not fight.is_stunned(bot) and bot.firing
    target, aim = _engagement_aim(fight, bot) if active else (None, None)
    if automatic_assassin and aim is None:
        assassin.cancel_lunge(bot)
        bot.pursuit = None
    # Release the current input before aiming at a replacement opponent.
    enabled = (aim is not None and previous in (None, bot.attack_target)
               and not sniper_reposition.repositioning(bot))
    if enabled:
        distance, (dx, dy, dz) = aim
        bot.yaw = float(bots.heading(dx, dz))
        bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
        bot.moved_tick = fight.tick
    value = button(bot)
    component = bot.body_script.component
    if (enabled and bot.body == SNIPER_BODY and bot.spawner.get("auto_engage")
            and distance <= 3 and sniper_reposition.try_begin(fight, bot, target)):
        enabled = False
    if enabled and bot.attack_slot is not None:
        requested_slot = bot.attack_slot
        if bot.spawner.get("auto_engage"):
            requested_slot = 2 if distance <= SNIPER_SIDEARM_DISTANCE else 1
        slot = component.vars.get(31)
        if slot is None or slot.value() != requested_slot:
            select = SNIPER_SLOT_INPUT[requested_slot]
            component.edges([(value, False), (select, True), (select, False)])
            bot.attack_input = False
            return False
    if enabled and bot.spawner.get("auto_engage"):
        enabled = not (
            _auto_reload(bot, component)
            or heavy_charge.auto_melee(bot, fight, target)
            or heavy_charge.auto_charge(bot, fight, distance)
        )
    if enabled and bot.hero == assassin.HERO:
        if automatic_assassin:
            enabled = (not assassin.lunge_windup(fight, bot, target, distance)
                       and assassin.approach(fight, bot, target, distance))
        # A projected or personal barrier denies body contact before the pin starts.
        enabled = enabled and fight.barriers.attached_enemy(bot, target.body) is None
    desired = target.body if enabled else None
    if bot.hero == assassin.HERO and component.world.manual_target != desired:
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    if bot.reload_requested:
        bot.reload_requested = False
        if not fight.is_stunned(bot):
            bot.body_script.request_trooper_reload()
    return bool(enabled)
