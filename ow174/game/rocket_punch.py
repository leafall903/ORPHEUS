"""Reconstructed 104319 Rocket Punch contact and wall transport."""

import math
from dataclasses import dataclass

from ow174.game import bots, contacts, defenses, reaper, sigma_grasp
from ow174.game.mover import STEP
from ow174.game.script import expr, graph, nodes

HERO = 0x02E000000000012F
GRAPH = 0x0D06
LOADOUT = 0x0B90000000000107


@dataclass
class Dash:
    state: object
    activation: int
    direction: tuple
    destination: tuple
    speed: float
    radius: float
    damage: float
    wall_damage: float
    force: float
    control_ms: int
    finished: bool = False


@dataclass
class WallImpact:
    owner: object
    source_body: int
    direction: tuple
    damage: float
    deadline: int


def direction_query(world, cfg, instance):
    if (cfg.get("$") != "STU_021D01CD" or instance is None or instance.graph.index != GRAPH
            or world.hero != HERO or world.match is None or instance.stopped or instance.teardown
            or cfg is not instance.graph.node(87).field("m_value")):
        return NotImplemented
    for player in world.match.players:
        if (player.has_body and player.body == world.body and player.body_script is not None
                and player.body_script.component is instance.component and not player.body_script.retired):
            from ow174.game.combat import aim

            return expr.Vec3(*aim(player.mover.yaw, 0))
    return NotImplemented


def active(dash):
    return (dash is not None and not dash.finished
            and dash.state.active and dash.activation == dash.state.counter)


def recovering(dash):
    if dash is None or not dash.finished:
        return False
    ability = dash.state.instance.state(0)
    return bool(ability.flags & nodes.Ability.RUNNING)


def needs_clock(dash):
    return (active(dash) or recovering(dash)
            or (dash is not None and dash.finished and dash.state.instance.state(0).remaining() > 0))


def step_pose(fight, player, command, seconds, tick, before, ground, dash):
    mover = player.mover
    script = player.body_script
    if script.world_frame_anchor is None:
        script.world_frame_anchor = fight.tick, command.frame
    outcome = mover.step_pose(
        command, seconds, tick, move_to=(dash.destination, dash.speed), ground_override=ground,
    )
    swept = tuple(mover.position)
    fight.walls.move_player(player, before)
    if tuple(mover.position) != swept:
        outcome = False
    advance(fight, player, command, dash, before, outcome)


def prepare(fight, player):
    if player.hero.guid != HERO or player.body_script is None or fight.is_dead(player):
        return None
    instance = next((i for i in player.body_script.component.instances.values()
                     if i.graph.index == GRAPH), None)
    state = instance.states.get(7) if instance is not None else None
    if state is None or not state.active:
        fight.punch_dashes.pop(player.body, None)
        return None
    dash = fight.punch_dashes.get(player.body)
    if dash is None or dash.state is not state or dash.activation != state.counter:
        dash = create(player, instance, state)
        if dash is None:
            return None
        fight.punch_dashes[player.body] = dash
    return None if dash.finished else dash


def create(player, instance, state):
    helper = graph.graph(0x0D07)
    vector = state.evaluate(state.node.field("m_B1003775"), watch=False)
    if helper is None or not isinstance(vector, expr.Vec3):
        return None
    length = math.sqrt(sum(v * v for v in vector))
    duration, low, high = (expr.to_float(instance.find_var(expr.INSTANCE, var).value())
                           for var in (215, 11271, 567))
    speed = expr.to_float(state.evaluate(state.node.field("m_87A4D0AD"), watch=False))
    search = instance.state(6)
    radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    if not all(math.isfinite(v) and v > 0 for v in (length, duration, speed, radius, high - low)):
        return None
    charge = max(0.0, min(1.0, (duration - low) / (high - low)))

    def scaled(minimum, maximum):
        a, b = (expr.to_float(instance.evaluate(helper.node(pos).field("m_value")))
                for pos in (minimum, maximum))
        return a + (b - a) * charge

    direction = tuple(v / length for v in vector)
    destination = tuple(p + d * speed * duration
                        for p, d in zip(player.mover.position, direction, strict=True))
    hold = instance.evaluate(helper.node(1).field("m_timeout"))
    return Dash(state, state.counter, direction, destination, speed, radius,
                scaled(34, 33), scaled(36, 35), scaled(32, 31), nodes.seconds_to_ms(hold))


