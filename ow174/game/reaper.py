"""Scoped Hellfire Shotguns and Reaping facts for build 104319."""

import math
from dataclasses import dataclass, field

from ow174.game import contacts, sigma_hyperspheres
from ow174.game.mover import quantise
from ow174.game.script import expr, runtime

HERO = 0x02E0000000000002
SHOTGUN_GRAPH = 0x0019
REAPING_GRAPH = 0x0075
REAPING_RATIO = 0.35000002  # Authored 0075 state 5 ModifyHealth multiplier.


def reaping_ratio(actor):
    if getattr(getattr(actor, "hero", None), "guid", None) != HERO:
        return 0.0
    script = actor.body_script
    if script is None or script.retired or script.component.owner_dead:
        return 0.0
    if any(instance.graph.index == REAPING_GRAPH and not instance.stopped
           for instance in script.component.instances.values()):
        return REAPING_RATIO
    return 0.0


BLOSSOM_GRAPH = 0x0044
# 104319 effect 4 -> graph 0045's target volley. The root volley is visual.
BLOSSOM_DAMAGE = 34.0
BLOSSOM_INTERVAL = 0.2
BLOSSOM_LOADOUT = 0x0B90000000000010


def blossom_state(script):
    if script is None or script.hero != HERO or script.retired or script.component.owner_dead:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == BLOSSOM_GRAPH:
            state = instance.states.get(3)
            if state is not None and state.active:
                return state
    return None


@dataclass
class Blossom:
    owner: object
    activation: tuple[int, int, int]
    end: float
    radius: float
    due: dict[tuple[str, int], float] = field(default_factory=dict)
    ended: bool = False


def register_blossom(fight, owner, script):
    state = blossom_state(script)
    if state is None:
        return
    activation = state.instance.id, state.counter, state.start
    previous = fight.reaper_blossoms.get(owner.body)
    if previous is not None and previous.activation == activation:
        return
    tracker = state.instance.states.get(5)
    if tracker is None or not tracker.active:
        return
    duration = expr.to_float(state.evaluate(state.node.field("m_timeout"), watch=False))
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    if duration > 0 and radius > 0:
        age = max(0.0, (state.now - state.start) / 1000)
        channel = Blossom(owner, activation, fight.now - age + duration, radius)
        fight.reaper_blossoms[owner.body] = channel
        tick_blossom(fight, channel)


def interrupt_blossom(fight, owner):
    channel = fight.reaper_blossoms.pop(owner.body, None)
    if channel is None:
        return
    state = blossom_state(owner.body_script)
    if state is not None:
        state.request_finish()
        state.instance.component.run_now()


def tick_blossom(fight, channel):
    from ow174.game.combat import EPSILON, ray_world, segment_distance

    if channel.ended:
        return
    owner = channel.owner
    state = blossom_state(owner.body_script)
    if fight.is_dead(owner) or (state is None and fight.now < channel.end):
        channel.ended = True
        channel.due.clear()
        return

    x, y, z = owner.mover.position
    origin = (x, y + 1.8, z)  # Reconstructed owner-relative TrackTargets sample.
    available = {}
    for target in fight._enemies(owner):
        a, b = target.segment()
        if segment_distance(origin, a, b) - target.radius > channel.radius:
            continue
        point = (a[0], (a[1] + b[1]) * 0.5, a[2])
        delta = tuple(p - o for p, o in zip(point, origin, strict=True))
        distance = math.hypot(*delta)
        key = "body", target.entity
        if distance > EPSILON:
            direction = tuple(axis / distance for axis in delta)
            wall = ray_world(fight._collision(), origin, direction, distance)
            if (wall is not None and wall < distance - EPSILON) or fight.walls.blocked(origin, point):
                continue
            shield = sigma_hyperspheres.barrier_contact(fight, owner, origin, direction, distance)
            if shield is not None:
                # Retain the hidden target's cadence across a shield break.
                available[key] = (None, None)
                available[("shield", shield[3])] = (None, shield)
                continue
        available[key] = (target, None)

    for key in channel.due.keys() - available.keys():
        del channel.due[key]
    for key, (target, shield) in available.items():
        if key not in channel.due and fight.now >= channel.end:
            continue
        due = channel.due.get(key, fight.now)
        while due <= fight.now + EPSILON and due < channel.end - EPSILON:
            due += BLOSSOM_INTERVAL
            if target is not None:
                fight._damage(owner, target, BLOSSOM_DAMAGE, False, BLOSSOM_LOADOUT,
                              fight.tick, world_tick=fight.tick)
            elif shield is not None:
                _, damage, args, _ = shield
                damage(*args, BLOSSOM_DAMAGE * fight.offense_scale(owner))
        channel.due[key] = due
    if fight.now >= channel.end:
        channel.ended = True
        channel.due.clear()


