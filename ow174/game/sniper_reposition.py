"""Selected Sniper 142C movement with a reconstructed close-range retreat."""

import math

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime

HERO = 0x02E00000000001B8
COOLDOWN_MS = 6000


def state_of(bot, *, create=True):
    if bot.hero != HERO or bot.body_script is None:
        return None
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == 0x142C:
            node = instance.graph.node(16)
            return instance.node_state(node) if create else instance.states.get(node.state)
    return None


def stop(bot):
    state = state_of(bot, create=False)
    if state is not None and state.active:
        state.request(runtime.ABORT)
    if bot.hero == HERO:
        bot.special_mover = None


def update(fight, bot, target):
    state = state_of(bot)
    world = fight._collision()
    if state is None or world is None:
        return False
    if state.active:
        motion = state.move_request()
        if motion is None:
            state.request(runtime.ABORT)
            return True
        mover = bot.special_mover
        if mover is None or mover.world is not world:
            mover = bot.special_mover = Mover(bot.position, round(bot.yaw), world, mover_data(bot.body))
        destination, speed = motion
        tick = fight.tick
        outcome = mover.step_pose(Command(tick, yaw=round(bot.yaw)), combat.TICK, tick,
                                  move_to=(tuple(destination), speed))
        bot.position, bot.velocity = tuple(mover.position), mover.velocity
        bot.moved_tick = tick
        if outcome is not None:
            state.request(runtime.FINISH if outcome else runtime.ABORT)
            bot.special_mover = None
        return True
    if state.ended_at and state.now-state.ended_at < COOLDOWN_MS:
        return False
    x, y, z = bot.position
    tx, ty, tz = target.mover.position
    dx, dz = x-tx, z-tz
    distance = math.hypot(dx, dz)
    if not .1 <= distance <= 3 or abs(y-ty) > .75:
        return False
    direction = (dx/distance, 0, dz/distance)
    if combat.ray_world(world, (x, y+combat.EYE, z), direction, 4) is not None:
        return False
    nx, nz = x+4*direction[0], z+4*direction[2]
    floor = world.ground(nx, nz, y+.5, y-.5)
    if floor is None:
        return False
    state.instance.write((expr.INSTANCE, 1220), expr.Vec3(nx, floor.y, nz))
    state.request(runtime.BEGIN)
    return True
