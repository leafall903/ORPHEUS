"""Selected 1B55/1A2A host with reconstructed actor identity and travel."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import contacts
from ow174.game.collision import World
from ow174.game.script import expr, graph, play, play_queue, runtime


def configured_mesh():
    path = os.environ.get("ORPHEUS_SIGMA_COLLISION")
    if not path:
        return None, ()
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != "c166d81d2a5095a7b55cebc9fc9575df9f0ba4708195d2e6f0127e22081ef7b6":
        raise ValueError("Sigma C015 descriptor hash mismatch")
    triangles = tuple(tuple(row) for row in json.loads(raw)["triangles"])
    return World(triangles, name="Sigma C015 build 104319"), triangles

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
        self.actors = {}
        self.mesh, self.triangles = configured_mesh()

    def create(self, instance, node):
        if (instance.component.strict
                or (instance.graph.index, node.pos) != (0x1B55, 6)):
            return NotImplemented
        if node.field("m_entityDefinition")["m_entityDef"] != "0x0400000000002D25":
            raise ValueError("Sigma barrier definition changed")
        if graph.entity_body(0x0400000000002D25) is None or graph.graph(0x1A2A) is None:
            return NotImplemented
        source_body = instance.component.entity
        # The Pet can start during BodyScript construction, before the new
        # script is assigned to the player. Match the live body at that point.
        owner = next((p for p in self.fight.match.players
                      if p.body == source_body and p.has_body and not p.gone), None)
        if owner is None or instance.component.owner_dead:
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
        if (instance.component.strict
                or (instance.graph.index, node.pos) != (0x1B55, 8)):
            return NotImplemented
        pet = instance.states.get(4)
        if pet is None or not pet.active:
            return NotImplemented
        if self.create(instance, pet.node) is NotImplemented:
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
        if mode == 1:
            # Cooldown output 61 is a time-derived link. The static VM does
            # not notify this watcher when the countdown naturally reaches zero.
            gate = actor.source.states.get(106)
            if (gate is not None and gate.active and not gate.current
                    and expr.truthy(gate.evaluate(gate.node.field("m_condition"), watch=False))):
                gate.dependency_changed()
                actor.source.component.run_now()
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

    @staticmethod
    def axes(actor):
        from ow174.game.combat import aim

        return aim(actor.yaw + 16384, 0), aim(actor.yaw, actor.pitch - 16384), actor.direction

    def enemies(self, shooter):
        for actor in self.actors.values():
            if (actor.mode in (2, 3) and actor.health > 0 and actor.owner is not shooter
                    and (self.fight.match.game_map.free_for_all or actor.owner.team != shooter.team)):
                yield actor

    def ray(self, actor, origin, direction, reach):
        axes = self.axes(actor)
        local = tuple(contacts.dot(contacts.sub(origin, actor.position), axis) for axis in axes)
        ray = tuple(contacts.dot(direction, axis) for axis in axes)
        return self.mesh.cast_ray(local, ray, reach)

    def intercept(self, shooter, origin, direction, reach):
        hit = min(self.projectile_contacts(shooter, origin, direction, reach), key=lambda hit: hit[0], default=None)
        return hit[:2] if hit is not None else None

    def projectile_contacts(self, shooter, origin, direction, reach):
        if self.mesh is None:
            return
        for actor in self.enemies(shooter):
            hit = self.ray(actor, origin, direction, reach)
            if hit is not None:
                yield hit.distance, actor, actor.body

    def blast_distance(self, actor, origin, reach, *, ice_walls=None):
        from ow174.game.combat import ray_world

        axes = self.axes(actor)
        local = tuple(contacts.dot(contacts.sub(origin, actor.position), axis) for axis in axes)
        nearest = None
        collision = self.fight._collision()
        for row in self.triangles:
            point = contacts.closest_on_triangle(row[:3], row[3:6], row[6:9], local)
            delta = contacts.sub(point, local)
            distance = math.sqrt(contacts.dot(delta, delta))
            if distance > reach or (nearest is not None and distance >= nearest):
                continue
            if distance > 1e-9:
                direction = tuple(sum(axis[k] * value for axis, value in zip(axes, delta, strict=True)) / distance
                                  for k in range(3))
                wall = ray_world(collision, origin, direction, distance, ice_walls=ice_walls)
                if wall is not None and wall < distance - 1e-6:
                    continue
            nearest = distance
        return nearest

    def explosion(self, shooter, origin, targets, reach, blast_amount):
        protected = set()
        if self.mesh is None:
            return protected
        for actor in self.enemies(shooter):
            distance = self.blast_distance(actor, origin, reach)
            if distance is None:
                continue
            for target in targets:
                centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                length = math.dist(origin, centre)
                if length <= 1e-9:
                    continue
                direction = tuple(axis / length for axis in contacts.sub(centre, origin))
                if self.ray(actor, origin, direction, length) is not None:
                    protected.add(target.entity)
            self.damage(actor, blast_amount(distance))
        return protected

    def damage(self, actor, amount):
        if amount > 0 and actor.health > 0 and actor.mode in (2, 3):
            actor.set_health(actor.health - amount)
            actor.source.component.run_now()
            self.sync(actor.owner)
