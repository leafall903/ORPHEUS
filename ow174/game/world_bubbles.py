"""Scoped Winston00FA creation ->00FC host -> fixed finite world sphere.

DATA:01C3/model0D02 radius5, native request params700/9 and00FC ChaseVar11.
RECONSTRUCTED: private untransported actor identity, caster context, world-tick
clock mapping, finite health bridge, area visibility and source retirement.
Native spawn options, transforms/filter policy and actor replication remain open.
"""
import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, play, play_queue, runtime

FIRST, SPAN = 0xA0140000, 0x40000
WINSTON = 0x02E0000000000009


@dataclass
class FixedBubble:
    entity: int
    owner: object
    source_component: object
    source_body: int
    team: int
    point: tuple
    born_tick: int
    origin_frame: int
    origin_ms: int
    component: object = None
    maximum: float = 0.0
    health: float = 0.0
    radius: float = 5.0

    @property
    def body(self):
        return self.entity  # a world actor, never a protected player's body

    def centre(self):
        return self.point


def shell(origin, direction, centre, radius, length):
    """First crossed surface, including the exit when the origin is inside."""
    delta = tuple(a-b for a,b in zip(origin,centre,strict=True))
    c = sum(v*v for v in delta)-radius*radius
    b = sum(v*d for v,d in zip(delta,direction,strict=True))
    determinant = b*b-c
    if determinant < 0:
        return None
    root = math.sqrt(determinant)
    distance = -b-root if c >= 0 else -b+root
    return distance if 0 <= distance <= length else None


