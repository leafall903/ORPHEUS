"""Selected 1B55/1A2A host. Pose, contacts and finite-pool writes are reconstructed."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import barriers
from ow174.game.script import expr, graph, play, play_queue, runtime
from ow174.paths import DATA_DIR

GEOMETRY_SHA256 = "c166d81d2a5095a7b55cebc9fc9575df9f0ba4708195d2e6f0127e22081ef7b6"


def configured_geometry():
    selected = os.environ.get("ORPHEUS_SIGMA_COLLISION")
    path = Path(selected) if selected else DATA_DIR / "sigma_collision_174.json"
    if not selected and not path.is_file():
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
        raise ValueError("Sigma C015 descriptor hash mismatch")
    data = json.loads(raw)
    if (data.get("schema"), data.get("build")) != ("orpheus-sigma-barrier-v1", 104319):
        raise ValueError("invalid Sigma barrier descriptor")
    triangles = tuple(tuple(row) for row in data["triangles"])
    return barriers.Geometry(triangles, (0, 0, 0), (0, 0, 0), 0, 0, 0, 0, 0, GEOMETRY_SHA256)


@dataclass
class Shield:
    owner: object
    source: object
    body: int
    component: object = None
    health: float = 0
    maximum: float = 0
    mode: int = 1
    position: tuple = (0, 0, 0)
    yaw: int = 0
    pitch: int = 0
    direction: tuple = (0, 0, 1)
    speed: float = 0
    clearance: float = 0
    blocked: bool = False

    def instance(self):
        return next((i for i in self.component.instances.values() if i.graph.index == 0x1A2A), None)

    def active(self):
        return self.health > 0 and self.mode in (2, 3)

    def set_health(self, health):
        health = expr.f32(max(0, min(self.maximum, health)))
        if health == self.health:
            return
        self.health = health
        self.component.set(253, health)
        self.component.set(478, health <= 0)
        stack = self.source.states.get(16)
        if stack is not None and stack.active:
            stack.dependency_changed()


class BarrierWorld(runtime.World):
    def __init__(self, mode, actor):
        super().__init__(0x02E000000000023B, mode)
        self.actor = actor

    def position(self, entity):
        if entity == self.actor.body:
            return expr.Vec3(*self.actor.position)
        return super().position(entity)

    def entity_var_slot(self, entity, var, *, create=False):
        source = self.actor.source
        if entity == source.component.entity and var in (19777, 2083):
            return source.vars.get(var)
        return NotImplemented

    def modify_health(self, state, elapsed_ms):
        if (state.instance.graph.index, state.node.pos) != (0x1A2A, 50):
            return NotImplemented
        actor = self.actor
        if actor.mode == 1:
            amount = state.node.field("m_modifyHealth")["m_amount"]
            rate, = amount["m_expression"]["m_D99EF254"]
            actor.set_health(actor.health + rate*elapsed_ms/1000)
        return True


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.geometry = configured_geometry()
        self.actors = {}

    def create(self, instance, node):
        if (self.geometry is None or instance.component.strict
                or (instance.graph.index, node.pos) != (0x1B55, 6)):
            return NotImplemented
        if node.field("m_entityDefinition")["m_entityDef"] != "0x0400000000002D25":
            raise ValueError("Sigma barrier definition changed")
        source_body = instance.component.entity
        owner = next((p for p in self.fight.match.players if p.body == source_body and not p.gone), None)
        if owner is None:
            return True
        self.retire(source_body)
        body = self.fight.next_bot
        if not 0xC0001000 <= body < 0xD0000000:
            raise ValueError("ability entity allocator exhausted")
        self.fight.next_bot += 1
        actor = Shield(owner, instance, body)
        component = runtime.Component(body, frame_ms=instance.component.frame_ms,
                                      world=BarrierWorld(self.fight.match.game_map.mode_guid, actor))
        component.now, component.frame = instance.component.now, instance.component.frame
        actor.component = component
        self.actors[source_body] = actor
        instance.write(expr.dynamic(node.field("m_B3019338")), expr.Entity(body))
        return True

    def play(self, instance, node):
        if (self.geometry is None or instance.component.strict
                or (instance.graph.index, node.pos) != (0x1B55, 8)):
            return NotImplemented
        actor = self.actors.get(instance.component.entity)
        if actor is None:
            return True
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != 0x0580000000001A2A:
            raise ValueError("Sigma barrier graph changed")
        source = instance.component.entity
        request = play.resolve_request(instance, node, resource, context_provider=lambda:
            play.InvocationContext(source, source, instance.source_entity()))
        if request is None or request.targets != (actor.body,):
            raise ValueError("Sigma barrier target does not resolve to its pet")
        queue = play_queue.Queue(actor.component, lambda _: play_queue.Load("ready", graph.graph(0x1A2A)))
        queue.submit(request)
        actor.component.run_now()
        child = actor.instance()
        pool = child.states[25]
        actor.maximum = expr.to_float(pool.evaluate(pool.node.field("m_amount"), watch=False))
        actor.set_health(actor.maximum)
        actor.speed = expr.to_float(child.evaluate(child.graph.node(22).field("m_speed")))
        actor.clearance = expr.to_float(child.evaluate(child.graph.node(24).field("m_castLength")))
        if not pool.active or not all(math.isfinite(v) and v > 0 for v in (
            actor.maximum, actor.speed, actor.clearance,
        )):
            raise ValueError("Sigma barrier graph inputs failed to initialize")
        self.set_mode(actor, 1)
        return True

    def retire(self, source_body):
        actor = self.actors.pop(source_body, None)
        if actor is not None:
            actor.mode = 1
            for child in tuple(actor.component.instances.values()):
                actor.component.destroy(child)

    def health(self, instance, target):
        if instance.graph.index == 0x1B55:
            actor = self.actors.get(instance.component.entity)
            if actor is not None and target == actor.body:
                return actor.health
        return NotImplemented

    def set_mode(self, actor, mode):
        actor.mode = mode
        child = actor.instance()
        if child is not None:
            child.write((expr.INSTANCE, 59), mode)
            actor.component.run_now()

    def sync(self, player):
        from ow174.game.combat import aim

        actor = self.actors.get(player.body)
        if actor is None or actor.instance() is None:
            return
        script = player.body_script
        if (player.gone or script is None or script.retired or script.component.owner_dead
                or script.component is not actor.source.component):
            self.retire(player.body)
            return
        phase = actor.source.vars.get(59)
        mode = expr.to_int(phase.value()) if phase is not None else 1
        if mode == actor.mode:
            return
        if mode == 2:
            actor.position = self.fight._eye(player)
            actor.yaw, actor.pitch = round(player.mover.yaw), round(player.mover.pitch)
            actor.direction = aim(actor.yaw, actor.pitch)
            actor.blocked = False
        self.set_mode(actor, mode)

    def update(self, tick, dt):
        for body, actor in tuple(self.actors.items()):
            if actor.owner.body != body:
                self.retire(body)
                continue
            self.sync(actor.owner)
            if body not in self.actors:
                continue
            component = actor.component
            elapsed = tick-component.frame
            if elapsed <= 0:
                continue
            if actor.mode == 2 and not actor.blocked:
                self.advance(actor, dt*elapsed)
            # In storage, start repair at the authored wait event even when
            # the next update skips over its deadline. ModifyHealth consumes
            # the elapsed duration after that event.
            while actor.mode == 1 and actor.health < actor.maximum:
                due = component.next_event_time()
                if due is None:
                    break
                frame = max(component.frame+1, due//component.frame_ms)
                if frame >= tick:
                    break
                component.run_frame(frame)
            component.run_frame(tick)

    def advance(self, actor, dt):
        step = actor.speed*dt
        hit = self.fight._collision().cast_ray(actor.position, actor.direction, step+actor.clearance)
        if hit is not None:
            step = max(0, hit.distance-actor.clearance)
            actor.blocked = True
        actor.position = tuple(p+step*d for p, d in zip(actor.position, actor.direction, strict=True))

    def enemies(self, shooter, team=None):
        team = shooter.team if team is None else team
        for actor in self.actors.values():
            if actor.active() and actor.owner is not shooter and (
                self.fight.match.game_map.free_for_all or actor.owner.team != team
            ):
                yield actor

    def ray(self, actor, origin, direction, length, radius=0):
        return barriers.intersect(
            self.geometry, actor.position, actor.yaw, actor.pitch, origin, direction, length, radius,
        )

    def damage(self, actor, amount):
        if amount > 0 and actor.active():
            actor.set_health(actor.health-amount)
            actor.source.component.run_now()
            self.sync(actor.owner)
