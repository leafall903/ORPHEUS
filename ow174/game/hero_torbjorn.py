"""104319 Torbjorn bridges for reconstructed authoritative combat.

Hammer timing uses the authored swing duration and its0.42 normalized threshold.
This is an explicit substitute for the unimplemented native54FEB21C consumer;
it does not claim a complete native animation-event or TrackTargets backend.
"""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000006


def hammer(fight, player, command, script):
    from ow174.game import combat

    if command is None or player.hero.guid != HERO:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x168C), None)
    if instance is None:
        return
    swing = instance.states.get(13)
    if swing is None or not swing.active:
        return
    duration = combat._number(swing.evaluate(swing.node.field("m_86701C3E"), watch=False), 0.0)
    trigger = instance.graph.node(43)
    fraction = combat._number(swing.evaluate(trigger.field("m_ED97A97C"), watch=False), 0.0)
    if duration <= 0.0 or not 0.0 <= fraction <= 1.0:
        return
    if script.component.now < swing.start + duration * fraction * 1000:
        return
    counters = fight.melee_seen.setdefault(script.entity, {})
    key = (instance.id, 6)
    if counters.get(key, 0) == swing.counter:
        return
    counters[key] = swing.counter
    state = instance.state(6)
    modify = state.node.field("m_modifyHealth") or {}
    amount = combat._number(state.evaluate(modify.get("m_amount"), watch=False), 0.0)
    loadout = state.evaluate(modify.get("m_loadout"), watch=False)
    loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
    # Authored local centre(0,1.2,1.5), radius1. Geometry follows command aim;
    # animated pose/hitboxes are not reconstructed by the capsule solver.
    x, y, z = player.mover.position
    origin = (x, y + 1.2, z)
    centre = combat._along(origin, combat.aim(*command.aim()), 1.5)
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None:
            continue
        a, b = shown.segment()
        if combat.segment_distance(centre, a, b) - shown.radius > 1.0:
            continue
        middle = (shown.feet[0], shown.feet[1] + shown.height * 0.5, shown.feet[2])
        delta = combat._sub(middle, origin)
        distance = math.sqrt(combat._dot(delta, delta))
        if distance > combat.EPSILON:
            direction = tuple(axis / distance for axis in delta)
            wall = combat.ray_world(fight._collision(), origin, direction, distance)
            if wall is not None and wall < distance - shown.radius:
                continue
        fight._damage(player, target, amount, False, loadout, command.frame)
