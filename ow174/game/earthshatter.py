"""Explicit server reconstruction of104319 Earthshatter's ground propagation.

Authored speed/range/cone/damage/control drive a sampled ground front. This is
not the missing native Shockwave implementation or its skeletal presentation.
"""

import math
from dataclasses import dataclass, field

from ow174.game import barriers, winston_barrier
from ow174.game.collision import MOVER


@dataclass
class Wave:
    shooter: object
    body: int
    instance: int
    activation: int
    origin: tuple
    yaw: float
    speed: float
    reach: float
    cone: float
    height: float
    duration: float
    amount: float
    near_amount: float
    near_centre: tuple
    near_radius: float
    loadout: int | None
    started: float
    travelled: float = 0.0
    near_done: bool = False
    fronts: list = field(default_factory=list)
    blocked: set = field(default_factory=set)
    hit: set[tuple[int, int | None]] = field(default_factory=set)
    near_hit: set[tuple[int, int | None]] = field(default_factory=set)
    shield_hit: set[tuple[int, int | None, int | None]] = field(default_factory=set)
    shield_poses: list = field(default_factory=list)
    dome_poses: dict = field(default_factory=dict)
    tags: tuple = ()
    near_tags: tuple = ()


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


def _shield_poses(fight, wave):
    found = [(tuple(owner.mover.position), owner.mover.yaw, owner.mover.pitch,
              owner.mover.values.stand_radius, owner.mover.values.stand_height, owner.body, owner, state)
             for owner, state in fight._enemy_barriers(wave.shooter)]
    # Blocked rays stay stopped. A shield shattered by this wave also retains
    # its impact pose for the rest of that wave.
    found.extend((*pose, None, None) for pose in wave.shield_poses)
    found.extend((actor.centre, None, None, None, None, actor.body, actor, actor)
                 for actor in fight.winston_bubbles.opponents(wave.shooter))
    found.extend((centre, None, None, None, None, body, None, None)
                 for body, centre in wave.dome_poses.items())
    found.extend((actor.position, actor.yaw, None, None, None, actor.body, actor, actor)
                 for actor in fight.orisa_barriers.opponents(wave.shooter))
    found.extend((actor.position, actor.yaw, actor.pitch, 0, 0, actor.body, actor, actor)
                 for actor in fight.sigma_barriers.enemies(wave.shooter))
    return found


def _shield(fight, poses, start, end):
    delta = tuple(b - a for a, b in zip(start, end, strict=True))
    length = math.hypot(*delta)
    if not length:
        return None
    toward = tuple(v / length for v in delta)
    found = []
    for pose in poses:
        feet, yaw, pitch, radius, height, _, _, _ = pose
        if radius is None and yaw is not None:
            distance = fight.orisa_barriers.geometry.cast(feet, yaw, start, toward, length)
        elif radius is None:
            distance = winston_barrier.shell_ray(feet, start, toward)
            if distance is not None and distance > length:
                distance = None
        elif radius == 0:
            distance = barriers.intersect(
                fight.sigma_barriers.geometry, feet, yaw, pitch, start, toward, length,
            )
        else:
            distance = barriers.intersect(fight.barrier_geometry, feet, yaw, pitch, start, toward, length,
                                         owner_radius=radius, owner_height=height)
        if distance is not None:
            found.append((distance, pose, toward))
    return min(found, key=lambda item: item[0]) if found else None


def _hurt(fight, wave, target, amount, *, near=False):
    key = (target.entity, fight.replacement_ticks.get(target.entity))
    if fight._damage(wave.shooter, target, amount, False, wave.loadout, fight.tick, world_tick=fight.tick,
                     tags=wave.near_tags if near else wave.tags):
        fight.control(target, wave.body, "earthshatter", wave.duration)
    (wave.near_hit if near else wave.hit).add(key)


def _before_shield(fight, point, start, pose):
    if pose is None:
        return True
    # Capsule overlap with a clipped endpoint does not expose a body behind the shield.
    origin = (start[0], start[1] + .1, start[2])
    end = (point[0], point[1] + .1, point[2])
    return _shield(fight, (pose,), origin, end) is None


