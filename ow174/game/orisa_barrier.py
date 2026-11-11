"""Host Orisa's thrown Protective Barrier and its landed 0BD8 graph."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import contacts
from ow174.game.collision import NOT_GROUND
from ow174.game.fortify import HERO
from ow174.game.script import expr, graph, runtime
from ow174.paths import DATA_DIR

THROW = (0x0BD7, 1)
LANDED = 0x0BD8
PROJECTILE = 0x0400000000001846
ENTITY = 0x04000000000014A1
PATH = Path(os.environ.get("ORPHEUS_ORISA_COLLISION", DATA_DIR / "orisa_collision_174.json"))


@lru_cache(maxsize=2)
def geometry(path):
    if not path.is_file():
        return (), None
    raw = path.read_bytes()
    data = json.loads(raw)
    if (data.get("schema"), data.get("build")) != ("orpheus-orisa-barrier-v1", 104319):
        raise ValueError("unsupported Orisa barrier geometry")
    rows = data["triangles"]
    if not rows or any(len(row) != 9 or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
                       for row in rows):
        raise ValueError("invalid Orisa barrier triangles")
    bottom = min(row[i] for row in rows for i in (1, 4, 7))
    mesh = tuple(tuple((row[i], row[i + 1] - bottom, row[i + 2]) for i in (0, 3, 6)) for row in rows)
    return mesh, hashlib.sha256(raw).hexdigest()


@dataclass
class Flight:
    owner: object
    source: runtime.Component
    entity: int
    team: int
    position: tuple
    velocity: tuple
    yaw: int
    gravity: float
    remaining: float
    tick: int


@dataclass
class Shield:
    owner: object
    source_body: int
    team: int
    position: tuple
    yaw: int
    component: runtime.Component
    mesh: tuple
    geometry_digest: str
    vitals: object
    end_tick: int
    retired: bool = False

    @property
    def entity(self):
        return self.component.entity

    def active(self):
        instance = next((i for i in self.component.instances.values() if i.graph.index == LANDED), None)
        scope = instance.states.get(0) if instance is not None else None
        return not self.retired and self.vitals.total > 0 and scope is not None and scope.active

    def pose(self):
        yaw = self.yaw * math.tau / 65536
        sine, cosine = math.sin(yaw), math.cos(yaw)
        return self.position, ((cosine, 0, -sine), (0, 1, 0), (sine, 0, cosine))

    def damage(self, amount):
        self.vitals.damage(amount)
        self.component.set(253, self.vitals.total)
        if self.vitals.total <= 0:
            self.component.set(478, True)
        self.component.run_now()

    def overlaps_melee(self, origin, centre, radius, collision):
        anchor, axes = self.pose()
        local = tuple(sum((centre[k] - anchor[k]) * axis[k] for k in range(3)) for axis in axes)
        for triangle in self.mesh:
            point = contacts.closest_on_triangle(*triangle, local)
            if math.dist(point, local) > radius:
                continue
            world = tuple(anchor[k] + sum(point[i] * axes[i][k] for i in range(3)) for k in range(3))
            offset = tuple(a - b for a, b in zip(world, origin, strict=True))
            distance = math.hypot(*offset)
            if distance <= 1e-9 or collision.cast_ray(
                origin, tuple(v / distance for v in offset), distance,
            ) is None:
                return True
        return False


def launch(fight, player, state, origin, direction, yaw):
    node = state.node
    impact = node.field("m_49CBA3BC") or {}
    graph_id = expr.guid_of((impact.get("m_410BF8BD") or {}).get("m_graph"))
    if (expr.guid_of(node.field("m_projectileEntity.m_entityDef.m_entityDef")) != PROJECTILE
            or expr.guid_of((impact.get("m_entityDefinition") or {}).get("m_entityDef")) != ENTITY
            or graph_id != (0x0580000000000000 | LANDED)):
        return
    if not geometry(PATH)[0] or graph.graph(LANDED) is None:
        return
    launch_flight(fight, fight.orisa_flights, player, state, origin, direction, yaw)


def launch_flight(fight, flights, player, state, origin, direction, yaw):
    node = state.node
    speed = expr.to_float(state.evaluate(node.field("m_projectileSpeed"), watch=False))
    lifetime = expr.to_float(state.evaluate(node.field("m_projectileLifetime"), watch=False))
    motion, = node.field("m_projectileMotions")
    gravity = expr.to_float(state.evaluate(motion.get("m_599AB8D7"), watch=False))
    if not all(math.isfinite(value) and value > 0 for value in (speed, lifetime, gravity)):
        return
    entity = fight.projectiles.allocate_seen_entity()
    flights[player.body] = Flight(
        player, state.instance.component, entity, player.team, origin,
        tuple(speed * value for value in direction), yaw, gravity, lifetime, fight.tick,
    )
    state.instance.write(expr.dynamic(node.field("m_505472EF")), expr.Entity(entity))


def land(fight, flight, position, tick):
    from ow174.game.combat import Vitals
    from ow174.game.reinhardt_barrier import retire

    entity = fight.projectiles.allocate_seen_entity()
    parent_world = flight.source.world
    world = type(parent_world)(HERO, parent_world.mode, entity)
    world.match = fight.match
    component = runtime.Component(entity, flight.source.frame_ms, world)
    component.frame, component.now = tick, tick * component.frame_ms
    mesh, digest = geometry(PATH)
    barrier = Shield(flight.owner, flight.source.entity, flight.team, position, flight.yaw,
                     component, mesh, digest, Vitals(entity, 0), tick)
    fight.barriers[entity] = barrier
    instance = component.play_loaded_graph(graph.graph(LANDED), source=barrier.source_body,
                                           instigator=barrier.source_body)
    component.run_now()
    pool = expr.to_float(instance.vars[587].value())
    countdown = instance.states.get(10)  # 0BD8/29 writes the entity's remaining duration
    expiry = countdown.expiry_frame() if countdown is not None else None
    if pool <= 0 or expiry is None:
        retire(fight, entity)
        return
    barrier.vitals = Vitals(entity, pool)
    tail = expr.to_float(instance.evaluate(instance.graph.node(35).field("m_timeout")))
    barrier.end_tick = expiry + math.ceil(tail * 1000 / component.frame_ms) + 2
    component.set(253, pool)
    component.set(478, False)
    for old in tuple(fight.barriers.values()):
        if isinstance(old, Shield) and old.owner is flight.owner and old is not barrier:
            retire(fight, old.entity)


def advance(fight, tick, seconds):
    advance_flights(fight, fight.orisa_flights, tick, seconds, land)


def advance_flights(fight, flights, tick, seconds, on_land):
    for source, flight in tuple(flights.items()):
        script = flight.owner.body_script
        if (flight.owner.gone or script is None or script.retired or script.component is not flight.source
                or fight.is_dead(flight.owner) or flight.remaining <= 0):
            del flights[source]
            continue
        dt = min((tick - flight.tick) * seconds, flight.remaining)
        if dt <= 0:
            continue
        flight.tick = tick
        flight.remaining -= dt
        x, y, z = flight.velocity
        travel = (x * dt, (y - flight.gravity * dt / 2) * dt, z * dt)
        velocity = (x, y - flight.gravity * dt, z)
        distance = math.hypot(*travel)
        hit = (
            fight._line_collision().cast_ray(flight.position, tuple(v / distance for v in travel), distance)
            if distance > 1e-9 else None
        )
        if hit is None:
            flight.position = tuple(a + b for a, b in zip(flight.position, travel, strict=True))
            flight.velocity = velocity
        elif hit.normal[1] > 0.5 and y <= 0 and not hit.surface & NOT_GROUND:
            on_land(fight, flight, hit.point, tick)
            del flights[source]
        else:
            projection = sum(a * b for a, b in zip(velocity, hit.normal, strict=True))
            flight.velocity = tuple(a - 1.5 * projection * b
                                    for a, b in zip(velocity, hit.normal, strict=True))
            flight.position = tuple(a + 0.002 * b for a, b in zip(hit.point, hit.normal, strict=True))


def replay_until(fight, previous, seconds):
    # The throw can land in an omitted prefix and still leave a twenty-second barrier.
    flight_end = max((f.tick + math.ceil((f.remaining + 21) / seconds)
                      for f in fight.orisa_flights.values()), default=previous)
    landed_end = max((b.end_tick for b in fight.barriers.values() if isinstance(b, Shield)), default=previous)
    return max(flight_end, landed_end)
