"""Reconstructed ground propagation for the authored 104319 Earthshatter."""

import math
from dataclasses import dataclass, field

from ow174.game import actors, barrier, reinhardt_barrier, world_bubbles
from ow174.game.collision import MOVER
from ow174.game.script import expr, runtime

RAYS = 101


def prepare(fight, player):
    """Project the posed mover into the selected airborne release branch."""
    script = player.body_script
    if script.hero != barrier.REINHARDT or fight.is_dead(player) or fight.is_stunned(player):
        return
    component = script.component
    component.set(417, player.mover.airborne)
    instance = next((i for i in component.instances.values() if i.graph.index == 0xBE), None)
    if instance is None or player.mover.airborne:
        return
    air = instance.states.get(8)
    if air is not None and air.active:
        # Node 44's elapsed-state predicate needs the host landing pose.
        gate = instance.graph.node(44)
        delay = expr.to_float(air.evaluate(gate.field("m_AA6E611E"), watch=False))
        if component.now - air.start >= delay * 1000 and instance.follow(gate, "m_truePlug", air):
            air.request(runtime.ABORT)
    tracker = instance.states.get(37)
    if tracker is not None and tracker.active:
        x, y, z = player.mover.position
        ground = fight._collision().ground(x, z, y + .05, y - 1.5, player.mover.category)
        instance.write((0, 10360), ground is not None)
        if ground is not None:
            instance.write((0, 1724), expr.Vec3(x, ground.y, z))
            marker = instance.states.get(29)
            if marker is not None and marker.active:
                marker.request_finish()


@dataclass
class Wave:
    shooter: object
    state: object
    origin: tuple
    centre: tuple
    yaw: float
    speed: float
    reach: float
    cone: float
    height: float
    radius: float
    stun_seconds: float
    normal: object
    close: object
    started: float
    fronts: list
    hit: set = field(default_factory=set)
    shields: set = field(default_factory=set)
    travelled: float = 0.0
    blocked: set = field(default_factory=set)
    shield_poses: list = field(default_factory=list)
    dome_poses: dict = field(default_factory=dict)


def start(fight, player):
    from ow174.game.combat import hit_def

    instances = player.body_script.component.instances.values()
    instance = next((instance for instance in instances if instance.graph.index == 0xBE), None)
    state = instance.states.get(0) if instance is not None else None
    if state is None or not state.active:
        return None

    def value(key):
        var = instance.find_var(0, key)
        return var.value() if var is not None else None

    def number(node, name):
        return expr.to_float(state.evaluate(node.field(name), watch=False))

    origin, centre = value(431), value(1188)
    if not isinstance(origin, expr.Vec3) or not isinstance(centre, expr.Vec3):
        return None
    speed = number(state.node, "m_speed")
    reach = number(state.node, "m_6A79BD98")
    if speed <= 0 or reach <= 0:
        return None
    origin = tuple(origin)
    wave = Wave(
        player, state, origin, tuple(centre), player.mover.yaw * math.tau / 65536,
        speed, reach, number(state.node, "m_A30B383E"), number(state.node, "m_525E5A40"),
        number(instance.state(40).node, "m_radius"), expr.to_float(value(215)),
        hit_def(state, instance.state(1).node.fields),
        hit_def(state, instance.state(41).node.fields), fight.now, [origin] * RAYS,
    )
    if wave.normal is None or wave.close is None:
        return None
    close_hit(fight, wave)
    return wave


def hurt(fight, wave, target, definition):
    accepted = fight._damage(wave.shooter, target, definition.amount, False, definition.loadout, fight.tick,
                             world_tick=fight.tick, source_body=wave.state.instance.component.entity,
                             hit_tags=definition.tags)
    if accepted and not isinstance(target.owner, actors.Deployable):
        fight.control(target, wave.state.instance.component.entity, "earthshatter", wave.stun_seconds)


def _legacy_close_hit(fight, wave):
    from ow174.game.combat import capsule_visible, segment_distance

    start = (wave.origin[0], wave.origin[1] + .1, wave.origin[2])
    shields = {}
    for target in fight._enemies(wave.shooter):
        a, b = target.segment()
        if (segment_distance(wave.centre, a, b) > wave.radius + target.radius
                or not capsule_visible(fight._collision(), start, a, b, target.radius)):
            continue
        end = (target.feet[0], target.feet[1] + .1, target.feet[2])
        delta = tuple(q - p for p, q in zip(start, end, strict=True))
        length = math.hypot(*delta)
        hit = (fight.barrier.intersect(wave.shooter, start, tuple(d / length for d in delta), length)
               if length else None)
        if hit is None:
            hurt(fight, wave, target, wave.close)
        else:
            shields[hit[1]] = hit[2]
    for entity, health in shields.items():
        fight.barrier.damage(entity, health, wave.close.amount)


