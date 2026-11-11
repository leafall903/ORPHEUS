"""Scoped 007D Charge movement and contact services."""

import math
from dataclasses import dataclass, field

from ow174.game import bots, fortify, heavy_assault, knockback, mei, phase_defenses, reinhardt_barrier
from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.reinhardt_offense import HERO, segment_pair_distance
from ow174.game.script import expr

GRAPH = 0x007D
REPLAY_LIMIT = 256 + knockback.REPLAY_LIMIT


@dataclass
class Contact:
    activation: tuple
    heading: float
    hit: set = field(default_factory=set)
    pinned: int | None = None
    mover: Mover | None = None


def enter(world, state):
    if (world.hero != HERO or state.instance.component.strict
            or (state.instance.graph.index, state.index) != (GRAPH, 37)):
        return
    if all(state.node.field(key) is None for key in (
        "m_ED97A97C", "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5",
    )):
        state.request_finish()


def instance_for(actor):
    script = getattr(actor, "body_script", None)
    if getattr(script, "hero", None) != HERO:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == GRAPH), None)


def active(instance, index):
    state = instance.states.get(index) if instance is not None else None
    return state if state is not None and state.active else None


def value(instance, key):
    var = instance.vars.get(key)
    return expr.to_float(var.value()) if var is not None else 0.


def target_for(fight, entity):
    if entity is None:
        return None
    return next((target for target in fight._targets()
                 if target.entity == entity and not fight.vitals_of(target).dead), None)


def pinned(fight, entity):
    return any(contact.pinned == entity and fight.pins.get(source) == entity
               for source, contact in fight.rein_charges.items())


def unpin(fight, player, contact):
    if contact.pinned is not None and fight.pins.get(player.body) == contact.pinned:
        fight.pins.pop(player.body, None)
    contact.pinned = contact.mover = None


def release(fight, player):
    contact = fight.rein_charges.pop(player.body, None)
    if contact is not None:
        unpin(fight, player, contact)
        player.mover.velocity = (0., player.mover.velocity[1], 0.)


def _amount(state, cfg):
    return expr.to_float(state.evaluate(cfg, watch=False))


def damage(fight, player, target, instance, index, frame):
    from ow174.game.combat import damage_tags

    state = instance.state(index)
    modify = state.node.field("m_modifyHealth")
    amount = _amount(state, modify["m_amount"]) * fight.outgoing_scale(player.body)
    if reinhardt_barrier.protect_body(fight, target, amount, attacker=player.body):
        return False
    asset = state.evaluate(modify["m_loadout"], watch=False)
    loadout = asset.guid if isinstance(asset, expr.Asset) else None
    before = fight.vitals_of(target).total
    fight._damage(player, target, amount, False, loadout, frame, tags=damage_tags(state, modify),
                  source_body=player.body, source_scaled=True)
    return fight.vitals_of(target).total < before


def impulse(fight, target, instance, index, direction):
    state = instance.state(index)
    modify = state.node.field("m_modifyHealth")
    horizontal = _amount(state, modify.get("m_E33EFEE1"))
    lift = _amount(state, modify.get("m_583C208B"))
    if horizontal > 0 or lift > 0:
        fight.impulses.hit(target, direction, 0.,
                           knockback.Force(horizontal, horizontal, lift, lift, ((1., 1., 1., 1.),)),
                           ballistic=True)


def cancel(fight, player, *, impact=False, frame=None):
    instance = instance_for(player)
    contact = fight.rein_charges.get(getattr(player, "body", None))
    if instance is None or (active(instance, 40) is None and contact is None and not value(instance, 59)):
        return
    target = (target_for(fight, contact.pinned) if contact is not None
              and fight.pins.get(player.body) == contact.pinned else None)
    if impact and target is not None:
        damage(fight, player, target, instance, 12, fight.tick if frame is None else frame)
    instance.write((expr.INSTANCE, 59), 0)
    instance.component.run_now()
    phase = instance.states.get(1)
    running_case = "m_05706D1C[1]"
    if active(instance, 3) is not None and phase is not None and phase.case != running_case:
        instance.exit_subgraph(phase.node, running_case)
        instance.component.run_now()
    release(fight, player)


def sync(fight, player):
    contact = fight.rein_charges.get(player.body)
    if contact is None:
        return
    instance = instance_for(player)
    if active(instance, 3) is not None:
        return
    target = target_for(fight, contact.pinned) if fight.pins.get(player.body) == contact.pinned else None
    release(fight, player)
    if (target is not None and instance is not None
            and not fight.is_stunned(player) and not fight.is_dead(player)):
        impulse(fight, target, instance, 49, (math.sin(contact.heading), 0., math.cos(contact.heading)))


