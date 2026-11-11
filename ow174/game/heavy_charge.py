"""Static host for Heavy Assault's 1408 charge and 1407 victim effect."""

import math

from ow174.game import combat, immunity, mei_wall, npc_melee
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph

HERO = 0x02E00000000001BA
BODY = 0x0400000000001EBB
CHARGE = 0x1408
VICTIM = 0x1407
MAX_TRAVEL_MS = 2000  # reconstructed fallback when no stop contact arrives


def blocks_bob_uppercut(owner, charge_yaw):
    """Read Heavy Assault's live charge flag for B.O.B.'s 1537/16 filter."""
    # The authored Heavy Assault definition has no 067D fallback tag.
    if getattr(owner, "body", None) != BODY:
        return False
    script = owner.body_script
    if script is None or script.component.fault is not None or script.component.owner_dead:
        return False
    flag = script.component.entity_var(596, create=False)
    turn = (charge_yaw - round(owner.yaw) + 32768) % 65536 - 32768
    return flag is not None and flag.value() is True and abs(turn) > 16384


def _instance(component):
    if component.strict or component.world.hero != HERO:
        return None
    return next((item for item in component.instances.values() if item.graph.index == CHARGE), None)


def _state(instance, position):
    return instance.states.get(instance.graph.node(position).state)


def _read(instance, key):
    slot = instance.vars.get(key)
    return slot.value() if slot is not None else None


def _write(instance, key, value):
    instance.write((expr.INSTANCE, key), value)


def _speed(movement):
    rows = movement.node.field("m_BC5E91CF")
    velocity = movement.evaluate(rows[0]["m_9B7A63EA"], watch=False)
    return math.hypot(velocity.x, velocity.z) if isinstance(velocity, expr.Vec3) else 0.0


def update(fight, bot, component, target, engaged, visible):
    """Select the special at medium range and drive the mounted movement phase."""
    instance = _instance(component)
    ability = _state(instance, 82) if instance is not None else None
    if ability is None:
        return False
    button, _, _ = ability.buttons()
    if button in component.held:
        component.edges([(button, False)])
    phase = _read(instance, 59)
    if phase == 2:
        recovery = _state(instance, 50)
        if recovery is not None and recovery.active:
            duration = recovery.evaluate(recovery.node.field("m_86701C3E"), watch=False)
            if recovery.now - recovery.start >= round(duration * 1000):
                instance.run_node(instance.graph.node(8), None)
        return True
    if phase == 1:
        victim = expr.to_entity(_read(instance, 536))
        movement = _state(instance, 71)
        if (not engaged or bot.entity in fight.impulses.npcs
                or (victim and not _selected_victim(fight, target, victim))
                or (not visible and (movement is None or not movement.active))):
            stop(fight.match, bot)
        else:
            _advance(fight, bot, instance)
        return True
    if (visible and bot.entity not in fight.impulses.npcs and ability.remaining() <= 0
            and abs(bot.position[1] - target.mover.position[1]) < 0.8
            and 9 <= math.dist(bot.position, target.mover.position) <= 16):
        # Manual selection distance and initial facing are sandbox policy.
        melee = npc_melee.ability(component)
        if melee is None or not melee.flags & melee.RUNNING:
            component.world.match = fight.match
            component.edges([(button, True)])
            return True
    return False


def _advance(fight, bot, instance):
    movement = _state(instance, 71)
    if movement is None or not movement.active:
        return
    if movement.now - movement.start >= MAX_TRAVEL_MS:
        stop(fight.match, bot)
        return
    direction = _read(instance, 3192)
    if not isinstance(direction, expr.Vec3) or math.hypot(direction.x, direction.z) < 0.01:
        dx, _, dz = combat.aim(round(bot.yaw), 0)
        direction = expr.Vec3(dx, 0, dz)
        _write(instance, 3192, direction)
    speed = _speed(movement)
    if not math.isfinite(speed) or speed <= 0:
        stop(fight.match, bot)
        return
    x, y, z = bot.position
    yaw = round(math.atan2(direction.x, direction.z) * 65536 / (2 * math.pi))
    mover = Mover(bot.position, yaw, fight._collision(), mover_data(bot.body))
    destination = (x + direction.x, y, z + direction.z)
    result = mover.step_pose(
        Command(fight.tick, yaw=yaw), combat.TICK, fight.tick, move_to=(destination, speed))
    query = instance.graph.node(100)
    radius = expr.to_float(movement.evaluate(query.field("m_radius"), watch=False))
    offset = movement.evaluate(query.field("m_05BE62DD"), watch=False)
    hit = fight.ice_walls.sweep(bot.position, mover.position, radius=radius, y_offset=offset.y)
    if hit is not None:
        point, piece = hit
        mei_wall.clip_mover(mover, point)
        if not fight.ice_walls.hostile(bot, piece) or not fight.ice_walls.marked(piece):
            result = False
    bot.position = tuple(mover.position)
    bot.yaw, bot.velocity, bot.moved_tick = float(yaw), mover.velocity, fight.tick
    if mover.airborne:
        from ow174.game.ashe import NpcMotion

        stop(fight.match, bot)
        fight.impulses.npcs[bot.entity] = NpcMotion(bot, mover, fight.tick)
    elif result is False:
        bot.velocity = (0.0, 0.0, 0.0)
        _write(instance, 59, 2)


