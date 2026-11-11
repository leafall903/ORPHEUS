"""Authored Supercharger/status graphs with scoped reconstructed world consumers.

Target capsule/visibility, immediate aura departure and maximum nonstacking boost
are reconstruction policies. Native status refresh/linger/stack/credit and network
deployable transport remain open. No static/client acceptance follows from this module.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots
from ow174.game.orisa_barrier import Flight, advance_thrown_device
from ow174.game.script import expr, graph, message_receive, runtime

GRAPH = 0x0CF5
DEFINITION = 0x040000000000174E
APPLY = 0x024000000000000F
_bundle = os.environ.get("ORPHEUS_ORISA_SUPERCHARGER")
SUPPLEMENT_SHA256 = (
    "cd2d64c131ae24e1eae2e1e19df8befd707f003fe126704f831f3369a31c9dbb" if _bundle else None
)


@cache
def resources():
    if not _bundle:
        return None
    raw = Path(_bundle).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SUPPLEMENT_SHA256:
        raise ValueError("Supercharger supplement checksum mismatch")
    data = json.loads(raw)
    graphs = {row["index"]: graph.Graph(row) for row in data["graphs"]}
    triangles = tuple(tuple(tuple(row[k:k+3]) for k in (0, 3, 6))
                      for row in data["geometry"]["triangles"])
    return graphs, triangles


def interrupt_cast(component):
    """End 0CF5's detached windup without touching a released device."""
    for instance in component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        pending = instance.states.get(instance.graph.node(30).state)
        if pending is not None and pending.active:
            # Ability already stopped before this wait began. End the authored
            # wait and its scope without emitting their successful outputs.
            pending.end(False)
            scope = instance.states.get(instance.graph.node(7).state)
            if scope is not None and scope.active:
                scope.end(False)


class DeviceWorld(runtime.World):
    def __init__(self, fight, entity, position):
        super().__init__(mode=fight.match.game_map.mode_guid)
        self.fight, self.entity, self.location = fight, entity, position

    def position(self, entity):
        if entity == self.entity:
            return expr.Vec3(*self.location)
        return self.fight.match.script_body_position(entity, self.entity)


@dataclass
class Device:
    owner: object
    entity: int
    source_body: int
    team: int
    position: tuple
    yaw: int
    until: int
    health: float
    component: runtime.Component
    script: runtime.Instance

    def closest(self, point):
        from ow174.game.contacts import closest_on_triangle

        _, triangles = resources()
        yaw = self.yaw * math.tau / 65536
        sine, cosine = math.sin(yaw), math.cos(yaw)
        x, y, z = (a - b for a, b in zip(point, self.position, strict=True))
        local = (x * cosine - z * sine, y, x * sine + z * cosine)
        x, y, z = min(
            (closest_on_triangle(*triangle, local) for triangle in triangles),
            key=lambda vertex: math.dist(local, vertex),
        )
        px, py, pz = self.position
        return px + x * cosine + z * sine, py + y, pz - x * sine + z * cosine


