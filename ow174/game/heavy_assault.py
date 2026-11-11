"""Static host for the Heavy Assault's authored 1408 primary contact search."""

import math

from ow174.game import mei, npc_melee, reinhardt_barrier
from ow174.game.script import expr, graph, runtime
from ow174.game.script.runtime import InstanceContext

HERO = 0x02E00000000001BA
GRAPH = 0x1408
VICTIM_GRAPH = 0x1407


class CaptureSearchContext(InstanceContext):
    def __init__(self, instance, target):
        super().__init__(instance, None)
        self.target = target

    def member(self, entity, var):
        script = self.target.owner.body_script
        if entity == self.target.entity and var == 2024 and script is not None:
            slot = script.component.entity_var(var, create=False)
            if slot is not None and slot.links:
                source = slot.links[-1].state
                child = source.instance
                if (child.graph.index == VICTIM_GRAPH
                        and child.source_entity() == self.instance.component.entity):
                    return slot.value(below=source)
        return super().member(entity, var)


def capture_target(bot):
    if bot.hero != HERO or bot.dead or bot.body_script is None or not bot.spawner.get("manual_spawn"):
        return None
    component = bot.body_script.component
    instance = next((item for item in component.instances.values() if item.graph.index == GRAPH), None)
    if instance is None:
        return None
    state = instance.states.get(instance.graph.node(158).state)
    phase = instance.vars.get(59)
    if state is None or not state.active or phase is None or phase.value() != 1:
        return None
    return expr.to_entity(state.evaluate(state.node.field("m_targets"), watch=False))


def interrupt(bot):
    """Route applied silence through the authored running-phase exit."""
    if bot.hero != HERO or bot.body_script is None:
        return
    component = bot.body_script.component
    instance = next((item for item in component.instances.values() if item.graph.index == GRAPH), None)
    if instance is None:
        return
    phase = instance.vars.get(59)
    if phase is not None and phase.value() == 1:
        exit_node = 173 if capture_target(bot) else 174
        instance.follow(instance.graph.node(exit_node), "m_truePlug")


def sync_captures(fight):
    """Mirror the scoped 1407 victim graph for an active 1408 attachment."""
    wanted = {}
    for bot in fight.match.bots:
        entity = capture_target(bot)
        if not entity or fight.is_stunned(bot):
            continue
        target = next((item for item in fight._enemies(bot) if item.entity == entity), None)
        if target is not None and target.owner in fight.match.players and not mei.protected(target.owner):
            wanted.setdefault(entity, (bot.entity, target))

    for player in fight.match.players:
        script = player.body_script
        if script is None:
            continue
        component = script.component
        desired = wanted.get(component.entity)
        existing = [item for item in component.instances.values() if item.graph.index == VICTIM_GRAPH]
        for item in existing:
            source = item.source_entity()
            if desired is None or desired[0] != source or fight.pins.get(source) != component.entity:
                if fight.pins.get(source) == component.entity:
                    del fight.pins[source]
                component.destroy(item)
            else:
                desired = None
        if desired is not None:
            source, target = desired
            if component.entity in fight.pins.values():
                continue
            definition = graph.graph(VICTIM_GRAPH)
            if definition is None:
                continue
            child = component.create(
                definition, values={(expr.INSTANCE, 27): expr.Entity(source)}, init=False,
            )
            child.set_source_entity(source)
            child.init()
            fight.pins[source] = component.entity
            fight._interrupt(target)


def _special(bot):
    if bot.hero != HERO or not bot.spawner.get("manual_spawn") or bot.body_script is None:
        return None
    return next((item for item in bot.body_script.component.instances.values()
                 if item.graph.index == GRAPH), None)


def replay_pending(bot):
    instance = _special(bot)
    if bot.dead or instance is None:
        return False
    phase = instance.vars.get(59)
    impact = instance.states.get(instance.graph.node(10).state)
    return ((phase is not None and phase.value() == 1)
            or (impact is not None and impact.active)
            or any(event.kind == runtime.BUTTON and event.button == 35 and event.pressed
                   for event in instance.queue))


