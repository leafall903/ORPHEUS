"""104319 Blizzard flight and area service for 064A state 3."""

import math
from dataclasses import dataclass, field

from ow174.game import (
    barrier_pet,
    defenses,
    mei_frost,
    orisa_deployable,
    sigma_barrier,
    winston_barrier,
    zarya_barrier,
)
from ow174.game.script import expr

DEFINITION = 0x0400000000000946
LOADOUT = 0x0B900000000000D5
DELAY_MS, DURATION_MS = 200, 4250  # 065E
RADIUS, DAMAGE_RATE = 10.0, 20.0
FRAME_MS = 16


@dataclass
class Flight:
    owner: object
    body: int
    position: tuple
    velocity: tuple
    gravity: float
    left: float
    tick: int
    shot: object
    flown: float = 0.0


@dataclass
class Area:
    entity: int
    owner: object
    body: int
    point: tuple
    ends: int
    sampled: int
    frost_due: dict[int, int] = field(default_factory=dict)


def launch(fight, player, state, shot_frame, origin, direction, speed, lifetime, *, activation, snapshot):
    if state.instance.graph.index != 0x064A or state.index != 3:
        return False
    motion, = state.node.field("m_projectileMotions")
    gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
    shot = fight.projectiles.fired(player, state, shot_frame, DEFINITION, lifetime, origin, direction,
                                  activation=activation, snapshot=snapshot)
    fight.blizzard_flights.append(Flight(
        player, player.body, origin, tuple(axis * speed for axis in direction),
        gravity, lifetime, fight.tick, shot))
    return True


def end(fight, flight):
    fight.blizzard_flights.remove(flight)
    if flight.shot is not None:
        fight.projectiles.flown(flight.shot, flight.flown)


def land(fight, flight, point, at):
    end(fight, flight)
    entity = fight.next_bot
    if entity >= 0xD0000000:
        raise ValueError("Blizzard entity allocator exhausted")
    fight.next_bot += 1
    starts = at * FRAME_MS + DELAY_MS
    fight.blizzards[entity] = Area(entity, flight.owner, flight.body, point, starts + DURATION_MS, starts)


def sweep(fight, flight, seconds, at, reflections=0):
    from ow174.game.combat import _along, aim, ray_world

    vx, vy, vz = flight.velocity
    delta = vx * seconds, (vy - flight.gravity * seconds / 2) * seconds, vz * seconds
    length = math.hypot(*delta)
    if length <= 1e-9:
        flight.left -= seconds
        flight.velocity = vx, vy - flight.gravity * seconds, vz
        return False
    direction = tuple(axis / length for axis in delta)
    wall = ray_world(fight._collision(), flight.position, direction, length)
    ice = fight.walls.cast(flight.position, direction, length)
    ice_impact = ice is not None and (wall is None or ice[0] < wall)
    if ice_impact:
        wall = ice[0]
    gate = (defenses.projectile_gate(flight.position, direction, length, fight._enemies(flight.owner),
                                     matrix_eligible=True) if reflections < 8 else None)
    if gate is not None and (wall is None or gate[0] < wall):
        distance, target, mode, _ = gate
        spent = seconds * distance / length
        flight.flown += spent
        if mode != 0:
            end(fight, flight)
            return True
        flight.position = _along(flight.position, direction, distance)
        flight.owner, flight.body = target.owner, target.entity
        speed = math.hypot(vx, vy - flight.gravity * spent, vz)
        facing = aim(target.owner.mover.yaw, target.owner.mover.pitch)
        flight.velocity = tuple(axis * speed for axis in facing)
        flight.left -= spent
        return sweep(fight, flight, seconds - spent, at, reflections + 1)
    if wall is not None:
        flight.flown += seconds * wall / length
        offset = min(wall, .001) if ice_impact else 0.0
        land(fight, flight, _along(flight.position, direction, wall - offset), at)
        return True
    flight.position = _along(flight.position, direction, length)
    flight.velocity = vx, vy - flight.gravity * seconds, vz
    flight.left -= seconds
    flight.flown += seconds
    return False


def affect(fight, area, at):
    from ow174.game.combat import segment_distance

    until = min(at * FRAME_MS, area.ends)
    elapsed = until - area.sampled
    if elapsed <= 0:
        return
    enemies = fight._enemies(area.owner)
    team = area.owner.team
    colliders = barrier_pet.active_colliders(fight, enemies)
    devices = (winston_barrier.colliders(fight, area.body, team)
               + orisa_deployable.colliders(fight, area.body, team)
               + sigma_barrier.colliders(fight, area.body, team))
    protected = {bubble.body for bubble in zarya_barrier.active(fight)}
    origin = area.point[0], area.point[1] + .1, area.point[2]
    retained = {}
    for target in enemies:
        if (target.entity in protected or defenses.phase(target.owner)[0]
                or segment_distance(area.point, *target.segment()) - target.radius > RADIUS
                or not fight._blast_reaches(origin, target, colliders, devices)):
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(area.owner, target, DAMAGE_RATE * elapsed / 1000, False, LOADOUT, at,
                      world_tick=at)
        if vitals.dead or vitals.total >= before:
            continue
        due = area.frost_due.get(target.entity, area.sampled)
        while due < until:
            mei_frost.hit(fight, target, area.owner, channel=area.entity, initial=.5, increment=.040000003)
            due += 200
        retained[target.entity] = due
    area.frost_due = retained
    area.sampled = until


def update(fight, at, dt):
    for flight in tuple(fight.blizzard_flights):
        for tick in range(flight.tick + 1, at + 1):
            if sweep(fight, flight, min(dt, flight.left), tick):
                break
            flight.tick = tick
            if flight.left <= 1e-9:
                end(fight, flight)
                break
    for entity, area in tuple(fight.blizzards.items()):
        affect(fight, area, at)
        if at * FRAME_MS >= area.ends:
            del fight.blizzards[entity]


def retire(fight, body):
    for flight in tuple(fight.blizzard_flights):
        if flight.body == body:
            end(fight, flight)
    for entity, area in tuple(fight.blizzards.items()):
        if area.body == body:
            del fight.blizzards[entity]
