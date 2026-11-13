"""Server collision for Winston's authored Barrier Projector deployment."""

import math
from dataclasses import dataclass

from ow174.game import world
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000009
DEFINITION = 0x04000000000001C3
RADIUS = 5.0


@dataclass
class Dome:
    owner: int
    team: int
    centre: tuple[float, float, float]
    health: float
    until: float


def deploy(combat, body, instance, node):
    if instance.evaluate(node.field("m_script")) != expr.Asset(0x05800000000000FC):
        return
    owner = next((p for p in combat.match.players if p.body == body and not p.gone and p.has_body), None)
    if owner is None or combat.is_dead(owner):
        return
    params = {graph.guid(entry["m_B5051BCE"]): expr.to_float(instance.evaluate(entry["m_value"]))
              for entry in node.field("m_params") or ()}
    health, duration = params.get(0x0A40000000000044, 0), params.get(0x0A40000000000494, 0)
    if not all(math.isfinite(value) and value > 0 for value in (health, duration)):
        return
    x, y, z = owner.mover.position
    combat.winston_domes[body] = Dome(owner.entity, owner.team, (x, y + 0.1, z),
                                      health, combat.now + duration)


def intersection(combat, shooter, origin, direction, length, *, radius=0.0):
    nearest = None
    for dome in combat.winston_domes.values():
        if (dome.health <= 0 or combat.now >= dome.until or dome.owner == shooter.entity
                or (not combat.match.game_map.free_for_all and dome.team == shooter.team)):
            continue
        offset = tuple(a - b for a, b in zip(origin, dome.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        distance2 = sum(v * v for v in offset)
        inside = distance2 < RADIUS * RADIUS
        shell = max(0.0, RADIUS - radius) if inside else RADIUS + radius
        discriminant = projection * projection - distance2 + shell * shell
        if discriminant < 0:
            continue
        root = math.sqrt(discriminant)
        hit = max(0.0, -projection + root) if inside else -projection - root
        if 0 <= hit <= length and (nearest is None or hit < nearest[0]):
            nearest = hit, dome
    return nearest


HERO = 0x02E0000000000009


class DomeWorld(runtime.World):
    def __init__(self, manager, actor):
        super().__init__(HERO, manager.fight.match.game_map.mode_guid)
        self.manager, self.actor = manager, actor

    def position(self, entity):
        if entity == self.actor.body:
            return expr.Vec3(*self.actor.point)
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


def projection(fight, entity):
    from ow174.game import observers

    actor = fight.barriers.world.actors.get(entity)
    if actor is not None and actor.component is not None:
        return observers.SceneBody(actor.component)
    return None


def sync(manager):
    """Publish the existing sphere entity and its authored 00FC component."""
    live = {entity: actor for entity, actor in manager.actors.items()
            if not actor.retired and actor.component is not None}
    manager.wire_entities.update(live)
    for viewer in manager.fight.match.players:
        client = viewer.client
        if client is None or not viewer.spawned or viewer.gone:
            continue
        for entity in manager.wire_entities - live.keys():
            if entity in viewer.seen:
                del viewer.seen[entity]
                client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])
        for entity, actor in live.items():
            # #253 is derived from component 51 on the native client and is
            # excluded from VM snapshots. The collision pool must travel too.
            components = {26: [actor.owner.team_bit], 72: [actor.pet_source]}
            components.update(world.health(actor.health, actor.maximum))
            if entity not in viewer.seen:
                viewer.seen[entity] = False
                client.queue_entities([world.EntityUpdate(
                    entity, world.OP_CREATE,
                    lambda origin, c=components, point=actor.point:
                        world.create(origin, DEFINITION, c, position=point),
                )])
            elif client.bot_create_acked(entity):
                client.queue_body_health(entity, components)
            client.queue_bot_live(entity, projection(manager.fight, entity).latest_live)
    manager.wire_entities.intersection_update(
        set(live) | {entity for viewer in manager.fight.match.players for entity in viewer.seen})