class WorldBubbles:
    def __init__(self, fight):
        self.fight = fight
        self.actors = {}
        self.next_id = 0

    def create(self, world, instance, node):
        if instance.component.strict:
            return NotImplemented  # this explicit body consumer is not a native strict actor backend
        if world.hero != WINSTON or instance.graph.index != 0xFA or node.pos != 26:
            return NotImplemented
        definition = node.field('m_entityDefinition') or {}
        if expr.guid_of(definition.get('m_entityDef')) != 0x04000000000001C3:
            return NotImplemented
        owner = next((p for p in self.fight.match.players if p.body == world.body
                      and p.body_script is not None and not p.body_script.retired
                      and p.body_script.component is instance.component),None)
        if owner is None or self.fight.is_dead(owner) or graph.graph(0xFC) is None:
            return True
        point = instance.evaluate(node.field('m_position'))
        target = expr.dynamic(node.field('m_B3019338'))
        if not isinstance(point,expr.Vec3) or target is None or not all(math.isfinite(x) for x in point):
            return NotImplemented
        for _ in range(SPAN):
            entity = FIRST+self.next_id % SPAN
            self.next_id += 1
            if entity not in self.actors:
                break
        else:
            raise RuntimeError('static Winston actor identity range exhausted')
        self.actors[entity] = FixedBubble(entity,owner,instance.component,world.body,owner.team,
                                          tuple(point),self.fight.tick,instance.component.frame,
                                          instance.component.now)
        instance.write(target,expr.Entity(entity))
        return True

    def host(self, world, instance, node):
        if instance.component.strict:
            return NotImplemented
        if world.hero != WINSTON or instance.graph.index != 0xFA or node.pos != 2:
            return NotImplemented
        resource = play.resolve_resource(instance,node)
        if resource is None or resource.guid != 0x05800000000000FC:
            return NotImplemented
        request = play.resolve_request(instance,node,resource,context_provider=lambda: play.InvocationContext(
            world.body,world.body,world.body))  # explicit scoped caster-context reconstruction
        if request is None:
            return True
        from ow174.game.script.driver import BodyWorld

        for target in request.targets:
            actor = self.actors.get(target)
            if actor is None or actor.source_component is not instance.component:
                continue
            if actor.component is not None:
                continue
            actor_world = BodyWorld(WINSTON,world.mode,actor.entity)
            actor_world.match = self.fight.match
            component = runtime.Component(actor.entity,world=actor_world)
            component.frame, component.now = actor.origin_frame,actor.origin_ms
            queue = play_queue.Queue(component,lambda _guid: play_queue.Load('ready',graph.graph(0xFC)))
            queue.submit(request)
            component.run_now()
            actor.component = component
            hosted = next(i for i in component.instances.values() if i.graph.index == 0xFC)
            pool = hosted.states.get(0)
            timer = hosted.states.get(11)
            if pool is None or not pool.active or timer is None or not timer.active:
                self.drop(actor.entity)
                continue
            actor.maximum = actor.health = expr.to_float(pool.evaluate(pool.node.field('m_amount')))
        return True

    def drop(self, entity):
        actor = self.actors.pop(entity,None)
        if actor is not None and actor.component is not None:
            for instance in tuple(actor.component.instances.values()):
                actor.component.destroy(instance)

    def retire(self, body):
        for actor in tuple(self.actors.values()):
            if actor.source_body == body:
                self.drop(actor.entity)

    def update(self, tick):
        for actor in tuple(self.actors.values()):
            if (not self.live_source(actor)
                    or actor.owner not in self.fight.match.players):
                self.drop(actor.entity)
                continue
            if actor.component is None:
                if tick > actor.born_tick:
                    self.drop(actor.entity)
                continue
            elapsed = max(0,tick-actor.born_tick)
            component = actor.component
            component.frame = actor.origin_frame+elapsed
            component.now = actor.origin_ms+elapsed*16
            component.update()
            component.run_now()
            hosted = next((i for i in component.instances.values() if i.graph.index == 0xFC),None)
            timer = hosted.states.get(11) if hosted is not None else None
            if timer is None or not timer.active or timer.reached or actor.health <= 0:
                self.drop(actor.entity)

    def enemies(self, shooter):
        return [a for a in self.actors.values() if a.component is not None and a.health > 0
                and self.live_source(a)
                and a.owner is not shooter
                and (self.fight.match.game_map.free_for_all or a.team != shooter.team)]

    @staticmethod
    def live_source(actor):
        script = actor.owner.body_script
        return (script is not None and not script.retired and script.component is actor.source_component
                and actor.owner.spawned and actor.owner.has_body and not actor.owner.gone)

    def damage(self, actor, amount):
        lost = min(actor.health,max(0.0,amount))
        actor.health -= lost
        if actor.health <= 0:
            self.drop(actor.entity)
        return lost

    def explosion(self, shooter, point, blast, visible_from, *, forced=()):
        """One pool debit and all crossed body paths per area event; centre visibility is reconstructed."""
        from ow174.game.combat import _sub, capsule_visible

        actors = {a.entity:a for a in (*self.enemies(shooter),*forced)}
        protected = set()
        for actor in actors.values():
            distance = math.dist(point,actor.point)
            scalar = blast.splash(abs(distance-actor.radius))
            if scalar <= 0:
                continue
            direction = _sub(actor.point,visible_from)
            length = math.sqrt(sum(v*v for v in direction))
            surface = tuple(c+v*actor.radius/max(length,1e-12)
                            for c,v in zip(actor.point,_sub(visible_from,actor.point),strict=True))
            if not capsule_visible(self.fight._collision(),visible_from,surface,surface,.01):
                continue
            for target in self.fight._enemies(shooter,damageable=True):
                a,b = target.segment()
                centre = tuple((x+y)*.5 for x,y in zip(a,b,strict=True))
                delta = _sub(centre,visible_from)
                reach = math.sqrt(sum(v*v for v in delta))
                ray = tuple(v/max(reach,1e-12) for v in delta)
                if shell(visible_from,ray,actor.point,actor.radius,reach) is not None:
                    protected.add(target.entity)
            if actor.health > 0:
                self.damage(actor,blast.amount*scalar)
        return protected
