"""Scoped 007D charge drive and capture using the existing character solver."""

import math
from dataclasses import dataclass, field

from ow174.game import bots, defenses, phase, reinhardt_barrier
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr

GRAPH = 0x007D


@dataclass
class Contact:
    activation: int
    heading: float
    deadline: int | None = None
    pinned: int | None = None
    hit: set[int] = field(default_factory=set)


def instance_of(player):
    script = getattr(player, "body_script", None)
    if script is None or getattr(script, "hero", None) != reinhardt_barrier.HERO or script.retired:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)


def pinned(fight, entity):
    return any(contact.pinned == entity for contact in fight.charge_contacts.values())


def retire(fight, entity):
    fight.charge_contacts.pop(entity, None)
    for contact in fight.charge_contacts.values():
        if contact.pinned == entity:
            contact.pinned = None


def sync(fight):
    for player in fight.match.players:
        if player.body not in fight.charge_contacts:
            continue
        instance = instance_of(player)
        running = instance.states.get(3) if instance is not None else None
        available = not (player.gone or fight.match.ended or not player.spawned
                         or player.client is None or fight.is_dead(player))
        contact = fight.charge_contacts[player.body]
        expired = contact.deadline is not None and fight.tick >= contact.deadline
        if running is not None and running.active and available and not expired:
            continue
        if running is not None and running.active and available:
            stop(fight, player)
        else:
            fight.charge_contacts.pop(player.body, None)
            player.mover.velocity = (0.0, player.mover.velocity[1], 0.0)
        if available and running is not None and not fight.is_stunned(player):
            target = next((t for t in fight._enemies(player) if t.entity == contact.pinned), None)
            if target is not None:
                impulse(fight, player, target, instance, 49, contact.heading)


def drive(fight, player, command, seconds):
    instance = instance_of(player)
    state = instance.states.get(3) if instance is not None else None
    if state is None or not state.active or fight.is_stunned(player):
        fight.charge_contacts.pop(player.body, None)
        return None
    wanted = command.yaw * math.tau / 65536
    contact = fight.charge_contacts.get(player.body)
    if contact is None or contact.activation != state.counter:
        from ow174.game.combat import TICK

        chase = instance.states.get(9)
        clock = instance.graph.node(26)
        end = expr.to_float(state.evaluate(clock.field("m_destination"), watch=False))
        elapsed = expr.to_float(chase.cur) if chase is not None and chase.active else 0.0
        deadline = fight.tick + math.ceil(max(0.0, end - elapsed) / TICK)
        contact = fight.charge_contacts[player.body] = Contact(state.counter, wanted, deadline)
    delta = (wanted - contact.heading + math.pi) % math.tau - math.pi
    limit = math.radians(expr.to_float(instance.find_var(0, 34534).value())) * seconds
    contact.heading += max(-limit, min(limit, delta))
    speed = expr.to_float(instance.find_var(0, 7726).value()) * 60
    if speed <= 0:
        return None
    return (math.sin(contact.heading), 0.0, math.cos(contact.heading)), speed


def damage(fight, player, target, instance, index, frame):
    state = instance.state(index)
    payload = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(payload.get("m_amount"), watch=False))
    loadout = state.evaluate(payload.get("m_loadout"), watch=False)
    return fight._damage(player, target, amount, False,
                         loadout.guid if isinstance(loadout, expr.Asset) else None, frame)


def impulse(fight, player, target, instance, index, heading):
    state = instance.state(index)
    payload = state.node.field("m_modifyHealth")
    horizontal = expr.to_float(state.evaluate(payload.get("m_E33EFEE1"), watch=False))
    vertical = expr.to_float(state.evaluate(payload.get("m_583C208B"), watch=False))
    x, y, z = player.mover.position
    offset = 5.0 if index == 15 else 2.0
    origin = (x - math.sin(heading) * offset, y, z - math.cos(heading) * offset)
    fight.radial_impulse(target, origin, horizontal, vertical)


def stop(fight, player, *, impact=False, frame=None):
    contact = fight.charge_contacts.pop(player.body, None)
    instance = instance_of(player)
    if instance is None:
        return
    windup, running = instance.states.get(0), instance.states.get(3)
    if contact is None and not any(state is not None and state.active for state in (windup, running)):
        return
    if impact and contact is not None and contact.pinned is not None:
        target = next((t for t in fight._enemies(player) if t.entity == contact.pinned), None)
        if target is not None:
            damage(fight, player, target, instance, 12, frame)
    player.mover.velocity = (0.0, player.mover.velocity[1], 0.0)
    instance.write((0, 59), 0)
    instance.component.run_now()


def counter_duration(script, *, heavy=False):
    """Read the authored inline 224E knockdown used for counter contacts."""
    for instance in script.component.instances.values():
        if heavy and instance.graph.index != 0x1408:
            continue
        for node in instance.graph.nodes:
            if node is None or node.cls != "STUStatescriptActionPlayScript":
                continue
            inline = (node.field("m_69F4B52C") or {}).get("m_6525E98B") or {}
            if expr.guid_of(inline.get("m_graph")) != 0x058000000000224E:
                continue
            values = {expr.guid_of(row.get("m_0D09D2D9")): row.get("m_value")
                      for row in inline.get("m_1EB5A024") or () if isinstance(row, dict)}
            kind = instance.evaluate(values.get(0x0D80000000006BD6))
            counter = node.pos == 132 if heavy else expr.truthy(
                instance.evaluate(values.get(0x0D80000000007312))
            )
            if kind == expr.Asset(0x0D80000000006BBE) and counter:
                return expr.to_float(instance.evaluate(values.get(0x0D800000000000D7)))
    return 0.0


