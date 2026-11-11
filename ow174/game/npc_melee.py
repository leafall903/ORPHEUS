"""World hooks for the two authored Talon melee graphs."""

from ow174.game import shot_guards
from ow174.game.script import expr

# Hero -> graph, target search node, damage node, ability node.
SEARCH, DAMAGE, ABILITY = 1, 2, 3
SWINGS = {
    0x02E00000000001AC: (0x1422, 5, 4, 8),
    0x02E00000000001BA: (0x140E, 6, 5, 9),
}


def graph_entry(world, state, column):
    definition = SWINGS.get(world.hero)
    return definition is not None and (state.instance.graph.index, state.node.pos) == (
        definition[0], definition[column],
    )


def attacker(fight, state):
    component = state.instance.component
    return next((bot for bot in fight.match.bots if bot.entity == component.entity and not bot.dead
                 and bot.body_script is not None and bot.body_script.component is component), None)


def targets(world, state):
    if not graph_entry(world, state, SEARCH) or world.combat is None:
        return NotImplemented
    from ow174.game.combat import aim, capsule_visible, segment_distance

    fight = world.combat
    bot = attacker(fight, state)
    if bot is None or fight.is_stunned(bot):
        return ()
    offset = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    forward, right = aim(bot.yaw, 0), aim(bot.yaw + 16384, 0)
    x, y, z = bot.position
    origin = (x, y + offset.y, z)
    centre = (x + forward[0] * offset.z + right[0] * offset.x, origin[1],
              z + forward[2] * offset.z + right[2] * offset.x)
    found = []
    for target in fight._enemies(bot):
        if target.owner not in fight.match.players:
            continue
        a, b = target.segment()
        if (segment_distance(centre, a, b) - target.radius <= radius
                and capsule_visible(fight._line_collision(), origin, a, b, target.radius)):
            found.append(target.entity)
    return tuple(found)


def modify_health(world, state):
    if not graph_entry(world, state, DAMAGE) or world.combat is None:
        return
    from ow174.game.combat import damage_tags

    fight = world.combat
    bot = attacker(fight, state)
    if bot is None or fight.is_stunned(bot):
        return
    config = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(config.get("m_amount"), watch=False))
    selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    entities = {expr.to_entity(value) for value in selected} if isinstance(selected, tuple) else set()
    tags = damage_tags(state, config)
    for target in fight._enemies(bot):
        if target.entity in entities and not shot_guards.parries_melee(fight, target, bot.position, tags):
            fight._damage(bot, target, amount, False, None, fight.tick, tags=tags)
    state.request_finish()
