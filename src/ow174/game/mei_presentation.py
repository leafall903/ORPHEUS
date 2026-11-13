"""Deliver Mei's existing ice actors and their authored VMs to every viewer."""

from ow174.game import actors, barriers, ice_wall, observers, world

CRYO = 0x040000000000090B


def projection(fight, entity):
    barrier = fight.barriers.scripted.get(entity)
    if barrier is not None and barrier.component.world.hero == barriers.MEI:
        return observers.SceneBody(barrier.component)
    actor = fight.deployables.get(entity)
    if (actor is not None and actor.definition == ice_wall.PILLAR
            and isinstance(actor.body_script, actors.PillarScript)):
        return observers.SceneBody(actor.body_script.component)
    return None


def retire(fight, entity):
    for viewer in fight.match.players:
        if entity in viewer.seen:
            del viewer.seen[entity]
            if viewer.client is not None:
                viewer.client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])


def sync(fight):
    ice = [barrier for barrier in fight.barriers.scripted.values()
           if barrier.component.world.hero == barriers.MEI and not barrier.retired]
    pillars = [actor for actor in fight.deployables.values()
               if actor.definition == ice_wall.PILLAR
               and isinstance(actor.body_script, actors.PillarScript)]
    for viewer in fight.match.players:
        client = viewer.client
        if client is None or not viewer.spawned or viewer.gone:
            continue
        for barrier in ice:
            entity, owner = barrier.entity, barrier.parent
            mover = owner.mover
            if mover is None:
                continue
            movement = world.Movement(tuple(mover.position), mover.yaw, mover.pitch)
            if entity not in viewer.seen:
                viewer.seen[entity] = False
                components = {26: [owner.team_bit]}
                if barrier.pet_owner is not None:
                    components[72] = [barrier.pet_owner]
                client.queue_entities([world.EntityUpdate(
                    entity, world.OP_CREATE,
                    lambda origin, c=components, m=movement:
                        world.create(origin, CRYO, c, position=m.position, movement=m),
                )])
            elif client.bot_create_acked(entity):
                update = world.EntityUpdate(entity, movement=movement)
                update.resend = False  # superseded source poses must not rewind the ice actor
                client.queue_entities([update])
            client.queue_bot_live(entity, projection(fight, entity).latest_live)
        # Pillars already have one authoritative deployable create/destroy path.
        # Attach the same 0636 VM to that entity instead of publishing another actor.
        for actor in pillars:
            if actor.entity not in viewer.seen:
                viewer.seen[actor.entity] = False
                client.queue_entities([fight.deployable_create(actor)])
            client.queue_bot_live(actor.entity, projection(fight, actor.entity).latest_live)
