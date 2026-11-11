"""Static receiver for the server-side 0241 Particle Cannon beam.

The graph owns the channel and energy-scaled damage. The host supplies an
eye-origin ray and resolves the nearest enemy capsule or barrier.
"""

from ow174.game.script import expr

HERO = 0x02E0000000000068
GRAPH = 0x0241
TRACK_NODES = (81, 175)
DAMAGE_NODE = 28


def actor(fight, state):
    component = state.instance.component
    return next((player for player in fight.match.players
                 if not player.gone and player.body_script is not None
                 and player.body_script.component is component), None)


def trace(fight, player, state):
    from ow174.game.combat import EPSILON, aim, ray_capsule, ray_world

    track = state.instance.graph.node(TRACK_NODES[0])
    reach = expr.to_float(state.evaluate(track.field("m_F5732403.m_6A79BD98"), watch=False))
    if reach <= 0:
        return None
    origin = fight._eye(player)
    direction = aim(player.mover.yaw, player.mover.pitch)
    wall = ray_world(fight._collision(), origin, direction, reach)
    limit = reach if wall is None else wall
    body = None
    for target in fight._enemies(player):
        a, b = target.segment()
        distance = ray_capsule(origin, direction, a, b, target.radius)
        if distance is not None and distance < limit:
            limit, body = distance, target
    contact = fight._shield_trace(player, origin, direction, limit)
    if contact is not None and contact[0] < limit - EPSILON:
        shield = contact[1]
        if shield in fight.match.players:
            entity = shield.body
        else:
            entity = getattr(shield, "entity", None) or shield.body
        return entity, None, shield
    return (body.entity, body, None) if body is not None else None


def track_ray(fight, state):
    if (state.instance.component.strict or state.instance.graph.index != GRAPH
            or state.node.pos not in TRACK_NODES):
        return NotImplemented
    player = actor(fight, state)
    hit = None
    if player is not None and fight.match.collision_world is not None and not fight.is_dead(player):
        hit = trace(fight, player, state)
    # Both server rays feed the entity selector in 0241 node 176.
    return (expr.Entity(hit[0]) if hit is not None else None), None


def damage(fight, state, elapsed_ms):
    from ow174.game.combat import BEAM_ARMOUR_SCALE

    if (state.instance.component.strict
            or (state.instance.graph.index, state.node.pos) != (GRAPH, DAMAGE_NODE)):
        return NotImplemented
    player = actor(fight, state)
    if (not elapsed_ms or player is None or fight.match.collision_world is None
            or fight.is_dead(player) or fight.is_stunned(player)):
        return True
    hit = trace(fight, player, state)
    selected = expr.to_entity(state.evaluate(state.node.field("m_FA3ECC43"), watch=False))
    if hit is None or hit[0] != selected:
        return True
    _, target, shield = hit
    config = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(config["m_amount"], watch=False)) * elapsed_ms / 1000
    if amount <= 0:
        return True
    if shield is not None:
        fight._shield_damage(player, shield, amount)
    else:
        asset = state.evaluate(config["m_loadout"], watch=False)
        loadout = asset.guid if isinstance(asset, expr.Asset) else None
        fight._damage(player, target, amount, False, loadout, fight.tick, beam_armour_scale=BEAM_ARMOUR_SCALE)
    return True
