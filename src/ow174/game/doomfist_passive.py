"""Match services for the authored Doomfist Uppercut and shield chain."""

import math

from ow174.game import defenses
from ow174.game.script import expr, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E000000000012F
UPPERCUT, PASSIVE = 0x0DD8, 0x0DDD
POOL_TAG = 0x0D8000000000249D


def player(world):
    return next((p for p in world.match.players if p.body == world.body
                 and p.has_body and p.spawned and not p.gone), None)


def targets(world, state):
    from ow174.game.combat import aim, segment_distance, ray_cover

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
    blockers = defenses.cryo_blockers(fight.match.players, exclude=source)
    result = []
    for target in fight._enemies(source):
        a, b = target.segment()
        if segment_distance(centre, a, b) > radius + target.radius:
            continue
        x, y, z = target.feet
        for fraction in (.3, .6):
            delta = (x - origin[0], y + fraction * target.height - origin[1], z - origin[2])
            length = math.hypot(*delta)
            direction = tuple(v / length for v in delta) if length > 1e-6 else (0., 0., 1.)
            hit = ray_cover(collision, origin, direction, length, blockers)
            if hit is None or hit >= length:
                result.append(target.entity)
                break
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
    loadout = state.evaluate(settings["m_loadout"], watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else expr.guid_of(loadout)
    entities = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    selected = tuple(dict.fromkeys(expr.to_entity(value) for value in entities or ()))
    component = state.instance.component
    horizontal = expr.to_float(state.evaluate(settings.get("m_E33EFEE1"), watch=False))
    vertical = expr.to_float(state.evaluate(settings.get("m_583C208B"), watch=False))
    direction = aim(source.mover.yaw, 0)
    velocity = None
    if all(math.isfinite(v) and v >= 0 for v in (horizontal, vertical)):
        dx, _, dz = direction
        velocity = (dx * horizontal, vertical, dz * horizontal)
    if selected:
        component.health_reports.append((selected, amount, loadout, component.now, velocity))


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
