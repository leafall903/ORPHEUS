"""Reconstructed ground and aerial Seismic Slam using the 0B27 resources."""

import math
from dataclasses import dataclass, field

from ow174.game import bots, rocket_punch
from ow174.game.collision import NOT_GROUND
from ow174.game.script import expr, nodes

GRAPH = 0x0B27


@dataclass
class Cast:
    owner: object
    state: object
    activation: int
    direction: tuple
    airborne: bool
    destination: tuple | None = None
    speed: float = 0.0
    origin: tuple | None = None
    wave_tick: int | None = None
    hit: set[int] = field(default_factory=set)
    finished: bool = False


def needs_pose(cast):
    return cast is not None and not cast.finished and cast.origin is None


def needs_ticks(cast):
    return cast is not None and (
        not cast.finished or bool(cast.state.instance.state(0).flags & nodes.Ability.RUNNING)
    )


def needs_clock(cast):
    return needs_ticks(cast) or (cast is not None and cast.state.instance.state(0).remaining() > 0)


def register(fight, cast):
    player = cast.owner
    fight.slams[player.body] = cast
    script = player.body_script
    if script.world_frame_anchor is None:
        script.world_frame_anchor = fight.tick, script.frame


def instance_of(player):
    if player.hero.guid != rocket_punch.HERO or player.body_script is None or player.body_script.retired:
        return None
    component = player.body_script.component
    if component.strict or component.owner_dead:
        return None
    return next((instance for instance in component.instances.values()
                 if instance.graph.index == GRAPH and not instance.stopped and not instance.teardown), None)


def observe(fight, player):
    from ow174.game.combat import ray_world

    instance = instance_of(player)
    if instance is None:
        return
    airborne = player.mover.airborne
    instance.component.set(417, airborne)
    x, y, z = player.mover.position
    reach = expr.to_float(instance.evaluate(instance.graph.node(17).field("m_castLength")))
    near_ground = ray_world(fight._collision(), (x, y + .25, z), (0, -1, 0), reach)
    instance.write((expr.INSTANCE, 2413), near_ground is not None)
    cast = fight.slams.get(player.body)
    if cast is None or cast.finished or cast.origin is not None or cast.destination is not None:
        return
    if not instance.state(0).flags & nodes.Ability.RUNNING:
        cast.finished = True
    elif cast.airborne and not airborne:
        cast.origin = tuple(player.mover.position)
        instance.write((expr.INSTANCE, 59), 3)
    else:
        cast.airborne |= airborne


def aerial_target(fight, player, instance):
    from ow174.game.combat import _along, aim, ray_world

    search = instance.states.get(6)
    if search is None or not search.active:
        return None
    direction = aim(player.mover.yaw, player.mover.pitch)
    reach = expr.to_float(search.evaluate(search.node.field("m_6386E7B4"), watch=False))
    eye = fight._eye(player)
    world = fight._collision()
    distance = ray_world(world, eye, direction, reach) if direction[1] < -1e-6 and reach > 0 else None
    if distance is None:
        return None
    x, y, z = _along(eye, direction, distance)
    ground = world.ground(x, z, y + .1, y - .1, player.mover.category)
    if ground is None or ground.normal[1] <= player.mover.slope_cos or ground.surface & NOT_GROUND:
        return None
    feet = (x, ground.y, z)
    blocked = ray_world(world, (x, ground.y + .05, z), (0, 1, 0), player.mover.data.stand_height)
    if blocked is not None or fight.walls.blocked(eye, feet):
        return None
    return feet


def commit_aerial(fight, player, instance):
    from ow174.game.combat import aim

    search = instance.states.get(6)
    if search is None or not search.active:
        return
    destination = aerial_target(fight, player, instance)
    instance.write((expr.INSTANCE, 1627), destination is not None)
    held = instance.states.get(64)
    if destination is None:
        return
    instance.write((expr.INSTANCE, 431), expr.Vec3(*destination))
    instance.write((expr.INSTANCE, 1883), None)
    if held is None or not held.active or not held.down:
        return
    # Supply the target to the actual validity and held-input gates.
    instance.follow(search.node, "m_transitionPlug[0]", search)
    instance.follow(search.node, "m_F6955E81", search)
    instance.component.run_now()
    move = instance.states.get(9)
    if move is not None and move.active:
        speed = expr.to_float(move.evaluate(move.node.field("m_speed"), watch=False))
        if speed > 0 and math.isfinite(speed):
            register(fight, Cast(
                player, move, move.counter, aim(player.mover.yaw, 0), True, destination, speed,
            ))


def movement(fight, player):
    cast = fight.slams.get(player.body)
    if cast is None or cast.finished or cast.destination is None or cast.origin is not None:
        return None
    instance = cast.state.instance
    move = instance.states.get(9)
    if move is not None and move.active and instance.state(0).flags & nodes.Ability.RUNNING:
        return move, (cast.destination, cast.speed)
    return None


