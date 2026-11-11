"""Server contacts for the outgoing phase of Brigitte's authored Whip Shot."""

import math

from ow174.game.script import expr, nodes

GRAPH = 0x1259
LOADOUT = 0x0B900000000001A3


def launch(combat, player, command, script):
    from ow174.game.combat import Rocket, aim, hit_def

    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    if instance is None or instance.component.strict:
        return
    phase = instance.states.get(6)
    ability = instance.states.get(0)
    if phase is None or not phase.active or ability is None or not ability.flags & nodes.Ability.RUNNING:
        return
    key = instance.id, phase.index
    seen = combat.melee_seen.setdefault(player.body, {})
    if seen.get(key) == phase.counter:
        return
    seen[key] = phase.counter
    speed = expr.to_float(instance.vars[681].value())
    reach = expr.to_float(instance.vars[1648].value())
    radius = expr.to_float(instance.evaluate(instance.graph.node(11).field("m_radius")))
    direct = hit_def(phase, instance.graph.node(32).fields)
    if (direct is None or direct.loadout != LOADOUT or direct.amount <= 0
            or not all(math.isfinite(x) and x > 0 for x in (speed, reach, radius))):
        return
    combat.rockets.append(Rocket(
        player, player.team, combat._eye(player), aim(*command.aim()),
        speed, reach / speed, direct, None, last_tick=combat.tick,
        contact_extent=radius, source_body=player.body, whip_source=phase,
    ))


def active(combat, rocket):
    player = rocket.shooter
    phase = rocket.whip_source
    instance = phase.instance
    script = player.body_script
    if (rocket.source_body != player.body or script is None or combat.is_dead(player)
            or script.component is not instance.component
            or script.component.instances.get(instance.id) is not instance
            or not phase.active or phase.ending):
        return False
    ability = instance.states.get(0)
    return ability is not None and bool(ability.flags & nodes.Ability.RUNNING)


def impulse(combat, rocket, target):
    instance = rocket.whip_source.instance
    modify = instance.graph.node(32).field("m_modifyHealth")
    horizontal = expr.to_float(instance.evaluate(modify["m_E33EFEE1"]))
    vertical = expr.to_float(instance.evaluate(modify["m_583C208B"]))
    combat._blast_impulse(target, rocket.position, horizontal, vertical, direction=rocket.direction, carry=True)
