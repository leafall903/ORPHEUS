"""Static player Tesla host for actual00FE."""

import math

from ow174.game.script import expr

WINSTON = 0x02E0000000000009
GRAPH = 0xFE
HALF_ANGLE_COS = math.cos(math.radians(45))  # static approximation of the native arc


def targets(fight, state):
    from ow174.game.combat import aim, capsule_visible

    component = state.instance.component
    if component.strict or (state.instance.graph.index, state.node.pos) != (GRAPH, 25):
        return NotImplemented
    player = next((p for p in fight.match.players if not p.gone and p.body == component.entity
                   and p.hero.guid == WINSTON), None)
    if player is None or fight.match.collision_world is None or fight.is_dead(player):
        return ()
    origin = fight._eye(player)
    direction = aim(player.mover.yaw, player.mover.pitch)
    # The native radius adjustment in n25 is unresolved. Use the authored base radius.
    reach = expr.to_float(state.instance.vars[1648].value())
    found = []
    for target in fight._enemies(player):
        a, b = target.segment()
        centre = (a[0], min(max(origin[1], a[1]), b[1]), a[2])
        offset = tuple(p - q for p, q in zip(centre, origin, strict=True))
        distance = math.hypot(*offset)
        if distance - target.radius > reach:
            continue
        projection = sum(x * y for x, y in zip(offset, direction, strict=True))
        if projection + target.radius < distance * HALF_ANGLE_COS:
            continue
        if capsule_visible(fight._collision(), origin, a, b, target.radius):
            found.append(expr.Entity(target.entity))
    return tuple(found)


def command(fight, player, script, frame):
    from ow174.game.combat import BEAM_ARMOUR_SCALE, TICK

    if player.hero.guid != WINSTON or script.component.strict:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    track = instance.states.get(10) if instance else None
    damage = instance.states.get(13) if instance else None
    if track is None or not track.active or damage is None or not damage.active:
        return
    config = damage.node.field("m_modifyHealth")
    amount = TICK * expr.to_float(damage.evaluate(config["m_amount"], watch=False))
    asset = damage.evaluate(config["m_loadout"], watch=False)
    loadout = asset.guid if isinstance(asset, expr.Asset) else None
    selected = {entity.id for entity in track.targets}
    for target in fight._enemies(player):
        if target.entity not in selected:
            continue
        bubble = fight.bubbles.for_target(target.owner)
        if bubble is not None:
            fight.bubbles.damage(bubble, amount, player)
        else:
            fight._damage(player, target, amount, False, loadout, frame, beam_armour_scale=BEAM_ARMOUR_SCALE)