class Superchargers:
    def __init__(self, fight):
        self.fight = fight
        self.active = {}
        self.buffs = {}
        self.flights = {}

    def launch(self, owner, state, origin, direction, yaw):
        if not SUPPLEMENT_SHA256:
            return
        node = state.node
        impact = node.field("m_49CBA3BC.m_entityDefinition")
        if state.evaluate(impact, watch=False) != expr.Asset(DEFINITION):
            raise ValueError("Supercharger landing resource changed")
        speed = expr.to_float(state.evaluate(node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(node.field("m_projectileLifetime"), watch=False))
        motion, = node.field("m_projectileMotions")
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        body = self.fight.next_bot
        self.fight.next_bot += 1
        self.flights[owner.body] = Flight(owner, state.instance.component, body, origin,
                                         tuple(speed * axis for axis in direction), yaw,
                                         gravity, lifetime, self.fight.tick)

    def land(self, flight, position, tick):
        from ow174.game.combat import TICK

        graphs, _ = resources()
        source_body = flight.source.entity
        entity = self.fight.next_bot
        self.fight.next_bot += 1
        component = runtime.Component(entity, world=DeviceWorld(self.fight, entity, position))
        component.now, component.frame = tick * component.frame_ms, tick
        script = component.create(graphs[0xD00], values={
            (graph.INSTANCE, 40): expr.Entity(source_body),
            (graph.INSTANCE, 27): expr.Entity(source_body),
        })
        component.create(graphs[0x25B4])
        component.run_now()
        health = expr.to_float(script.find_var(graph.INSTANCE, 199).value())
        seconds = expr.to_float(script.find_var(graph.INSTANCE, 215).value())
        if health <= 0 or seconds <= 0:
            raise ValueError("authored Supercharger initialization did not set health/lifetime")
        self.active[entity] = Device(flight.owner, entity, source_body, flight.owner.team, position,
                                     flight.yaw, tick + math.ceil(seconds / TICK), health, component, script)
        for source in flight.source.instances.values():
            if source.graph.index == GRAPH and not source.stopped:
                message_receive.receive(source, message_receive.Packet(
                    0x0240000000000859, entity, source_body))
                source.component.run_now()
                break

    def enemies(self, shooter):
        for device in self.active.values():
            if (device.owner is not shooter and device.health > 0
                    and (self.fight.match.game_map.free_for_all or device.team != shooter.team)):
                yield device

    def trace(self, shooter, origin, direction, reach):
        from ow174.game.barriers import _triangle

        if not self.active:
            return None
        _, triangles = resources()
        best = None
        for device in self.enemies(shooter):
            yaw = device.yaw * math.tau / 65536
            sine, cosine = math.sin(yaw), math.cos(yaw)
            dx, dy, dz = (a - b for a, b in zip(origin, device.position, strict=True))
            vx, vy, vz = direction
            local_o = (dx * cosine - dz * sine, dy, dx * sine + dz * cosine)
            local_d = (vx * cosine - vz * sine, vy, vx * sine + vz * cosine)
            for triangle in triangles:
                distance = _triangle(local_o, local_d, *triangle)
                if distance is not None and distance <= reach and (best is None or distance < best[0]):
                    best = distance, device
        return best

    def damage(self, device, amount):
        device.health = expr.f32(max(0.0, device.health - max(0.0, amount)))
        device.script.write((graph.INSTANCE, 199), device.health)
        if not device.health:
            self.remove(device.entity)

    def remove(self, entity):
        device = self.active.pop(entity, None)
        for key, instance in tuple(self.buffs.items()):
            if key[0] == entity:
                instance.component.destroy(instance)
                del self.buffs[key]
        if device is not None:
            for instance in tuple(device.component.instances.values()):
                device.component.destroy(instance)
            owner = getattr(device.owner, "body_script", None)
            if owner is not None:
                for instance in owner.component.instances.values():
                    if instance.graph.index != GRAPH:
                        continue
                    handle = instance.find_var(graph.INSTANCE, 40, create=False)
                    if handle is not None and handle.value() == expr.Entity(entity):
                        switch = instance.node_state(instance.graph.node(31))
                        if switch.active:
                            switch.dependency_changed()
                            owner.component.run_now()  # Retire the existence flag before the next hit.

    def retire(self, body):
        self.flights.pop(body, None)
        # Landed devices keep their independent deadline.
        for key, instance in tuple(self.buffs.items()):
            if key[1] == body:
                instance.component.destroy(instance)
                del self.buffs[key]

    def update(self, tick, dt):
        for body, flight in tuple(self.flights.items()):
            script = flight.owner.body_script
            if flight.owner.gone or script is None or script.retired or script.component is not flight.source:
                del self.flights[body]
                continue
            for frame in range(flight.tick + 1, tick + 1):
                if flight.remaining <= 0 or advance_thrown_device(
                    self.fight, flight, frame, min(dt, flight.remaining), self.land,
                ):
                    del self.flights[body]
                    break
        for entity, device in tuple(self.active.items()):
            if tick >= device.until or device.health <= 0:
                self.remove(entity)
            else:
                device.component.run_frame(tick)
        self.refresh()

    def refresh(self):
        from ow174.game import barriers
        from ow174.game.combat import ray_world, segment_distance

        wanted = set()
        for device in self.active.values():
            search = device.script.state(1)  # original0D00 TrackTargets
            if search is None or not search.active:
                continue
            radius = expr.to_float(search.evaluate(search.node.field("m_radius")))
            origin = search.evaluate(search.node.field("m_D59188D5"))
            origin = (origin.x, origin.y, origin.z)
            for target in self.fight._targets():
                ally = (not self.fight.match.game_map.free_for_all and target.team == device.team)
                if target.owner is not device.owner and not ally:
                    continue
                if self.fight.vitals_of(target).dead:
                    continue
                a, b = target.segment()
                if segment_distance(device.position, a, b) - target.radius > radius:
                    continue
                # Original endpoint is hardpoint1; capsule eye is an explicit
                # reconstruction until animated hardpoint transforms are restored.
                sample = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                delta = tuple(a - b for a, b in zip(sample, origin, strict=True))
                distance = math.sqrt(sum(axis * axis for axis in delta))
                if distance > 1e-9:
                    direction = tuple(axis / distance for axis in delta)
                    wall = ray_world(self.fight._collision(), origin, direction, distance)
                    if wall is not None and wall < distance - 1e-6:
                        continue
                    if (self.fight.walls.blocked(origin, sample)
                            or barriers.intercept(self.fight, device.owner, origin, direction, distance)):
                        continue
                script = getattr(target.owner, "body_script", None)
                if script is None:
                    continue
                key = device.entity, target.entity
                wanted.add(key)
                if key not in self.buffs:
                    component = script.component
                    instance = component.create(resources()[0][0xD01], values={
                        (graph.INSTANCE, 27): expr.Entity(device.source_body),
                    })
                    message_receive.receive(instance, message_receive.Packet(
                        APPLY, device.entity, device.source_body))
                    component.run_now()  # original message sets#174 and starts CombatModFilter
                    self.buffs[key] = instance
        for key, instance in tuple(self.buffs.items()):
            if key not in wanted:
                instance.component.destroy(instance)
                del self.buffs[key]

    def scale(self, actor):
        if not self.buffs:
            return 1.0
        entity = actor.entity if isinstance(actor, bots.Bot) else actor.body
        values = [1.0]
        for (device, target), instance in self.buffs.items():
            if target != entity or device not in self.active:
                continue
            state = instance.state(3)
            if state is not None and state.active:
                values.append(1.0 + expr.to_float(state.evaluate(state.node.field("m_50530EAB"))))
        return max(values)


def native(world, cfg, instance):
    if (not SUPPLEMENT_SHA256 or world.match is None
            or instance.graph.index != GRAPH or instance.component.strict):
        return NotImplemented
    fight = world.match.combat
    if cfg is instance.graph.node(31).field("m_condition"):
        entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        return entity in fight.superchargers.active
    if cfg is instance.graph.node(34).field("m_condition.m_configVars[0]"):
        target = next((target for target in fight._targets() if target.entity == world.body), None)
        return fight.vitals_of(target).total if target is not None else 0
    return NotImplemented
