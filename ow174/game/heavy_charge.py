"""Actual Heavy Assault 1408 owner pose bridge for the manual charge graph."""

import math
from dataclasses import dataclass, field

from ow174.game import defensive_casts
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr

BODY, GRAPH, BUTTON = 0x0400000000001EBB, 0x1408, 35


def pose(world, cfg, instance):
    if world.match is None or instance.graph.index != GRAPH:
        return NotImplemented
    query = cfg.get("$")
    if query not in ("STU_71A87E92", "STU_2200A66D", "STU_021D01CD"):
        return NotImplemented
    owner = cfg.get("m_entity") or {}
    if owner.get("$") != "STU_B5A0CAF0":
        return NotImplemented
    bot = next((b for b in world.match.bots if b.entity == instance.component.entity
                and b.body == BODY and not b.dead), None)
    if bot is None:
        return None
    if query == "STU_71A87E92":
        return expr.Vec3(*bot.position)
    from ow174.game.combat import aim

    return expr.Vec3(*aim(round(bot.yaw), 0))

@dataclass
class Drive:
    vector: tuple[float, float, float]
    started: int
    touched: set[int] = field(default_factory=set)
    victim: int | None = None


def instance_of(bot):
    if bot.body != BODY or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def finish(fight, bot):
    drive = fight.heavy_drives.pop(bot.entity, None)
    if drive is not None:
        bot.velocity = (0.0, 0.0, 0.0)
        if fight.pins.get(bot.entity) == drive.victim:
            fight.pins.pop(bot.entity, None)
    instance = instance_of(bot)
    if instance is None:
        return
    phase = instance.states.get(36)
    if phase is None or not phase.active:
        return
    phase.end(False)
    motion = instance.state(33)
    if motion.active:
        motion.end(False)
    instance.drop_begins([motion])
    instance.state(41).stop()
    instance.write((expr.INSTANCE, 562), ())
    instance.write((expr.INSTANCE, 536), None)
    instance.run_node(instance.graph.node(6), None)  # authored phase reset
    instance.run_node(instance.graph.node(45), None)  # authored cooldown assignment
    bot.body_script.component.edges([(BUTTON, False)])
    bot.velocity = (0.0, 0.0, 0.0)


def _damage(fight, bot, instance, index, target, direction):
    state = instance.state(index)
    definition = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(definition["m_amount"], watch=False))
    if not fight._damage(bot, target, amount, False, None, fight.tick):
        return
    if index == 13 and not fight.vitals_of(target).dead:
        horizontal = expr.to_float(state.evaluate(definition.get("m_E33EFEE1"), watch=False))
        vertical = expr.to_float(state.evaluate(definition.get("m_583C208B"), watch=False))
        target.owner.mover.apply_impulse((direction[0] * horizontal, vertical, direction[2] * horizontal))


def _contacts(fight, bot, instance, drive, start, forward):
    from ow174.game import combat

    tracker = instance.states.get(48)
    if tracker is None or not tracker.active:
        return
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    cone = expr.to_float(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False))
    if not isinstance(offset, expr.Vec3) or radius <= 0:
        return

    def centre(feet):
        return combat._along((feet[0], feet[1] + offset.y, feet[2]), forward, offset.z)

    a, b = centre(start), centre(bot.position)
    for target in sorted(fight._enemies(bot), key=lambda t: (math.dist(start, t.feet), t.entity)):
        if (target.entity in drive.touched or getattr(target.owner, "mover", None) is None
                or defensive_casts.invulnerable(target.owner)):
            continue
        c, d = target.segment()
        if (combat.segment_pair_distance(a, b, c, d) > radius + target.radius
                or not combat.capsule_visible(fight._collision(), a, c, d, target.radius)):
            continue
        delta = (target.feet[0] - start[0], 0.0, target.feet[2] - start[2])
        distance = math.hypot(*delta)
        direction = tuple(value / distance for value in delta) if distance else forward
        drive.touched.add(target.entity)
        if (drive.victim is None and target.entity not in fight.pins.values()
                and combat._dot(direction, forward) >= math.cos(math.radians(cone) / 2)):
            drive.victim = target.entity
            fight.pins[bot.entity] = target.entity
            instance.write((expr.INSTANCE, 562), (expr.Entity(target.entity),))
            fight._interrupt(target)
        else:
            _damage(fight, bot, instance, 13, target, direction)


def advance(fight, bot, enabled):
    from ow174.game import combat

    instance = instance_of(bot)
    motion = instance.states.get(33) if instance is not None else None
    if bot.attack_mode != "charge":
        return
    if not enabled or motion is None or not motion.active:
        if not enabled or bot.entity in fight.heavy_drives:
            finish(fight, bot)
        return
    drive = fight.heavy_drives.get(bot.entity)
    if drive is None:
        cfg = motion.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
        vector = motion.evaluate(cfg, watch=False)
        if not isinstance(vector, expr.Vec3) or math.hypot(vector.x, vector.z) <= 0:
            finish(fight, bot)
            return
        drive = fight.heavy_drives[bot.entity] = Drive((vector.x, 0.0, vector.z), fight.tick)
    if (fight.tick - drive.started) * combat.TICK >= 3.0:
        finish(fight, bot)
        bot.firing = False
        return
    if drive.victim is not None and fight.pins.get(bot.entity) != drive.victim:
        finish(fight, bot)
        return
    speed = math.hypot(*drive.vector)
    forward = tuple(value / speed for value in drive.vector)
    start = tuple(bot.position)
    world = fight._collision()
    data = mover_data(bot.body)
    mover = Mover(start, round(bot.yaw), world, data)
    expected = combat._along(start, drive.vector, combat.TICK)
    mover._move_to(combat.TICK, combat._along(start, drive.vector, 1.0), speed)
    bot.position = tuple(mover.position)
    bot.velocity = tuple((b - a) / combat.TICK for a, b in zip(start, bot.position, strict=True))
    bot.moved_tick = fight.tick
    blocked = math.dist(bot.position, expected) > 0.03
    _contacts(fight, bot, instance, drive, start, forward)
    if drive.victim is not None:
        target = next((t for t in fight._enemies(bot) if t.entity == drive.victim), None)
        if (target is None or fight.pins.get(bot.entity) != drive.victim
                or defensive_casts.invulnerable(target.owner)):
            finish(fight, bot)
            return
        victim = target.owner.mover
        destination = combat._along(bot.position, forward, data.stand_radius + target.radius + 0.02)
        distance = math.dist(victim.position, destination)
        victim._move_to(combat.TICK, destination, distance / combat.TICK + 1)
        if victim.history and victim.history[-1].frame == fight.tick:
            victim.history[-1] = victim.snapshot(fight.tick)
        blocked |= math.dist(victim.position, destination) > 0.03
        if blocked:
            _damage(fight, bot, instance, 11, target, forward)
    if blocked:
        finish(fight, bot)
        bot.firing = False
