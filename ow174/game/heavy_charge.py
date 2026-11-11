"""Manual Heavy Assault 1408 Charge adapted from the Zarya lane.

Placement, cone and transport are reconstructed. The authored 1408
drive, target-search fields and impact states supply the selected inputs.
"""

import math
from dataclasses import dataclass, field

from ow174.game import defenses, reinhardt_charge
from ow174.game.contacts import closest_segments
from ow174.game.mover import FELL, Mover, mover_data
from ow174.game.script import expr

BODY = 0x0400000000001EBB
GRAPH = 0x1408
DRIVE_SPEED = 16.5  # reconstruction from Reinhardt's shared drive class


@dataclass
class Contact:
    activation: int
    heading: float
    pinned: int | None = None
    hit: set[int] = field(default_factory=set)


def instance_of(bot):
    if bot.body != BODY or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def finish(fight, bot, instance):
    if instance is not None and not instance.stopped:
        instance.write((expr.INSTANCE, 59), 0)
        instance.write((expr.INSTANCE, 562), ())
        instance.write((expr.INSTANCE, 536), None)
        instance.component.run_now()
    fight.heavy_charges.pop(bot.entity, None)
    bot.velocity = (0.0, 0.0, 0.0)


def advance(fight, bot, seconds, *, allowed):
    from ow174.game import combat

    instance = instance_of(bot)
    drive = instance.states.get(3) if instance is not None else None
    tracker = instance.states.get(48) if instance is not None else None
    if (drive is None or not drive.active or tracker is None or not tracker.active
            or bot.dead or not allowed or fight.is_stunned(bot)):
        if bot.entity in fight.heavy_charges or drive is not None and drive.active:
            finish(fight, bot, instance)
        return
    contact = fight.heavy_charges.get(bot.entity)
    if contact is None or contact.activation != drive.counter:
        heading = bot.yaw * math.tau / 65536
        contact = fight.heavy_charges[bot.entity] = Contact(drive.counter, heading)
    elif contact.pinned is None and expr.truthy(instance.context().var(expr.INSTANCE, 562)):
        finish(fight, bot, instance)
        return
    bot.yaw = contact.heading * 65536 / math.tau
    forward = (math.sin(contact.heading), 0, math.cos(contact.heading))
    start = tuple(bot.position)
    data = mover_data(bot.body)
    mover = Mover(start, world=fight._collision(), data=data)
    end = combat._along(start, forward, DRIVE_SPEED * seconds)
    mover._move_to(seconds, end, DRIVE_SPEED)
    bot.position = tuple(mover.position)
    bot.velocity = tuple((axis - old) / seconds for axis, old in zip(bot.position, start, strict=True))
    bot.moved_tick = fight.tick
    fight.walls.move_bot(bot, start)

    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    if isinstance(offset, expr.Vec3) and radius > 0:
        _contacts(fight, bot, instance, contact, start, forward, offset, radius)
    blocked = math.dist(bot.position, end) > .03
    target = next((t for t in fight._enemies(bot) if t.entity == contact.pinned), None)
    if target is not None:
        victim = target.owner.mover
        destination = combat._along(bot.position, forward, data.stand_radius + target.radius + .02)
        victim._move_to(seconds, destination, math.dist(victim.position, destination) / seconds + 1)
        if victim.history and victim.history[-1].frame == fight.tick:
            victim.history[-1] = victim.snapshot(fight.tick)
        blocked |= math.dist(victim.position, destination) > .03
    elif contact.pinned is not None:
        finish(fight, bot, instance)
        return
    if blocked:
        if target is not None:
            reinhardt_charge.impact(fight, bot, target, instance, 11, fight.tick)
        finish(fight, bot, instance)


def _contacts(fight, bot, instance, contact, start, forward, offset, radius):
    from ow174.game import combat

    def centre(feet):
        return combat._along((feet[0], feet[1] + offset.y, feet[2]), forward, offset.z)

    a, b = centre(start), centre(bot.position)
    tracker = instance.state(48)
    cone = math.radians(expr.to_float(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False))) / 2
    for target in sorted(fight._enemies(bot), key=lambda t: math.dist(start, t.feet)):
        if (target.entity in contact.hit or getattr(target.owner, "mover", None) is None
                or defenses.protection(target.owner)[0]):
            continue
        c, d = target.segment()
        if (math.dist(*closest_segments(a, b, c, d)) > radius + target.radius
                or not combat.capsule_visible(fight._collision(), a, c, d, target.radius, walls=fight.walls)):
            continue
        direction = (target.feet[0] - start[0], 0, target.feet[2] - start[2])
        length = math.hypot(*direction)
        direction = tuple(v / length for v in direction) if length else forward
        contact.hit.add(target.entity)
        if (contact.pinned is None and not fight.is_stunned(target.owner)
                and not defenses.control_immune(target.owner)
                and not fight.hooks.controlled(target.entity) and not fight.traps.rooted(target.entity, fight.tick)
                and target.entity not in fight.gravitons.pulled
                and sum(x * y for x, y in zip(direction, forward, strict=True)) >= math.cos(cone)):
            contact.pinned = target.entity
            instance.write((expr.INSTANCE, 562), (expr.Entity(target.entity),))
            fight.impulses.retire(target.entity, forget=False)
            fight._interrupt(target)
        else:
            vitals = fight.vitals_of(target)
            before = vitals.total
            reinhardt_charge.impact(fight, bot, target, instance, 13, fight.tick)
            if vitals.total < before and not vitals.dead and not defenses.control_immune(target.owner):
                state = instance.state(13)
                modify = state.node.field("m_modifyHealth")
                force = expr.to_float(state.evaluate(modify["m_E33EFEE1"], watch=False))
                lift = expr.to_float(state.evaluate(modify["m_583C208B"], watch=False))
                mover = target.owner.mover
                vx, vy, vz = mover.velocity
                mover.driven = False
                mover.velocity = vx + force * direction[0], vy + lift, vz + force * direction[2]
                if mover.velocity[1] > 0:
                    mover.flags_word |= FELL
                    mover.fall = 0.0