SHADOW_GRAPH = 0x00F2


@dataclass
class Shadow:
    selector: tuple[int, int]
    buttons: int
    point: tuple[float, float, float] | None = None
    cast: tuple[int, int] | None = None
    delay_ms: int = 0


def _shadow_fits(fight, owner, point):
    from ow174.game import mei_wall

    mover = owner.mover
    radius, segment = mover._capsule(False)
    world = fight._collision()
    ground = world.ground(point[0], point[2], point[1] + 0.1, point[1] - 0.1)
    if ground is None or ground.normal[1] < mover.slope_cos:
        return False
    body = contacts.CharacterBody(radius, segment + radius, world=world.engine, category=mover.category)
    body.teleport((point[0], point[1] + mover.data.stand_pogo, point[2]))
    if not body.fits(radius, segment + radius):
        return False
    if any(mei_wall.ray(piece, point, (0, 0, 0), 0, radius=radius, height=mover.data.stand_height)
           is not None for piece in fight.walls.pieces.values()):
        return False
    return not any(
        target.owner is not owner and not fight.vitals_of(target).dead
        and abs(point[1] - target.feet[1]) < max(mover.data.stand_height, target.height)
        and math.hypot(point[0] - target.feet[0], point[2] - target.feet[2]) < radius + target.radius
        for target in fight._targets()
    )


def command_shadow(fight, owner, script, command):
    from ow174.game.combat import aim

    instance = next((i for i in script.component.instances.values() if i.graph.index == SHADOW_GRAPH), None)
    if instance is None:
        return
    selector = instance.states.get(0)
    record = fight.reaper_shadows.get(owner.body)
    if not command.buttons & 16:
        idle, targeting, casting = (instance.states.get(index) for index in (1, 6, 19))
        if (idle is not None and not idle.active and not (targeting and targeting.active)
                and not (casting and casting.active)):
            idle.request(runtime.BEGIN)
            script.component.run_now()
    if selector is not None and selector.active:
        activation = instance.id, selector.counter
        if record is None or record.selector != activation:
            record = Shadow(activation, command.buttons)
            fight.reaper_shadows[owner.body] = record
        origin = fight._eye(owner)
        direction = aim(*command.aim())
        reach = expr.to_float(selector.evaluate(selector.node.field("m_6386E7B4"), watch=False))
        hit = fight._collision().cast_ray(origin, direction, reach)
        valid = (hit is not None and hit.normal[1] >= owner.mover.slope_cos
                 and fight.walls.cast(origin, direction, hit.distance) is None
                 and _shadow_fits(fight, owner, hit.point))
        record.point = tuple(hit.point) if valid else None
        instance.write((0, 983), valid)
        if hit is not None:
            instance.write((0, 432), expr.Vec3(*hit.point))
            instance.write((0, 985), expr.Vec3(*hit.point))
        pressed = command.buttons & ~record.buttons
        record.buttons = command.buttons
        if pressed & 2:
            instance.state(6).request(runtime.ABORT)
            script.component.run_now()
            fight.reaper_shadows.pop(owner.body, None)
        elif pressed & 1 and valid:
            instance.follow(selector.node, "m_F6955E81", selector)
            script.component.run_now()
            cast = instance.states.get(32)
            if cast is not None and cast.active:
                record.cast = cast.counter, cast.start
                record.delay_ms = round(1000 * expr.to_float(cast.evaluate(
                    cast.node.field("m_ED97A97C"), watch=False,
                )))
                cast.request_finish(max(0, record.delay_ms - (cast.now - cast.start)))
        return
    if record is None:
        return
    if record.cast is None:
        fight.reaper_shadows.pop(owner.body, None)
        return
    cast = instance.states.get(32)
    if cast is None or cast.active:
        return
    fight.reaper_shadows.pop(owner.body, None)
    point = record.point
    if ((cast.counter, cast.start) != record.cast or cast.ended_at - cast.start < record.delay_ms
            or point is None or not _shadow_fits(fight, owner, point)):
        return
    mover = owner.mover
    mover.position[:] = [quantise(axis) for axis in point]
    mover.velocity = (0.0, 0.0, 0.0)
    mover.spring = mover.spring2 = mover.spring_speed = mover.fall = 0.0
    mover.spring_steps = (0, 0)
    mover.body = None
    if mover.history and mover.history[-1].frame == script.frame:
        mover.history[-1] = mover.snapshot(script.frame)


def interrupt_shadow(fight, owner, script):
    if fight.reaper_shadows.pop(owner.body, None) is None:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == SHADOW_GRAPH:
            for index in (6, 19):
                state = instance.states.get(index)
                if state is not None and state.active:
                    state.request(runtime.ABORT)
    script.component.run_now()
