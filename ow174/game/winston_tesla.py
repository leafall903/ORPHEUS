"""Resolve the 104319 Tesla Cannon body channel from its active graph states."""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000009
GRAPH = 0x00FE
LOADOUT = 0x0B9000000000002F


def attack(fight, player, command, script):
    if script.hero != HERO or script.component.strict:
        return
    if command.frame <= fight.tesla_frames.get(player.body, -1):
        return
    fight.tesla_frames[player.body] = command.frame
    weapon = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    if weapon is None:
        return
    search, damage = weapon.states.get(10), weapon.states.get(13)
    if search is None or damage is None or not (search.active and damage.active):
        return

    from ow174.game.combat import _dot, _sub, aim, capsule_visible, hit_def

    reach = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
    angle = expr.to_float(search.evaluate(search.node.field("m_A30B383E"), watch=False))
    definition = hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
    if reach <= 0 or not 0 < angle < 90 or definition is None or definition.amount <= 0:
        return
    amount = definition.amount * player.quanta / 1e6
    width = math.tan(math.radians(angle))
    origin, direction = fight._eye(player), aim(*command.aim())
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    collision = fight._collision()
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None:
            continue
        a, b = shown.segment()
        point = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        offset = _sub(point, origin)
        distance_sq = _dot(offset, offset)
        forward = _dot(offset, direction)
        lateral = math.sqrt(max(0.0, distance_sq - forward * forward))
        if (distance_sq > (reach + shown.radius) ** 2 or forward < 0
                or lateral > forward * width + shown.radius):
            continue
        # The body channel uses static LOS. Grasp admits neither this tracking
        # state nor its ModifyHealth delivery, and ordinary barriers do not occlude it.
        if capsule_visible(collision, origin, a, b, shown.radius):
            fight._damage(player, target, amount, False, definition.loadout, command.frame, beam=True)
