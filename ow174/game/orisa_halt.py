"""Scoped host for the authored Orisa Halt volley and detonation."""

import math

from ow174.game import bots, defenses, interception, phase, reinhardt_charge
from ow174.game.collision import MOVER, TEAM0, TEAM1
from ow174.game.script import expr

GRAPH = 0x0CCB
WINDUP = 0.4
RADIUS = 5.0
PULL_SECONDS = 0.25
EFFECT_SECONDS = 0.65
SPEED = 20.0
SLOW = 0.65


def bind(fight, rocket, state):
    entity = fight.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Halt entity allocator exhausted")
    fight.next_bot += 1
    rocket.halt_entity = entity
    # Flight begins after the world tick that admitted the volley.
    rocket.last_tick = fight.match.tick
    state.instance.write(expr.lvalue(state.node.field("m_505472EF")), expr.Entity(entity))


def owner_instance(rocket):
    owner = rocket.shooter
    if owner.body != rocket.source_entity or owner.body_script is None:
        return None
    return owner.body_script.component.instances.get(rocket.halt_instance)


def flight(world, instance):
    if world.match is None or instance.component.strict or instance.graph.index != GRAPH:
        return None
    return next((r for r in world.match.combat.rockets
                 if r.halt_instance is not None and owner_instance(r) is instance), None)


def native(world, cfg, instance):
    if instance.graph.index != GRAPH or instance.component.strict or world.match is None:
        return NotImplemented
    kind = cfg.get("$")
    if kind == "STU_E72F6348":
        return flight(world, instance) is not None
    if kind == "STU_2B2E2F6D" and cfg.get("m_7BDB02D1") == 0:
        orb = flight(world, instance)
        if orb is not None and expr.to_entity(instance.evaluate(cfg["m_81A364DE"])) == orb.halt_entity:
            return expr.Entity(orb.source_entity)
    return NotImplemented


def trigger(instance, node):
    if instance.graph.index != GRAPH or node.pos != 26:
        return False
    rocket = flight(instance.component.world, instance)
    if rocket is None:
        return False
    match = instance.component.world.match
    if rocket.halt_at is None and rocket.halt_trigger_tick is None:
        if match.tick > match.combat.tick:
            # The orb can still travel or hit cover before the recast takes effect.
            rocket.halt_trigger_tick = match.tick
        else:
            arm(match.combat, rocket)
    return True


def arm(fight, rocket):
    from ow174.game.combat import TICK

    rocket.halt_at = fight.tick + math.ceil(WINDUP / TICK)
    rocket.halt_trigger_tick = None


def fly(fight, rocket):
    from ow174.game.combat import TICK

    instance = owner_instance(rocket)
    if rocket.halt_instance is not None and instance is None:
        fight._landed(rocket)
        return
    if rocket.halt_at is not None:
        if fight.tick >= rocket.halt_at:
            fight._landed(rocket)
            if instance is not None:
                instance.run_node(instance.graph.node(41), None)
            pulse(fight, rocket)
        return
    elapsed = fight.tick - rocket.last_tick
    if elapsed <= 0:
        return
    rocket.last_tick = fight.tick
    _travel(fight, rocket, instance, min(elapsed * TICK, rocket.left), 8)


def _travel(fight, rocket, instance, duration, reflections):
    from ow174.game.combat import EPSILON, _along, aim

    if duration <= EPSILON:
        if rocket.left <= EPSILON:
            arm(fight, rocket)
        return
    if reflections <= 0:
        fight._landed(rocket)
        return
    reach = duration * rocket.speed
    wall = fight.cover_ray(rocket.position, rocket.direction, reach, exclude=rocket.shooter, tick=fight.tick)
    limit = reach if wall is None else wall
    reflected = interception.reflection(fight, rocket.shooter, rocket.definition, rocket.position,
                                        rocket.direction, limit, team=rocket.team)
    bound = limit if reflected is None else reflected[0]
    absorbed = interception.absorption(fight, rocket.shooter, rocket.definition, rocket.position,
                                       rocket.direction, bound, team=rocket.team)
    contact = absorbed or reflected
    if contact is not None and (wall is None or contact[0] < wall):
        distance, state = contact[:2]
        rocket.position = _along(rocket.position, rocket.direction, distance)
        travelled = distance / rocket.speed
        rocket.flown += travelled
        rocket.left -= travelled
        if rocket.shot is not None:
            fight.projectiles._end(rocket.shot, fight.tick, False)
            rocket.shot = None
        if absorbed is not None:
            fight.rockets.remove(rocket)
        # Defense contact ends the original caster's control of this orb.
        rocket.halt_instance = rocket.halt_trigger_tick = None
        if instance is not None:
            instance.run_node(instance.graph.node(41), None)
        interception.absorbed(state, 0.0)
        if absorbed is None:
            target = reflected[2]
            rocket.shooter, rocket.team, rocket.source_entity = target.owner, target.team, target.entity
            rocket.direction = aim(target.owner.mover.yaw, target.owner.mover.pitch)
            rocket.position = _along(rocket.position, rocket.direction, 1e-4)
            _travel(fight, rocket, None, duration - travelled, reflections - 1)
        return
    distance = reach if wall is None else max(0.0, wall - 0.01)
    rocket.position = _along(rocket.position, rocket.direction, distance)
    rocket.flown += distance / rocket.speed
    rocket.left -= duration
    triggered = rocket.halt_trigger_tick is not None and fight.tick >= rocket.halt_trigger_tick
    if wall is not None or rocket.left <= EPSILON or triggered:
        arm(fight, rocket)