def _legacy_advance(fight, wave):
    """Sweep consecutive ground chords, retaining blocked rays and hit identities."""
    from ow174.game.combat import segment_distance

    if not wave.state.active:
        return True
    distance = min(wave.reach, (fight.now - wave.started) * wave.speed)
    if distance <= wave.travelled:
        return False
    segments = []
    shields = {}
    world = fight._collision()
    for index, previous in enumerate(wave.fronts):
        if previous is None:
            continue
        angle = wave.yaw + math.radians(wave.cone * (index / (RAYS - 1) - .5))
        x, z = wave.origin[0] + math.sin(angle) * distance, wave.origin[2] + math.cos(angle) * distance
        ground = world.ground(x, z, previous[1] + wave.height, previous[1] - wave.height)
        wave.fronts[index] = None
        if ground is None or ground.normal[1] < .5:
            continue
        end = (x, ground.y, z)
        delta = tuple(q - p for p, q in zip(previous, end, strict=True))
        length = math.hypot(*delta)
        direction = tuple(d / length for d in delta)
        start = (previous[0], previous[1] + wave.height, previous[2])
        if world.cast_ray(start, direction, length) is not None:
            continue
        shield_start = (previous[0], previous[1] + .1, previous[2])
        shield = fight.barrier.intersect(wave.shooter, shield_start, direction, length)
        if shield is not None:
            shields[shield[1]] = shield[2]
            continue
        wave.fronts[index] = end
        segments.append((previous, end))
    for entity, health in shields.items():
        key = fight.barrier.children.get(entity)
        if key not in wave.shields:
            wave.shields.add(key)
            fight.barrier.damage(entity, health, wave.normal.amount)
    for target in fight._enemies(wave.shooter):
        key = (target.entity, fight.replacement_ticks.get(target.entity))
        if key in wave.hit:
            continue
        x, y, z = target.feet
        if any(-.1 <= y - end[1] <= wave.height
               and segment_distance((x, 0, z), (start[0], 0, start[2]), (end[0], 0, end[2])) <= target.radius
               for start, end in segments):
            wave.hit.add(key)
            hurt(fight, wave, target, wave.normal)
    wave.travelled = distance
    if distance < wave.reach:
        return False
    wave.state.request_finish()
    wave.state.instance.component.run_now()
    return True


def _distance_xz(point, a, b):
    dx, dz = b[0] - a[0], b[2] - a[2]
    squared = dx * dx + dz * dz
    share = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[2] - a[2]) * dz) / squared)) if squared else 0
    return math.hypot(point[0] - a[0] - share * dx, point[2] - a[2] - share * dz)


def _before_wall(point, clip):
    if clip is None:
        return True
    end, normal = clip
    return sum((p - q) * n for p, q, n in zip(point, end, normal, strict=True)) < 0


def _captured_trace(pose, origin, direction, length):
    if len(pose) == 4 and pose[0] == "dome":
        _, centre, radius, _ = pose
        return world_bubbles.shell(origin, direction, centre, radius, length)
    geometry, feet, yaw, pitch, radius, height, _ = pose
    return reinhardt_barrier.intersect(geometry, feet, yaw, pitch, origin, direction, length,
                                      owner_radius=radius, owner_height=height)


def _shield_contact(fight, wave, origin, direction, length):
    contact = fight._barrier_contact(wave.shooter, origin, direction, length)
    # A shield broken by this wave retains its original blocking plane even
    # for the not-yet-visited rays in the current step. No live actor clone.
    for pose in wave.shield_poses:
        distance = _captured_trace(pose, origin, direction, length)
        if distance is not None and (contact is None or distance < contact[0]):
            contact = distance, None, pose
    for body, (centre, radius) in wave.dome_poses.items():
        pose = ("dome", centre, radius, body)
        distance = _captured_trace(pose, origin, direction, length)
        if distance is not None and (contact is None or distance < contact[0]):
            contact = distance, None, pose
    return contact


def close_hit(fight, wave):
    from ow174.game.combat import capsule_visible, segment_distance

    start = (wave.origin[0], wave.origin[1] + .1, wave.origin[2])
    for target in fight._enemies(wave.shooter):
        a, b = target.segment()
        if segment_distance(wave.centre, a, b) > wave.radius + target.radius:
            continue
        end = (target.feet[0], target.feet[1] + .1, target.feet[2])
        delta = tuple(q - p for p, q in zip(start, end, strict=True))
        length = math.hypot(*delta)
        shield = (_shield_contact(fight, wave, start, tuple(d / length for d in delta), length)
                  if length else None)
        if (capsule_visible(fight._collision(), start, a, b, target.radius)
                and not fight.walls.blocked(start, end) and shield is None):
            hurt(fight, wave, target, wave.close)