def step_pose(fight, player, command, seconds, tick, before, ground, slam):
    state, movement = slam
    mover = player.mover
    outcome = mover.step_pose(command, seconds, tick, move_to=movement, ground_override=ground)
    swept = tuple(mover.position)
    fight.walls.move_player(player, before)
    if tuple(mover.position) != swept:
        outcome = False
    if outcome is None:
        return None
    movement_result(fight, player, outcome)
    return state, outcome


def movement_result(fight, player, outcome):
    cast = fight.slams[player.body]
    if outcome:
        cast.origin = tuple(player.mover.position)
    else:
        cast.finished = True
    player.mover.driven = False
    player.mover.velocity = (0., 0., 0.)


def command(fight, player, command):
    from ow174.game.combat import aim

    cast = fight.slams.get(player.body)
    if command is None:
        settle_landing(fight, cast)
        return
    instance = instance_of(player)
    if instance is None:
        return
    phase = instance.state(1)
    ability = instance.state(0)
    phase_value = instance.find_var(expr.INSTANCE, 59).value()
    if phase_value == 2 and ability.flags & nodes.Ability.RUNNING:
        if cast is None or cast.finished or cast.destination is None:
            commit_aerial(fight, player, instance)
        return
    if (phase_value == 1 and phase.active and ability.flags & nodes.Ability.RUNNING
            and (cast is None or cast.state is not phase or cast.activation != phase.counter)):
        cast = Cast(player, phase, phase.counter, aim(player.mover.yaw, 0), player.mover.airborne)
        cast.finished = phase.start < instance.component.now - instance.component.frame_ms
        register(fight, cast)
    settle_landing(fight, cast)


def settle_landing(fight, cast):
    if cast is None or cast.finished or cast.wave_tick is not None:
        return
    instance = cast.state.instance
    if cast.origin is None:
        if not instance.state(0).flags & nodes.Ability.RUNNING:
            cast.finished = True
        return
    recovery = instance.states.get(37)
    if recovery is not None and recovery.active:
        recovery.request_finish()
        cast.wave_tick = fight.tick


def advance(fight, cast):
    from ow174.game.combat import TICK, _dot, _sub, capsule_visible, hit_def

    instance = cast.state.instance
    wave = instance.graph.node(95)
    speed = expr.to_float(instance.evaluate(wave.field("m_speed")))
    reach = expr.to_float(instance.find_var(expr.INSTANCE, 422).value())
    angle = expr.to_float(instance.evaluate(wave.field("m_A30B383E")))
    damage = instance.node_state(instance.graph.node(93))
    hit = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
    if not (speed > 0 and reach > 0 and 0 < angle <= 360) or hit is None or hit.amount <= 0:
        cast.finished = True
        return
    horizontal = expr.to_float(damage.evaluate(damage.node.field("m_modifyHealth.m_E33EFEE1"), watch=False))
    vertical = expr.to_float(damage.evaluate(damage.node.field("m_modifyHealth.m_583C208B"), watch=False))
    radius = min(reach, speed * (fight.tick - cast.wave_tick) * TICK)
    threshold = math.cos(math.radians(angle / 2))
    x, y, z = cast.origin
    origin = (x, y + 1, z)
    for target in fight._enemies(cast.owner):
        if target.entity in cast.hit:
            continue
        dx, dy, dz = _sub(target.feet, cast.origin)
        distance = math.hypot(dx, dz)
        if distance > radius + target.radius or abs(dy) > 1.45:
            continue
        direction = (dx / distance, 0, dz / distance) if distance > 1e-9 else cast.direction
        if _dot(direction, cast.direction) < threshold:
            continue
        a, b = target.segment()
        if (not capsule_visible(fight._collision(), origin, a, b, target.radius)
                or fight.walls.blocked(origin, b)):
            continue
        cast.hit.add(target.entity)
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(cast.owner, target, hit.amount, False, hit.loadout, fight.tick, world_tick=fight.tick)
        if vitals.total < before:
            rocket_punch.grant_shield(fight, cast.owner)
            if (not vitals.dead and not isinstance(target.owner, bots.Bot)
                    and not fight.fortify_control_immune(target)):
                velocity = (horizontal * direction[0], vertical, horizontal * direction[2])
                if all(map(math.isfinite, velocity)):
                    fight.npc_impulses[target.entity] = (fight.tick, velocity)
    if radius >= reach:
        cast.finished = True


def update(fight):
    for body, cast in tuple(fight.slams.items()):
        if cast.owner.body != body or cast.owner.gone or fight.is_dead(cast.owner):
            fight.slams.pop(body, None)
        elif cast.wave_tick is not None and not cast.finished:
            advance(fight, cast)


def interrupt(fight, owner):
    cast = fight.slams.get(owner.body)
    if cast is not None and cast.wave_tick is None:
        cast.finished = True