def drive(bot, tick, seconds, collision, paused_until):
    """Advance the authored running phase against static capsule collision."""
    instance = _special(bot)
    if instance is None or collision is None:
        return False
    component = instance.component
    impact = instance.states.get(instance.graph.node(10).state)
    if impact is not None and impact.active:
        # Interpret the authored transition scalar as elapsed state time in seconds.
        condition = instance.graph.node(15)
        recovery = expr.to_float(instance.evaluate(condition.field("m_AA6E611E")))
        if tick * component.frame_ms - impact.start >= recovery * 1000:
            instance.follow(condition, "m_truePlug", impact)
    node = instance.graph.node(71)
    state = instance.states.get(node.state)
    phase = instance.vars.get(59)
    if state is None or not state.active or phase is None or phase.value() != 1:
        return False
    if bot.last_tick is not None and tick <= bot.last_tick:
        return True
    bot.last_tick = tick
    selected = instance.vars.get(562)
    fight = component.world.combat
    victim = None
    if fight is not None and bot.entity in fight.pins:
        entity = fight.pins[bot.entity]
        target = next((item for item in fight._enemies(bot) if item.entity == entity), None)
        if target is not None and target.owner in fight.match.players:
            victim = target.owner
    if tick < paused_until or (victim is None and selected is not None and selected.value()):
        bot._pause(tick)
        return True
    rows = node.field("m_BC5E91CF")
    velocity = instance.evaluate(rows[0]["m_9B7A63EA"])
    if not isinstance(velocity, expr.Vec3) or not any(velocity):
        bot._pause(tick)
        return True

    from ow174.game.mover import Mover, mover_data, quantise

    start = bot.position
    mover = Mover(start, round(bot.yaw), collision, mover_data(bot.body))
    carrier_blocked = _sweep(mover, velocity, tick, seconds)
    if victim is None:
        bot.position = tuple(mover.position)
    else:
        pose = victim.mover
        probe = Mover(pose.position, pose.yaw, collision, pose.data, pose.category)
        victim_blocked = _sweep(probe, velocity, tick, seconds)
        # Both bodies travel only as far as the more constrained capsule.
        speed = math.hypot(*velocity)
        direction = tuple(axis / speed for axis in velocity)
        stride = min(speed * seconds, _progress(mover.position, start, direction),
                     _progress(probe.position, pose.position, direction))
        displacement = tuple(axis * stride for axis in direction)
        bot.position = tuple(a + b for a, b in zip(start, displacement, strict=True))
        pose.position = [quantise(a + b) for a, b in zip(pose.position, displacement, strict=True)]
        pose.velocity = (0.0, 0.0, 0.0)
        pose.body = None  # reconstruct the engine capsule at the carried feet on its next pose
        if carrier_blocked or victim_blocked:
            # The static wall impact enters the authored damage phase with the captured target intact.
            instance.follow(instance.graph.node(11), "m_truePlug", state)
    bot.velocity = tuple((axis - old) / seconds for axis, old in zip(bot.position, start, strict=True))
    bot.moved_tick = tick
    return True


def _sweep(mover, velocity, tick, seconds):
    from ow174.game.commands import Command
    from ow174.game.mover import TOUCHING

    start = tuple(mover.position)
    destination = tuple(axis + speed * seconds for axis, speed in zip(start, velocity, strict=True))
    outcome = mover.step_pose(Command(tick, yaw=mover.yaw), seconds, tick,
                              move_to=(destination, math.hypot(*velocity)))
    x, y, z = mover.position
    if mover.world.ground(x, z, y + 0.1, y - 0.25, mover.category) is None:
        mover.position[:] = start
        return False
    return outcome is False and bool(mover.flags_word & TOUCHING)


def _progress(position, start, direction):
    return max(0.0, sum((end - begin) * axis
                        for end, begin, axis in zip(position, start, direction, strict=True)))


def native(world, cfg, instance):
    if instance is None or world.hero != HERO or world.match is None or instance.graph.index != GRAPH:
        return NotImplemented
    if (cfg.get("$") == "STU_021D01CD"
            and expr.to_entity(instance.evaluate(cfg.get("m_entity"))) == instance.component.entity):
        direction = instance.vars.get(3192)
        if direction is not None and isinstance(direction.value(), expr.Vec3):
            return direction.value()
    if cfg.get("$") != "STU_AC829876":
        return NotImplemented
    condition = instance.graph.node(96).field("m_filter")["m_condition"]
    if cfg is not condition["m_configVars"][0]:
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    return any(player.has_body and player.spawned and player.body == entity for player in world.match.players)