def advance(fight, player, command, dash, before, outcome):
    from ow174.game.combat import TICK, _along, _dot, _sub, capsule_visible, ray_capsule

    current = tuple(player.mover.position)
    delta = tuple(b - a for a, b in zip(before, current, strict=True))
    length = math.sqrt(sum(v * v for v in delta))
    heading = tuple(v / length for v in delta) if length > 1e-9 else dash.direction
    origin = _along(before, (0, 1, 0), fight._eye(player)[1] - current[1] - .18)
    nearest = None
    for target in fight._enemies(player):
        if _dot(_sub(target.feet, before), dash.direction) < 0:
            continue
        a, b = target.segment()
        distance = ray_capsule(origin, heading, a, b, target.radius + dash.radius)
        if distance is None or distance > length + 1e-9:
            continue
        if (not capsule_visible(fight._collision(), origin, a, b, target.radius)
                or fight.walls.blocked(origin, b)):
            continue
        if nearest is None or distance < nearest[0]:
            nearest = distance, target
    if nearest is not None:
        _, target = nearest
        vitals = fight.vitals_of(target)
        before_health = vitals.total
        fight._damage(player, target, dash.damage, False, LOADOUT, command.frame, world_tick=fight.tick)
        if vitals.total < before_health:
            grant_shield(fight, player)
            if not fight.fortify_control_immune(target) and not vitals.dead:
                fight._hit_effect(target, None, stun_ms=dash.control_ms)
                if not isinstance(target.owner, bots.Bot) and dash.force > 0:
                    velocity = tuple(axis * dash.force for axis in dash.direction)
                    fight.npc_impulses[target.entity] = (fight.tick, velocity)
                    deadline = fight.tick + math.ceil(dash.control_ms / (1000 * TICK))
                    fight.punch_walls[target.entity] = WallImpact(
                        player, player.body, dash.direction, dash.wall_damage, deadline,
                    )
    if nearest is not None or outcome is not None:
        finish(player, dash)


def finish(player, dash):
    dash.finished = True
    player.mover.driven = False
    player.mover.velocity = (0., player.mover.velocity[1], 0.)
    wait = dash.state.instance.states.get(5)
    if wait is not None and wait.active:
        wait.request_finish()


def retire(fight, entity, *, replaced=False):
    fight.punch_dashes.pop(entity, None)
    fight.punch_walls.pop(entity, None)
    fight.doomfist_shields.pop(entity, None)
    if replaced:
        for body, impact in tuple(fight.punch_walls.items()):
            if impact.source_body == entity:
                del fight.punch_walls[body]


def update(fight):
    from ow174.game.combat import _along, ray_world

    if not fight.punch_walls:
        return
    targets = {target.entity: target for target in fight._targets()}
    for body, impact in tuple(fight.punch_walls.items()):
        target = targets.get(body)
        if (fight.tick >= impact.deadline or target is None or fight.vitals_of(target).dead
                or reaper.intangible(target.owner) or defenses.invulnerable(target.owner)
                or fight.fortify_control_immune(target) or impact.owner.body != impact.source_body):
            fight.punch_walls.pop(body, None)
            continue
        centre = _along(target.feet, (0, 1, 0), target.height * .5)
        reach = target.radius + contacts.SKIN + STEP
        if (ray_world(fight._collision(), centre, impact.direction, reach) is not None
                or fight.walls.cast(centre, impact.direction, reach) is not None):
            fight.punch_walls.pop(body, None)
            fight._damage(
                impact.owner, target, impact.damage, False, LOADOUT, fight.tick, world_tick=fight.tick,
            )
        elif body not in fight.npc_impulses:
            fight.punch_walls.pop(body, None)


SHIELD_TAG = 0x0DDD


def grant_shield(fight, player, *, gain_node=20):
    passive = next((i for i in player.body_script.component.instances.values()
                    if i.graph.index == SHIELD_TAG), None)
    target = next((t for t in fight._targets() if t.owner is player), None)
    if passive is None or target is None:
        return
    cap = passive.find_var(expr.INSTANCE, 11218, create=False)
    if cap is None:
        return
    amount = expr.to_float(passive.evaluate(passive.graph.node(gain_node).field("m_value")))
    limit = expr.to_float(cap.value())
    delay = expr.to_float(passive.state(0).evaluate(passive.graph.node(2).field("m_timeout")))
    rate = expr.to_float(passive.state(1).evaluate(passive.graph.node(3).field("m_modifyHealth.m_amount")))
    if not all(math.isfinite(value) and value >= 0 for value in (amount, limit, delay, rate)):
        return
    vitals = fight.vitals_of(target)
    given = vitals.grant_temporary_shields(SHIELD_TAG, amount, limit)
    if given > 0:
        fight.doomfist_shields[player.body] = sigma_grasp.ShieldDecay(fight.now + delay, fight.now, rate)
        fight._sync_body_health(player, vitals)
