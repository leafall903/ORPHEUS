"""Host the authored Earthshatter ground queries, effects and wave parameters."""

import math
from dataclasses import dataclass, field

from ow174.game import fortify, reinhardt_barrier
from ow174.game.collision import MOVER
from ow174.game.reinhardt_offense import HERO
from ow174.game.script import expr, runtime

GRAPH = 0x00BE
RAYS = 101


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
    shield_hit: set[int] = field(default_factory=set)
    shield_poses: list = field(default_factory=list)
    team: int = 0
    started_tick: int = 0
    scale: float = 1.0
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
    found = []
    for barrier in reinhardt_barrier.enemy_barriers(fight, wave.shooter, team=wave.team):
        if barrier.mesh:
            found.append((barrier.entity, reinhardt_barrier.pose(barrier), barrier.mesh, barrier))
    found.extend((*pose, None) for pose in wave.shield_poses)
    return found


def _shield(poses, start, end):
    from ow174.game.barrier_geometry import _dot, _sub, ray_mesh

    delta = _sub(end, start)
    length = math.hypot(*delta)
    if not length:
        return None
    toward = tuple(v / length for v in delta)
    best = None
    for pose in poses:
        _, (anchor, basis), mesh, _ = pose
        local = _sub(start, anchor)
        distance = ray_mesh(mesh, tuple(_dot(local, axis) for axis in basis),
                            tuple(_dot(toward, axis) for axis in basis), length)
        if distance is not None and (best is None or distance < best[0]):
            best = distance, pose, toward
    return best


def _hurt(fight, wave, target, amount, *, near=False):
    key = (target.entity, fight.replacement_ticks.get(target.entity))
    (wave.near_hit if near else wave.hit).add(key)
    amount *= wave.scale
    if reinhardt_barrier.protect_body(fight, target, amount, attacker=wave.body):
        return
    vitals = fight.vitals_of(target)
    before = vitals.total
    fight._damage(wave.shooter, target, amount, False, wave.loadout, fight.tick, world_tick=fight.tick,
                  tags=wave.near_tags if near else wave.tags, source_body=wave.body, source_scaled=True)
    if (not target.is_device and not vitals.dead and vitals.total < before
            and not fortify.active(fight, target.owner)):
        fight.apply_stun(target, wave.duration, extend_only=True)


def _before_shield(point, start, pose):
    if pose is None:
        return True
    # Capsule overlap with a clipped endpoint does not expose a body behind the shield.
    origin = (start[0], start[1] + .1, start[2])
    end = (point[0], point[1] + .1, point[2])
    return _shield((pose,), origin, end) is None


def advance(fight, wave):
    """Sample101 rays across the authored cone; retain front, terrain and shield history."""
    from ow174.game.combat import capsule_visible, segment_distance

    world = fight._collision()
    poses = _shield_poses(fight, wave)
    targets = fight._enemies(wave.shooter, team=wave.team)
    if not wave.fronts:
        wave.fronts = [wave.origin] * RAYS
    if not wave.near_done:
        wave.near_done = True
        for target in targets:
            a, b = target.segment()
            if segment_distance(wave.near_centre, a, b) > wave.near_radius + target.radius:
                continue
            end = (target.feet[0], target.feet[1] + .1, target.feet[2])
            start = (wave.origin[0], wave.origin[1] + .1, wave.origin[2])
            if (capsule_visible(fight._line_collision(), start, a, b, target.radius)
                    and _shield(poses, start, end) is None):
                _hurt(fight, wave, target, wave.near_amount, near=True)
    distance = min(wave.reach, max(0.0, (fight.now - wave.started) * wave.speed))
    if distance <= wave.travelled:
        return False
    segments = []
    for index, previous in enumerate(wave.fronts):
        if index in wave.blocked:
            continue
        angle = wave.yaw + math.radians(wave.cone * (index / (RAYS - 1) - .5))
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
        shield = _shield(poses, start, end)
        pose = None
        if shield is not None and (wall_clip is None or shield[0] < limit):
            at, pose, toward = shield
            entity, _, _, barrier = pose
            if barrier is not None and entity not in wave.shield_hit:
                reinhardt_barrier.damage(barrier, wave.amount * wave.scale, attacker=wave.body)
                wave.shield_hit.add(entity)
                if barrier.vitals.total <= 0:
                    wave.shield_poses.append(pose[:3])
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
               and _before_shield(target.feet, start, pose)
               for start, end, wall_clip, pose in segments):
            _hurt(fight, wave, target, wave.amount)
    wave.travelled = distance
    return distance >= wave.reach

