"""Static Sniper reposition through the authored 142C MoveTo state.

The sandbox destination and cooldown follow the Rialto navigation checkpoint.
Native anchor search and controller messaging are separate from this adapter.
"""

import math

from ow174.game import bots
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime

BODY = 0x0400000000001E7D
GRAPH = 0x142C
MOVE_NODE = 16
COOLDOWN_MS = 6000


def move_state(bot):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == GRAPH:
            return instance.node_state(instance.graph.node(MOVE_NODE))
    return None


def blocked(fight, bot):
    vitals = fight.vitals.get(bot.entity)
    return fight.is_stunned(bot) or (vitals is not None and fight.now < vitals.hack_until)


def request(match, bot, opponent):
    """Explicit static controller request against a current hostile body."""
    from ow174.game import combat

    fight = match.combat
    if match.ended or bot not in match.bots or blocked(fight, bot):
        return False
    state = move_state(bot)
    world = fight._collision()
    if state is None or world is None or state.active:
        return False
    now = match.tick * state.instance.component.frame_ms
    if state.ended_at is not None and now - state.ended_at < COOLDOWN_MS:
        return False
    target = next((t for t in fight._enemies(bot) if t.owner is opponent), None)
    if target is None:
        return False
    x, y, z = bot.position
    dx, dz = x - target.feet[0], z - target.feet[2]
    distance = math.hypot(dx, dz)
    if not 0.1 <= distance <= 3 or abs(y - target.feet[1]) > 0.75:
        return False
    a, b = target.segment()
    eye = (x, y + combat.EYE, z)
    if not combat.capsule_visible(world, eye, a, b, target.radius):
        return False
    profile = mover_data(bot.body)
    for angle in (0, math.pi / 4, -math.pi / 4):
        cosine, sine = math.cos(angle), math.sin(angle)
        direction = ((dx * cosine - dz * sine) / distance, 0.0, (dz * cosine + dx * sine) / distance)
        if combat.ray_world(world, eye, direction, 4.0) is not None:
            continue
        nx, _, nz = combat._along(bot.position, direction, 4.0)
        floor = world.ground(nx, nz, y + 0.5, y - 0.5)
        if floor is None or floor.normal[1] < math.cos(math.radians(profile.max_slope)):
            continue
        destination = (nx, floor.y, nz)
        if not combat.capsule_visible(world, (nx, floor.y + combat.EYE, nz), a, b, target.radius):
            continue
        bot.body_script.advance_live(match.tick, bot.position)
        state.instance.write((expr.INSTANCE, 1220), expr.Vec3(*destination))
        state.request(runtime.BEGIN)
        state.instance.component.run_now()
        if state.move_request() is None:
            if state.active:
                state.end(False)
            return False
        fight.sniper_repositions[bot.entity] = Mover(bot.position, round(bot.yaw), world, profile)
        return True
    return False


def cancel(fight, bot):
    mover = fight.sniper_repositions.pop(bot.entity, None)
    if mover is None:
        return
    state = move_state(bot)
    if state is not None and state.active:
        state.end(False)
    bot.velocity = (0.0, 0.0, 0.0)


def step(fight, bot, frame):
    from ow174.game import combat

    mover = fight.sniper_repositions.get(bot.entity)
    if mover is None:
        return
    state = move_state(bot)
    movement = state.move_request() if state is not None else None
    if (movement is None or mover.world is not fight._collision()
            or blocked(fight, bot)):
        cancel(fight, bot)
        return
    destination, speed = movement
    x, _, z = bot.position
    yaw = bots.turned(bot.yaw, bots.heading(destination.x - x, destination.z - z),
                      bots.TURN_RATE * combat.TICK)
    outcome = mover.step_pose(Command(frame, yaw=round(yaw), pitch=round(bot.pitch)),
                              combat.TICK, frame, move_to=(tuple(destination), speed))
    bot.position, bot.velocity, bot.yaw = tuple(mover.position), mover.velocity, float(yaw)
    bot.moved_tick = frame
    if outcome is not None:
        state.end(outcome)
        state.instance.component.run_now()
        fight.sniper_repositions.pop(bot.entity, None)
        bot.velocity = (0.0, 0.0, 0.0)
