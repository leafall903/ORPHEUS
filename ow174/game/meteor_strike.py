"""Selected 0C51 Meteor Strike stages and reconstructed spatial services."""

import math
from dataclasses import dataclass, replace

from ow174.game import bots, rocket_punch
from ow174.game.collision import NOT_GROUND
from ow174.game.script import expr, runtime

GRAPH = 0x0C51
LOADOUT = 0x0B90000000000108
IMPACT = (GRAPH, 19)
# Bound extra static tick replay for one cast, including selection timeout and recovery.
MAX_RETAINED_TICKS = 512


@dataclass
class Cast:
    activation: tuple
    target: tuple
    confirm_down: bool = False
    committed: bool = False
    impacted: bool = False
    finished: bool = False
    hit: tuple = ()


def phase_of(player):
    if getattr(getattr(player, "hero", None), "guid", None) != rocket_punch.HERO:
        return None
    script = player.body_script
    if script is None or script.retired or script.component.owner_dead or script.component.strict:
        return None
    instance = next((i for i in script.component.instances.values()
                     if i.graph.index == GRAPH and not i.stopped and not i.teardown), None)
    return instance.states.get(24) if instance is not None else None


def protected(player):
    phase = phase_of(player)
    if phase is None or not phase.active:
        return False
    cast = player.match.combat.meteors.get(player.body)
    return cast is None or (not cast.impacted and not cast.finished)


def aiming(phase):
    state = phase.instance.states.get(18) if phase is not None else None
    return state is not None and state.active


def freezes_pose(player):
    phase = phase_of(player)
    return phase is not None and phase.active and not aiming(phase)


def prepare_command(fight, player, command):
    phase = phase_of(player)
    if phase is None or not phase.active:
        return command
    selecting = aiming(phase)
    cast = fight.meteors.get(player.body)
    down = bool(command.buttons & 1)
    if cast is not None:
        if selecting and down and not cast.confirm_down:
            instance = phase.instance
            delay = max(0, command.frame * instance.component.frame_ms - instance.component.now)
            instance.state(18).request(runtime.FINISH, delay)
        cast.confirm_down = down
    # Confirm with a fresh primary edge, without passing that edge to the weapon.
    return replace(command, buttons=command.buttons & 2 if selecting else 0, action=0,
                   forward=command.forward if selecting else 0, right=command.right if selecting else 0)


def ground(fight, player, instance):
    x, y, z = player.mover.position
    reach = instance.graph.node(127).field("m_F5732403.m_6A79BD98.m_value")
    world = fight._collision()
    hit = world.ground(x, z, y + .2, y - reach, player.mover.category)
    if hit is None or hit.normal[1] <= player.mover.slope_cos or hit.surface & NOT_GROUND:
        return None
    return x, hit.y, z


def stop_motion(player):
    mover = player.mover
    mover.driven = mover.launching = False
    mover.velocity = (0., 0., 0.)


def visible(fight, landing, target):
    from ow174.game.combat import EPSILON, ray_world

    x, y, z = landing
    tx, ty, tz = target.feet
    # Reconstructed landing and target sockets for the authored three-ray group.
    for start_y, end_y in ((.5, .5), (.6, .3 * target.height), (.6, .6 * target.height)):
        start, end = (x, y + start_y, z), (tx, ty + end_y, tz)
        delta = tuple(b - a for a, b in zip(start, end, strict=True))
        length = math.hypot(*delta)
        if length <= EPSILON:
            return True
        direction = tuple(axis / length for axis in delta)
        if (ray_world(fight._collision(), start, direction, length) is None
                and not fight.walls.blocked(start, end)):
            return True
    return False


def strike(fight, player, instance, cast, frame):
    from ow174.game.mei_wall import Walls

    stop_motion(player)
    Walls.place_mover(player.mover, cast.target, ((0.0, 1.0, 0.0),))
    radius = expr.to_float(instance.find_var(expr.INSTANCE, 6252).value())
    found, distances = [], []
    definition = instance.graph.node(7)
    for target in fight._enemies(player):
        distance = math.dist(target.feet, cast.target)
        if distance > radius or not visible(fight, cast.target, target):
            continue
        instance.write((expr.INSTANCE, 680), distance)
        amount = expr.to_float(instance.evaluate(instance.graph.node(6).field("m_value")))
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(player, target, amount, False, LOADOUT, frame)
        if vitals.total < before:
            found.append(target.entity)
            distances.append(distance)
            rocket_punch.grant_shield(fight, player, gain_node=21)
        if (vitals.total >= before or vitals.dead or isinstance(target.owner, bots.Bot)
                or fight.fortify_control_immune(target)):
            continue
        instance.write((expr.INSTANCE, 9275), instance.evaluate(instance.graph.node(139).field("m_value")))
        horizontal = expr.to_float(instance.evaluate(definition.field("m_modifyHealth.m_E33EFEE1")))
        up = expr.to_float(instance.evaluate(definition.field("m_modifyHealth.m_583C208B")))
        dx, dz = target.feet[0] - cast.target[0], target.feet[2] - cast.target[2]
        length = math.hypot(dx, dz) or 1
        velocity = (dx / length * horizontal, up, dz / length * horizontal)
        if all(map(math.isfinite, velocity)):
            fight.npc_impulses[target.entity] = (fight.tick, velocity)
    cast.hit = tuple(found)
    instance.write((expr.INSTANCE, 375), tuple(expr.Entity(entity) for entity in found))
    instance.write((expr.INSTANCE, 9326), tuple(distances))


def command(fight, player, command, *, defer_impact=False):
    if player.hero.guid != rocket_punch.HERO:
        return
    phase = phase_of(player)
    cast = fight.meteors.get(player.body)
    if phase is None or not phase.active:
        if cast is not None:
            cast.finished = True
        return
    instance = phase.instance
    activation = instance.id, phase.counter, phase.start
    if cast is None or cast.activation != activation:
        if command is None:
            return
        player.body_script.world_frame_anchor = (fight.tick, player.body_script.frame)
        cast = Cast(activation, tuple(player.mover.position))
        cast.finished = phase.start < instance.component.now - instance.component.frame_ms
        fight.meteors[player.body] = cast
        stop_motion(player)
    if cast.finished:
        return
    if command is None:
        cast.confirm_down = False
    if not cast.committed:
        if command is not None and aiming(phase) and (target := ground(fight, player, instance)) is not None:
            cast.target = target
        descent = instance.states.get(16)
        if descent is not None and descent.active:
            cast.committed = True
            instance.write((expr.INSTANCE, 431), expr.Vec3(*cast.target))
            stop_motion(player)
    if not defer_impact:
        settle(fight, player, phase, cast)


def settle(fight, player, phase, cast):
    instance = phase.instance
    impact = instance.states.get(10)
    if impact is None or not impact.counter or impact.start < phase.start or cast.impacted:
        return
    cast.impacted = True
    if instance.component.now - impact.start <= instance.component.frame_ms:
        strike(fight, player, instance, cast, player.body_script.frame)


def settle_world(fight):
    # All bodies' completed input windows settle before a world-driven impact
    # reads recipient protections, regardless of the match's player order.
    for player in fight.match.players:
        cast = fight.meteors.get(player.body)
        if cast is None or cast.finished or cast.impacted:
            continue
        phase = phase_of(player)
        if phase is not None and phase.active:
            settle(fight, player, phase, cast)