def advance(fight, wave):
    """Sample101 rays across the authored cone; retain front, terrain and shield history."""
    from ow174.game.combat import capsule_visible, segment_distance

    world = fight._collision()
    poses = _shield_poses(fight, wave)
    targets = list(fight._enemies(wave.shooter))
    if not wave.fronts:
        wave.fronts = [wave.origin] * 101
    if not wave.near_done:
        wave.near_done = True
        for target in targets:
            a, b = target.segment()
            if segment_distance(wave.near_centre, a, b) > wave.near_radius + target.radius:
                continue
            end = (target.feet[0], target.feet[1] + .1, target.feet[2])
            start = (wave.origin[0], wave.origin[1] + .1, wave.origin[2])
            if (capsule_visible(world, start, a, b, target.radius)
                    and not fight.walls.blocked(start, end)
                    and _shield(fight, poses, start, end) is None):
                _hurt(fight, wave, target, wave.near_amount, near=True)
    distance = min(wave.reach, max(0.0, (fight.now - wave.started) * wave.speed))
    if distance <= wave.travelled:
        return False
    segments = []
    for index, previous in enumerate(wave.fronts):
        if index in wave.blocked:
            continue
        angle = wave.yaw + math.radians(wave.cone * (index / 100 - .5))
        x, z = wave.origin[0] + math.sin(angle) * distance, wave.origin[2] + math.cos(angle) * distance
        ground = world.ground(x, z, previous[1] + wave.height, previous[1] - wave.height, MOVER)
        if ground is None or ground.normal[1] < .5:
            wave.blocked.add(index)
            continue
        current = (x, ground.y, z)
        # A waist-height chord rejects walls while allowing ground-height changes
        # up to the declared step envelope. Exact native terrain traversal is open.
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
        normal = None
        if ice is not None:
            limit = ice[0]
            normal = ice[2]
        elif wall is not None:
            normal = wall.normal
        wall_clip = None
        if normal is not None:
            wave.blocked.add(index)
            if limit <= 0:
                continue
            current = tuple(p + v * limit for p, v in zip(previous, toward, strict=True))
            # Orient the wall plane toward the blocked side of the advancing ray.
            if sum(v * n for v, n in zip(toward, normal, strict=True)) < 0:
                normal = tuple(-n for n in normal)
            wall_clip = (current, normal)
        start = (previous[0], previous[1] + .1, previous[2])
        end = (current[0], current[1] + .1, current[2])
        shield = _shield(fight, poses, start, end)
        pose = None
        if shield is not None and (wall_clip is None or shield[0] < limit):
            at, pose, toward = shield
            feet, yaw, pitch, radius, height, body, owner, state = pose
            carried = isinstance(state, barriers.Barrier)
            generation, activation = (fight._barrier_activation(owner) if carried
                                      else (fight.replacement_ticks.get(body), None))
            key = (body, generation, activation)
            if owner is not None and key not in wave.shield_hit:
                fight._damage_barrier((at, owner, state), wave.amount)
                wave.shield_hit.add(key)
                remaining = state.current if carried else state.health
                if remaining <= 0:
                    if radius is None and yaw is None:
                        wave.dome_poses[body] = feet
                    else:
                        captured = (feet, yaw, pitch, radius, height, body)
                        if captured not in wave.shield_poses:
                            wave.shield_poses.append(captured)
            current = tuple(start[k] + toward[k] * at - (.1 if k == 1 else 0) for k in range(3))
            wave.blocked.add(index)
        segments.append((previous, current, wall_clip, pose))
        wave.fronts[index] = current
    for target in targets:
        key = (target.entity, fight.replacement_ticks.get(target.entity))
        if key in wave.hit or fight.vitals_of(target).dead:
            continue
        if any(-.1 <= target.feet[1] - end[1] <= wave.height
               and _distance_xz(target.feet, start, end) <= target.radius
               and _before_wall(target.feet, wall_clip)
               and _before_shield(fight, target.feet, start, pose)
               for start, end, wall_clip, pose in segments):
            _hurt(fight, wave, target, wave.amount)
    wave.travelled = distance
    return distance >= wave.reach
