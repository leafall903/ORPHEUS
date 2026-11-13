"""Scoped Winston00FA creation ->00FC host -> fixed finite world sphere.

DATA:01C3/model0D02 radius5, native request params700/9 and00FC ChaseVar11.
RECONSTRUCTED: actor identity, caster context, world-tick
clock mapping, finite health bridge, area visibility and source retirement.
Native spawn options and transforms/filter policy remain open.
"""
import math
from dataclasses import dataclass

from ow174.game import winston
from ow174.game.script import expr, graph, play, play_queue, runtime

FIRST, SPAN = 0xA0140000, 0x40000
WINSTON = 0x02E0000000000009


@dataclass
class FixedBubble:
    entity: int
    owner: object
    source_component: object
    source_body: int
    pet_source: int
    team: int
    point: tuple
    born_tick: int
    origin_frame: int
    origin_ms: int
    component: object = None
    maximum: float = 0.0
    health: float = 0.0
    radius: float = 5.0
    retired: bool = False

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
        self.pending = []
        self.wire_entities = set()

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
        # 00FA/26 asks component43 for the caster's controller. Pet72 owns
        # this identity; the hosted 00FC instigator remains the caster body.
        pet_source = instance.evaluate(node.field('m_D749D7D8'))
        target = expr.dynamic(node.field('m_B3019338'))
        if (not isinstance(pet_source, expr.Entity) or pet_source.id != owner.entity
                or not isinstance(point,expr.Vec3) or target is None
                or not all(math.isfinite(x) for x in point)):
            return NotImplemented
        for _ in range(SPAN):
            entity = FIRST+self.next_id % SPAN
            self.next_id += 1
            if entity not in self.actors:
                break
        else:
            raise RuntimeError('static Winston actor identity range exhausted')
        born = winston.world_tick_at_body(owner.body_script, instance.component.frame)
        self.actors[entity] = FixedBubble(entity,owner,instance.component,world.body,
                                          expr.to_entity(pet_source),owner.team,
                                          tuple(point),born,instance.component.frame,
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
        from ow174.game.winston_barrier import DomeWorld

        for target in request.targets:
            actor = self.actors.get(target)
            if actor is None or actor.source_component is not instance.component:
                continue
            if actor.component is not None:
                continue
            actor_world = DomeWorld(self, actor)
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
            hosted.write((expr.ENTITY, 253), actor.health)
            hosted.write((expr.ENTITY, 478), False)
            component.run_now()
            self.advance(actor, self.fight.tick)
        return True

    def drop(self, entity):
        actor = self.actors.pop(entity,None)
        if actor is not None:
            actor.retired = True
        if actor is not None and actor.component is not None:
            for instance in tuple(actor.component.instances.values()):
                actor.component.destroy(instance)

    def retire(self, body):
        for actor in tuple(self.actors.values()):
            if actor.source_body == body:
                self.drop(actor.entity)

    def advance(self, actor, tick):
        """Catch the existing VM up from the event's world tick, including packet delay."""
        component = actor.component
        elapsed = max(0, tick - actor.born_tick) * 16 // component.frame_ms
        component.frame = actor.origin_frame + elapsed
        component.now = actor.origin_ms + elapsed * component.frame_ms
        component.update()
        component.run_now()
        hosted = next((i for i in component.instances.values() if i.graph.index == 0xFC), None)
        timer = hosted.states.get(11) if hosted is not None else None
        if actor.retired or timer is None:
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
            self.advance(actor, tick)

        pending, self.pending = self.pending, []
        for actor, params in pending:
            if self.live_source(actor) and actor.source_component.fault is None:
                if runtime.send_message(actor.source_component, 0x0240000000000053, params, actor.entity):
                    actor.source_component.run_now()
        from ow174.game import winston_barrier

        winston_barrier.sync(self)

    def enemies(self, shooter, *, team=None):
        if team is None:
            team = shooter.team
        return [a for a in self.actors.values() if not a.retired and a.component is not None and a.health > 0
                and self.live_source(a)
                and a.owner is not shooter
                and (self.fight.match.game_map.free_for_all or a.team != team)]

    @staticmethod
    def live_source(actor):
        script = actor.owner.body_script
        return (script is not None and not script.retired and script.component is actor.source_component
                and actor.owner.spawned and actor.owner.has_body and not actor.owner.gone)

    def trace(self, shooter, origin, direction, length, radius=0.0, *, team=None, bubbles=None):
        best = None
        for actor in self.enemies(shooter, team=team) if bubbles is None else bubbles:
            inside = math.dist(origin, actor.point) < actor.radius
            extent = max(0.0, actor.radius - radius) if inside else actor.radius + radius
            distance = shell(origin, direction, actor.point, extent, length)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, actor
        return best

    def expiry_tick(self, actor):
        """Map the existing00FC timer from this actor's observed birth pair."""
        if actor.component is None:
            return None
        hosted = next((i for i in actor.component.instances.values() if i.graph.index == 0xFC), None)
        timer = hosted.states.get(11) if hosted is not None else None
        if timer is None or not timer.active:
            return None
        seconds = expr.to_float(timer.evaluate(timer.node.field("m_EB0FC261"), watch=False))
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        elapsed = timer.start + round(seconds * 1000) - actor.origin_ms
        return actor.born_tick + -(-elapsed // actor.component.frame_ms)

    def cover_end(self, shooter, origin, target, ended):
        from ow174.game.symmetra_turrets import _capsule_ray

        direction, reach = _capsule_ray(origin, target)
        owner = getattr(shooter, "combat_owner", shooter)
        return max((at for actor, at in ended
                    if actor.owner is not owner
                    and (self.fight.match.game_map.free_for_all or actor.team != shooter.team)
                    and shell(origin, direction, actor.point, actor.radius, reach) is not None),
                   default=None) if reach else None

    def melee(self, shooter, origin, centre, radius, amount):
        from ow174.game.combat import capsule_visible

        for actor in self.enemies(shooter):
            distance = math.dist(centre, actor.point)
            if distance <= 0 or abs(distance - actor.radius) > radius:
                continue
            point = tuple(a + (b - a) * actor.radius / distance
                          for a, b in zip(actor.point, centre, strict=True))
            if capsule_visible(self.fight._collision(), origin, point, point, 0., walls=self.fight.walls):
                self.damage(actor, amount)

    def damage(self, actor, amount):
        lost = min(actor.health,max(0.0,amount))
        actor.health = expr.f32(actor.health - lost)
        hosted = next((i for i in actor.component.instances.values() if i.graph.index == 0xFC), None)
        if hosted is not None:
            hosted.write((expr.ENTITY, 253), actor.health)
            if actor.health <= 0:
                hosted.write((expr.ENTITY, 478), True)
            actor.component.run_now()
        return lost

    @staticmethod
    def nearest_shell(actor, point):
        radial = tuple(a - b for a, b in zip(point, actor.point, strict=True))
        distance = math.hypot(*radial)
        if distance == 0:
            radial, distance = (1.0, 0.0, 0.0), 1.0
        return tuple(centre + actor.radius * axis / distance
                     for centre, axis in zip(actor.point, radial, strict=True))

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
            surface = self.nearest_shell(actor, visible_from)
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