def _source(world, state):
    if state.instance.graph.index != CHARGE or world.hero != HERO:
        return None
    return npc_melee.manual_source(world, state.instance.component)


def _selected_victim(fight, player, victim):
    if victim == player.body:
        return player.body_script is not None
    companion = fight.companions.active.get(player.body)
    return (companion is not None and companion.bot.entity == victim
            and not companion.bot.dead and companion.bot.body_script is not None)


def _companion_contact(fight, bot, state, target):
    actor = fight.companions.actor_for(target.entity)
    if actor is None or actor.owner.entity != bot.attack_target or actor.bot.body_script is None:
        return False
    flag = actor.bot.body_script.component.entity_var(6601, create=False)
    turn = (round(bot.yaw) - actor.charge_yaw + 32768) % 65536 - 32768
    # Static B.O.B. hosts its charge phase outside the 1537 stack that writes 596.
    return (not (flag is not None and flag.value() is True)
            and not (actor.phase == "charge" and abs(turn) > 16384)
            and npc_melee.in_contact(bot, state, 96, target))


def _carry_contact(fight, bot, state):
    if not bot.firing or bot.dead or fight.is_stunned(bot):
        return None
    player = npc_melee.contact_target(bot, state, 96)
    candidates = (target for target in fight._enemies(bot)
                  if ((player is not None and target.entity == player.entity)
                      or _companion_contact(fight, bot, state, target)))
    x, _, z = bot.position
    forward_x, _, forward_z = combat.aim(round(bot.yaw), 0)
    threshold = math.cos(math.radians(state.evaluate(state.node.field("m_A30B383E"), watch=False)))
    nearest = None
    for target in candidates:
        if (immunity.cryo_active(target.owner, target.entity)
                or immunity.fortify_active(target.owner, target.entity)
                or immunity.heavy_carry_active(target.owner, target.entity)
                or fight.barrier_phase(target) is not None):
            continue
        dx, dz = target.feet[0] - x, target.feet[2] - z
        distance = math.hypot(dx, dz)
        if (dx * forward_x + dz * forward_z >= distance * threshold
                and (nearest is None or distance < nearest[0])):
            nearest = distance, target
    return nearest[1] if nearest is not None else None


def track_targets(world, state):
    bot = _source(world, state)
    if bot is None:
        return NotImplemented
    if state.node.pos == 100:
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
        x, y, z = bot.position
        walls = world.match.combat.ice_walls
        hit = walls.cast((x, y + offset.y, z), combat.aim(round(bot.yaw), 0), 0.002, radius=radius)
        if hit is not None and walls.hostile(bot, hit[1]) and walls.marked(hit[1]):
            return (expr.Entity(hit[1].entity),)
        return ()
    if state.node.pos == 85:
        # The authored Cryo tracker takes live slot 3692. The static capsule
        # stands in for the native frozen collision proxy.
        return tuple(expr.Entity(target.entity) for target in world.match.combat._enemies(bot)
                     if immunity.cryo_active(target.owner, target.entity)
                     and npc_melee.in_contact(bot, state, 85, target))
    if state.node.pos != 96:
        return NotImplemented
    fight = world.match.combat
    carry = _state(state.instance, 158)
    victim = expr.to_entity(_read(state.instance, 536)) if carry is not None and carry.active else 0
    target = (next((item for item in fight._enemies(bot) if item.entity == victim), None)
              if victim else _carry_contact(fight, bot, state))
    if (target is None or immunity.cryo_active(target.owner, target.entity)
            or immunity.fortify_active(target.owner, target.entity)):
        return ()
    # A barrier applied during an existing static carry is handled at impact.
    return (expr.Entity(target.entity),)


