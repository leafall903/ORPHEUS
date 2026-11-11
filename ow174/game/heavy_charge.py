"""Manual Heavy Assault 1408 drive and contacts on the static combat host."""

import math

from ow174.game import defenses
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr

BODY = 0x0400000000001EBB
GRAPH = 0x1408
DRIVE_SPEED = 16.5  # reconstruction from Reinhardt's shared drive class


def instance_of(bot):
    if bot.body != BODY or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def counter_tracker(bot):
    instance = instance_of(bot) if bot.attack_action == "charge" else None
    tracker = instance.states.get(70) if instance is not None else None
    return tracker if tracker is not None and tracker.active else None


def ready_for_input(bot):
    instance = instance_of(bot)
    cooldown = instance.vars.get(61) if instance is not None else None
    return cooldown is None or expr.to_float(cooldown.value()) <= 0


def finish(fight, bot, instance):
    if instance is not None:
        instance.write((expr.INSTANCE, 59), 0)
        instance.write((expr.INSTANCE, 562), ())
        instance.write((expr.INSTANCE, 536), None)
        fight._drain_charge_end(instance)
    fight._release_charge(bot.entity)
    bot.velocity = (0.0, 0.0, 0.0)


def advance(fight, bot, seconds):
    from ow174.game import combat

    instance = instance_of(bot)
    if fight._charge_counter(bot):
        return
    drive = instance.states.get(3) if instance is not None else None
    tracker = instance.states.get(48) if instance is not None else None
    if (drive is None or not drive.active or tracker is None or not tracker.active
            or bot.dead or fight.controlled(bot.entity)):
        if bot.entity in fight.charge_contacts:
            finish(fight, bot, instance)
        return
    contact = fight.charge_contacts.get(bot.entity)
    if contact is None or contact.activation != drive.counter:
        contact = combat.ChargeContact(drive.counter)
        fight.charge_contacts[bot.entity] = contact
        fight.charge_drives[bot.entity] = (drive.counter, bot.yaw)
    elif contact.pinned is None and expr.truthy(instance.context().var(expr.INSTANCE, 562)):
        finish(fight, bot, instance)
        return
    if contact.pinned is not None and not fight.match.game_map.free_for_all:
        captured = fight._target_entity(contact.pinned)
        if captured is not None and captured.team == bot.team:
            finish(fight, bot, instance)
            return
    _, bot.yaw = fight.charge_drives[bot.entity]
    forward = combat.aim(round(bot.yaw), 0)
    start = tuple(bot.position)
    data = mover_data(bot.body)
    mover = Mover(start, world=fight._collision(), data=data)
    end = combat._along(start, forward, DRIVE_SPEED * seconds)
    mover._move_to(seconds, end, DRIVE_SPEED)
    bot.position, _, ice_blocker = fight.walls.constrain(
        start, tuple(mover.position), data.stand_radius, data.stand_height,
    )
    bot.velocity = tuple((axis - old) / seconds for axis, old in zip(bot.position, start, strict=True))
    bot.moved_tick = fight.tick
    if fight._charge_counter(bot):
        return
    if _cryo_impact(fight, bot, instance, start, forward):
        return
    impact_tracker = instance.states.get(51)
    if fight._charge_deployable_impact(bot, impact_tracker, contact, start, bot.position, forward, ice_blocker):
        bot.velocity = (0.0, 0.0, 0.0)
        return

    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    if isinstance(offset, expr.Vec3) and radius > 0:
        _contacts(fight, bot, instance, contact, start, forward, offset, radius)
    blocked = ice_blocker is not None or math.dist(bot.position, end) > .03
    target = fight._target_entity(contact.pinned)
    if target is not None:
        victim = target.owner.mover
        previous = tuple(victim.position)
        destination = combat._along(bot.position, forward, data.stand_radius + target.radius + .02)
        victim._move_to(seconds, destination, math.dist(previous, destination) / seconds + 1)
        blocked |= fight.walls.move_player(target.owner, previous) is not None
        if victim.history and victim.history[-1].frame == fight.tick:
            victim.history[-1] = victim.snapshot(fight.tick)
        blocked |= math.dist(victim.position, destination) > .03
    elif contact.pinned is not None:
        finish(fight, bot, instance)
        return
    if blocked:
        if target is not None:
            fight._charge_damage(bot, target, instance, 11, fight.tick)
        finish(fight, bot, instance)


def _has_cryo_flag(target):
    script = getattr(target.owner, "body_script", None)
    flag = script.component.entity_var(3692, create=False) if script is not None else None
    return flag is not None and expr.truthy(flag.value())


def _cryo_impact(fight, bot, instance, start, forward):
    from ow174.game import combat

    tracker = instance.states.get(42)
    if tracker is None or not tracker.active:
        return False
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    if not isinstance(offset, expr.Vec3) or radius <= 0:
        return False
    a, b = (combat._along((x, y + offset.y, z), forward, offset.z) for x, y, z in (start, bot.position))
    for target in fight._enemies(bot):
        if not _has_cryo_flag(target) or combat._dot(combat._sub(target.feet, a), forward) < 0:
            continue
        c, d = target.segment()
        if (combat.segment_pair_distance(a, b, c, d) > radius + target.radius
                or fight.walls.blocked(a, c)
                or not combat.capsule_visible(fight._collision(), a, c, d, target.radius)):
            continue
        fight._end_charge_on_tracker_hit(bot.entity, tracker, target.entity)
        bot.velocity = (0.0, 0.0, 0.0)
        return True
    return False


def _contacts(fight, bot, instance, contact, start, forward, offset, radius):
    from ow174.game import combat

    def centre(feet):
        return combat._along((feet[0], feet[1] + offset.y, feet[2]), forward, offset.z)

    a, b = centre(start), centre(bot.position)
    sight_origin = (start[0], a[1], start[2])
    tracker = instance.state(48)
    cone = math.radians(expr.to_float(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False))) / 2
    for target in sorted(fight._enemies(bot), key=lambda t: math.dist(start, t.feet)):
        key = (target.entity, fight.replacement_ticks.get(target.entity))
        if key in contact.hit or getattr(target.owner, "mover", None) is None or _has_cryo_flag(target):
            continue
        c, d = target.segment()
        if (combat.segment_pair_distance(a, b, c, d) > radius + target.radius
                or fight.walls.blocked(sight_origin, c)
                or not combat.capsule_visible(fight._collision(), a, c, d, target.radius)):
            continue
        direction = (target.feet[0] - start[0], 0, target.feet[2] - start[2])
        length = math.hypot(*direction)
        direction = tuple(v / length for v in direction) if length else forward
        contact.hit.add(key)
        if (contact.pinned is None and not fight.pinned(target.entity)
                and not defenses.control_immune(target.owner)
                and combat._dot(direction, forward) >= math.cos(cone)):
            contact.pinned = target.entity
            instance.write((expr.INSTANCE, 562), (expr.Entity(target.entity),))
            fight.control(target, bot.entity, "pin", 3.2)
        else:
            fight._charge_damage(bot, target, instance, 13, fight.tick)
            modify = instance.state(13).node.field("m_modifyHealth")
            horizontal = expr.to_float(instance.evaluate(modify["m_E33EFEE1"]))
            vertical = expr.to_float(instance.evaluate(modify["m_583C208B"]))
            velocity = (direction[0] * horizontal, vertical, direction[2] * horizontal)
            fight.knockback(target, velocity, fight.tick)
