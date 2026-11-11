"""Static host for Ana's authored 0B9E cast and 0269.053 target effect."""

import math

from ow174.game import zenyatta_discord
from ow174.game.script import expr, graph

CAST = 0x0B9E
STATUS = 0x0B9F
CONE = math.cos(math.radians(10))


def prepare(instance, node, caller):
    """Supply the allied target array before the authored cast continuation."""
    if node.pos != 53 or graph.graph(STATUS) is None:
        return False
    match = getattr(instance.component.world, "match", None)
    if match is None:
        return False
    player = next((p for p in match.players if p.has_body and p.body == instance.component.entity), None)
    if player is None:
        return False
    from ow174.game.combat import aim

    fight = match.combat
    origin = fight._eye(player)
    direction = aim(player.mover.yaw, player.mover.pitch)
    reach = expr.to_float(instance.find_var(0, 1648).value())
    candidates = []
    if not match.game_map.free_for_all:
        for target in fight._targets():
            owner = target.owner
            if (owner is player or owner not in match.players or owner.gone
                    or target.team != player.team or fight.vitals_of(target).dead):
                continue
            delta = tuple(b - a for a, b in zip(origin, fight._eye(owner), strict=True))
            distance = math.hypot(*delta)
            if not 0 < distance <= reach:
                continue
            alignment = sum(a * b for a, b in zip(direction, delta, strict=True)) / distance
            if alignment >= CONE and zenyatta_discord.visible(fight, player, target, acquisition=True):
                candidates.append((-alignment, distance, target.entity))
    targets = (expr.Entity(min(candidates)[2]),) if candidates else ()
    # Use the authored target check, cost, windup and status scope.
    instance.write((0, 15068), False)
    instance.write((0, 375), targets)
    instance.follow(instance.graph.node(50), "m_outPlug", caller)
    return True


def apply(fight, player):
    script = player.body_script
    if script is None or script.hero != 0x02E000000000013B:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == CAST), None)
    if instance is None:
        return
    state = instance.states.get(instance.graph.node(33).state)
    if state is None or not state.active or fight.nano_casts.get(player.body) == state.counter:
        return
    fight.nano_casts[player.body] = state.counter
    body = expr.to_entity(instance.find_var(0, 8070).value())
    target = next((t for t in fight._targets() if t.entity == body and t.team == player.team), None)
    status = graph.graph(STATUS)
    if target is None or fight.vitals_of(target).dead or status is None:
        return
    from ow174.game.combat import TICK

    duration = expr.to_float(instance.find_var(0, 215).value())
    heal = status.state(20).field("m_modifyHealth")["m_amount"]
    incoming = status.state(18).field("m_50530EAB")
    outgoing = status.state(19).field("m_50530EAB")
    fight.nano_buffs[body] = (
        fight.tick + math.ceil(duration / TICK),
        1 + expr.to_float(instance.evaluate(incoming)),
        1 + expr.to_float(instance.evaluate(outgoing)),
    )
    fight._heal_target(player, target, expr.to_float(instance.evaluate(heal)), fight.tick, fight.tick)


def outgoing(fight, source, originating_body, *, tags=()):
    dealt = fight.nano_buffs.get(source) if originating_body in (None, source) else None
    if dealt is None or fight.tick >= dealt[0]:
        return 1.0
    if tags:
        status = graph.graph(STATUS)
        modifier = status.state(19) if status is not None else None
        # D275A0 rejects outgoing events carrying a tag in this authored list.
        denied = modifier.field("m_5CFFCBAE") if modifier is not None else None
        if any(graph.guid(tag) in tags for tag in denied or ()):
            return 1.0
    return dealt[2]


def multiplier(fight, source, target, originating_body, *, tags=()):
    if source == target:
        return 1.0
    received = fight.nano_buffs.get(target)
    incoming = received[1] if received is not None and fight.tick < received[0] else 1.0
    return incoming * outgoing(fight, source, originating_body, tags=tags)


def retire(fight, body):
    fight.nano_casts.pop(body, None)
    fight.nano_buffs.pop(body, None)


def expire(fight):
    for body, (deadline, _, _) in tuple(fight.nano_buffs.items()):
        if fight.tick >= deadline:
            del fight.nano_buffs[body]
