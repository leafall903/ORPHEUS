"""Sigma 1D02 projectile absorption with a reconstructed host clock and pose."""

import math

from ow174.game.script import expr

HERO = 0x02E000000000023B
GRAPH = 0x1D02


def window(instance, at_tick):
    state, chase = instance.states.get(1), instance.states.get(20)
    if (state is None or not state.active or state.node.field('m_8B251D53') != 1
            or chase is None or not chase.active or chase.node.pos != 45):
        return None
    if chase.remaining is not None:
        remaining = max(0, chase.remaining) / 1000
    else:
        destination = expr.to_float(chase.evaluate(chase.node.field('m_destination'), watch=False))
        rate = expr.to_float(chase.evaluate(chase.node.field('m_9B0385B4'), watch=False))
        remaining = abs(chase.cur - destination) / rate if rate > 0 else math.inf
    now = at_tick * instance.component.frame_ms / 1000
    if (now + (instance.component.frame_ms - 1) / 1000 < state.start / 1000
            or now >= chase.last / 1000 + remaining):
        return None
    return state


def absorbed(state, hit, source):
    """Feed the actual projectile's damage into the authored accumulator."""
    if hit is None or hit.amount <= 0:
        return
    instance = state.instance
    instance.write(expr.lvalue(state.node.field("m_F6CDC81C")), expr.f32(hit.amount))
    instance.write(expr.lvalue(state.node.field("m_0A2CABA3")), expr.Entity(source))
    instance.follow(state.node, "m_160B5DBE", state)
    instance.component.run_now()

SHIELD_KEY = ("sigma-grasp", 0x5847)


def grant_shields(fight, instance, node, resource):
    """Reconstruct the self-targeted 1D03 request on the shared temporary pool."""
    if (instance.graph.index != GRAPH or node.pos != 34
            or resource.guid != 0x0580000000001D03):
        return False
    entity = instance.component.entity
    if instance.evaluate(node.field("m_targets")) != expr.Entity(entity):
        return False
    target = next((target for target in fight._targets() if target.entity == entity), None)
    if target is None or fight.vitals_of(target).dead:
        return True
    guard = instance.states.get(1)
    if guard is None or guard.counter == 0:
        return True
    activation = (instance.id, guard.counter)
    existing = fight.grasp_shields.get(entity)
    if existing is not None and existing[0] == activation:
        return True
    params = {expr.guid_of(row.get("m_B5051BCE")): expr.to_float(instance.evaluate(row.get("m_value")))
              for row in node.field("m_params") or ()}
    amount = params.get(0x0A4000000000002A, 0)
    limit = params.get(0x0A400000000003D1, 0)
    delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
    rate = expr.to_float(instance.evaluate(instance.graph.node(70).field("m_modifyHealth.m_amount")))
    if not all(math.isfinite(value) and value >= 0 for value in (amount, limit, delay, rate)):
        return True
    vitals = fight.vitals_of(target)
    pool = vitals.temporary_shields.get(SHIELD_KEY)
    capacity = min(limit, amount + (pool.current if pool is not None else 0))
    if amount > 0 and capacity > 0:
        vitals.grant_temporary_shields(SHIELD_KEY, capacity)
        now = instance.component.now / 1000
        fight.grasp_shields[entity] = (activation, now + delay, now, rate)
        fight._sync_body_health(target.owner, vitals)
    return True


def decay_shields(fight, now):
    """Apply authored rate on world time without emitting damage or healing."""
    if not fight.grasp_shields:
        return
    targets = {target.entity: target for target in fight._targets()}
    for entity, (activation, starts, previous, rate) in list(fight.grasp_shields.items()):
        target = targets.get(entity)
        if target is None or fight.vitals_of(target).dead:
            del fight.grasp_shields[entity]
            continue
        vitals = fight.vitals_of(target)
        elapsed = max(0, now - max(starts, previous))
        if elapsed and vitals.drain_temporary_shields(SHIELD_KEY, elapsed * rate):
            fight._sync_body_health(target.owner, vitals)
        fight.grasp_shields[entity] = (activation, starts, max(previous, now), rate)
