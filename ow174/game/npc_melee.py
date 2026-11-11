"""Manual Trooper and Heavy Assault melee with reconstructed contact routing."""

import math

from ow174.game import heavy_charge
from ow174.game.script import expr, nodes

BUTTON = 31
TROOPER = 0x0400000000001D40
TROOPER_GRAPH = 0x1422
TROOPER_GATE = 38
SPECS = {
    TROOPER: (TROOPER_GRAPH, 8, 5, 4),
    heavy_charge.BODY: (0x140E, 9, 6, 5),
}


def instance_of(bot):
    spec = SPECS.get(bot.body)
    if spec is None or bot.dead or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values() if i.graph.index == spec[0]), None)


def clocked(bot):
    instance = instance_of(bot)
    if instance is None:
        return False
    state = instance.states.get(instance.graph.node(SPECS[bot.body][1]).state)
    return (bot.pending_ability == BUTTON or BUTTON in instance.component.held
            or (isinstance(state, nodes.Ability) and bool(state.flags & (state.RUNNING | state.PUSHED))))


def ready(bot):
    instance = instance_of(bot)
    if instance is None:
        return False
    state = instance.states.get(instance.graph.node(SPECS[bot.body][1]).state)
    return state is not None and state.active


def stop(bot):
    instance = instance_of(bot)
    if instance is None:
        return
    if bot.pending_ability == BUTTON:
        bot.pending_ability = None
    state = instance.states.get(instance.graph.node(SPECS[bot.body][1]).state)
    if state is not None:
        state.presses.clear()
        state.stop()


def contacts(bot, fight, tracker):
    from ow174.game import combat, genji_deflect, mei_wall

    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    forward = combat.aim(round(bot.yaw), 0)
    x, y, z = bot.position
    origin = (x, y + combat.EYE, z)
    centre = combat._along((x, y + offset.y, z), forward, offset.z)
    reach = None
    if bot.body == TROOPER:
        gate = tracker.instance.graph.node(TROOPER_GATE)
        reach = gate.field("m_condition.m_expression.m_D99EF254")[1]
    selected = []
    for target in fight._enemies(bot):
        a, b = target.segment()
        if ((reach is None or math.dist(target.feet, bot.position) < reach)
                and combat.segment_distance(centre, a, b) <= radius + target.radius
                and combat.capsule_visible(fight._collision(), origin, a, b, target.radius)
                and not fight.walls.blocked(origin, (a[0], a[1], a[2]))
                and not genji_deflect.blocks_melee(fight, target.owner, forward)):
            selected.append(target)
    pieces = []
    for piece in fight.walls.pieces.values():
        surface = mei_wall.closest(piece, origin)
        if ((reach is None or mei_wall.distance(piece, bot.position) < reach)
                and mei_wall.distance(piece, centre) <= radius
                and combat.capsule_visible(fight._collision(), origin, surface, surface, 0.)
                and not fight.walls.blocked(origin, surface)):
            pieces.append(piece)
    return selected, pieces


def sample_trooper(bot, fight):
    """Bridge tracker 5 and branch 38 after the authored windup."""
    if bot.body != TROOPER or fight.frozen(bot) or fight.is_stunned(bot):
        return
    instance = instance_of(bot)
    tracker = instance.states.get(instance.graph.node(SPECS[TROOPER][2]).state) if instance else None
    if tracker is None or not tracker.active:
        return
    counters = fight.melee_seen.setdefault(bot.entity, {})
    key = (instance.id, tracker.index)
    if counters.get(key) == tracker.counter:
        return
    targets, pieces = contacts(bot, fight, tracker)
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    instance.write(slot, tuple(expr.Entity(target.entity) for target in targets))
    if targets or pieces:
        counters[key] = tracker.counter
        instance.follow(instance.graph.node(TROOPER_GATE), "m_truePlug", None)
        instance.component.run_now()


def modify_health(world, state, elapsed):
    if world.match is None or not any(
        state.instance.graph.index == spec[0] and state.node.pos == spec[3] for spec in SPECS.values()
    ):
        return NotImplemented
    fight = world.match.combat
    bot = next((b for b in fight.match.bots if b.entity == state.instance.component.entity), None)
    if (bot is None or instance_of(bot) is not state.instance or elapsed
            or fight.frozen(bot) or fight.is_stunned(bot)):
        return True
    tracker = state.instance.node_state(state.instance.graph.node(SPECS[bot.body][2]))
    targets, pieces = contacts(bot, fight, tracker)
    slot = expr.lvalue(tracker.node.field("m_8A2905A0"))
    state.instance.write(slot, tuple(expr.Entity(target.entity) for target in targets))
    amount = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    for target in targets:
        fight._damage(bot, target, amount, False, None, fight.tick, world_tick=fight.tick)
    for piece in pieces:
        fight.walls.damage(piece, bot, amount, fight.tick)
    return True
