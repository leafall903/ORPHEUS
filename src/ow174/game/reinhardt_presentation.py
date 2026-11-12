"""Replicate the existing authored011A shield; never run a second shield VM."""

import math

from ow174.game import observers, reinhardt_barrier, world

HERO = 0x02E0000000000007
DEFINITION = 0x040000000000011A


def projection(fight, entity):
    barrier = fight.barriers.scripted.get(entity)
    if barrier is not None and barrier.component.world.hero == HERO:
        return observers.SceneBody(barrier.component)
    return None


def sync(fight):
    manager = fight.barriers.reinhardt
    live = {entity: barrier for entity, barrier in fight.barriers.scripted.items()
            if barrier.component.world.hero == HERO and not barrier.retired}
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
            owner = barrier.parent
            mover = owner.mover
            geometry = manager.geometry
            if geometry is None or mover is None:
                continue
            position, _ = reinhardt_barrier.frame(
                geometry, mover.position, mover.yaw, mover.pitch, mover.data.stand_radius,
                mover.data.crouch_height if mover.crouched else mover.data.stand_height,
            )
            y, p = mover.yaw * math.pi / 65536, mover.pitch * math.pi / 65536
            rotation = (math.cos(y) * math.sin(p), math.sin(y) * math.cos(p),
                        -math.sin(y) * math.sin(p), math.cos(y) * math.cos(p))
            movement = world.Movement(position, mover.yaw, mover.pitch)
            components = {26: [owner.team_bit]}
            if barrier.vitals is not None:
                components.update(barrier.vitals.component(barrier.vitals.most))
            if entity not in viewer.seen:
                viewer.seen[entity] = False
                client.queue_entities([world.EntityUpdate(
                    entity, world.OP_CREATE,
                    lambda origin, c=components, pos=position, rot=rotation, m=movement:
                        world.create(origin, DEFINITION, c, position=pos, rotation=rot, movement=m),
                )])
            elif client.bot_create_acked(entity):
                update = world.EntityUpdate(entity, movement=movement)
                update.resend = False  # superseded poses must never rewind a moving shield
                client.queue_entities([update])
                client.queue_body_health(entity, components)
            client.queue_bot_live(entity, projection(fight, entity).latest_live)
    manager.wire_entities.intersection_update(
        set(live) | {entity for viewer in fight.match.players for entity in viewer.seen}
    )