def advance(fight, wave):
    """Delivered clipped ground front on the existing Wave and native surfaces."""
    world = fight._collision()
    distance = min(wave.reach, max(0.0, (fight.now - wave.started) * wave.speed))
    if distance <= wave.travelled:
        return False
    segments = []
    for index, previous in enumerate(wave.fronts):
        if index in wave.blocked or previous is None:
            continue
        angle = wave.yaw + math.radians(wave.cone * (index / (RAYS - 1) - .5))
        x, z = wave.origin[0] + math.sin(angle) * distance, wave.origin[2] + math.cos(angle) * distance
        ground = world.ground(x, z, previous[1] + wave.height, previous[1] - wave.height, MOVER)
        if ground is None or ground.normal[1] < .5:
            wave.blocked.add(index)
            continue
        current = (x, ground.y, z)
        start = (previous[0], previous[1] + wave.height, previous[2])
        end = (x, ground.y + wave.height, z)
        delta = tuple(b - a for a, b in zip(start, end, strict=True))
        length = math.hypot(*delta)
        if not length:
            continue
        toward = tuple(v / length for v in delta)
        wall = world.cast_ray(start, toward, length, MOVER)
        limit = wall.distance if wall is not None else length
        ice = fight.walls.cast(start, toward, limit)
        normal = ice[2] if ice is not None else (wall.normal if wall is not None else None)
        if ice is not None:
            limit = ice[0]
        wall_clip = None
        if normal is not None:
            wave.blocked.add(index)
            if limit <= 0:
                continue
            current = tuple(p + v * limit for p, v in zip(previous, toward, strict=True))
            if sum(v * n for v, n in zip(toward, normal, strict=True)) < 0:
                normal = tuple(-n for n in normal)
            wall_clip = current, normal
        start = (previous[0], previous[1] + .1, previous[2])
        end = (current[0], current[1] + .1, current[2])
        delta = tuple(b - a for a, b in zip(start, end, strict=True))
        shield_length = math.hypot(*delta)
        direction = tuple(v / shield_length for v in delta) if shield_length else toward
        shield = _shield_contact(fight, wave, start, direction, shield_length)
        actor = None
        if shield is not None and (wall_clip is None or shield[0] < limit):
            at, owner, actor = shield
            if owner is not None:
                entity = actor if type(actor) is int else actor.entity if hasattr(actor, 'entity') else actor.body
                source = getattr(actor, 'body', getattr(actor, 'owner_body', entity))
                phase = actor.instance.states.get(17) if isinstance(actor, reinhardt_barrier.RaisedBarrier) else None
                key = (source, fight.replacement_ticks.get(source), phase.counter if phase is not None else None)
                if key not in wave.shields:
                    pose = None
                    if isinstance(actor, reinhardt_barrier.RaisedBarrier):
                        feet, yaw, pitch, owner_radius, height = actor.pose()
                        pose = actor.geometry, tuple(feet), yaw, pitch, owner_radius, height, actor.entity
                    fight._damage_contact_barrier(shield, wave.normal.amount, shooter=wave.shooter)
                    wave.shields.add(key)
                    if pose is not None and actor.health <= 0 and pose not in wave.shield_poses:
                        wave.shield_poses.append(pose)
                    if isinstance(actor, world_bubbles.FixedBubble) and actor.health <= 0:
                        wave.dome_poses[actor.body] = tuple(actor.point), actor.radius
            current = tuple(start[k] + direction[k] * at - (.1 if k == 1 else 0) for k in range(3))
            wave.blocked.add(index)
        segments.append((previous, current, wall_clip, actor))
        wave.fronts[index] = current
    for target in fight._enemies(wave.shooter):
        key = target.entity, fight.replacement_ticks.get(target.entity)
        if key in wave.hit or fight.vitals_of(target).dead:
            continue
        for start, end, wall_clip, actor in segments:
            if (not -.1 <= target.feet[1] - end[1] <= wave.height
                    or _distance_xz(target.feet, start, end) > target.radius
                    or not _before_wall(target.feet, wall_clip)):
                continue
            if actor is not None:
                origin = (start[0], start[1] + .1, start[2])
                point = (target.feet[0], target.feet[1] + .1, target.feet[2])
                delta = tuple(b - a for a, b in zip(origin, point, strict=True))
                length = math.hypot(*delta)
                direction = tuple(v / length for v in delta) if length else (0., 0., 0.)
                if (isinstance(actor, reinhardt_barrier.RaisedBarrier)
                        and actor.trace(origin, direction, length) is not None):
                    continue
                if type(actor) is tuple and _captured_trace(actor, origin, direction, length) is not None:
                    continue
                if _shield_contact(fight, wave, origin, direction, length) is not None:
                    continue
            wave.hit.add(key)
            hurt(fight, wave, target, wave.normal)
            break
    wave.travelled = distance
    if distance < wave.reach:
        return False
    wave.state.request_finish()
    wave.state.instance.component.run_now()
    return True
