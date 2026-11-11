"""Selected 1566 Adaptive Shield pool and match-clock expiry."""

import math
from dataclasses import dataclass

from ow174.game.piledriver import HERO
from ow174.game.script import expr

GRAPH = 0x1566
POOL = 0x0D80000000003EBB


@dataclass
class Lease:
    owner: object
    state: object
    activation: int
    expires: int


def drop(fight, body):
    lease = fight.adaptive_shields.pop(body, None)
    if lease is not None:
        empty_pool(fight, body, lease)


def empty_pool(fight, body, lease):
    vitals = fight.vitals.get(body)
    if vitals is None:
        return
    pool = vitals.temporary_shields.get(POOL)
    if (pool is not None and vitals.decay_temporary_shields(POOL, pool.now) > 0
            and lease.owner.body == body):
        fight._sync_body_health(lease.owner, vitals)


def command(fight, player):
    from ow174.game.combat import TICK, segment_distance

    if player.hero.guid != HERO:
        return
    component = player.body_script.component
    if component.strict or component.owner_dead:
        return
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    state = instance.states.get(6) if instance is not None else None
    old = fight.adaptive_shields.get(player.body)
    if state is None or not state.active:
        drop(fight, player.body)
        return
    if old is not None and old.state is state and old.activation == state.counter:
        return
    drop(fight, player.body)
    search = instance.state(9)
    radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    if not math.isfinite(radius) or radius <= 0:
        return
    clock = instance.state(11)
    initial = expr.to_float(clock.evaluate(clock.node.field("m_EB0FC261"), watch=False))
    destination = expr.to_float(clock.evaluate(clock.node.field("m_destination"), watch=False))
    rate_config = clock.node.field("m_9B0385B4")
    rate = expr.to_float(clock.evaluate(rate_config, watch=False)) if rate_config is not None else 1.0
    if not all(map(math.isfinite, (initial, destination, rate))) or rate <= 0:
        return
    remaining = abs(initial - destination) / rate - max(0, component.now - state.start) / 1000
    if remaining <= 0:
        return
    origin = fight._eye(player)
    count = 0
    own = None
    for target in fight._targets():
        if target.owner is player:
            own = target
        elif ((fight.match.game_map.free_for_all or target.team != player.team)
              and not fight.vitals_of(target).dead
              and segment_distance(origin, *target.segment()) - target.radius <= radius):
            count += 1
    if own is None:
        return
    instance.write((expr.INSTANCE, 11398), count)
    amount = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
    if not math.isfinite(amount) or amount <= 0:
        return
    vitals = fight.vitals_of(own)
    if vitals.grant_temporary_shields(POOL, amount, amount) > 0:
        fight.adaptive_shields[player.body] = Lease(
            player, state, state.counter, fight.tick + math.ceil(remaining / TICK),
        )
        fight._sync_body_health(player, vitals)


def update(fight, tick):
    for body, lease in tuple(fight.adaptive_shields.items()):
        if lease.owner.body != body or not lease.state.active or lease.state.instance.stopped:
            drop(fight, body)
        elif tick >= lease.expires:
            # Retain activation identity until the graph ends. A body with
            # delayed commands must not grant the same expired pool again.
            empty_pool(fight, body, lease)
