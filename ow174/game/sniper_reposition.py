"""Static Sniper retreat through the authored 142C MoveTo state."""

import math

from ow174.game import bots
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime

BODY = 0x0400000000001E7D
GRAPH = 0x142C
MOVE_NODE = 16
COOLDOWN_MS = 6000  # Reconstructed from the Rialto navigation checkpoint.


def move_state(bot):
    if bot.body != BODY or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == GRAPH:
            return instance.node_state(instance.graph.node(MOVE_NODE))
    return None


def request(fight, bot, opponent):
    """Reconstruct a grounded retreat destination from an explicit hostile body."""
    from ow174.game import combat

    state = move_state(bot)
    if state is None or state.active or fight.match.collision_world is None:
        return False
    now = fight.match.tick * state.instance.component.frame_ms
    if state.counter and now - state.ended_at < COOLDOWN_MS:
        return False
    target = next((t for t in fight._enemies(bot) if t.owner is opponent), None)
    if target is None:
        return False
    x, y, z = bot.position
    dx, dz = x - target.feet[0], z - target.feet[2]
    distance = math.hypot(dx, dz)
    if not 0.1 <= distance <= 3. or abs(y - target.feet[1]) > .75:
        return False
    world = fight._collision()
    eye = (x, y + combat.EYE, z)
    a, b = target.segment()
    target_eye = (a[0], target.feet[1] + target.eye, a[2])
    if (not combat.capsule_visible(world, eye, a, b, target.radius)
            or fight.walls.blocked(eye, target_eye)):
        return False
    data = mover_data(bot.body)
    for angle in (0., math.pi / 4, -math.pi / 4):
        cosine, sine = math.cos(angle), math.sin(angle)
        direction = ((dx * cosine - dz * sine) / distance, 0., (dz * cosine + dx * sine) / distance)
        if combat.ray_world(world, eye, direction, 4.) is not None or fight.walls.cast(eye, direction, 4.):
            continue
        nx, _, nz = combat._along(bot.position, direction, 4.)
        floor = world.ground(nx, nz, y + .5, y - .5)
        if floor is None or floor.normal[1] < math.cos(math.radians(data.max_slope)):
            continue
        destination = (nx, floor.y, nz)
        end_eye = (nx, floor.y + combat.EYE, nz)
        if (not combat.capsule_visible(world, end_eye, a, b, target.radius)
                or fight.walls.blocked(end_eye, target_eye)):
            continue
        bot.body_script.advance_live(fight.match.tick, bot.position)
        state.instance.write((expr.INSTANCE, 1220), expr.Vec3(*destination))
        state.request(runtime.BEGIN)
        state.instance.component.run_now()
        if state.move_request() is None:
            if state.active:
                state.end(False)
            return False
        fight.sniper_repositions[bot.entity] = Mover(bot.position, round(bot.yaw), world, data)
        return True
    return False


def finish(fight, bot, reached=False) -> bool:
    if fight.sniper_repositions.pop(bot.entity, None) is None:
        return False
    state = move_state(bot)
    if state is not None and state.active:
        state.end(reached)
        state.instance.component.run_now()
    bot.velocity = (0., 0., 0.)
    return True


def step(fight, bot, frame):
    from ow174.game import combat

    mover = fight.sniper_repositions[bot.entity]
    state = move_state(bot)
    movement = state.move_request() if state is not None else None
    if (movement is None or mover.world is not fight.match.collision_world or bot.dead
            or fight.frozen(bot, tick=frame) or fight.is_stunned(bot, tick=frame)):
        finish(fight, bot)
        return
    destination, speed = movement
    x, _, z = bot.position
    heading = bots.heading(destination.x - x, destination.z - z)
    yaw = bots.turned(bot.yaw, heading, bots.TURN_RATE * combat.TICK)
    outcome = mover.step_pose(Command(frame, yaw=round(yaw)), combat.TICK, frame,
                              move_to=(tuple(destination), speed))
    position, normals = fight.walls.constrain(bot.position, tuple(mover.position),
                                             mover.data.stand_radius, mover.data.stand_height)
    bot.position, bot.velocity, bot.yaw = position, mover.velocity, float(yaw)
    bot.moved_tick = frame
    if normals or outcome is not None:
        finish(fight, bot, reached=bool(outcome) and not normals)
