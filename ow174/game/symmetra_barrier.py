"""Selected Photon Barrier placement, pet graph and world interception.

Use the widest authored plane for map-spanning collision. The position query,
orientation bridge, finite pool and team filtering are server reconstruction.
"""

import hashlib
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import barriers
from ow174.game.script import expr, graph, play, play_queue, runtime
from ow174.paths import DATA_DIR

HERO = 0x02E0000000000016
PATH = Path(os.environ.get("ORPHEUS_SYMMETRA_COLLISION", DATA_DIR / "symmetra_collision_174.json"))
GEOMETRY_SHA256 = hashlib.sha256(PATH.read_bytes()).hexdigest() if PATH.is_file() else None


@dataclass
class Shield:
    owner: object
    source: object
    body: int
    team: int
    position: tuple
    yaw: int
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
        self.geometry = barriers.load_model_mesh(PATH, GEOMETRY_SHA256, "orpheus-symmetra-barrier-v1")
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
        actor = Shield(owner, instance, body, owner.team, position, yaw, 4000,
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

    def enemies(self, shooter):
        return (actor for actor in self.actors.values()
                if actor.health > 0 and actor.owner is not shooter
                and (self.fight.match.game_map.free_for_all or actor.team != shooter.team))

    def ray(self, actor, origin, direction, length, radius=0):
        return barriers.intersect(
            self.geometry, actor.position, actor.yaw, 0, origin, direction, length, radius,
        )

    def damage(self, actor, amount):
        actor.health = expr.f32(max(0, actor.health - max(0, amount)))
        if actor.health <= 0:
            self.end(actor)
