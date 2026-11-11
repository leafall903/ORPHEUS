"""Explicit server reconstruction of104319 Earthshatter's ground propagation.

Authored speed/range/cone/damage/control drive a sampled ground front. This is
not the missing native Shockwave implementation or its skeletal presentation.
"""

import math
from dataclasses import dataclass, field

from ow174.game import barrier_mesh, orisa_barrier, sigma_barrier, winston_barrier
from ow174.game.collision import MOVER


@dataclass
class Wave:
    shooter: object
    body: int
    team: int
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
    hit: set = field(default_factory=set)
    near_hit: set = field(default_factory=set)
    shield_hit: set = field(default_factory=set)
    shield_poses: list = field(default_factory=list)


def _distance_xz(point, a, b):
    dx, dz = b[0] - a[0], b[2] - a[2]
    squared = dx * dx + dz * dz
    share = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[2] - a[2]) * dz) / squared)) if squared else 0
    return math.hypot(point[0] - a[0] - share * dx, point[2] - a[2] - share * dz)


def _shield_poses(fight, wave):
    barriers = fight.reinhardt_barriers
    found = []
    for owner in barriers.enemies(wave.shooter, team=wave.team):
        if isinstance(owner, winston_barrier.Bubble):
            pose = None, owner.centre, (), owner.entity
        elif isinstance(owner, (sigma_barrier.Barrier, orisa_barrier.Barrier)):
            pose = owner.geometry, owner.position, owner.basis, owner.entity
        else:
            data = owner.mover.values
            position, axes = barrier_mesh.frame(barriers.geometry, owner.mover.position, owner.mover.yaw,
                                                owner.mover.pitch, data.stand_radius, data.stand_height)
            pose = barrier_mesh.mesh(barriers.geometry), position, axes, owner.body
        found.append((pose, owner))
    # A shield which stopped the wave keeps its sampled pose for the rest of that wave.
    found.extend((pose, None) for pose in wave.shield_poses)
    return found


def _shield(poses, start, end):
    delta = tuple(b - a for a, b in zip(start, end, strict=True))
    length = math.hypot(*delta)
    if not length:
        return None
    toward = tuple(v / length for v in delta)
    found = []
    for pose, owner in poses:
        mesh, position, axes, _ = pose
        distance = (winston_barrier.intersection(position, start, toward, length) if mesh is None
                    else barrier_mesh.intersect_pose(mesh, position, axes, start, toward, length))
        if distance is not None:
            found.append((distance, pose, owner, toward))
    return min(found, key=lambda item: item[0]) if found else None


def _hurt(fight, wave, target, amount, *, near=False):
    if fight._damage(wave.shooter, target, amount, False, wave.loadout, fight.tick,
                     world_tick=fight.tick, source_entity=wave.body, ultimate_damage=True):
        fight.stun(target, wave.duration)
    (wave.near_hit if near else wave.hit).add(target.entity)


def _front_reaches(wave, target, start, end, poses):
    x, y, z = target.feet
    if not (-.1 <= y - end[1] <= wave.height and _distance_xz(target.feet, start, end) <= target.radius):
        return False
    # Capsule overlap can extend behind a shield that stopped the sampled front.
    return not poses or _shield(poses, (start[0], start[1] + .1, start[2]), (x, y + .1, z)) is None


def advance(fight, wave):
    """Sample101 rays across the authored cone; retain front, terrain and shield history."""
    from ow174.game.combat import segment_distance

    world = fight._collision()
    poses = _shield_poses(fight, wave)
    targets = fight._enemies(wave.shooter, team=wave.team)
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
            if (fight.combat_visible(start, a, b, target.radius, exclude=wave.shooter)
                    and _shield(poses, start, end) is None):
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
        wall = None
        if length:
            wall = fight.cover_ray(start, tuple(v / length for v in delta), length, exclude=wave.shooter)
        if wall is not None:
            wave.blocked.add(index)
            continue
        start = (previous[0], previous[1] + .1, previous[2])
        end = (x, ground.y + .1, z)
        shield = _shield(poses, start, end)
        if shield is not None:
            at, pose, owner, toward = shield
            identity = pose[3]
            if pose not in wave.shield_poses:
                wave.shield_poses.append(pose)
            if owner is not None and identity not in wave.shield_hit:
                fight.reinhardt_barriers.damage(owner, wave.amount)
                wave.shield_hit.add(identity)
            current = tuple(start[k] + toward[k] * at - (.1 if k == 1 else 0) for k in range(3))
            wave.blocked.add(index)
        segments.append((previous, current))
        wave.fronts[index] = current
    for target in targets:
        if target.entity in wave.hit or fight.vitals_of(target).dead:
            continue
        if any(_front_reaches(wave, target, start, end, poses) for start, end in segments):
            _hurt(fight, wave, target, wave.amount)
    wave.travelled = distance
    return distance >= wave.reach
