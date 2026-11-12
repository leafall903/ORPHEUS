"""Reconstructed contact and transport for the authored 104319 Charge."""

import math
from dataclasses import dataclass, field

from ow174.game import actors, barrier, bots
from ow174.game.contacts import closest_segments
from ow174.game.mover import FELL, Mover, mover_data
from ow174.game.script import expr


@dataclass
class Charge:
    player: object
    state: object
    deadline: int
    pinned: int | None = None
    hit: set = field(default_factory=set)
    heading: float | None = None
    activation: int | None = None


def drive(fight, player, command, seconds):
    """Delivered007D horizontal drive; authored phases retain lifetime ownership."""
    from ow174.game.combat import TICK

    script = player.body_script
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x7D), None) if script is not None and script.hero == barrier.REINHARDT else None
    state = instance.states.get(3) if instance is not None else None
    if (state is None or not state.active or fight.is_dead(player) or fight.is_stunned(player)
            or player.gone or not player.spawned or player.client is None):
        if (charge := fight.charges.get(player.body)) is not None:
            stop(fight, player.body, charge)
        return None
    retained = fight.charges.get(player.body)
    victim = fight._target_entity(retained.pinned) if retained is not None else None
    if victim is not None and not fight.match.game_map.free_for_all and victim.team == player.team:
        stop(fight, player.body, retained)
        return None
    charge = fight.charges.get(player.body)
    wanted = command.yaw * math.tau / 65536
    if charge is None or charge.state is not state or charge.activation != state.counter:
        duration = expr.to_float(state.evaluate(instance.graph.node(26).field("m_destination"), watch=False))
        charge = Charge(player, state, fight.tick + math.ceil(duration / TICK),
                        heading=wanted, activation=state.counter)
        fight.charges[player.body] = charge
    if charge.heading is None:
        charge.heading = wanted
    delta = (wanted - charge.heading + math.pi) % math.tau - math.pi
    rate = expr.to_float(instance.find_var(0, 34534).value())
    limit = math.radians(rate) * seconds
    charge.heading += max(-limit, min(limit, delta))
    speed = expr.to_float(instance.find_var(0, 7726).value()) * 60
    return (math.sin(charge.heading) * speed, math.cos(charge.heading) * speed) if speed > 0 else None


def drain_end(instance):
    """Delivered phase exit also retires a run queued by an aborted windup."""
    instance.component.run_now()
    drive = instance.states.get(3)
    phase = instance.states.get(1)
    running_case = "m_05706D1C[1]"
    if drive is not None and drive.active and phase is not None and phase.case != running_case:
        instance.exit_subgraph(phase.node, running_case)
        instance.component.run_now()


def pinned(fight, entity):
    return any(charge.pinned == entity for charge in fight.charges.values())


def interrupt(fight, target, script):
    if script.component.world.hero != barrier.REINHARDT or isinstance(target.owner, bots.Bot):
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x007D), None)
    root = instance.states.get(0) if instance is not None else None
    if root is not None and root.active:
        # The windup Wait can start the run from its end plug even after Ability.stop().
        # Clear the authored charge stack condition so its subgraph cannot resume.
        instance.write((0, 59), 0)
        fight.charges.pop(target.entity, None)
        mover = target.owner.mover
        mover.velocity = (0., mover.velocity[1], 0.)


def stop(fight, body, charge):
    fight.charges.pop(body, None)
    player = charge.player
    player.mover.velocity = (0., player.mover.velocity[1], 0.)
    if charge.state.active:
        instance = charge.state.instance
        instance.write((0, 59), 0)
        drain_end(instance)


def sync(fight):
    for body, charge in list(fight.charges.items()):
        owner = charge.player
        if owner.gone or owner.body != body:
            fight.charges.pop(body, None)
        elif (not charge.state.active or fight.is_dead(owner) or fight.is_stunned(owner)
              or fight.tick >= charge.deadline):
            victim = fight._target_entity(charge.pinned)
            release = (victim is not None and not charge.state.active and not fight.is_dead(owner)
                       and not fight.is_stunned(owner) and owner.spawned and owner.client is not None)
            stop(fight, body, charge)
            if release:
                heading = charge.heading if charge.heading is not None else owner.mover.yaw * math.tau / 65536
                lateral_impulse(owner.mover.position, victim, charge.state.instance.state(49),
                                (math.sin(heading), 0., math.cos(heading)), fight=fight, frame=owner.body_script.frame)
        elif charge.pinned is not None and fight._target_entity(charge.pinned) is None:
            charge.pinned = None


