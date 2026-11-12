"""Publish the existing0BD8 barrier VM and original14A1 client definition."""

import math

from ow174.game import observers, world
from ow174.game.orisa_barrier import DEFINITION


def projection(fight, entity):
    barrier = fight.orisa_barriers.active.get(entity) or fight.orisa_barriers.retiring.get(entity)
    return observers.SceneBody(barrier.component) if barrier is not None and barrier.component is not None else None


def sync(manager):
    fight = manager.fight
    live = {**manager.active, **manager.retiring}
    manager.wire_entities.update(live)
    for viewer in fight.match.players:
        client = viewer.client
        if client is None or not viewer.spawned or viewer.gone:
            continue
        for entity in manager.wire_entities - live.keys():
            if entity in viewer.seen:
                del viewer.seen[entity]
                client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])
        for entity, barrier in live.items():
            if entity not in viewer.seen:
                half = barrier.yaw / 2
                rotation = (0.0, math.sin(half), 0.0, math.cos(half))
                owner = next((p for p in fight.match.players if p.body == barrier.source_body), None)
                team_bit = owner.team_bit if owner is not None else 0x800000 << barrier.team
                components = {26: [team_bit]}
                viewer.seen[entity] = False
                client.queue_entities([world.EntityUpdate(
                    entity, world.OP_CREATE,
                    lambda origin, c=components, pos=barrier.position, rot=rotation:
                        world.create(origin, DEFINITION, c, position=pos, rotation=rot),
                )])
            script = projection(fight, entity)
            if script is not None:
                client.queue_bot_live(entity, script.latest_live)
    manager.wire_entities.intersection_update(
        set(live) | {entity for viewer in fight.match.players for entity in viewer.seen}
    )
