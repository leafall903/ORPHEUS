"""Selected Photon Barrier placement, pet graph and world interception.

Use the widest authored plane for map-spanning collision. The position query,
orientation bridge, finite pool and team filtering are server reconstruction.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game.collision import World
from ow174.game.orisa_barrier import Geometry as MeshGeometry
from ow174.game.script import expr, graph, play, play_queue, runtime
from ow174.paths import DATA_DIR

HERO = 0x02E0000000000016
GEOMETRY_SHA256 = "d11a09fc7224c2b73819a06a40f9dbb1cb35ff681c373aa1891a987de6eedfe8"


class Geometry(MeshGeometry):
    def __init__(self, path):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
            raise ValueError("Photon Barrier geometry does not match the verified 104319 mesh")
        data = json.loads(raw)
        triangles = data["triangles"]
        self.world = World(triangles)
        self.triangles = tuple(tuple(tuple(t[k:k + 3]) for k in (0, 3, 6)) for t in triangles)
        self.source = data["model_sha256"]


@dataclass
class Shield:
    owner: object
    source: object
    body: int
    team: int
    position: tuple
    yaw: float
    health: float
    expires: int
    component: object = None


class BarrierWorld(runtime.World):
    def __init__(self, mode, actor):
        super().__init__(HERO, mode)
        self.actor = actor

    def position(self, entity):
        if entity == self.actor.body:
            return expr.Vec3(*self.actor.position)
        return super().position(entity)


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        override = os.environ.get("ORPHEUS_SYMMETRA_COLLISION")
        path = Path(override) if override else DATA_DIR / "symmetra_collision_174.json"
        self.geometry = Geometry(path) if override or path.is_file() else None
        self.actors = {}

    def confirm(self, state):
        if self.geometry is None or not state.active:
            return False
        # The original confirm edge follows this output before the cast wait.
        state.instance.follow(state.node, "m_F6955E81", state)
        return True

    def create(self, instance, node):
        if self.geometry is None or (instance.graph.index, node.pos) != (0x156B, 17):
            return NotImplemented
        from ow174.game.combat import TICK, aim, ray_world

        if node.field("m_entityDefinition")["m_entityDef"] != "0x04000000000026B0":
            raise ValueError("Photon Barrier entity definition changed")
        source = instance.component.entity
        owner = next((p for p in self.fight.match.players if p.body == source and not p.gone), None)
        if owner is None or self.fight.is_dead(owner):
            return True
        origin = self.fight._eye(owner)
        yaw = round(owner.mover.yaw)
        direction = aim(yaw, round(owner.mover.pitch))
        placement = instance.graph.node(57)
        reach = expr.to_float(instance.evaluate(placement.field("m_6386E7B4")))
        hit = ray_world(self.fight._collision(), origin, direction, reach)
        if hit is None:
            direction = aim(yaw, 0)
            hit = min(3.0, reach)
        position = tuple(o + d * hit for o, d in zip(origin, direction, strict=True))
        turn = instance.vars.get(4201)
        if turn is not None and expr.truthy(turn.value()):
            yaw = (yaw + 32768 + 16384) % 65536 - 32768
        lifetime = expr.to_float(instance.find_var(0, 567).value())
        body = self.fight.next_bot
        self.fight.next_bot += 1
        self.retire(source)
        actor = Shield(owner, instance, body, owner.team, position, yaw * math.tau / 65536, 4000,
                       self.fight.match.tick + math.ceil(lifetime / TICK))
        actor.component = runtime.Component(body, frame_ms=instance.component.frame_ms,
                                            world=BarrierWorld(self.fight.match.game_map.mode_guid, actor))
        actor.component.now, actor.component.frame = instance.component.now, instance.component.frame
        self.actors[source] = actor
        instance.write(expr.dynamic(node.field("m_B3019338")), expr.Entity(body))
        return True

    def play(self, instance, node):
        if self.geometry is None or (instance.graph.index, node.pos) != (0x156B, 13):
            return NotImplemented
        actor = self.actors.get(instance.component.entity)
        if actor is None:
            return True
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != 0x058000000000156C:
            raise ValueError("Photon Barrier receiver graph changed")
        source = instance.component.entity
        request = play.resolve_request(instance, node, resource, context_provider=lambda:
                                       play.InvocationContext(source, source, instance.source_entity()))
        if request is None or request.targets != (actor.body,):
            raise ValueError("Photon Barrier graph target does not resolve")
        queue = play_queue.Queue(actor.component, lambda _: play_queue.Load("ready", graph.graph(0x156C)))
        queue.submit(request)
        actor.component.run_now()
        return True

    def retire(self, source):
        actor = self.actors.pop(source, None)
        if actor is not None:
            for instance in tuple(actor.component.instances.values()):
                actor.component.destroy(instance)

    def update(self, tick):
        for source, actor in tuple(self.actors.items()):
            if tick >= actor.expires or actor.owner.gone or actor.owner.body != source:
                self.end(actor)
                continue
            if tick > actor.component.frame:
                actor.component.run_frame(tick)

    def end(self, actor):
        pet = actor.source.states.get(9)
        if pet is not None and pet.active and not pet.ending:
            pet.request(runtime.ABORT)
            actor.source.component.run_now()
        self.retire(actor.source.component.entity)

    def opponents(self, shooter, team=None):
        if team is None:
            team = shooter.team
        return tuple(actor for actor in self.actors.values()
                     if actor.health > 0 and actor.owner is not shooter
                     and (self.fight.match.game_map.free_for_all or actor.team != team))

    def closest(self, barrier, point):
        return self.geometry.closest(barrier.position, barrier.yaw, point)

    def trace(self, shooter, origin, direction, reach, *, team=None, barriers=None):
        if barriers is None:
            barriers = self.opponents(shooter, team)
        best = None
        for actor in barriers:
            distance = self.geometry.cast(actor.position, actor.yaw, origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, actor
        return best

    def damage(self, actor, amount):
        actor.health = expr.f32(max(0, actor.health - max(0, amount)))
        if actor.health <= 0:
            self.end(actor)