def impact(fight, player, target, instance, index, frame):
    from ow174.game.combat import hit_def

    state = instance.state(index)
    hit = hit_def(state, state.node.fields)
    if hit is not None:
        fight._damage(player, target, hit.amount, False, hit.loadout, frame, world_tick=fight.tick)


def lateral_impulse(source_position, target, state, forward, *, fight=None, frame=None):
    payload = state.node.field("m_modifyHealth")
    horizontal = expr.to_float(state.evaluate(payload["m_E33EFEE1"], watch=False))
    vertical = expr.to_float(state.evaluate(payload["m_583C208B"], watch=False))
    if fight is not None:
        offset = 5.0 if state.index == 15 else 2.0
    else:
        offset, = payload["m_C0139FA6"]["m_expression"]["m_D99EF254"]
    x = target.feet[0] - (source_position[0] - forward[0] * offset)
    z = target.feet[2] - (source_position[2] - forward[2] * offset)
    distance = math.hypot(x, z)
    # Reconstructed velocity deltas from the authored radial force operands.
    # Native force conversion and recipient resistance are separate.
    scale = horizontal / distance if distance > 0 else 0
    if fight is not None:
        fight.knockbacks.apply(target, expr.Vec3(x * scale, vertical, z * scale), impact_frame=frame)
        return
    mover = getattr(target.owner, "mover", None)
    if mover is None:
        return
    vx, vy, vz = mover.velocity
    mover.driven = False
    mover.velocity = (vx + x * scale, vy + vertical, vz + z * scale)
    if mover.velocity[1] > 0:
        mover.flags_word |= FELL
        mover.fall = 0.0


def counter_duration(instance):
    node = instance.graph.node(153)
    inline = (node.field("m_69F4B52C") or {}).get("m_6525E98B") or {}
    if expr.guid_of(inline.get("m_graph")) != 0x058000000000224E:
        return 0.0
    values = {expr.guid_of(row.get("m_0D09D2D9")): row.get("m_value")
              for row in inline.get("m_1EB5A024") or ()}
    if (instance.evaluate(values.get(0x0D80000000006BD6)) != expr.Asset(0x0D80000000006BBE)
            or not expr.truthy(instance.evaluate(values.get(0x0D80000000007312)))):
        return 0.0
    return expr.to_float(instance.evaluate(values.get(0x0D800000000000D7)))


def counter_frame(fight, player):
    """Delivered posed-command hook also admits close opposing windups."""
    if player.hero.guid != barrier.REINHARDT or fight.is_stunned(player) or fight.is_dead(player):
        return False
    script = player.body_script
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x7D), None)
    phase = instance.states.get(40) if instance is not None else None
    if phase is None or not phase.active:
        return False
    yaw = player.mover.yaw * math.tau / 65536
    return counter(fight, player, instance, (math.sin(yaw), 0., math.cos(yaw)), fight._enemies(player))


def counter(fight, player, instance, forward, enemies):
    from ow174.game.combat import aim, capsule_visible, segment_distance

    tracker = instance.states.get(74)
    if tracker is None or not tracker.active:
        return False
    node = tracker.node
    local = tracker.evaluate(node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(node.field("m_radius"), watch=False))
    cone = expr.to_float(tracker.evaluate(node.field("m_A30B383E"), watch=False))
    if not isinstance(local, expr.Vec3) or radius <= 0:
        return False
    fx, _, fz = forward
    x, y, z = player.mover.position
    centre = (x + fz * local.x + fx * local.z, y + local.y, z - fx * local.x + fz * local.z)
    for target in enemies:
        owner = target.owner
        script = getattr(owner, "body_script", None)
        if (script is None or isinstance(owner, bots.Bot)
                or script.component.world.hero != barrier.REINHARDT or fight.is_stunned(owner)):
            continue
        flag = script.component.entity_var(596, create=False)
        tx, _, tz = aim(owner.mover.yaw, 0)
        if flag is None or not expr.truthy(flag.value()) or fx * tx + fz * tz >= -expr.EPSILON:
            continue
        a, b = target.segment()
        if segment_distance(centre, a, b) > radius + target.radius:
            continue
        dx, dz = target.feet[0] - centre[0], target.feet[2] - centre[2]
        distance = math.hypot(dx, dz)
        if distance:
            angle = math.acos(max(-1., min(1., (dx * fx + dz * fz) / distance)))
            if angle > math.radians(cone) / 2 + math.asin(min(1., target.radius / distance)):
                continue
        from ow174.game.reinhardt_hammer import _wall_clear
        if (not capsule_visible(fight._collision(), centre, a, b, target.radius)
                or not _wall_clear(fight, centre, a)):
            continue
        opposing = next((i for i in script.component.instances.values() if i.graph.index == 0x007D), None)
        own = next((t for t in fight._targets() if t.owner is player), None)
        if opposing is None or own is None:
            continue
        duration, other_duration = counter_duration(instance), counter_duration(opposing)
        if duration > 0 and other_duration > 0:
            fight.stun(own, duration)
            fight.stun(target, other_duration)
            return True
    return False