def counter(fight, actor, tracker, enemies):
    """Resolve an authored counter tracker before ordinary capture or bump."""
    from ow174.game.combat import segment_distance

    if tracker is None or not tracker.active:
        return False
    local = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    cone = expr.to_float(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False))
    if not isinstance(local, expr.Vec3) or radius <= 0:
        return False
    heavy = isinstance(actor, bots.Bot)
    yaw = (actor.yaw if heavy else actor.mover.yaw) * math.tau / 65536
    sin, cos = math.sin(yaw), math.cos(yaw)
    x, y, z = actor.position if heavy else actor.mover.position
    centre = (x + cos * local.x + sin * local.z, y + local.y, z - sin * local.x + cos * local.z)
    for target in enemies:
        script = getattr(target.owner, "body_script", None)
        if (isinstance(target.owner, bots.Bot) or script is None or script.retired
                or fight.is_stunned(target.owner) or phase.protection(target.owner) is not None
                or (heavy and script.hero != reinhardt_barrier.HERO)):
            continue
        flag = script.component.entity_var(596, create=False)
        if flag is None or not expr.truthy(flag.value()):
            continue
        facing = target.owner.mover.yaw * math.tau / 65536
        if math.cos(facing - yaw) >= 0:
            continue
        a, b = target.segment()
        if segment_distance(centre, a, b) > radius + target.radius:
            continue
        dx, dz = target.feet[0] - centre[0], target.feet[2] - centre[2]
        distance = math.hypot(dx, dz)
        if distance:
            alignment = (dx * sin + dz * cos) / distance
            angle = math.acos(max(-1.0, min(1.0, alignment)))
            padding = math.asin(min(1.0, target.radius / distance))
            if angle > math.radians(cone) / 2 + padding:
                continue
        if not fight.combat_visible(centre, a, b, target.radius, exclude=actor):
            continue
        duration = counter_duration(actor.body_script, heavy=heavy)
        opposing_duration = counter_duration(script)
        own = next((candidate for candidate in fight._targets() if candidate.owner is actor), None)
        if duration > 0 and opposing_duration > 0 and own is not None:
            fight.stun(own, duration)
            fight.stun(target, opposing_duration)
            return True
    return False


def advance(fight, player, command, start, forward, seconds, blockers):
    """Acquire one frontal victim and carry it through the actual collision solver."""
    from ow174.game.combat import _along, segment_pair_distance

    contact = fight.charge_contacts.get(player.body)
    instance = instance_of(player)
    if contact is None or instance is None:
        return
    radius = expr.to_float(instance.find_var(0, 9149).value())
    width = expr.to_float(instance.find_var(0, 11861).value())
    feet = tuple(player.mover.position)
    a = (start[0], start[1] + 1, start[2])
    b = (feet[0], feet[1] + 1, feet[2])
    tip = _along(b, forward, radius)
    enemies = sorted(fight._enemies(player), key=lambda t: (math.dist(feet, t.feet), t.entity))
    run = instance.states.get(40)
    if run is not None and run.active and counter(fight, player, instance.states.get(74), enemies):
        return
    for target in enemies:
        if target.entity in contact.hit or phase.protection(target.owner) is not None:
            continue
        relative = tuple(target.feet[k] - start[k] for k in range(3))
        if sum(relative[k] * forward[k] for k in range(3)) < 0:
            continue
        c, d = target.segment()
        if (segment_pair_distance(a, b, c, d) > radius + target.radius
                or not fight.combat_visible(a, c, d, target.radius, exclude=player)):
            # Reserve frontal and carried targets for the existing capture and counter paths.
            lateral = abs(relative[0] * forward[2] - relative[2] * forward[0])
            if (lateral > width + target.radius and not pinned(fight, target.entity)
                    and fight._melee_bubble(player, a, target, ((a, b, radius),))):
                contact.hit.add(target.entity)
                damage(fight, player, target, instance, 15, command.frame)
            continue
        contact.hit.add(target.entity)
        frontal = segment_pair_distance(a, tip, c, d) <= width + target.radius
        if frontal and isinstance(target.owner, bots.Bot) and target.owner.body_script is not None:
            slot = target.owner.body_script.component.entity_var(6601, create=False)
            if slot is not None and expr.truthy(slot.value()):
                stop(fight, player, impact=True, frame=command.frame)
                return
        if (frontal and contact.pinned is None and not fight.is_stunned(target.owner)
                and not defenses.invulnerable(target.owner) and defenses.fortify_state(target.owner) is None):
            contact.pinned = target.entity
            fight._interrupt(target, preserve_zarya_barriers=True, tick=fight.match.tick)
        elif damage(fight, player, target, instance, 15, command.frame) and not fight.vitals_of(target).dead:
            impulse(fight, player, target, instance, 15, contact.heading)
    target = next((t for t in enemies if t.entity == contact.pinned), None)
    if target is None or fight.vitals_of(target).dead:
        contact.pinned = None
        return
    separation = player.mover.data.stand_radius + target.radius + .02
    destination = _along(feet, forward, separation)
    owner = target.owner
    mover = owner.mover if not isinstance(owner, bots.Bot) else Mover(
        target.feet, world=fight._collision(), data=mover_data(owner.body),
    )
    previous = tuple(mover.position)
    mover._move_to(seconds, destination, math.dist(previous, destination) / seconds + 1, blockers)
    if isinstance(owner, bots.Bot):
        owner.position = tuple(mover.position)
        owner.velocity = mover.velocity
        owner.impulse_mover = None
        owner.moved_tick = fight.match.tick
    elif mover.history and mover.history[-1].frame == command.frame:
        mover.history[-1] = mover.snapshot(command.frame)
    if math.dist(mover.position, destination) > .03:
        stop(fight, player, impact=True, frame=command.frame)
