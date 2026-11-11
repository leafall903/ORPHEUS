"""Match services for the authored Doomfist Uppercut and shield chain."""

import math

from ow174.game import accretion
from ow174.game.script import expr, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E000000000012F
UPPERCUT, PASSIVE = 0x0DD8, 0x0DDD
POOL_TAG = 0x0D8000000000249D


def player(world):
    return next((p for p in world.match.players if p.body == world.body
                 and p.has_body and p.spawned and not p.gone), None)


def targets(world, state):
    from ow174.game.combat import aim, capsule_visible, segment_distance

    fight = world.match.combat
    source = player(world)
    if source is None or fight.is_dead(source):
        return ()
    node = state.node
    radius = expr.to_float(state.evaluate(node.field("m_radius"), watch=False))
    reach, _, lift = node.field("m_05BE62DD.m_expression.m_D99EF254")
    direction = aim(source.mover.yaw, 0)
    x, y, z = source.mover.position
    origin = x, y + lift, z
    centre = x + reach * direction[0], y + lift, z + reach * direction[2]
    collision = fight._collision()
    result = []
    for target in fight._enemies(source):
        a, b = target.segment()
        if (segment_distance(centre, a, b) <= radius + target.radius
                and capsule_visible(collision, origin, a, b, target.radius)):
            result.append(expr.Entity(target.entity))
    return tuple(result)


def hit(world, state):
    from ow174.game.combat import aim

    fight = world.match.combat
    source = player(world)
    if source is None or fight.is_dead(source):
        return
    settings = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    if not math.isfinite(amount) or amount <= 0:
        return
    loadout = expr.guid_of(state.evaluate(settings["m_loadout"], watch=False))
    tags = tuple(state.evaluate(row["m_EE3055BD"], watch=False) for row in settings["m_148F3152"])
    entities = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    selected = {expr.to_entity(value) for value in entities or ()}
    component = state.instance.component
    passive = next((i for i in component.instances.values() if i.graph.index == PASSIVE), None)
    knockback = accretion.knockback(state, settings)
    direction = aim(source.mover.yaw, 0)
    for target in fight._enemies(source):
        if target.entity not in selected:
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total
        fight._damage(source, target, amount, False, loadout, component.frame)
        if vitals.total >= before:
            continue
        distance = math.dist(source.mover.position, target.feet)
        accretion.impulse(fight, target, knockback, direction, distance)
        if passive is not None:
            params = {0x3D: tags, 0x13: expr.Entity(source.body), 0x15: expr.Entity(target.entity)}
            runtime.send_instance_message(passive, 0x0240000000000012, params, source.body)


def qualified(world, instance, cfg):
    source = player(world)
    if source is None:
        return False
    victim = expr.to_entity(instance.evaluate(cfg["m_entity"]))
    return any(p.body == victim and p is not source and p.has_body and p.spawned and not p.gone
               and (p.team != source.team or world.match.game_map.free_for_all)
               for p in world.match.players)


def grant(world, instance, node):
    fight = world.match.combat
    source = player(world)
    target = next((target for target in fight._targets() if target.owner is source), None)
    if target is None:
        return False
    vitals = fight.vitals_of(target)
    if vitals.dead:
        return False
    params = {expr.guid_of(row["m_B5051BCE"]) & 0xFFFF:
              expr.to_float(instance.evaluate(row["m_value"])) for row in node.field("m_params")}
    amount, cap = params[0x2A], params[0x3D1]
    if not (math.isfinite(amount) and math.isfinite(cap) and amount > 0 and cap > 0):
        return False
    delay = expr.to_float(instance.evaluate(instance.graph.node(2).field("m_timeout")))
    rate = expr.to_float(instance.evaluate(instance.graph.node(3).field("m_modifyHealth.m_amount")))
    if not (math.isfinite(delay) and math.isfinite(rate) and delay >= 0 and rate > 0):
        return False
    through, _ = getattr(vitals, "doomfist_decay", (fight.now, 0.0))
    # Wait2 is not rearmed by another gain while its existing wait is active.
    if vitals.bonus_shields <= 0 or through <= fight.now:
        through = fight.now + delay
    vitals.doomfist_decay = through, rate
    vitals.bonus_shields = min(cap, vitals.bonus_shields + amount)
    vitals.bonus_shields_max = max(vitals.bonus_shields_max, vitals.bonus_shields)
    vitals.changed.add(SHIELDS_POOL)
    fight._sync_body_health(source, vitals)
    return True


def advance(fight, targets):
    """Integrate the passive pool once on the authoritative combat clock."""
    for target in targets:
        vitals = fight.vitals.get(target.entity)
        decay = getattr(vitals, "doomfist_decay", None)
        if decay is None:
            continue
        if vitals.dead or vitals.bonus_shields <= 0:
            del vitals.doomfist_decay
            continue
        through, rate = decay
        if fight.now <= through:
            continue
        vitals.bonus_shields = max(0.0, vitals.bonus_shields - rate * (fight.now - through))
        if vitals.bonus_shields > 0:
            vitals.doomfist_decay = fight.now, rate
        else:
            del vitals.doomfist_decay
        vitals.changed.add(SHIELDS_POOL)
        fight._sync_body_health(target.owner, vitals)
