"""Selected00FA/26 ->01C3 ->00FC actor host, without native entity replication.

DATA: authored creation/output38, PlayScript parameters700/9, graph-owned
ChaseVar expiry and two-frame death tail, D02 sphere radius5. RECONSTRUCTED:
private identity, fixed feet+.1 placement, finite pool -> entity478 death flag,
team pass-through, shell interception/overkill and owner-disconnect lifetime.
Unhosted presentation/filter/credit/wire services are not native acceptance.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, play, play_queue, runtime

RADIUS = 5.0


@dataclass
class Bubble:
    owner: object
    source_body: int
    body: int
    team: int
    centre: tuple
    component: object
    source_script: object = None
    request: object = None
    health: float = 0.0
    maximum: float = 0.0
    retired: bool = False

    def ray(self, origin, direction, radius=0.0):
        offset = tuple(a-b for a, b in zip(origin, self.centre, strict=True))
        projection = sum(a*b for a, b in zip(offset, direction, strict=True))
        distance2 = sum(v*v for v in offset)
        # Swept sphere: outside meets expanded shell; inside meets contracted
        # shell. Bodies entirely inside a dome remain independently hittable.
        effective = max(0.0, RADIUS-radius) if distance2 < RADIUS*RADIUS else RADIUS+radius
        c = distance2-effective*effective
        disc = projection*projection-c
        if disc < 0:
            return None
        root = math.sqrt(disc)
        if distance2 < RADIUS*RADIUS:
            return max(0.0, -projection+root)
        entry = -projection-root
        if entry < -1e-8 or (entry <= 1e-8 and projection >= 0):
            return None
        return max(0.0, entry)


class DomeWorld(runtime.World):
    def __init__(self, manager, actor):
        super().__init__(0x02E0000000000009, manager.fight.match.game_map.mode_guid)
        self.manager, self.actor = manager, actor

    def position(self, entity):
        if entity == self.actor.body:
            return expr.Vec3(*self.actor.centre)
        return super().position(entity)

    def destroy_entity(self, instance, node):
        if instance.graph.index == 0xFC and node.pos in (12, 28):
            self.actor.retired = True  # teardown after dispatch, never reentrantly
            return True
        return NotImplemented

    def send_message(self, target, message, params, sender):
        actor = self.actor
        if (target == actor.source_body and sender == actor.body
                and message == 0x0240000000000053):
            self.manager.pending.append((actor, dict(params)))
            return
        super().send_message(target, message, params, sender)


class Barriers:
    def __init__(self, fight):
        self.fight, self.actors, self.pending = fight, {}, []

    def create(self, instance, node):
        if (instance.graph.index, node.pos) != (0xFA, 26):
            return NotImplemented
        definition = node.field('m_entityDefinition') or {}
        if definition.get('m_entityDef') != '0x04000000000001C3':
            raise ValueError('Winston barrier definition changed')
        owner = next((p for p in self.fight.match.players
                      if p.body == instance.component.entity and not p.gone and p.has_body), None)
        if owner is None or self.fight.is_dead(owner):
            instance.write(expr.dynamic(node.field('m_B3019338')), expr.Entity(0))
            return True
        entity = self.fight.next_bot
        self.fight.next_bot += 1
        x, y, z = owner.mover.position
        actor = Bubble(owner, owner.body, entity, owner.team, (x, y+.1, z), None)
        actor.source_script = owner.body_script
        actor.component = runtime.Component(entity, frame_ms=instance.component.frame_ms,
                                            world=DomeWorld(self, actor))
        actor.component.now = instance.component.now
        actor.component.frame = instance.component.frame
        self.actors[entity] = actor
        instance.write(expr.dynamic(node.field('m_B3019338')), expr.Entity(entity))
        return True

    def play(self, instance, node):
        if (instance.graph.index, node.pos) != (0xFA, 2):
            return NotImplemented
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != 0x05800000000000FC:
            raise ValueError('Winston barrier graph changed')
        request = play.resolve_request(instance, node, resource, context_provider=lambda:
            play.InvocationContext(instance.component.entity, instance.component.entity,
                                   instance.source_entity()))
        if request is None:
            return True
        for target in request.targets:
            actor = self.actors.get(target)
            if actor is None or actor.source_body != instance.component.entity or actor.request is not None:
                raise ValueError('Winston barrier PlayScript target is not its created actor')
            actor.request = request
            queue = play_queue.Queue(actor.component, lambda _: play_queue.Load('ready', graph.graph(0xFC)))
            queue.submit(request)
            actor.component.run_now()
            callee = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
            state = callee.states[0]
            if not state.active:
                raise ValueError('Winston barrier health pool did not start')
            actor.maximum = actor.health = expr.to_float(state.evaluate(state.node.field('m_amount')))
            callee.write((expr.ENTITY, 253), actor.health)
            callee.write((expr.ENTITY, 478), False)
            actor.component.run_now()
        return True

    def update(self, frame):
        for entity, actor in tuple(self.actors.items()):
            if not actor.retired and frame > actor.component.frame:
                actor.component.run_frame(frame)
            if actor.retired:
                for instance in tuple(actor.component.instances.values()):
                    actor.component.destroy(instance)
                del self.actors[entity]
        pending, self.pending = self.pending, []
        for actor, params in pending:
            script = actor.source_script
            if script.retired or actor.owner.body_script is not script or script.component.fault is not None:
                continue
            if runtime.send_message(script.component, 0x0240000000000053, params, actor.body):
                script.component.run_now()

    def enemy(self, actor, shooter):
        return (actor.owner is not shooter and (self.fight.match.game_map.free_for_all
                                                or actor.team != shooter.team))

    def intercept(self, shooter, origin, direction, length, radius=0.0):
        found = []
        for actor in self.actors.values():
            if actor.retired or actor.health <= 0 or not self.enemy(actor, shooter):
                continue
            distance = actor.ray(origin, direction, radius)
            if distance is not None and distance <= length:
                found.append((distance, actor, None))
        return found

    def damage(self, actor, amount):
        if amount <= 0 or actor.health <= 0 or actor.retired:
            return
        actor.health = expr.f32(max(0.0, actor.health-amount))
        callee = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
        callee.write((expr.ENTITY, 253), actor.health)
        if actor.health <= 0:
            callee.write((expr.ENTITY, 478), True)
        actor.component.run_now()