def advance(fight, player, command, start, seconds):
    from ow174.game.combat import TICK, _along, aim, capsule_visible

    script = player.body_script
    if (script is None or script.hero != barrier.REINHARDT or fight.is_dead(player)
            or fight.is_stunned(player) or player.gone or not player.spawned or player.client is None):
        fight.charges.pop(player.body, None)
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x007D), None)
    running = instance.states.get(3) if instance is not None else None
    if running is None or not running.active:
        if (retained := fight.charges.get(player.body)) is not None:
            stop(fight, player.body, retained)
        return
    charge = fight.charges.get(player.body)
    if charge is None or charge.state is not running:
        duration = running.evaluate(instance.graph.node(26).field("m_destination"), watch=False)
        charge = Charge(player, running, fight.tick + math.ceil(expr.to_float(duration) / TICK))
        fight.charges[player.body] = charge
    feet = tuple(player.mover.position)
    forward = (math.sin(charge.heading), 0., math.cos(charge.heading)) if charge.heading is not None else aim(command.yaw, 0)
    a = (start[0], start[1] + 1, start[2])
    b = (feet[0], feet[1] + 1, feet[2])
    radius = expr.to_float(instance.find_var(0, 9149).value())
    width = expr.to_float(instance.find_var(0, 11861).value())
    tip = _along(b, forward, radius)
    enemies = sorted(fight._enemies(player), key=lambda t: (math.dist(feet, t.feet), t.entity))
    if counter(fight, player, instance, forward, enemies):
        return
    from ow174.game.reinhardt_hammer import _wall_clear
    for target in enemies:
        key = (target.entity, fight.replacement_ticks.get(target.entity))
        if key in charge.hit or isinstance(target.owner, actors.Deployable):
            continue
        if sum((p - q) * d for p, q, d in zip(target.feet, start, forward, strict=True)) < 0:
            continue
        c, d = target.segment()
        if (math.dist(*closest_segments(a, b, c, d)) > radius + target.radius
                or not capsule_visible(fight._collision(), a, c, d, target.radius)
                or not _wall_clear(fight, a, (target.feet[0], target.feet[1] + 1, target.feet[2]))):
            continue
        charge.hit.add(key)
        frontal = math.dist(*closest_segments(a, tip, c, d)) <= width + target.radius
        if frontal and isinstance(target.owner, bots.Bot) and target.owner.body_script is not None:
            immunity = target.owner.body_script.component.entity_var(6601, create=False)
            if immunity is not None and expr.truthy(immunity.value()):
                stop(fight, player.body, charge)
                return
        if frontal and charge.pinned is None and not fight.is_stunned(target.owner) and not fight._control_immune(target):
            charge.pinned = target.entity
            fight.interrupt(target)
        else:
            before = fight.vitals_of(target).total
            impact(fight, player, target, instance, 15, command.frame)
            vitals = fight.vitals_of(target)
            if (not vitals.dead and vitals.total < before and not pinned(fight, target.entity)
                    and not fight._control_immune(target)):
                lateral_impulse(player.mover.position, target, instance.state(15), forward, fight=fight, frame=command.frame)
    victim = next((t for t in enemies if t.entity == charge.pinned and not fight.vitals_of(t).dead), None)
    wall = fight._collision().cast_ray(b, forward, player.mover.data.stand_radius + .08)
    if victim is not None:
        owner = victim.owner
        mover = (Mover(victim.feet, world=fight._collision(), data=mover_data(owner.body))
                 if isinstance(owner, bots.Bot) else owner.mover)
        destination = _along(feet, forward, player.mover.data.stand_radius + victim.radius + .02)
        mover._move_to(seconds, destination, math.dist(mover.position, destination) / seconds + 1)
        if isinstance(owner, bots.Bot):
            owner.position = tuple(mover.position)
        elif mover.history and mover.history[-1].frame == command.frame:
            mover.history[-1] = mover.snapshot(command.frame)
        if wall is not None or math.dist(mover.position, destination) > .03:
            impact(fight, player, victim, instance, 12, command.frame)
            stop(fight, player.body, charge)
    else:
        charge.pinned = None
        if wall is not None:
            stop(fight, player.body, charge)