def targets(world, state):
    if (world.hero != HERO or world.combat is None or state.instance.graph.index != GRAPH
            or state.node.pos not in (32, 85, 96, 100)):
        return NotImplemented
    from ow174.game.combat import capsule_visible, segment_distance

    fight = world.combat
    bot = npc_melee.attacker(fight, state)
    if bot is None or fight.is_stunned(bot):
        return ()
    instance = state.instance
    direction = instance.vars.get(3192)
    forward = direction.value() if direction is not None else None
    offset = instance.evaluate(state.node.field("m_05BE62DD"))
    if not isinstance(forward, expr.Vec3) or not isinstance(offset, expr.Vec3) or not any(forward):
        return ()
    radius = expr.to_float(instance.evaluate(state.node.field("m_radius")))
    angle = expr.to_float(instance.evaluate(state.node.field("m_A30B383E")))
    x, y, z = bot.position
    origin = (x, y + offset.y, z)
    centre = (x + forward.x * offset.z, origin[1], z + forward.z * offset.z)
    minimum_dot = math.cos(math.radians(angle))
    condition = state.node.field("m_filter")["m_condition"]
    found = []
    for target in fight._enemies(bot):
        if target.owner not in fight.match.players or mei.protected(target.owner):
            continue
        if (state.node.pos == 96 and fight.pins.get(bot.entity) != target.entity
                and reinhardt_barrier.body_bubble(fight, target.owner) is not None):
            continue
        dx, dz = target.feet[0] - x, target.feet[2] - z
        if dx * forward.x + dz * forward.z < minimum_dot * math.hypot(dx, dz):
            continue
        a, b = target.segment()
        distance = segment_distance(centre, a, b) - target.radius
        if distance > radius or not capsule_visible(fight._line_collision(), origin, a, b, target.radius):
            continue
        with instance.component.parameter_scope({8: expr.Entity(target.entity)}):
            context = CaptureSearchContext(instance, target) if state.node.pos == 96 else instance.context()
            if expr.truthy(expr.evaluate(condition, context)):
                found.append((distance, target.entity))
    limit = state.node.field("m_C39691D9")
    maximum = expr.to_int(instance.evaluate(limit)) if limit is not None else len(found)
    return tuple(entity for _, entity in sorted(found)[:maximum])


def secondary_damage(world, state, elapsed_ms):
    if (world.hero != HERO or world.combat is None or state.instance.graph.index != GRAPH
            or state.node.pos != 35):
        return NotImplemented
    from ow174.game.combat import damage_tags

    if not elapsed_ms:
        state.heavy_contacts = set()
    fight = world.combat
    bot = npc_melee.attacker(fight, state)
    if bot is None or fight.is_stunned(bot):
        return None
    config = state.node.field("m_modifyHealth")
    selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if not isinstance(selected, tuple):
        return None
    pending = {expr.to_entity(value) for value in selected} - state.heavy_contacts
    if pending:
        amount = expr.to_float(state.evaluate(config["m_amount"], watch=False))
        loadout = expr.guid_of(state.evaluate(config["m_loadout"], watch=False)) or None
        tags = damage_tags(state, config)
        for target in fight._enemies(bot):
            if target.entity in pending:
                state.heavy_contacts.add(target.entity)
                if mei.protected(target.owner) or reinhardt_barrier.protect_body(
                    fight, target, amount, attacker=bot.entity,
                ):
                    continue
                fight._damage(
                    bot, target, amount, False, loadout, fight.tick, world_tick=fight.tick, tags=tags,
                )
                if not fight.vitals_of(target).dead:
                    secondary_impulse(fight, state, target, config)
    return None


def secondary_impulse(fight, state, target, config):
    """Reconstruct the node35 launch through the player's colliding mover."""
    origin = state.evaluate(config["m_C0139FA6"], watch=False)
    if not isinstance(origin, expr.Vec3):
        return
    dx, dz = target.feet[0] - origin.x, target.feet[2] - origin.z
    length = math.hypot(dx, dz)
    if not length:
        return
    horizontal = expr.to_float(state.evaluate(config["m_E33EFEE1"], watch=False))
    upward = expr.to_float(state.evaluate(config["m_583C208B"], watch=False))
    fight.impulses.launch(target, (dx / length * horizontal, upward, dz / length * horizontal))


def entry_damage(world, state):
    if (world.hero != HERO or world.combat is None or state.instance.graph.index != GRAPH
            or state.node.pos not in (29, 102)):
        return
    from ow174.game.combat import damage_tags

    fight = world.combat
    bot = npc_melee.attacker(fight, state)
    if bot is None or fight.is_stunned(bot):
        return
    selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    entities = {expr.to_entity(value) for value in (selected if isinstance(selected, tuple) else (selected,))}
    config = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(config["m_amount"], watch=False))
    loadout = expr.guid_of(state.evaluate(config["m_loadout"], watch=False)) or None
    tags = damage_tags(state, config)
    for target in fight._enemies(bot):
        if target.entity not in entities:
            continue
        if not reinhardt_barrier.protect_body(fight, target, amount, attacker=bot.entity):
            fight._damage(bot, target, amount, False, loadout, fight.tick, world_tick=fight.tick, tags=tags)
