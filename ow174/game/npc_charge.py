"""Drive Heavy Assault's authored 1408 charge during a manual engagement."""

import math

from ow174.game import bots, combat, npc_melee, npc_motion
from ow174.game.mover import mover_data
from ow174.game.script import expr

BUTTON, ABILITY, WINDUP, MOTION, RECOVERY = 35, 82, 137, 71, 50
PHASE, COOLDOWN_DURATION, DIRECTION = 59, 472, 3192
FLOOR_TOLERANCE = 0.8


def _value(instance, key):
    var = instance.vars.get(key)
    return var.value() if var is not None else None


def _state(instance, position):
    node = instance.graph.node(position)
    return instance.states.get(node.state) if node is not None else None


def _ability(bot):
    if bot.hero == npc_melee.HEAVY and bot.body_script is not None:
        return npc_melee.ability_at(bot.body_script.component, npc_melee.CHARGE_GRAPH, ABILITY)
    return None


def stop(bot):
    charge = _ability(bot)
    if charge is None:
        return
    instance = charge.instance
    if BUTTON in instance.component.held:
        instance.component.edges([(BUTTON, False)])
    if not expr.to_int(_value(instance, PHASE)):
        return
    charge.presses.clear()
    charge.stop()
    instance.write((expr.INSTANCE, PHASE), 0)
    for position in (WINDUP, MOTION, RECOVERY):
        state = _state(instance, position)
        if state is not None and state.active:
            state.end(False)
    pending = [_state(instance, position) for position in (WINDUP, MOTION, RECOVERY)]
    instance.drop_begins([state for state in pending if state is not None])
    duration = expr.to_float(_value(instance, COOLDOWN_DURATION))
    if charge.remaining() <= 0 and math.isfinite(duration) and duration > 0:
        instance.write((expr.INSTANCE, 61), duration)
    if not bot.airborne:
        bot.velocity = (0.0, 0.0, 0.0)
        bot.held = (0, 0)


def _lane(match, world, bot, target, data):
    start = bot.position
    end = target.mover.position
    distance = math.dist(start, end)
    if not 8 <= distance <= 16 or not npc_motion.teammate_clear(match, bot, end, data):
        return False
    floor = npc_motion.supported_lane(world, data, start, end[0], end[2])
    return floor is not None and abs(floor - end[1]) <= data.step_down


def _move(fight, world, bot, motion, direction, data):
    rows = motion.node.field('m_BC5E91CF')
    if not rows or rows[0].get('$') != 'STU_09F9B1FE':
        return False
    velocity = motion.evaluate(rows[0].get('m_9B7A63EA'), watch=False)
    if not isinstance(velocity, expr.Vec3):
        return False
    speed = math.hypot(velocity.x, velocity.z)
    if not math.isfinite(speed) or speed <= 0:
        return False
    x, y, z = bot.position
    dx, dz = velocity.x / speed, velocity.z / speed
    nx, nz = x + dx * speed * combat.TICK, z + dz * speed * combat.TICK
    floor = npc_motion.floor_at(world, data, nx, y, nz)
    if floor is None:
        return False
    destination = (nx, floor, nz)
    if not npc_motion.teammate_clear(fight.match, bot, destination, data):
        return False
    yaw = bots.heading(direction.x, direction.z)
    # Keep the authored horizontal speed while following the supported floor.
    speed = math.dist(bot.position, destination) / combat.TICK
    npc_motion.drive(fight, world, bot, data, destination, speed, yaw)
    progress = (bot.position[0] - x) * dx + (bot.position[2] - z) * dz
    if progress > 0.01:
        bot.held = bot.last_held = (0, 127)
        bot.input_tick = fight.tick
    return progress > 0.01


def update(fight, bot, target, enabled):
    charge = _ability(bot)
    if charge is None:
        return False
    instance = charge.instance
    if not enabled or bot.airborne:
        stop(bot)
        return False
    if BUTTON in instance.component.held:
        instance.component.edges([(BUTTON, False)])
    world = fight._collision()
    phase = expr.to_int(_value(instance, PHASE))
    if world is None or abs(bot.position[1] - target.mover.position[1]) >= FLOOR_TOLERANCE:
        stop(bot)
        return False
    data = mover_data(bot.body)
    if not phase:
        if not charge.active or charge.remaining() > 0 or not _lane(fight.match, world, bot, target, data):
            return False
        instance.component.edges([(BUTTON, True)])
        return True
    if phase == 2:
        bot.velocity = (0.0, 0.0, 0.0)
        bot.held = (0, 0)
        recovery = _state(instance, RECOVERY)
        if recovery is None or not recovery.active or recovery.now - recovery.start >= 500:
            stop(bot)
        return True
    direction = _value(instance, DIRECTION)
    if not isinstance(direction, expr.Vec3) or math.hypot(direction.x, direction.z) < 0.01:
        dx, _, dz = combat.aim(bot.yaw, 0)
        direction = expr.Vec3(dx, 0.0, dz)
        instance.write((expr.INSTANCE, DIRECTION), direction)
    motion = _state(instance, MOTION)
    if motion is not None and motion.active and (
        motion.now - motion.start >= 2000 or not _move(fight, world, bot, motion, direction, data)
    ):
        stop(bot)
    return True
