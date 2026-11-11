"""Static combat host for the authored Talon Trooper and Heavy melee graphs."""

from ow174.game.script import expr

TROOPER = 0x02E00000000001AC
HEAVY = 0x02E00000000001BA
SWINGS = {
    TROOPER: (0x1422, 5, 4),
    HEAVY: (0x140E, 6, 5),
}


def attacker(fight, state):
    component = state.instance.component
    return next((bot for bot in fight.match.bots
                 if bot.entity == component.entity and not bot.dead
                 and bot.spawner.get("manual_spawn") is True
                 and bot.body_script is not None and bot.body_script.component is component), None)


def _matches(world, state, index):
    swing = SWINGS.get(world.hero)
    return (world.combat is not None and swing is not None
            and (state.instance.graph.index, state.node.pos) == (swing[0], swing[index]))


def _geometry(bot, state, search):
    from ow174.game.combat import aim

    offset = state.evaluate(search.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3):
        return None
    radius = expr.to_float(state.evaluate(search.field("m_radius"), watch=False))
    forward, right = aim(bot.yaw, 0), aim(bot.yaw + 16384, 0)
    x, y, z = bot.position
    origin = (x, y + offset.y, z)
    centre = (x + forward[0] * offset.z + right[0] * offset.x, origin[1],
              z + forward[2] * offset.z + right[2] * offset.x)
    return origin, centre, radius, forward


def targets(world, state):
    if not _matches(world, state, 1):
        return NotImplemented
    from ow174.game.combat import capsule_visible, segment_distance

    fight = world.combat
    bot = attacker(fight, state)
    if bot is None or fight.is_stunned(bot):
        return ()
    geometry = _geometry(bot, state, state.node)
    if geometry is None:
        return ()
    origin, centre, radius, _ = geometry
    found = []
    for target in fight._enemies(bot):
        if target.owner not in fight.match.players:
            continue
        a, b = target.segment()
        if (segment_distance(centre, a, b) - target.radius <= radius
                and capsule_visible(fight.match.collision_world, origin, a, b, target.radius)):
            found.append(expr.Entity(target.entity))
    return tuple(found)


def modify_health(world, state):
    if not _matches(world, state, 2):
        return NotImplemented
    from ow174.game import arrow_deflection

    fight = world.combat
    bot = attacker(fight, state)
    if bot is not None and not fight.is_stunned(bot):
        config = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(config.get("m_amount"), watch=False))
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        entities = {expr.to_entity(value) for value in selected} if isinstance(selected, tuple) else set()
        search = state.instance.graph.node(SWINGS[world.hero][1])
        geometry = _geometry(bot, state, search)
        if geometry is not None and amount > 0:
            origin, centre, radius, direction = geometry
            fight.barriers.melee(bot, centre, radius, amount, origin=origin)
            fight.orisa_barriers.melee(bot, centre, radius, amount, origin=origin)
            fight.winston_barriers.melee(bot, centre, radius, amount, origin=origin)
            fight.sigma_barriers.melee(bot, centre, radius, amount, origin=origin)
            protected = fight.zarya_barriers.melee(bot, centre, radius, amount, bot.entity, origin=origin)
            for target in fight._enemies(bot):
                if (target.entity in entities and target.entity not in protected
                        and arrow_deflection.frontal(target, direction) is None):
                    fight._damage(bot, target, amount, False, None, fight.tick)
    state.request_finish()
    return True
