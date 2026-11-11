"""Manual Heavy Assault 1408 drive and contacts on the static combat host."""

import math

from ow174.game import defenses, phase, reinhardt_charge
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr

BODY = 0x0400000000001EBB
GRAPH = 0x1408
DRIVE_SPEED = 16.5  # reconstruction from Reinhardt's shared drive class


def instance_of(bot):
    if bot.body != BODY or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def finish(fight, bot, instance):
    if instance is not None:
        instance.write((expr.INSTANCE, 59), 0)
        instance.write((expr.INSTANCE, 562), ())
        instance.write((expr.INSTANCE, 536), None)
        instance.component.run_now()
    fight.charge_contacts.pop(bot.entity, None)
    bot.velocity = (0.0, 0.0, 0.0)


def advance(fight, bot, seconds, *, allowed):
    from ow174.game import combat

    instance = instance_of(bot)
    windup = instance.states.get(0) if instance is not None else None
    drive = instance.states.get(3) if instance is not None else None
    tracker = instance.states.get(48) if instance is not None else None
    counter = instance.states.get(70) if instance is not None else None
    if allowed and reinhardt_charge.counter(fight, bot, counter, fight._enemies(bot)):
        return
    if (drive is None or not drive.active or tracker is None or not tracker.active
            or bot.dead or not allowed or fight.is_stunned(bot)):
        cast_active = ((windup is not None and windup.active) or (drive is not None and drive.active))
        if bot.entity in fight.charge_contacts or (not allowed and cast_active):
            finish(fight, bot, instance)
        return
    contact = fight.charge_contacts.get(bot.entity)
    if contact is None or contact.activation != drive.counter:
        heading = bot.yaw * math.tau / 65536
        contact = fight.charge_contacts[bot.entity] = reinhardt_charge.Contact(drive.counter, heading)
    elif contact.pinned is None and expr.truthy(instance.context().var(expr.INSTANCE, 562)):
        finish(fight, bot, instance)
        return
    bot.yaw = contact.heading * 65536 / math.tau
    forward = (math.sin(contact.heading), 0, math.cos(contact.heading))
    start = tuple(bot.position)
    data = mover_data(bot.body)
    mover = Mover(start, world=fight._collision(), data=data)
    end = combat._along(start, forward, DRIVE_SPEED * seconds)
    blockers = phase.cryo_blockers(fight.match.players, tick=fight.tick)
    mover._move_to(seconds, end, DRIVE_SPEED, blockers)
    bot.position = tuple(mover.position)
    bot.velocity = tuple((axis - old) / seconds for axis, old in zip(bot.position, start, strict=True))
    bot.moved_tick = fight.tick
    if reinhardt_charge.counter(fight, bot, counter, fight._enemies(bot)):
        return

    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    if isinstance(offset, expr.Vec3) and radius > 0:
        _contacts(fight, bot, instance, contact, start, forward, offset, radius)
    blocked = math.dist(bot.position, end) > .03
    target = next((t for t in fight._enemies(bot) if t.entity == contact.pinned), None)
    if target is not None:
        victim = target.owner.mover
        destination = combat._along(bot.position, forward, data.stand_radius + target.radius + .02)
        victim._move_to(seconds, destination, math.dist(victim.position, destination) / seconds + 1, blockers)
        if victim.history and victim.history[-1].frame == fight.tick:
            victim.history[-1] = victim.snapshot(fight.tick)
        blocked |= math.dist(victim.position, destination) > .03
    elif contact.pinned is not None:
        finish(fight, bot, instance)
        return
    if blocked:
        if target is not None:
            reinhardt_charge.damage(fight, bot, target, instance, 11, fight.tick)
        finish(fight, bot, instance)


def _contacts(fight, bot, instance, contact, start, forward, offset, radius):
    from ow174.game import combat

    def centre(feet):
        return combat._along((feet[0], feet[1] + offset.y, feet[2]), forward, offset.z)

    a, b = centre(start), centre(bot.position)
    tracker = instance.state(48)
    cone = math.radians(expr.to_float(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False))) / 2
    alignment = math.cos(cone)
    for target in sorted(fight._enemies(bot), key=lambda t: math.dist(start, t.feet)):
        if (target.entity in contact.hit or getattr(target.owner, "mover", None) is None
                or phase.protection(target.owner) is not None):
            continue
        direction = (target.feet[0] - start[0], 0, target.feet[2] - start[2])
        frontal = combat._dot(direction, forward) >= alignment * math.hypot(*direction)
        c, d = target.segment()
        if (combat.segment_pair_distance(a, b, c, d) > radius + target.radius
                or not fight.combat_visible(a, c, d, target.radius, exclude=bot)):
            if (not frontal and not reinhardt_charge.pinned(fight, target.entity)
                    and fight._melee_bubble(bot, a, target, ((a, b, radius),))):
                contact.hit.add(target.entity)
                reinhardt_charge.damage(fight, bot, target, instance, 13, fight.tick)
            continue
        contact.hit.add(target.entity)
        if (contact.pinned is None and not fight.is_stunned(target.owner)
                and defenses.fortify_state(target.owner) is None and not defenses.invulnerable(target.owner)
                and frontal):
            contact.pinned = target.entity
            instance.write((expr.INSTANCE, 562), (expr.Entity(target.entity),))
            fight._interrupt(target, preserve_zarya_barriers=True)
        elif reinhardt_charge.damage(fight, bot, target, instance, 13, fight.tick):
            if not fight.vitals_of(target).dead:
                modify = instance.state(13).node.field("m_modifyHealth")
                horizontal = expr.to_float(instance.evaluate(modify["m_E33EFEE1"]))
                vertical = expr.to_float(instance.evaluate(modify["m_583C208B"]))
                fight.radial_impulse(target, start, horizontal, vertical)
