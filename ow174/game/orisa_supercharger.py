"""Static host for 0CF5 deployment and the authored 0D00/0D01 aura graphs.

Capsule admission, eye visibility, immediate status removal and nonstacking
are static policies. The device uses its recovered mesh for projectile contact.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import barrier_mesh, bots
from ow174.game.script import expr, graph, message_receive, runtime
from ow174.paths import DATA_DIR

HERO = 0x02E000000000013E
CAST = 0x0CF5
STATUS = 0x0D01
SPAWN = 0x040000000000174E
PATH = Path(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER", DATA_DIR / "orisa_supercharger_174.json"))


@dataclass
class Device:
    entity: int
    owner: object
    source_body: int
    team: int
    position: tuple
    yaw: int
    until: int
    health: float
    script: runtime.Instance


class DeviceWorld(runtime.World):
    def __init__(self, mode, entity, location):
        super().__init__(mode=mode)
        self.entity = entity
        self.location = location

    def position(self, entity):
        if entity == self.entity:
            return expr.Vec3(*self.location)
        return super().position(entity)


def cancel_windup(component):
    """Retire the detached launch wait after the cast Ability has already stopped."""
    if component.strict:
        return
    for instance in component.instances.values():
        if instance.graph.index != CAST:
            continue
        pending = instance.states.get(instance.graph.node(30).state)
        if pending is None or not pending.active:
            continue
        pending.end(False)
        scope = instance.states.get(instance.graph.node(7).state)
        if scope is not None and scope.active:
            scope.end(False)


@cache
def assets():
    if not PATH.is_file():
        return {}, None
    raw = PATH.read_bytes()
    data = json.loads(raw)
    if (data.get("schema"), data.get("build")) != ("orpheus-orisa-supercharger-v1", "1.74.0.0.104319"):
        raise ValueError("invalid Supercharger supplement")
    definitions = {row["index"]: graph.Graph(row) for row in data["graphs"]}
    if not {0x0D00, STATUS, 0x25B4} <= definitions.keys():
        raise ValueError("missing Supercharger graphs")
    geometry = data["geometry"]
    if geometry["model"] != "0x0D000000000042A2":
        raise ValueError("invalid Supercharger collision model")
    triangles = tuple(tuple(triangle) for triangle in geometry["triangles"])
    if not triangles or any(len(t) != 9 or not all(math.isfinite(v) for v in t) for t in triangles):
        raise ValueError("invalid Supercharger triangles")
    digest = hashlib.sha256(raw).hexdigest()
    mesh = barrier_mesh.Geometry(triangles, (0, 0, 0), (0, 0, 0), 0, 0, 0, 0, 0,
                                 geometry["raw_sha256"], digest)
    return definitions, mesh


class Superchargers:
    def __init__(self, fight):
        self.fight = fight
        self.active = {}
        self.buffs = {}
        _, self.geometry = assets()

    def land(self, rocket, point):
        from ow174.game.combat import TICK

        owner = rocket.shooter
        if self.geometry is None or owner not in self.fight.match.players or owner.gone:
            return
        body_script = owner.body_script
        if body_script is None or body_script.retired:
            return
        if body_script.hero != HERO:
            return
        entity = self.fight.next_bot
        self.fight.next_bot += 1
        tick = self.fight.tick
        world = DeviceWorld(self.fight.match.game_map.mode_guid, entity, point)
        component = runtime.Component(entity, world=world)
        component.now, component.frame = tick * 16, tick
        script = component.create(assets()[0][0x0D00], values={
            (expr.INSTANCE, 40): expr.Entity(owner.body),
            (expr.INSTANCE, 27): expr.Entity(owner.body),
        })
        component.create(assets()[0][0x25B4])
        component.run_now()
        hp = expr.to_float(script.vars[199].value())
        seconds = expr.to_float(script.vars[215].value())
        yaw = round(math.atan2(rocket.direction[0], rocket.direction[2]) * 65536 / math.tau)
        self.active[entity] = Device(
            entity, owner, owner.body, rocket.team, point, yaw, tick + math.ceil(seconds / TICK), hp, script,
        )
        self.bind_owner(owner, body_script)
        self.refresh()

    def bind_owner(self, owner, body_script):
        if body_script.hero != HERO or body_script.component.strict:
            return
        devices = (device for device in self.active.values() if device.owner is owner)
        device = max(devices, key=lambda item: (item.until, item.entity), default=None)
        if device is None:
            return
        source = next((i for i in body_script.component.instances.values() if i.graph.index == CAST), None)
        if source is not None:
            packet = message_receive.Packet(0x0240000000000859, device.entity, device.source_body)
            message_receive.receive(source, packet)
            source.component.run_now()

    def remove(self, device):
        self.active.pop(device.entity)
        for key, status in tuple(self.buffs.items()):
            if key[0] == device.entity:
                status.component.destroy(status)
                del self.buffs[key]
        script = device.owner.body_script
        if script is not None and not script.retired:
            for source in script.component.instances.values():
                if source.graph.index != CAST:
                    continue
                handle = source.vars.get(40)
                if handle is None or handle.value() != expr.Entity(device.entity):
                    continue
                switch = source.state(12)
                if switch.active:
                    switch.dependency_changed()
            script.component.run_now()

    def retire(self, body):
        for key, status in tuple(self.buffs.items()):
            if key[1] == body:
                status.component.destroy(status)
                del self.buffs[key]

    def update(self, tick):
        for device in tuple(self.active.values()):
            if tick >= device.until or self.fight.match.ended:
                self.remove(device)
            else:
                device.script.component.run_frame(tick)
        self.refresh()

    def refresh(self):
        from ow174.game.combat import capsule_visible, segment_distance

        wanted = set()
        fight = self.fight
        for device in self.active.values():
            search = device.script.state(1)
            if not search.active:
                continue
            radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
            origin = search.evaluate(search.node.field("m_D59188D5"), watch=False)
            origin = (origin.x, origin.y, origin.z)
            for target in fight._targets():
                owner = target.owner
                if getattr(owner, "combat_owner", None) is not None or fight.vitals_of(target).dead:
                    continue
                script = owner.body_script
                if (script is None or getattr(script, "retired", False)
                        or script.component.owner_dead or script.component.strict):
                    continue
                allied = not fight.match.game_map.free_for_all and target.team == device.team
                if owner is not device.owner and not allied:
                    continue
                a, b = target.segment()
                if (segment_distance(device.position, a, b) - target.radius > radius
                        or not capsule_visible(fight._collision(), origin, a, b, target.radius)):
                    continue
                eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                distance = math.dist(origin, eye)
                if distance > target.radius:
                    direction = tuple((end - start) / distance
                                      for start, end in zip(origin, eye, strict=True))
                    if fight._shield_trace(device.owner, origin, direction, distance - target.radius,
                                           include_attached=False) is not None:
                        continue
                key = device.entity, target.entity
                wanted.add(key)
                if key not in self.buffs:
                    component = script.component
                    status = component.create(assets()[0][STATUS], values={
                        (expr.INSTANCE, 27): expr.Entity(device.source_body),
                    })
                    message_receive.receive(status, message_receive.Packet(0x024000000000000F,
                                                                           device.entity, device.source_body))
                    component.run_now()
                    self.buffs[key] = status
        for key in self.buffs.keys() - wanted:
            status = self.buffs.pop(key)
            status.component.destroy(status)

    def scale(self, actor):
        body = actor.entity if isinstance(actor, bots.Bot) else getattr(actor, "body", None)
        scale = 1.0
        for (_, target), status in self.buffs.items():
            if target == body and status.state(3).active:
                state = status.state(3)
                bonus = state.evaluate(state.node.field("m_50530EAB"), watch=False)
                scale = max(scale, 1.0 + expr.to_float(bonus))
        return scale

    def trace(self, shooter, origin, direction, reach):
        hits = []
        owner = getattr(shooter, "combat_owner", None) or shooter
        for device in self.active.values():
            allied = not self.fight.match.game_map.free_for_all and device.team == shooter.team
            if device.owner is owner or allied:
                continue
            distance = barrier_mesh.intersect(
                self.geometry, device.position, device.yaw, 0, origin, direction, reach,
            )
            if distance is not None:
                hits.append((distance, device))
        return min(hits, key=lambda hit: hit[0], default=None)

    def damage(self, device, amount):
        device.health = max(0.0, device.health - amount)
        if device.health == 0:
            self.remove(device)
