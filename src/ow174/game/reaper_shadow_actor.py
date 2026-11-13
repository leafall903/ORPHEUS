"""Original00F2/74 authoritative1F0E actor; no health, Pet or palette override.

104319 mirror root allocation64C940 ->977F20 ->1B95230 sets instance bit3.
8EAC30 skips this non-clientOnly factory on that mirror; authority creates via
8F9ED0 and writes E95B313B. The actor cache is owned by this state's cleanup.
"""
import math

from ow174.game import reaper, world
from ow174.game.script import expr, runtime

DEFINITION = 0x0400000000001F0E


def _source(state):
    component = state.instance.component
    match = getattr(component.world, 'match', None)
    if match is None:
        return None
    return next((p for p in match.players if not p.gone and p.has_body
                 and p.body == component.entity and p.body_script is not None
                 and p.body_script.component is component and reaper.is_reaper(p)), None)


class ShadowActor(runtime.State):
    """Cache a real allocated identity for only the authored Shadow actor state."""

    def on_created(self):
        self.actor_entity = 0
        self.actor_point = None

    def on_begin(self):
        source = _source(self)
        if source is None or source.match.combat.is_dead(source):
            return
        instance, node = self.instance, self.node
        # These are the actual fields consumed by priority2 OnBegin8EAC30.
        # Authored118/120 (1883/12080) are not Pet/owner inputs on this path.
        definition = instance.evaluate(node.field('m_entityDef'))
        point = instance.evaluate(node.field('m_position'))
        if (node.client_only or node.server_only
                or definition != expr.Asset(DEFINITION) or not isinstance(point, expr.Vec3)
                or not all(math.isfinite(v) for v in point)
                or any(node.field(k) is not None for k in ('m_40AF7E2D', 'm_1C558638'))):
            return  # Native failed creation leaves the authored output unchanged.
        output = expr.dynamic(node.field('m_E95B313B'))
        if output != (0, 12105):
            return
        entity = source.match.combat.allocate_combat_entity()
        self.actor_entity, self.actor_point = entity, tuple(point)
        # Queue the definition/create before an owner/observer chunk can refer to it.
        for viewer in source.match.players:
            if viewer.spawned and viewer.client is not None:
                viewer.client.queue_entities([create_update(self)])
        instance.write(output, expr.Entity(entity))

    def on_cleanup(self):
        entity, self.actor_entity = self.actor_entity, 0
        self.actor_point = None
        if not entity:
            return
        match = getattr(self.instance.component.world, 'match', None)
        if match is not None:
            for viewer in match.players:
                if viewer.spawned and viewer.client is not None:
                    viewer.client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])
        # Original cleanup destroys the cached actor; it does not clear E95B313B.

    on_failed = on_cleanup
    on_destroyed = on_cleanup


def live_actors(match):
    for player in match.players:
        script = player.body_script
        if script is None or script.retired:
            continue
        for instance in script.component.instances.values():
            if instance.graph.index != reaper.SHADOW_GRAPH:
                continue
            state = instance.states.get(43)
            if type(state) is ShadowActor and state.actor_entity and state.actor_point is not None:
                yield state


def lookup(match, entity):
    return next((s for s in live_actors(match) if s.actor_entity == entity), None)


def create_update(state):
    entity, point = state.actor_entity, state.actor_point
    return world.EntityUpdate(entity, world.OP_CREATE,
        lambda origin: world.create(origin, DEFINITION, {},
            flags=world.WITH_TRANSFORM | world.WITH_SCALE, position=point,
            rotation=(0.0, 0.0, 0.0, 1.0)))


def spawn_updates(match):
    return [create_update(state) for state in live_actors(match)]


def record_ready(client, item):
    """Use the existing create generation/ACK; queued is not received."""
    if item.chunk is None:
        return True
    for actor in live_actors(client.player.match):
        if actor.instance.component.entity != item.entity:
            continue
        generation = client.actor_generation(actor.actor_entity)
        if generation is None or client._actor_create_acks.get(actor.actor_entity) != generation:
            return False
    return True


def retire_source(script):
    """Release this source's caches before BodyScript detaches its match."""
    if script.hero != reaper.HERO:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == reaper.SHADOW_GRAPH:
            state = instance.states.get(43)
            if type(state) is ShadowActor:
                state.on_cleanup()
