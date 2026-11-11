"""Reconstructed ground propagation for the authored 104319 Earthshatter."""

import math
from dataclasses import dataclass, field

from ow174.game import actors, barrier
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
    vitals = fight.vitals_of(target)
    before = vitals.total
    fight._damage(wave.shooter, target, definition.amount, False, definition.loadout, fight.tick,
                  world_tick=fight.tick, source_body=wave.state.instance.component.entity)
    if not isinstance(target.owner, actors.Deployable) and not vitals.dead and vitals.total < before:
        fight.stun(target, wave.stun_seconds)


def close_hit(fight, wave):
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


def advance(fight, wave):
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
