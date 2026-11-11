"""NPC target selection and weapon input policy for the sandbox.

Slot0 comes from each body's extracted AI definition. The sandbox can select
authored close attacks and opt-in target gates. Native acquisition and navigation
are separate services.
"""

import math

from ow174.game import assassin, bots, combat, npc_charge, npc_melee, npc_reposition
from ow174.game.script import expr, graph, message_receive, runtime
from ow174.game.script.bot import ACQUISITION_GRAPHS, ASSASSIN_ACQUISITION, BotWorld
from ow174.game.script.driver import OWNER_HEALTH

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


def start(match, player):
    bot = selected(match, player)
    if bot is None:
        raise ValueError('No living manual enemy under the crosshair')
    button(bot)  # validate before changing policy
    retire_acquisition(bot)
    if bot.attack_target is not None and bot.attack_target != player.entity:
        npc_charge.stop(bot)
        npc_reposition.stop(bot)
        npc_melee.stop(bot.body_script.component)
    bot.attack_target = player.entity
    bot.firing = True
    return bot.entity


def stop(match):
    count = 0
    for bot in match.bots:
        if not manual(bot):
            continue
        retire_acquisition(bot)
        bot.firing = False
        bot.attack_target = None
        bot.attack_input = False
        if bot.body_script is not None:
            component = bot.body_script.component
            assassin.bind(component.world, component, None)
            npc_charge.stop(bot)
            npc_reposition.stop(bot)
            npc_melee.stop(component)
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


def enable_acquisition(match, bot):
    """Host the authored target gate for an opt-in live NPC."""
    acquisition = ACQUISITION_GRAPHS.get(bot.hero)
    assassin_gate = bot.hero == assassin.HERO
    if bot not in match.bots or (acquisition is None and not assassin_gate) or not manual(bot) or bot.dead:
        raise ValueError('acquisition needs a live spawned NPC with an authored gate')
    if getattr(bot, 'target_controller', None) is not None:
        return
    button(bot)
    index = ASSASSIN_ACQUISITION if assassin_gate else acquisition[0]
    definition = graph.graph(index)
    if definition is None:
        raise ValueError(f'NPC acquisition needs authored graph {index:04X} in the prepared bundle')
    world = BotWorld(bot.hero, match.game_map.mode_guid, {})
    owner = bot.entity
    if assassin_gate:
        owner = 0xB0000000 | (bot.entity & 0x0FFFFFFF)
        world.assassin_body = bot.entity
        world.match = match
    component = runtime.Component(owner, world=world)
    component.frame = match.tick
    component.now = match.tick * component.frame_ms
    needs_body_stack = acquisition is not None and acquisition[3] is not None
    refresh_acquisition_body(component, match.combat, bot, needs_body_stack)
    component.create(definition)
    component.run_now()
    bot.target_controller = component


def retire_acquisition(bot):
    component = getattr(bot, 'target_controller', None)
    if component is not None:
        del bot.target_controller
        if component.fault is None:
            for instance in tuple(component.instances.values()):
                component.destroy(instance)


def update_acquired_target(fight, bot, component):
    """Resolve a visible enemy and apply the authored target message and gate."""
    candidates = []
    if not fight.is_stunned(bot):
        x, y, z = bot.position
        origin = (x, y + combat.EYE, z)
        for target in fight._enemies(bot):
            player = target.owner
            if player not in fight.match.players or player.client is None or player.gone:
                continue
            point = fight._eye(player)
            distance = math.dist(origin, point)
            reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else AIM_RANGE
            if not 0 < distance <= reach:
                continue
            direction = tuple((b - a) / distance for a, b in zip(origin, point, strict=True))
            wall = combat.ray_world(fight.match.collision_world, origin, direction, distance)
            if wall is None or wall >= distance:
                candidates.append((distance, target.entity, player))
    selected = min(candidates, default=None, key=lambda row: row[:2])
    entity = selected[1] if selected is not None else None
    if bot.hero == assassin.HERO:
        update_assassin_target(fight, bot, component, selected)
        return
    _, gate_node, target_slot, body_gate_node = ACQUISITION_GRAPHS[bot.hero]
    instance, = component.instances.values()
    gate = instance.node_state(instance.graph.node(gate_node))
    if component.world.manual_target != entity:
        component.world.manual_target = entity
        if entity is not None:
            packet = message_receive.Packet(0x0240000000000079, bot.entity, bot.entity, (
                (0x0A40000000000057, 0), (0x0A40000000000059, expr.Entity(entity)),
            ))
            if message_receive.receive(instance, packet) != 1:
                raise ValueError('unexpected NPC target receiver')
        if gate.active:
            gate.dependency_changed()
    refresh_acquisition_body(component, fight, bot, body_gate_node is not None)
    if component.frame < fight.tick:
        component.run_frame(fight.tick)
    else:
        component.run_now()
    body_gate = instance.node_state(instance.graph.node(body_gate_node)) if body_gate_node else gate
    latched = instance.find_var(*target_slot, create=False)
    bot.firing = bool(entity is not None and gate.active and gate.current and body_gate.active
                      and body_gate.current and latched is not None
                      and latched.value() == expr.Entity(entity))
    bot.attack_target = selected[2].entity if bot.firing else None


def update_assassin_target(fight, bot, component, selected):
    entity = selected[1] if selected is not None else None
    world = component.world
    instance, = component.instances.values()
    if world.manual_target != entity:
        world.manual_target = entity
        world.assassin_handoff = None
        branch = instance.node_state(instance.graph.node(15))
        branch.end(False)
        instance.write((runtime.ENTITY_SCOPE, 1650), expr.Entity(0))
        tracker = instance.node_state(instance.graph.node(4))
        if not tracker.active:
            tracker.request(runtime.BEGIN)
    if component.frame < fight.tick:
        component.run_frame(fight.tick)
    else:
        component.run_now()
    bot.firing = entity is not None and world.assassin_handoff == entity
    bot.attack_target = selected[2].entity if bot.firing else None


def refresh_acquisition_body(component, fight, bot, needs_body_stack):
    health = fight.vitals[bot.entity].total
    if component.entity_var(OWNER_HEALTH).value() != health:
        component.set(OWNER_HEALTH, health)
    if needs_body_stack:
        slot = bot.body_script.component.entity_var(888, create=False)
        value = slot.value() if slot is not None else None
        if component.entity_var(888).value() != value:
            component.set(888, value)


def update_input(fight, bot):
    """Refresh manual aim and release input on death, occlusion, range or target loss."""
    controller = getattr(bot, 'target_controller', None)
    if controller is not None:
        update_acquired_target(fight, bot, controller)
    target = next((p for p in fight.match.players if p.entity == bot.attack_target), None)
    enabled = (not bot.dead and not fight.is_stunned(bot) and bot.firing
               and target is not None and target.spawned and target.has_body
               and not target.gone and target.client is not None and not fight.is_dead(target)
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
            enabled = wall is None or wall >= distance
            bot.yaw = float(bots.heading(dx, dz))
            bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
            bot.moved_tick = fight.tick
    value = button(bot)
    component = bot.body_script.component
    if (npc_charge.update(fight, bot, target, enabled) or npc_reposition.update(fight, bot, target, enabled)
            or npc_melee.update(bot, component, enabled)):
        enabled = False
    desired = target.body if enabled else None
    if bot.hero == assassin.HERO and component.world.manual_target != desired:
        assassin.bind(component.world, component, desired)
        bot.attack_input = False
    if bot.attack_input != enabled or (value in component.held) != enabled:
        component.edges([(value, bool(enabled))])
    bot.attack_input = bool(enabled)
    return bool(enabled)