def clock_ticks(fight, tick):
    """Retain bounded flight, pulse, and pull ticks across a scheduler gap."""
    from ow174.game.combat import TICK

    first = fight.tick + 1
    windup = math.ceil(WINDUP / TICK)
    pull_duration = math.ceil(PULL_SECONDS / TICK)
    ticks = set()
    for rocket in fight.rockets:
        if rocket.halt_entity is None:
            continue
        if rocket.halt_at is not None:
            start, pulse_at = rocket.halt_at, rocket.halt_at
        else:
            start = rocket.last_tick + 1
            flight_end = rocket.last_tick + math.ceil(rocket.left / TICK)
            if rocket.halt_trigger_tick is not None:
                flight_end = min(flight_end, rocket.halt_trigger_tick)
            pulse_at = flight_end + windup
        ticks.update(range(max(first, start), min(tick + 1, pulse_at + pull_duration)))
    for _, pull_end, _ in fight.halt_pulls.values():
        ticks.update(range(first, min(tick + 1, pull_end)))
    return ticks


def protected(fight, owner, tick):
    if isinstance(owner, bots.Bot):
        script = owner.body_script
        if owner.spawner.get("manual_spawn") is not True or script is None:
            return True
        immunity = script.component.entity_var(6601, create=False)
        if immunity is not None and expr.truthy(immunity.value()):
            return True
    return (phase.protection(owner, tick=tick) is not None
            or defenses.fortify_state(owner, tick=tick) is not None
            or fight.zarya_barriers.protects(owner, tick=tick))


def pulse(fight, rocket):
    from ow174.game.combat import TICK, segment_distance

    for target in fight._enemies(rocket.shooter, team=rocket.team):
        owner = target.owner
        if (fight.replacement_ticks.get(target.entity, 0) > fight.tick
                or protected(fight, owner, fight.tick)):
            continue
        a, b = target.segment()
        if (segment_distance(rocket.position, a, b) - target.radius > RADIUS
                or not fight.combat_visible(rocket.position, a, b, target.radius,
                                            exclude=rocket.shooter, tick=fight.tick)):
            continue
        x, y, z = rocket.position
        fight.halt_pulls[target.entity] = ((x, y - target.height / 2, z),
                                           fight.tick + math.ceil(PULL_SECONDS / TICK),
                                           fight.tick + math.ceil(EFFECT_SECONDS / TICK))


def update(fight, tick):
    from ow174.game.combat import TICK

    if not fight.halt_pulls:
        return
    targets = {target.entity: target for target in fight._targets()}
    for body, (goal, pull_end, effect_end) in tuple(fight.halt_pulls.items()):
        target = targets.get(body)
        if (target is None or fight.vitals_of(target).dead or protected(fight, target.owner, tick)
                or tick >= effect_end):
            del fight.halt_pulls[body]
            continue
        if (tick >= pull_end or reinhardt_charge.pinned(fight, body)
                or any(fight.zarya_gravitons.pulls(field, target) for field in fight.zarya_gravitons.fields)):
            continue
        owner = target.owner
        npc = isinstance(owner, bots.Bot)
        if npc:
            category = MOVER if fight.match.game_map.free_for_all else (TEAM0, TEAM1)[owner.team]
            mover = owner.ensure_impulse_mover(tick, fight._collision(), category=category)
        else:
            reinhardt_charge.stop(fight, owner)
            fight.stop_genji_strike(owner)
            mover = owner.mover
        mover.driven = True
        mover._move_to(TICK, goal, SPEED, phase.cryo_blockers(fight.match.players, exclude=owner, tick=tick))
        if npc:
            owner.position, owner.velocity = tuple(mover.position), mover.velocity
            owner.pace, owner.held = 0.0, (0, 0)
            owner.last_tick = owner.moved_tick = tick


def moving(fight, body, tick):
    effect = fight.halt_pulls.get(body)
    return effect is not None and tick < effect[1]


def slowing(fight, body, tick):
    effect = fight.halt_pulls.get(body)
    return effect is not None and effect[1] <= tick < effect[2]