def modify_health(world, state, elapsed_ms):
    bot = _source(world, state)
    if bot is None or state.node.pos not in (29, 102):
        return NotImplemented
    if elapsed_ms or not bot.firing or bot.dead:
        return False
    victim = expr.to_entity(state.evaluate(state.node.field("m_FA3ECC43"), watch=False))
    fight = world.match.combat
    settings = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    if not math.isfinite(amount) or amount <= 0:
        return False
    if state.node.pos == 102:
        piece = fight.ice_walls.pieces.get(victim)
        if piece is not None and fight.ice_walls.marked(piece):
            fight.ice_walls.damage(piece, bot, amount)
        return False
    target = next((item for item in fight._enemies(bot) if item.entity == victim), None)
    script = getattr(target.owner, "body_script", None) if target is not None else None
    if script is None or getattr(script, "retired", False) or _effect(script.component, bot.entity) is None:
        return False
    loadout = state.evaluate(settings["m_loadout"], watch=False)
    barrier = fight.barrier_phase(target)
    if barrier is not None:
        fight.damage_personal_barrier(target.entity, barrier, bot, amount)
    else:
        fight._damage(bot, target, amount, False, getattr(loadout, "guid", None), fight.tick)
    return False


def _effect(component, source):
    return next((item for item in component.instances.values()
                 if item.graph.index == VICTIM and item.cached_source_entity == source), None)


def retire_victim(component):
    for item in tuple(component.instances.values()):
        if item.graph.index == VICTIM:
            component.destroy(item)


def sync_victim(match, bot, component=None):
    """Mirror the authored carry state onto the selected player or companion."""
    instance = _instance(component) if component is not None else None
    if component is not None and (instance is None or (not bot.firing and not _read(instance, 59))):
        return
    if instance is not None and _read(instance, 59) == 2 and bot.entity not in match.combat.impulses.npcs:
        bot.velocity = (0.0, 0.0, 0.0)
    state = _state(instance, 158) if instance is not None else None
    victim = expr.to_entity(_read(instance, 536)) if state is not None and state.active else 0
    candidates = [(p, p.body, p) for p in match.players]
    candidates.extend((actor.bot, actor.bot.entity, actor.owner)
                      for actor in match.combat.companions.active.values())
    for owner, entity, controller in candidates:
        script = owner.body_script
        if script is None or getattr(script, "retired", False) or script.component.fault is not None:
            continue
        body = script.component
        current = _effect(body, bot.entity)
        vitals = match.combat.vitals.get(entity)
        if (entity == victim and controller.entity == bot.attack_target and bot.firing
                and not controller.gone and not body.owner_dead and (vitals is None or not vitals.dead)):
            if current is None:
                resource = state.node.field("m_script")
                definition = graph.graph(expr.guid_of(resource["m_graph"]) & 0xFFFFFFFFFFFF)
                if definition is not None and definition.index == VICTIM:
                    body.play_loaded_graph(definition, source=bot.entity, instigator=bot.entity)
                    body.run_now()
        elif current is not None:
            body.destroy(current)


def stop(match, bot):
    if bot.hero != HERO:
        return
    script = bot.body_script
    instance = _instance(script.component) if script is not None else None
    if instance is not None:
        ability = _state(instance, 82)
        if ability is not None:
            ability.presses.clear()
            script.component.edges([(ability.buttons()[0], False)])
        if _read(instance, 59) == 1:
            _write(instance, 536, False)
            windup = _state(instance, 137)
            if windup is not None and windup.active:
                windup.end(False)
            movement = _state(instance, 71)
            if movement is not None:
                instance.drop_begins([movement])
            _write(instance, 59, 2)
            bot.velocity = (0.0, 0.0, 0.0)
    sync_victim(match, bot)


def carry_move(match, owner, entity):
    """Resolve a collision-safe approximation of 1407's source-bone attachment."""
    if not immunity.heavy_carry_active(owner, entity):
        return None
    for bot in match.bots:
        if bot.hero != HERO or bot.dead or not bot.firing or bot.body_script is None:
            continue
        instance = _instance(bot.body_script.component)
        state = _state(instance, 158) if instance is not None else None
        if (state is None or not state.active or expr.to_entity(_read(instance, 536)) != entity
                or _effect(owner.body_script.component, bot.entity) is None):
            continue
        dx, _, dz = combat.aim(round(bot.yaw), 0)
        x, y, z = bot.position
        return ((x + dx, y, z + dz), 20.0)  # reconstructed attachment offset and catch-up speed
    return None
