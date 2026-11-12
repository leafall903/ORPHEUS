"""Replicate Sigma's existing 2D25 actor and its one authored 1A2A VM."""

import math

from ow174.game import observers, sigma_barrier, world


def projection(fight, entity):
    actor = fight.sigma_barriers.get(entity)
    if actor is not None and actor.component is not None:
        return observers.SceneBody(actor.component)
    return None


def sync(fight):
    live = {entity: actor for entity, actor in fight.sigma_barriers.items()
            if actor.component is not None}
    fight.sigma_barrier_wire.update(live)
    for viewer in fight.match.players:
        client = viewer.client
        if client is None or not viewer.spawned or viewer.gone:
            continue
        for entity in fight.sigma_barrier_wire - live.keys():
            if entity in viewer.seen:
                del viewer.seen[entity]
                client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])
        for entity, actor in live.items():
            # The actual child graph hides the stored/returning shield. Keep its
            # entity alive across recalls so the client can redeploy the same Pet.
            forward = actor.basis[2]
            yaw = math.atan2(forward[0], forward[2])
            pitch = -math.atan2(forward[1], math.hypot(forward[0], forward[2]))
            y, p = yaw / 2, pitch / 2
            rotation = (math.cos(y) * math.sin(p), math.sin(y) * math.cos(p),
                        -math.sin(y) * math.sin(p), math.cos(y) * math.cos(p))
            movement = world.Movement(actor.position, round(yaw * 65536 / math.tau),
                                      round(pitch * 65536 / math.tau))
            components = {26: [actor.owner.team_bit]}
            components.update(actor.vitals.component(actor.vitals.most))
            if entity not in viewer.seen:
                viewer.seen[entity] = False
                client.queue_entities([world.EntityUpdate(
                    entity, world.OP_CREATE,
                    lambda origin, c=components, pos=actor.position, rot=rotation, m=movement:
                        world.create(origin, sigma_barrier.BARRIER_DEFINITION, c,
                                     position=pos, rotation=rot, movement=m))])
            elif client.bot_create_acked(entity):
                update = world.EntityUpdate(entity, movement=movement)
                update.resend = False
                client.queue_entities([update])
                client.queue_body_health(entity, components)
            client.queue_bot_live(entity, projection(fight, entity).latest_live)
    fight.sigma_barrier_wire.intersection_update(
        set(live) | {entity for viewer in fight.match.players for entity in viewer.seen})
