"""Authored Supercharger/status graphs with scoped reconstructed world consumers.

Target capsule/visibility, immediate aura departure and maximum nonstacking boost
are reconstruction policies. Native status refresh/linger/stack/credit and network
deployable transport remain open. No static/client acceptance follows from this module.
"""

import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, sigma_hyperspheres
from ow174.game.orisa_barrier import HERO, BarrierWorld, Geometry
from ow174.game.script import expr, graph, message_receive, runtime
from ow174.paths import DATA_DIR

GRAPH = 0x0CF5
DEFINITION = 0x040000000000174E
APPLY = 0x024000000000000F
DEPLOYED = 0x0240000000000859
TICK = 0.016


def charging_blocked(script):
    if script.hero != HERO:
        return False
    gate = script.component.entity_var(842, create=False)
    return gate is not None and expr.truthy(gate.value())


@cache
def _authored_graph(row):
    return graph.Graph(json.loads(row))


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


class Superchargers:
    def __init__(self, fight):
        self.fight = fight
        self.active: dict[int, Device] = {}
        self.buffs: dict[tuple[int, int], runtime.Instance] = {}
        self.graph_rows = {}
        self.geometry = None
        path = Path(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER", DATA_DIR / "orisa_supercharger_174.json"))
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("schema") != "orpheus-orisa-supercharger-v1"
                or data.get("build") != "1.74.0.0.104319"):
            raise ValueError("Supercharger supplement has wrong schema/build")
        self.graph_rows = {row["index"]: json.dumps(row, separators=(",", ":")) for row in data["graphs"]}
        if not {0xD00, 0xD01, 0x25B4} <= self.graph_rows.keys():
            raise ValueError("Supercharger supplement lacks authored graphs")
        self.geometry = Geometry.__new__(Geometry)
        self.geometry.load(data["geometry"], model="0x0D000000000042A2")

    def landed(self, rocket):
        if self.geometry is None or rocket.shooter.body != rocket.supercharger_body or rocket.shooter.gone:
            return
        entity = self.fight.next_bot
        if not 0xC0001000 <= entity < 0xD0000000:
            raise ValueError("ability entity allocator exhausted")
        self.fight.next_bot += 1
        tick = self.fight.tick
        component = runtime.Component(entity, world=BarrierWorld(self.fight, entity, rocket.position))
        component.now, component.frame = tick * 16, tick
        script = component.create(_authored_graph(self.graph_rows[0xD00]), values={
            (graph.INSTANCE, 40): expr.Entity(rocket.supercharger_body),
            (graph.INSTANCE, 27): expr.Entity(rocket.supercharger_body),
        })
        component.create(_authored_graph(self.graph_rows[0x25B4]))
        component.run_now()  # original Entries set#199=200 and#215=15
        health = expr.to_float(script.find_var(graph.INSTANCE, 199).value())
        seconds = expr.to_float(script.find_var(graph.INSTANCE, 215).value())
        if health <= 0 or seconds <= 0:
            raise ValueError("authored Supercharger initialization did not set health/lifetime")
        component.set(253, health)
        direction = rocket.direction
        device = Device(entity, rocket.shooter, rocket.supercharger_body, rocket.team,
                        rocket.position, math.atan2(direction[0], direction[2]),
                        tick + math.ceil(seconds / TICK), health, component, script)
        self.active[entity] = device
        self._bind_device(device, rocket.shooter.body_script)
        self.refresh()

    @staticmethod
    def _bind_device(device, script):
        if script is None:
            return
        source = next((instance for instance in script.component.instances.values()
                       if instance.graph.index == GRAPH and not instance.stopped), None)
        if source is not None:
            # Original 0CF5 entry 32 binds caller to #40 and owns charge gate #842.
            message_receive.receive(source, message_receive.Packet(
                DEPLOYED, device.entity, device.source_body))
            source.component.run_now()

    def bind_owner(self, owner, script):
        if script.hero != HERO:
            return
        device = max((d for d in self.active.values()
                      if d.owner is owner and d.until > self.fight.tick),
                     key=lambda d: d.until, default=None)
        if device is not None:
            self._bind_device(device, script)

    def opponents(self, shooter, team):
        for device in self.active.values():
            if device.owner is shooter:
                continue
            if not self.fight.match.game_map.free_for_all and device.team == team:
                continue
            yield device

    def first(self, shooter, origin, direction, limit, *, radius=0.0):
        if self.geometry is None:
            return None
        best = None
        for device in self.opponents(shooter, shooter.team):
            distance = self.geometry.cast(device.position, device.yaw, origin, direction, limit, radius)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, device
        return best

    def within(self, shooter, centre, radius, *, visible):
        if self.geometry is None or radius <= 0:
            return
        for device in self.opponents(shooter, shooter.team):
            distance = self.geometry.surface(
                device.position, device.yaw, centre, radius,
                lambda point, entity=device.entity: visible(point, entity),
            )
            if distance is not None:
                yield device, distance

    def damage(self, device, amount):
        device.health = max(0.0, device.health - amount)
        device.component.set(253, device.health)
        if device.health == 0:
            self.remove(device.entity)

    def interrupt_cast(self, script):
        if self.geometry is None:
            return
        for instance in script.component.instances.values():
            if instance.graph.index != GRAPH:
                continue
            # The cast's Ability ends before the detached launch wait does.
            launch_wait = instance.state(11)
            if launch_wait is not None and launch_wait.active:
                launch_wait.end(False)
                cast_scope = instance.state(1)
                if cast_scope is not None and cast_scope.active:
                    cast_scope.end(False)

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
            elif tick > device.component.frame:
                device.component.run_frame(tick)
        self.refresh()

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
                    shield = sigma_hyperspheres.barrier_contact(
                        self.fight, device.owner, origin, direction, distance,
                    )
                    if shield or self.fight.walls.blocked(origin, sample):
                        continue
                script = getattr(target.owner, "body_script", None)
                if script is None:
                    continue
                key = device.entity, target.entity
                wanted.add(key)
                if key not in self.buffs:
                    component = script.component
                    instance = component.create(_authored_graph(self.graph_rows[0xD01]), values={
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