def ground(world):
    match = world.match
    if match is None or match.collision_world is None:
        return None
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if player is None:
        return None
    x, y, z = player.mover.position
    return match.collision_world.ground(x, z, y + .05, y - 1.5, player.mover.category)


def prepare(world, player):
    script = player.body_script
    if script is None or script.hero != HERO:
        return
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    if instance is None:
        return
    component.set(417, player.mover.airborne)
    air = instance.states.get(8)
    if air is not None and air.active and not player.mover.airborne:
        gate = instance.graph.node(44)
        delay = expr.to_float(air.evaluate(gate.field("m_AA6E611E"), watch=False))
        if component.now - air.start >= delay * 1000 and instance.follow(gate, "m_truePlug", air):
            air.request(runtime.ABORT)
    tracker = instance.states.get(37)
    if tracker is None or not tracker.active:
        return
    surface = ground(world)
    instance.write((0, 10360), surface is not None)
    if surface is not None:
        x, _, z = player.mover.position
        instance.write((0, 1724), expr.Vec3(x, surface.y, z))
        marker = instance.states.get(29)
        if marker is not None and marker.active:
            marker.request_finish()


def start(fight, player):
    from ow174.game.combat import damage_tags

    script = player.body_script
    if script.hero != HERO or player.body in fight.shatters:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    state = instance.states.get(0) if instance is not None else None
    if state is None or not state.active:
        return
    origin, near = (instance.find_var(0, key).value() for key in (431, 1188))
    if not isinstance(origin, expr.Vec3) or not isinstance(near, expr.Vec3):
        return

    def number(name):
        return expr.to_float(state.evaluate(state.node.field(name), watch=False))

    base, close = (instance.state(key).node.field("m_modifyHealth") for key in (1, 41))
    loadout = state.evaluate(base["m_loadout"], watch=False)
    wave = Wave(
        player, player.body, instance.id, state.counter, tuple(origin),
        player.mover.yaw * math.tau / 65536, number("m_speed"), number("m_6A79BD98"),
        number("m_A30B383E"), number("m_525E5A40"), expr.to_float(instance.find_var(0, 215).value()),
        expr.to_float(state.evaluate(base["m_amount"], watch=False)),
        expr.to_float(state.evaluate(close["m_amount"], watch=False)), tuple(near),
        expr.to_float(state.evaluate(instance.state(40).node.field("m_radius"), watch=False)),
        loadout.guid if isinstance(loadout, expr.Asset) else None, fight.now,
        team=player.team, started_tick=fight.tick, scale=fight.outgoing_scale(player.body),
        tags=damage_tags(state, base), near_tags=damage_tags(state, close),
    )
    if wave.speed > 0 and wave.reach > 0:
        fight.shatters[player.body] = wave
        advance(fight, wave)


def update(fight):
    for body, wave in tuple(fight.shatters.items()):
        if (wave.shooter.body == body and not wave.shooter.gone and not fight.match.ended
                and not advance(fight, wave)):
            continue
        del fight.shatters[body]
        script = wave.shooter.body_script
        if script is None or wave.shooter.body != body or script.component.owner_dead:
            continue
        instance = script.component.instances.get(wave.instance)
        state = instance.states.get(0) if instance is not None else None
        if state is not None and state.active and state.counter == wave.activation:
            state.request(runtime.FINISH)
            script.component.run_now()


def enter(world, state):
    if (world.hero == HERO and not state.instance.component.strict
            and (state.instance.graph.index, state.index) == (GRAPH, 29) and ground(world) is not None
            and all(state.node.field(key) is None for key in (
                "m_ED97A97C", "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5",
            ))):
        state.request_finish()
