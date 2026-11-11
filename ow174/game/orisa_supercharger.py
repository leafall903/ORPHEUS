"""Host Orisa's authored 0CF5 throw, 0D00 device and 0D01 boost status."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import barrier_geometry, orisa_barrier
from ow174.game.script import expr, graph, message_receive, runtime
from ow174.paths import DATA_DIR

CAST = 0x0CF5
THROW = CAST, 29
PROJECTILE = 0x0400000000001CCB
ENTITY = 0x040000000000174E
DEVICE = 0x0D00
BOOST = 0x0D01
DAMAGE_FILTER = 0x25B4
DEPLOYED = 0x0240000000000859
APPLY = 0x024000000000000F


def cancel_cast(component):
    instance = next((i for i in component.instances.values() if i.graph.index == CAST), None)
    if instance is None:
        return
    pending = instance.states.get(instance.graph.node(30).state)
    if pending is None or not pending.active:
        return
    with component.operation():
        pending.end(False)
        scope = instance.states.get(instance.graph.node(7).state)
        if scope is not None and scope.active:
            scope.end(False)


def definitions():
    path = Path(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER", DATA_DIR / "orisa_supercharger_174.json"))
    if not path.is_file():
        return None, (), 0.0, 0.0
    data = json.loads(path.read_text(encoding="utf-8"))
    geometry = data["geometry"]
    if ((data.get("schema"), data.get("build")) != ("orpheus-orisa-supercharger-v1", "1.74.0.0.104319")
            or geometry.get("model") != "0x0D000000000042A2"):
        raise ValueError("unsupported Supercharger resource")
    rows = {row["index"]: row for row in data["graphs"]}
    if not {DEVICE, BOOST, DAMAGE_FILTER} <= rows.keys():
        raise ValueError("incomplete Supercharger graph definitions")
    triangles = geometry["triangles"]
    if not triangles or any(len(t) != 9 or not all(math.isfinite(value) for value in t) for t in triangles):
        raise ValueError("invalid Supercharger geometry")
    bottom = min(t[i] for t in triangles for i in (1, 4, 7))
    mesh = tuple(tuple((t[i], t[i + 1] - bottom, t[i + 2]) for i in (0, 3, 6)) for t in triangles)
    height = max(point[1] for triangle in mesh for point in triangle)
    radius = max(math.hypot(point[0], point[2]) for triangle in mesh for point in triangle)
    return rows, mesh, height, radius


@dataclass
class Device:
    owner: object
    source_body: int
    team: int
    position: tuple
    yaw: int
    component: runtime.Component
    script: runtime.Instance
    vitals: object
    mesh: tuple
    height: float
    radius: float
    end_tick: int

    @property
    def entity(self):
        return self.component.entity

    def ray(self, origin, direction, length):
        angle = self.yaw * math.tau / 65536
        c, s = math.cos(angle), math.sin(angle)

        def local(v):
            x, y, z = v
            return c * x - s * z, y, s * x + c * z

        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        return barrier_geometry.ray_mesh(self.mesh, local(delta), local(direction), length)


class Superchargers:
    def __init__(self, fight):
        self.fight = fight
        self.flights = {}
        self.active = {}
        self.buffs = {}
        self.resources = None

    def launch(self, owner, state, origin, direction, yaw):
        node = state.node
        if (expr.guid_of(node.field("m_projectileEntity.m_entityDef.m_entityDef")) != PROJECTILE
                or expr.guid_of(node.field("m_49CBA3BC.m_entityDefinition.m_entityDef")) != ENTITY):
            return
        if self.resources is None:
            self.resources = definitions()
        if not self.resources[0]:
            return
        orisa_barrier.launch_flight(self.fight, self.flights, owner, state, origin, direction, yaw)

    def land(self, fight, flight, position, tick):
        from ow174.game.combat import Vitals

        rows, mesh, height, radius = self.resources
        entity = fight.projectiles.allocate_seen_entity()
        parent_world = flight.source.world
        world = type(parent_world)(orisa_barrier.HERO, parent_world.mode, entity)
        world.match = fight.match
        component = runtime.Component(entity, flight.source.frame_ms, world)
        component.frame, component.now = tick, tick * component.frame_ms
        source = flight.source.entity
        script = component.create(graph.Graph(rows[DEVICE]), values={
            (graph.INSTANCE, 40): expr.Entity(source),
            (graph.INSTANCE, 27): expr.Entity(source),
        })
        component.create(graph.Graph(rows[DAMAGE_FILTER]))
        component.run_now()
        health = expr.to_float(script.find_var(graph.INSTANCE, 199).value())
        duration = expr.to_float(script.find_var(graph.INSTANCE, 215).value())
        if health <= 0 or duration <= 0:
            return
        end_tick = tick + math.ceil(duration * 1000 / component.frame_ms)
        device = Device(flight.owner, source, flight.team, position, flight.yaw, component, script,
                        Vitals(entity, health), mesh, height, radius, end_tick)
        self.active[entity] = device
        fight.vitals[entity] = device.vitals
        fight.record_replacement(entity)
        component.set(253, health)
        component.set(478, False)
        self.bind_owner(flight.owner, flight.owner.body_script)

    def bind_owner(self, owner, body):
        device = next((d for d in reversed(self.active.values()) if d.owner is owner), None)
        if device is None or body is None or body.hero != orisa_barrier.HERO:
            return
        source = next((i for i in body.component.instances.values() if i.graph.index == CAST), None)
        if source is not None:
            packet = message_receive.Packet(DEPLOYED, device.entity, device.source_body)
            message_receive.receive(source, packet)
            source.component.run_now()

    def remove(self, entity):
        device = self.active.pop(entity, None)
        if device is None:
            return
        for key in tuple(self.buffs):
            if key[0] == entity:
                self.drop_buff(key)
        for script in tuple(device.component.instances.values()):
            device.component.destroy(script)
        for table in (self.fight.vitals, self.fight.history, self.fight.replacement_ticks):
            table.pop(entity, None)
        body = device.owner.body_script
        if body is not None and not body.retired:
            for instance in body.component.instances.values():
                if instance.graph.index != CAST:
                    continue
                handle = instance.find_var(graph.INSTANCE, 40, create=False)
                if handle is not None and handle.value() == expr.Entity(entity):
                    state = instance.node_state(instance.graph.node(31))
                    if state.active:
                        state.dependency_changed()
            body.component.run_now()

    def drop_buff(self, key):
        instance = self.buffs.pop(key)
        if not instance.stopped:
            instance.component.destroy(instance)

    def retire_body(self, entity):
        self.flights.pop(entity, None)
        for key in tuple(self.buffs):
            if key[1] == entity:
                self.drop_buff(key)

    def damage(self, device, amount):
        if device.vitals.damage(amount) <= 0:
            return
        device.component.set(253, device.vitals.total)
        if device.vitals.total <= 0:
            device.component.set(478, True)
            self.remove(device.entity)

    def update(self, tick, seconds):
        orisa_barrier.advance_flights(self.fight, self.flights, tick, seconds, self.land)
        for entity, device in tuple(self.active.items()):
            if tick >= device.end_tick:
                self.remove(entity)
            elif device.component.frame < tick:
                device.component.run_frame(tick)
        if self.active:
            self.refresh()

    def replay_until(self, previous, seconds):
        ends = [d.end_tick for d in self.active.values()]
        ends.extend(f.tick + math.ceil((f.remaining + 15) / seconds) for f in self.flights.values())
        return max(ends, default=previous)

    def refresh(self):
        from ow174.game.combat import capsule_visible, segment_distance
        from ow174.game.reinhardt_barrier import enemy_barriers

        fight = self.fight
        wanted = set()
        targets = fight._targets()
        for device in self.active.values():
            search = device.script.states.get(1)  # original 0D00/7 TrackTargets
            if search is None or not search.active:
                continue
            radius = expr.to_float(search.evaluate(search.node.field("m_radius")))
            origin = search.evaluate(search.node.field("m_D59188D5"))
            if not isinstance(origin, expr.Vec3):
                continue
            screens = tuple(b for b in enemy_barriers(fight, device.owner, team=device.team) if b.mesh)
            for target in targets:
                body = getattr(target.owner, "body_script", None)
                if (body is None or getattr(body, "retired", False) or target.is_device
                        or fight.vitals_of(target).dead
                        or (target.owner is not device.owner and (
                            fight.match.game_map.free_for_all or target.team != device.team))):
                    continue
                if segment_distance(device.position, *target.segment()) - target.radius > radius:
                    continue
                x, y, z = target.feet
                eye = x, y + target.eye, z
                if not capsule_visible(fight._line_collision(), tuple(origin), eye, eye, 0, barriers=screens):
                    continue
                key = device.entity, target.entity
                wanted.add(key)
                if key not in self.buffs:
                    self.apply(device, body, key)
        for key in tuple(self.buffs):
            if key not in wanted:
                self.drop_buff(key)

    def apply(self, device, body, key):
        rows, _, _, _ = self.resources
        component = body.component
        status = component.create(graph.Graph(rows[BOOST]), values={
            (graph.INSTANCE, 27): expr.Entity(device.source_body),
        })
        message_receive.receive(status, message_receive.Packet(APPLY, device.entity, device.source_body))
        component.run_now()
        self.buffs[key] = status

    def scale(self, entity):
        scale = 1.0
        for (_, recipient), instance in self.buffs.items():
            if recipient != entity or instance.stopped:
                continue
            state = instance.states.get(3)
            if state is not None and state.active:
                scale = max(scale, 1.0 + expr.to_float(state.evaluate(state.node.field("m_50530EAB"))))
        return scale