def drive(fight, player, command, dt, *, blocked):
    instance = instance_for(player)
    running = active(instance, 3)
    if instance is None:
        return None
    if blocked or player.body in fight.hacks:
        cancel(fight, player)
        return None
    if running is None:
        sync(fight, player)
        return None
    activation = (instance.id, running.counter, running.start)
    wanted = command.yaw * math.tau / 65536
    contact = fight.rein_charges.get(player.body)
    if contact is None or contact.activation != activation:
        release(fight, player)
        contact = fight.rein_charges[player.body] = Contact(activation, wanted)
    delta = (wanted - contact.heading + math.pi) % math.tau - math.pi
    turn = math.radians(value(instance, 34534)) * dt
    contact.heading += max(-turn, min(turn, delta))
    speed = value(instance, 7726) * 60
    return (math.sin(contact.heading) * speed, math.cos(contact.heading) * speed) if speed > 0 else None


def _transport(fight, player, target, contact, feet, direction, dt, frame):
    from ow174.game.combat import _along

    owner = target.owner
    destination = _along(feet, direction, player.mover.data.stand_radius + target.radius + .02)
    npc = isinstance(owner, bots.Bot)
    if npc:
        if contact.mover is None:
            category = MOVER if fight.match.game_map.free_for_all else (TEAM0, TEAM1)[owner.team]
            contact.mover = owner.dva_mover or Mover(
                target.feet, world=fight._collision(), data=mover_data(owner.body), category=category,
            )
        mover = contact.mover
        mover.use(world=fight._collision())
    else:
        mover = owner.mover
    before = tuple(mover.position)
    mover._move_to(dt, destination, math.dist(before, destination) / dt + 1)
    if npc:
        after, normals = fight.walls.constrain(before, mover.position, target.radius, target.height)
        if normals:
            mover.position[:] = after
            mover.body = None
        owner.position, owner.velocity, owner.moved_tick = tuple(mover.position), mover.velocity, fight.tick
    else:
        fight.walls.move_player(owner, before)
        if mover.history and mover.history[-1].frame == frame:
            mover.history[-1] = mover.snapshot(frame)
    _record_pose(fight, target.entity, mover.position, fight.tick)
    if math.dist(mover.position, destination) > .03:
        cancel(fight, player, impact=True, frame=frame)


def contacts(fight, player, command, before, velocity, dt):
    from ow174.game.combat import _along, _dot, _sub, capsule_visible

    contact = fight.rein_charges.get(player.body)
    if contact is None or _counter(fight, player):
        return
    instance = instance_for(player)
    speed = math.hypot(*velocity)
    forward = velocity[0] / speed, 0., velocity[1] / speed
    radius, width = value(instance, 9149), value(instance, 11861)
    feet = tuple(player.mover.position)
    a = before[0], before[1] + 1., before[2]
    b = feet[0], feet[1] + 1., feet[2]
    tip = _along(b, forward, radius)
    target = target_for(fight, contact.pinned) if fight.pins.get(player.body) == contact.pinned else None
    if target is None:
        unpin(fight, player, contact)
    elif (phase_defenses.active(target.owner) or mei.protected(target.owner)
          or (not fight.match.game_map.free_for_all and target.team == player.team)):
        cancel(fight, player)
        return
    for candidate in sorted(fight._enemies(player), key=lambda t: (math.dist(feet, t.feet), t.entity)):
        key = (candidate.entity, fight.replacement_ticks.get(candidate.entity))
        if candidate.is_device or key in contact.hit or _dot(_sub(candidate.feet, before), forward) < 0:
            continue
        c, d = candidate.segment()
        if (segment_pair_distance(a, b, c, d) > radius + candidate.radius
                or not capsule_visible(fight._line_collision(), a, c, d, candidate.radius)):
            continue
        contact.hit.add(key)
        if fortify.active(fight, candidate.owner) or mei.protected(candidate.owner):
            continue  # The separate authored counter query owns immune-body impacts.
        if (target is None and candidate.entity not in fight.pins.values()
                and not fight.hooks.controlled(candidate.entity)
                and candidate.entity not in fight.gravitons.player_pulls
                and candidate.entity not in fight.gravitons.npc_movers
                and segment_pair_distance(a, tip, c, d) <= width + candidate.radius):
            target = candidate
            contact.pinned = candidate.entity
            fight.pins[player.body] = candidate.entity
            if isinstance(candidate.owner, bots.Bot):
                heavy_assault.interrupt(candidate.owner)
            fight._interrupt(candidate)
        elif damage(fight, player, candidate, instance, 15, command.frame):
            lateral = _sub(candidate.feet, _along(before, forward, -5.))
            impulse(fight, candidate, instance, 15, lateral)
    if target is not None:
        _transport(fight, player, target, contact, feet, forward, dt, command.frame)
    if _dot(_sub(feet, before), forward) < speed * dt / 2:
        cancel(fight, player, impact=True, frame=command.frame)


