"""Combat host for Wrecking Ball's authored 1566 temporary shield."""

import math
from dataclasses import dataclass

from ow174.game.script import expr
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E00000000001CA
GRAPH = 0x1566
POOL_STATE = 6  # Node12.


@dataclass
class Pool:
    activation: tuple[int, int, int]
    capacity: float
    amount: float
    until: float


def prepare_count(instance):
    """Supply node20's count immediately before node14 snapshots it."""
    from ow174.game import ball

    component = instance.component
    world = component.world
    match = getattr(world, "match", None)
    if component.strict or world.hero != HERO or match is None:
        return
    fight = match.combat
    player = next((p for p in match.players if p.has_body and not p.gone
                   and p.body == component.entity), None)
    if player is None:
        return
    tracker = instance.graph.node(20)
    radius = expr.to_float(instance.evaluate(tracker.field("m_radius")))
    count = 0
    if math.isfinite(radius) and radius > 0:
        count = ball.shield_enemy_count(fight, player, radius)
    instance.write(expr.lvalue(tracker.field("m_1B89EA79")), count)


def observe(fight, player):
    if player.hero.guid != HERO:
        return
    component = player.body_script.component
    if component.strict or component.owner_dead or component.fault is not None:
        return
    for instance in component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        state = instance.states.get(POOL_STATE)
        if state is None or not state.active or state.ending:
            continue
        vitals = fight.vitals.get(player.body)
        if vitals is None:
            target = next((target for target in fight._targets() if target.owner is player), None)
            if target is None:
                return
            vitals = fight.vitals_of(target)
        activation = (instance.id, state.counter, state.start)
        if vitals.adaptive_shield is not None and vitals.adaptive_shield.activation == activation:
            return
        amount = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
        timer = instance.graph.node(26)
        duration = expr.to_float(instance.evaluate(timer.field("m_EB0FC261")))
        if not all(math.isfinite(value) and value > 0 for value in (amount, duration)):
            return
        offset = state.start % component.frame_ms / 1000.0
        vitals.adaptive_shield = Pool(activation, amount, amount, fight.now + offset + duration)
        vitals.changed.add(SHIELDS_POOL)
        fight._sync_body_health(player, vitals)
        return


def expire(fight, targets):
    for target in targets:
        vitals = fight.vitals[target.entity]
        pool = vitals.adaptive_shield
        if pool.amount > 0 and fight.now + 1e-9 >= pool.until:
            pool.amount = 0.0
            vitals.changed.add(SHIELDS_POOL)
            fight._sync_body_health(target.owner, vitals)
