"""Heavy Assault 1408 motion and interruption for the explicit NPC route."""

import math

from ow174.game import bots
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr

HERO = 0x02E00000000001BA
GRAPH = 0x1408
BUTTON = 35
CONTACT = (GRAPH, 53, 100)
LIMIT_MS = 3000  # reconstructed limit from the authored phase chase destination


def instance(bot):
    if bot.hero != HERO or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == GRAPH), None)


def stop(bot):
    i = instance(bot)
    if i is None:
        return
    ability, phase, motion = i.states.get(41), i.states.get(36), i.states.get(33)
    if ability is not None:
        ability.presses.clear()
    if phase is not None and phase.active:
        phase.end(False)
        if motion is not None and motion.active:
            motion.end(False)
        if ability is not None:
            ability.stop()
        i.run_node(i.graph.node(6), None)
        i.run_node(i.graph.node(45), None)
        # The windup wait at node137 follows its end plug even on abort.
        # Drop the queued entrance to node71 before it opens motion state33.
        continuation = i.states.get(i.graph.node(71).state)
        i.drop_begins([state for state in (continuation, motion) if state is not None])
    if bot.charge_mover is not None:
        bot.charge_mover = None
        if bot.impulse_mover is None:
            bot.velocity = (0.0, 0.0, 0.0)


def step(fight, bot, tick):
    if bot.entity in fight.zarya_gravitons.pulled:
        return
    i = instance(bot)
    if i is None:
        return
    phase, ability = i.vars.get(59), i.states.get(41)
    if phase is not None and phase.value() == 2 and ability is not None and ability.remaining() == 0:
        i.run_node(i.graph.node(6), None)
    if not bot.firing or bot.impulse_mover is not None or fight.is_stunned(bot, tick=tick):
        stop(bot)
        return
    motion = i.states.get(33)
    if motion is None or not motion.active or motion.ending:
        if bot.charge_mover is not None:
            bot.charge_mover = None
            bot.velocity = (0.0, 0.0, 0.0)
        return
    if (tick + bot.clock_offset) * i.component.frame_ms - motion.start >= LIMIT_MS:
        stop(bot)
        return
    row = motion.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
    vector = motion.evaluate(row, watch=False)
    if not isinstance(vector, expr.Vec3):
        stop(bot)
        return
    speed = math.hypot(vector.x, vector.z)
    if speed <= 0:
        stop(bot)
        return
    from ow174.game.combat import TICK

    world = fight._collision()
    mover = bot.charge_mover
    if mover is None or mover.world is not world or math.dist(mover.position, bot.position) > 0.1:
        mover = bot.charge_mover = Mover(bot.position, round(bot.yaw), world, mover_data(bot.body))
    destination = tuple(a + b for a, b in zip(bot.position, vector, strict=True))
    yaw = bots.heading(vector.x, vector.z)
    outcome = mover.step_pose(Command(tick, yaw=yaw), TICK, tick, move_to=(destination, speed))
    bot.position, bot.velocity, bot.yaw = tuple(mover.position), mover.velocity, float(yaw)
    bot.moved_tick = tick
    if outcome is False:
        stop(bot)