def _record_pose(fight, entity, position, tick):
    history = fight.history.get(entity)
    if history and history[-1][0] >= tick:
        at, _, height, eye = history[-1]
        history[-1] = at, tuple(position), height, eye


def replay_pending(fight):
    return bool(fight.rein_charges or any(
        (instance := instance_for(player)) is not None and value(instance, 59)
        for player in fight.match.players
    ))


def replay(fight, tick, dt):
    if not replay_pending(fight) or fight.match.ended:
        return
    match = fight.match
    scheduled = match.tick
    try:
        match.tick = tick
        for player in match.players:
            instance = instance_for(player)
            if (instance is None or not value(instance, 59) or not match._shown(player)
                    or player.body_script.frame >= tick or fight.is_dead(player)):
                continue
            mover = player.mover
            command = Command(tick, yaw=mover.yaw, pitch=mover.pitch)
            mover.last_command = command
            player._step_body_pose(command, dt, tick)
            _record_pose(fight, player.body, mover.position, tick)
    finally:
        match.tick = scheduled


def _counter_duration(instance, *, heavy=False):
    for node in instance.graph.nodes:
        if node is None or node.cls != "STUStatescriptActionPlayScript" or (heavy and node.pos != 132):
            continue
        inline = (node.field("m_69F4B52C") or {}).get("m_6525E98B") or {}
        if expr.guid_of(inline.get("m_graph")) != 0x058000000000224E:
            continue
        rows = {expr.guid_of(row.get("m_0D09D2D9")): row.get("m_value")
                for row in inline.get("m_1EB5A024") or () if isinstance(row, dict)}
        kind = instance.evaluate(rows.get(0x0D80000000006BD6))
        if kind != expr.Asset(0x0D80000000006BBE):
            continue
        if not heavy and not expr.truthy(instance.evaluate(rows.get(0x0D80000000007312))):
            continue
        return expr.to_float(instance.evaluate(rows.get(0x0D800000000000D7)))
    return 0.


def _counter(fight, player):
    from ow174.game.combat import capsule_visible, segment_distance

    instance = instance_for(player)
    tracker = active(instance, 74)
    own = target_for(fight, player.body)
    if tracker is None or own is None:
        return False
    yaw = player.mover.yaw * math.tau / 65536
    forward = math.sin(yaw), math.cos(yaw)
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = _amount(tracker, tracker.node.field("m_radius"))
    aperture = math.radians(_amount(tracker, tracker.node.field("m_A30B383E"))) / 2
    if not isinstance(offset, expr.Vec3) or radius <= 0:
        return False
    x, y, z = player.mover.position
    centre = (x + math.cos(yaw) * offset.x + forward[0] * offset.z,
              y + offset.y, z - math.sin(yaw) * offset.x + forward[1] * offset.z)
    for target in fight._enemies(player):
        if target.is_device:
            continue
        actor = target.owner
        script = getattr(actor, "body_script", None)
        if script is None or fight.is_stunned(actor) or fight.pins.get(player.body) == target.entity:
            continue
        heavy = isinstance(actor, bots.Bot) and actor.hero == heavy_assault.HERO
        impact = fortify.active(fight, actor) or mei.protected(actor)
        other = None
        if not impact:
            if heavy:
                other = next((i for i in script.component.instances.values()
                              if i.graph.index == heavy_assault.GRAPH), None)
                other_yaw = actor.yaw
            else:
                other = instance_for(actor)
                if active(other, 3) is None:
                    continue
                other_yaw = actor.mover.yaw
            flag = script.component.entity_var(596, create=False)
            if other is None or flag is None or not expr.truthy(flag.value()):
                continue
            heading = other_yaw * math.tau / 65536
            if forward[0] * math.sin(heading) + forward[1] * math.cos(heading) >= 0:
                continue
        a, b = target.segment()
        if segment_distance(centre, a, b) > radius + target.radius:
            continue
        dx, dz = target.feet[0] - centre[0], target.feet[2] - centre[2]
        distance = math.hypot(dx, dz)
        if distance > 0:
            cosine = (dx * forward[0] + dz * forward[1]) / distance
            if math.acos(max(-1., min(1., cosine))) > aperture + math.asin(min(1., target.radius / distance)):
                continue
        if not capsule_visible(fight._line_collision(), centre, a, b, target.radius):
            continue
        if impact:
            instance.write((expr.INSTANCE, 559), True)
            instance.write((expr.INSTANCE, 11401), True)
            cancel(fight, player, impact=True)
            return True
        duration, other_duration = _counter_duration(instance), _counter_duration(other, heavy=heavy)
        if duration > 0 and other_duration > 0:
            fight.apply_stun(own, duration)
            fight.apply_stun(target, other_duration)
            return True
    return False
