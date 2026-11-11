"""Collision helpers for scripted manual NPC movement."""

import math

from ow174.game import bots, combat
from ow174.game.collision import NOT_GROUND
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data


def floor_at(world, data, x, y, z):
    ground = world.ground(x, z, y + bots.STEP_UP + 0.05, y - data.step_down - 0.05)
    if (ground is None or ground.surface & NOT_GROUND
            or ground.normal[1] < math.cos(math.radians(data.max_slope))):
        return None
    return ground.y


def supported_lane(world, data, start, x, z):
    """Sample connected floor along the lane. The mover checks capsule clearance."""
    sx, y, sz = start
    dx, dz = x - sx, z - sz
    samples = max(1, math.ceil(math.hypot(dx, dz) / 0.25))
    for index in range(1, samples + 1):
        fraction = index / samples
        floor = floor_at(world, data, sx + dx * fraction, y, sz + dz * fraction)
        if floor is None:
            return None
        y = floor
    return y


def teammate_clear(match, bot, destination, data):
    x, y, z = bot.position
    dx, _, dz = (end - axis for end, axis in zip(destination, bot.position, strict=True))
    length = math.hypot(dx, dz)
    if length <= combat.EPSILON:
        return True
    ux, uz = dx / length, dz / length
    for other in match.bots:
        if other is bot or other.dead or other.team != bot.team:
            continue
        ox, oy, oz = other.position
        other_data = mover_data(other.body)
        if y >= oy + other_data.stand_height or oy >= y + data.stand_height:
            continue
        bx, bz = ox - x, oz - z
        radius = data.stand_radius + other_data.stand_radius + 0.02
        along = min(length, max(0.0, bx * ux + bz * uz))
        if (math.hypot(bx - ux * along, bz - uz * along) < radius
                and (along > 0 or math.hypot(bx - dx, bz - dz) <= math.hypot(bx, bz))):
            return False
    return True


def drive(fight, world, bot, data, destination, speed, yaw):
    mover = Mover(bot.position, yaw, world, data)
    outcome = mover.step_pose(Command(fight.tick, yaw=yaw, pitch=round(bot.pitch)),
                              combat.TICK, fight.tick, move_to=(destination, speed))
    bot.position, bot.velocity, bot.yaw = tuple(mover.position), mover.velocity, float(yaw)
    bot.moved_tick = fight.tick
    return outcome
