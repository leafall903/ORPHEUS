"""Selected Photon Barrier placement, pet graph and world interception.

Use the widest authored plane for map-spanning collision. The position query,
orientation bridge, finite pool and team filtering are server reconstruction.
"""

import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import barrier_mesh, pools, symmetra_placement
from ow174.game.script import expr, graph, placement, play, play_queue, runtime
from ow174.paths import DATA_DIR

HERO = 0x02E0000000000016
BODY = 0x04000000000026B0
GEOMETRY_SHA256 = "d11a09fc7224c2b73819a06a40f9dbb1cb35ff681c373aa1891a987de6eedfe8"


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
    pitch: int = 0


class BarrierWorld(runtime.World):
    def __init__(self, mode, actor):
        super().__init__(HERO, mode)
        self.actor = actor

    def position(self, entity):
        if entity == self.actor.body:
            return expr.Vec3(*self.actor.position)
        return super().position(entity)


class Barriers(barrier_mesh.DeployedContacts):
    def __init__(self, fight):
        self.fight = fight
        path = Path(os.environ.get("ORPHEUS_SYMMETRA_COLLISION", DATA_DIR / "symmetra_collision_174.json"))
        self.geometry = barrier_mesh.load_deployed_geometry(
            path, GEOMETRY_SHA256 if path.is_file() else None, "orpheus-symmetra-barrier-v1",
        )
        self.actors = {}

    def confirm(self, state):
        if self.geometry is None or not state.active:
            return False
        if graph.graph(0x156C) is None or graph.entity_body(BODY) is None:
            return False
        # The original confirm edge follows this output before the cast wait.
        world = state.instance.component.world
        owner = next((player for player in self.fight.match.players if player.body == world.body), None)
        if owner is None or self.fight.is_stunned(owner):
            return True
        result = symmetra_placement.preview(world, state)
        if result is None or result is NotImplemented:
            return True
        instance, node = state.instance, state.node
        return placement.publish_static(
            result, commit=True,
            write=lambda field, value: instance.write(expr.lvalue(node.field(field)), value),
            frame=lambda: instance.component.frame & 0xFFFFFFFF,
            record=lambda _: True,
            follow=lambda plug: instance.follow(node, plug, state),
        )

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
        state = instance.node_state(node)
        point = state.evaluate(node.field("m_position"), watch=False)
        axis = state.evaluate(node.field("m_AD8E614B"), watch=False)
        if type(point) is not expr.Vec3 or type(axis) is not expr.Vec3 or not math.hypot(axis.x, axis.z):
            raise ValueError("Photon Barrier requires an authored position and horizontal axis")
        position = tuple(point)
        yaw = round(math.atan2(axis.x, axis.z) * 65536 / math.tau)
        lifetime = expr.to_float(instance.find_var(0, 567).value())
        body = self.fight.next_bot
        self.fight.next_bot += 1
        self.retire(source)
        health_row = next(row for row in graph.entity_body(BODY)["graphs"] if row["graph"] == pools.BODY_GRAPH)
        health = pools._numbers(health_row.get("m_1EB5A024"))[pools.HEALTH_VAR]
        actor = Shield(owner, instance, body, owner.team, position, yaw, health,
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
                if actor.health > 0 and actor.owner is not (getattr(shooter, "combat_owner", None) or shooter)
                and (self.fight.match.game_map.free_for_all or actor.team != shooter.team))

    def ray(self, actor, origin, direction, length, radius=0):
        return barrier_mesh.intersect(
            self.geometry, actor.position, actor.yaw, 0, origin, direction, length, radius,
        )

    def damage(self, actor, amount):
        actor.health = expr.f32(max(0, actor.health - max(0, amount)))
        if actor.health <= 0:
            self.end(actor)
