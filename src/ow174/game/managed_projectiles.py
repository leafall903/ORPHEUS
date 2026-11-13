"""Definition-backed source allocation for an existing accepted projectile.

This owns managed identity, spatial lookup and pending component47 allocation.
The existing flight remains the sole motion/lifetime/side-effect producer.
No rigid-body mass, managed +AC association or component clock is inferred.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, graph, runtime, startup

DYNAMITE = 0x0400000000002533


class SourceUnavailable(ValueError):
    """The source is not the current registered lifetime of its real flight."""


class SourceWorld(runtime.World):
    def __init__(self, match, definition):
        super().__init__(mode=match.game_map.mode_guid)
        self.match = match
        self.body_definition = definition
        self.source = None

    def position(self, entity):
        source = self.source
        if source is None or not source.current():
            raise SourceUnavailable("projectile source is retired or replaced")
        if entity == source.entity:
            return source.position()
        point = self.match.script_body_position(entity, source.entity)
        if point is None:
            raise SourceUnavailable("projectile world has no qualified foreign pose")
        return point


@dataclass
class Source:
    entity: int
    generation: int
    definition: int
    manager: object
    flight: object
    born_world_tick: int
    component: runtime.Component
    association_known: bool = False
    associated_component: int | None = None
    retired: bool = False

    def current(self):
        match = self.component.world.match
        return (not self.retired and self.component.world.source is self
                and match.body_components.get(self.entity) is self.component
                and getattr(self.flight, "managed_source", None) is self
                and any(item is self.flight for item in self.manager.grenades))

    def position(self):
        if not self.current():
            raise SourceUnavailable("projectile source is retired or replaced")
        point = self.flight.position
        if len(point) != 3 or not all(math.isfinite(value) for value in point):
            raise SourceUnavailable("projectile source pose is not finite")
        return expr.Vec3(*point)

    def retire(self):
        if self.retired:
            return False
        self.retired = True
        # Only this exact component may be removed. An old callback cannot
        # unregister a replacement even if a caller deliberately reuses an ID.
        self.component.world.match.unregister_body_component(self.component)
        for instance in tuple(self.component.instances.values()):
            self.component.destroy(instance)
        return True


def register(fight, manager, flight, *, definition, born_world_tick):
    """Allocate the qualified2533 source on the actual accepted flight.

    Missing legacy resource metadata leaves the source unavailable. Startup
    allocates authored graphs and schemas, without running their pending Entry
    events; the original1502 clock/fuse/damage owner is not activated here.
    """
    from ow174.game import ashe

    if (definition != DYNAMITE or manager is not fight.dynamite
            or not isinstance(flight, ashe.Grenade)
            or not any(item is flight for item in manager.grenades)
            or type(born_world_tick) is not int or not 0 <= born_world_tick <= fight.tick):
        raise SourceUnavailable("unqualified projectile source producer")
    existing = getattr(flight, "managed_source", None)
    if existing is not None:
        if type(existing) is Source and existing.current():
            return existing
        raise SourceUnavailable("a retired projectile source cannot be rebound")
    record = graph.entity_body(definition)
    if record is None:
        return None
    config = graph.entity_script_component(definition)
    if not isinstance(config, dict):
        return None
    declared = record.get("graphs")
    if (not isinstance(declared, list) or [row.get("graph") for row in declared] != [0x0033, 0x1502]
            or any(graph.graph(row["graph"]) is None for row in declared)):
        return None
    world = SourceWorld(fight.match, definition)
    entity = fight.allocate_combat_entity()
    fight.managed_source_generation += 1
    component = runtime.Component(entity, world=world, strict=True)
    source = Source(entity, fight.managed_source_generation, definition, manager, flight,
                    born_world_tick, component)
    world.source = source
    flight.managed_source = source
    fight.match.register_body_component(component)
    try:
        for instance in startup.ComponentStartup(component, config).start():
            # Native977F20 zero-source roots use their allocating component,
            # before pending schema initialization and Entry execution.
            instance.set_source_entity(component.entity)
    except Exception:
        source.retire()
        raise
    return source


def lookup(match, entity):
    component = match.body_components.get(entity)
    if component is None or type(component.world) is not SourceWorld:
        return None
    source = component.world.source
    return source if type(source) is Source and source.component is component and source.current() else None


def position(match, entity):
    source = lookup(match, entity)
    return source.position() if source is not None else None


def retire(flight):
    source = getattr(flight, "managed_source", None)
    return source.retire() if type(source) is Source else False
