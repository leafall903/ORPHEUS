"""Authored Supercharger/status graphs with scoped reconstructed world consumers.

Target capsule/visibility, immediate aura departure and maximum nonstacking boost
are reconstruction policies. Native status refresh/linger/stack/credit and network
deployable transport remain open. No static/client acceptance follows from this module.
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots, orisa_clock
from ow174.game.orisa_barrier import Barriers, Geometry
from ow174.game.script import expr, graph, message_receive, runtime
from ow174.paths import DATA_DIR

GRAPH = 0x0CF5
DEFINITION = 0x040000000000174E
APPLY = 0x024000000000000F
TICK = 0.016


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
    entity: int
    owner: object
    source_body: int
    team: int
    position: tuple
    yaw: float
    until: int
    health: float
    component: runtime.Component
    script: runtime.Instance
    filter_bits: int | None = None

    def __post_init__(self):
        if self.filter_bits is None:
            self.filter_bits = getattr(self.owner, "team_bit", 0x800000 << self.team)


class Superchargers(Barriers):
    def __init__(self, fight):
        self.fight = fight
        self.active: dict[int, Device] = {}
        self.buffs: dict[tuple[int, int], runtime.Instance] = {}
        self.graphs = {}
        self.geometry = None
        path = Path(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER", DATA_DIR / "orisa_supercharger_174.json"))
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("schema") != "orpheus-orisa-supercharger-v1"
                or data.get("build") != "1.74.0.0.104319"):
            raise ValueError("Supercharger supplement has wrong schema/build")
        self.graphs = {r["index"]: graph.Graph(r) for r in data["graphs"]}
        if not {0xD00, 0xD01, 0x25B4} <= self.graphs.keys():
            raise ValueError("Supercharger supplement lacks authored graphs")
        self.geometry = Geometry.__new__(Geometry)
        self.geometry.load(data["geometry"], model="0x0D000000000042A2")

    def landed(self, rocket):
        if (self.geometry is None or rocket.source_graph != GRAPH
                or rocket.spawn != DEFINITION or rocket.ended != "world"):
            return
        entity = self.fight.next_bot
        if not 0xC0001000 <= entity < 0xD0000000:
            raise ValueError("ability entity allocator exhausted")
        self.fight.next_bot += 1
        tick = orisa_clock.impact_tick(rocket, self.fight.tick)
        component = runtime.Component(entity, world=DeviceWorld(self.fight, entity, rocket.position))
        component.now, component.frame = tick * 16, tick
        script = component.create(self.graphs[0xD00], init=False)
        script.set_source_entity(rocket.source_entity or rocket.source_body)
        script.init()
        script.start_entries((), rocket.source_body)
        component.create(self.graphs[0x25B4])
        component.run_now()  # original Entries set#199=200 and#215=15
        health = expr.to_float(script.find_var(graph.INSTANCE, 199).value())
        seconds = expr.to_float(script.find_var(graph.INSTANCE, 215).value())
        if health <= 0 or seconds <= 0:
            raise ValueError("authored Supercharger initialization did not set health/lifetime")
        component.set(253, health)
        direction = rocket.launch_direction or rocket.direction
        filter_bits = getattr(getattr(rocket, "shot", None), "filter_bits", None)
        self.active[entity] = Device(entity, rocket.shooter, rocket.source_body, rocket.team,
                                     rocket.position, math.atan2(direction[0], direction[2]),
                                     tick + math.ceil(seconds / TICK), health, component, script,
                                     filter_bits=filter_bits)
        source = rocket.source_state.instance
        if not source.stopped and source.component.entity == rocket.source_body:
            # Original0CF5/entry32 binds caller to#40; its existence switch
            # owns charge-blocking#842 for the device's actual lifetime.
            message_receive.receive(source, message_receive.Packet(
                0x0240000000000859, entity, rocket.source_body))
            source.component.run_now()
        self.refresh()

    def charge_contact(self, shooter, tracker, start, end, forward):
        from ow174.game.combat import _along, _dot, _sub, capsule_visible

        if not self.active or tracker is None or not tracker.active:
            return None
        offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
        radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        if radius == 0 and tracker.instance.graph.index == 0x007D and tracker.index == 56:
            # The ordinary body uses the authored 0.5 m factor at unit scale.
            # The native 9339 scale evaluator is unavailable in the static host.
            radius, = tracker.node.field("m_radius.m_expression.m_D99EF254")
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return None
        a, b = (_along((x + offset.x * forward[2], y + offset.y, z - offset.x * forward[0]),
                       forward, offset.z) for x, y, z in (start, end))

        def visible(point):
            return (_dot(_sub(point, a), forward) >= 0
                    and not self.fight.walls.blocked(a, point)
                    and capsule_visible(self.fight._collision(), a, point, point, 0))

        for device in sorted(self.opponents(shooter, shooter.team), key=lambda item: math.dist(a, item.position)):
            flag = device.component.entity_var(961, create=False)
            if flag is not None and expr.truthy(flag.value()) and self.geometry.overlaps(
                    device.position, device.yaw, a, b, radius, visible):
                return device
        return None

    def bind_owner(self, owner, script):
        """Reconstruct the retained device's callback on its owner's new body."""
        devices = [d for d in self.active.values() if d.owner is owner]
        if not devices:
            return
        source = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
        if source is not None:
            device = max(devices, key=lambda d: d.until)
            message_receive.receive(source, message_receive.Packet(
                0x0240000000000859, device.entity, device.source_body))
            script.component.run_now()

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
                owner.component.run_now()  # invalidate existence before another same-tick hit

    def retire(self, body):
        # Retire target-body statuses, without guessing deployed-owner persistence.
        for key, instance in tuple(self.buffs.items()):
            if key[1] == body:
                instance.component.destroy(instance)
                del self.buffs[key]

    def update(self, tick):
        for entity, device in tuple(self.active.items()):
            if tick >= device.until or device.health <= 0:
                self.remove(entity)
            else:
                device.component.run_frame(tick)
        self.refresh()
        from ow174.game import orisa_presentation

        orisa_presentation.sync(self)

    def refresh(self):
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
                    if self.fight.orisa.barriers.trace(device.owner, origin, direction, distance):
                        continue
                script = getattr(target.owner, "body_script", None)
                if script is None:
                    continue
                key = device.entity, target.entity
                wanted.add(key)
                if key not in self.buffs:
                    component = script.component
                    instance = component.create(self.graphs[0xD01], values={
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
        entity = actor.entity if isinstance(actor, bots.Bot) else getattr(actor, "body", None)
        values = [1.0]
        for (device, target), instance in self.buffs.items():
            if target != entity or device not in self.active:
                continue
            state = instance.state(3)
            if state is not None and state.active:
                values.append(1.0 + expr.to_float(state.evaluate(state.node.field("m_50530EAB"))))
        return max(values)
