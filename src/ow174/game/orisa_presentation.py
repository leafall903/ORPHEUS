"""Publish the existing barrier and Supercharger VMs with their original definitions."""

import math

from ow174.game import observers, world
from ow174.game.orisa_barrier import DEFINITION, HEALTH
from ow174.game.orisa_supercharger import DEFINITION as SUPERCHARGER
from ow174.game.script import expr, graph


def barrier_health(barrier):
    identifier = 199 if barrier.script is not None and barrier.script.graph.index == 0xD00 else 587
    maximum = (expr.to_float(barrier.script.find_var(graph.INSTANCE, identifier).value())
               if barrier.script is not None else HEALTH)
    return world.health(barrier.health, maximum)


def projection(fight, entity):
    barrier = fight.orisa_barriers.active.get(entity) or fight.orisa_barriers.retiring.get(entity)
    actor = barrier or fight.orisa.superchargers.active.get(entity)
    if actor is None or actor.component is None:
        orb = fight.orisa.orbs.get(entity)
        if orb is not None and orb.query is not None:
            return observers.SceneBody(orb.query.instance.component)
        return None
    body = observers.SceneBody(actor.component)
    if barrier is not None:
        # 0BD8's ModelLook declares entity253 as a remote input. Hero bodies
        # derive it from component51, but this original deployable also needs
        # the declared VM value; keep that exception local to Orisa barriers.
        body.latest_live.vars[(0, 253)] = (barrier.component.entity_var(253).value(), ())
    return body


def sync_orbs(fight):
    """Publish original0CCC on its already-created projectile, under one actor lifetime."""
    for orb in fight.orisa.orbs.values():
        if orb.query is None or orb.rocket.shot is None:
            continue
        body = projection(fight, orb.entity)
        for delivery in orb.rocket.shot.observers:
            client = delivery.viewer.client
            bind = getattr(client, "bind_actor_component", None)
            if client is not delivery.client or bind is None:
                continue
            binding = orb.actor_generations.get(delivery.viewer.slot)
            if binding is None:
                generation = client.actor_generation(orb.entity)
                if generation is None:
                    continue
                binding = client, generation
                orb.actor_generations[delivery.viewer.slot] = binding
            if binding[0] is client and bind(orb.entity, body, generation=binding[1]):
                client.queue_bot_live(orb.entity, body.latest_live)


def sync(manager):
    fight = manager.fight
    manager = fight.orisa_barriers
    devices = fight.orisa.superchargers.active
    live = {**manager.active, **manager.retiring, **devices}
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
                components.update(barrier_health(barrier))
                definition = SUPERCHARGER if entity in devices else DEFINITION
                viewer.seen[entity] = False
                client.queue_entities([world.EntityUpdate(
                    entity, world.OP_CREATE,
                    lambda origin, c=components, pos=barrier.position, rot=rotation, guid=definition:
                        world.create(origin, guid, c, position=pos, rotation=rot),
                )])
            script = projection(fight, entity)
            if script is not None:
                client.queue_bot_live(entity, script.latest_live)
    manager.wire_entities.intersection_update(
        set(live) | {entity for viewer in fight.match.players for entity in viewer.seen}
    )
