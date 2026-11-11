"""Connect the authored Trooper and Heavy Assault close attacks to combat."""

import math

from ow174.game import barriers, combat
from ow174.game.mover import FELL, IN_AIR
from ow174.game.script import expr, nodes

BUTTON = 31
HEAVY = 0x02E00000000001BA
CHARGE_GRAPH = 0x1408
CHARGE_QUERY = 100
CHARGE_IMPACT = 102

MELEE = {
    0x02E00000000001AC: (0x1422, 5, 4, 8),  # Trooper
    HEAVY: (0x140E, 6, 5, 9),
}


def ability_at(component, graph, position):
    for instance in component.instances.values():
        if instance.graph.index == graph:
            node = instance.graph.node(position)
            return instance.states.get(node.state) if node is not None else None
    return None


def ability(component):
    melee = MELEE.get(component.world.hero)
    return ability_at(component, melee[0], melee[3]) if melee is not None else None


def update(bot, component, enabled):
    """Prefer the authored melee while in reach and suspend the primary during the swing."""
    state = ability(component)
    if state is None or component.world.match is None:
        return False
    if not enabled or bot.airborne:
        stop(component)
        return False
    if BUTTON in component.held:
        component.edges([(BUTTON, False)])
    if state.flags & nodes.Ability.RUNNING:
        return True
    query = MELEE[component.world.hero][1]
    if state.active and state.remaining() <= 0 and _target(component.world, state, bot, query) is not None:
        component.edges([(BUTTON, True)])
        return True
    return False


def stop(component):
    state = ability(component)
    if state is not None:
        state.presses.clear()
        state.stop()
        if BUTTON in component.held:
            component.edges([(BUTTON, False)])


def _source(world, state):
    component = state.instance.component
    if world.match is None or world.match.ended:
        return None
    return next((bot for bot in world.match.bots
                 if bot.entity == component.entity and bot.spawner.get('manual_spawn') is True
                 and bot.body_script is not None and bot.body_script.component is component
                 and bot.firing and not bot.dead and not world.match.combat.is_stunned(bot)), None)


def _target(world, state, source, query_position):
    fight = world.match.combat
    collision = fight._collision()
    if collision is None:
        return None
    target = next((candidate for candidate in fight._enemies(source)
                   if getattr(candidate.owner, 'client', None) is not None
                   and not candidate.owner.gone and candidate.owner.entity == source.attack_target), None)
    if target is None:
        return None
    query = state.instance.graph.node(query_position)
    offset = state.evaluate(query.field('m_05BE62DD'), watch=False)
    radius = expr.to_float(state.evaluate(query.field('m_radius'), watch=False))
    if not isinstance(offset, expr.Vec3) or not math.isfinite(radius) or radius <= 0:
        return None
    x, y, z = source.position
    dx, _, dz = combat.aim(source.yaw, 0)
    eye = (x, y + offset.y, z)
    centre = (x + dz * offset.x + dx * offset.z, eye[1], z - dx * offset.x + dz * offset.z)
    a, b = target.segment()
    if (combat.segment_distance(centre, a, b) <= radius + target.radius
            and combat.capsule_visible(collision, eye, a, b, target.radius)):
        return target
    return None


def _attack_nodes(world, state):
    graph = state.instance.graph.index
    if world.hero == HEAVY and graph == CHARGE_GRAPH:
        return CHARGE_QUERY, CHARGE_IMPACT
    melee = MELEE.get(world.hero)
    return melee[1:3] if melee is not None and graph == melee[0] else None


def track_targets(world, state):
    attack = _attack_nodes(world, state)
    if attack is None or state.node.pos != attack[0]:
        return False
    source = _source(world, state)
    target = _target(world, state, source, attack[0]) if source is not None else None
    state.instance.write(expr.lvalue(state.node.field('m_8A2905A0')),
                         (expr.Entity(target.entity),) if target is not None else ())
    return True


def modify_health(world, state, elapsed_ms):
    attack = _attack_nodes(world, state)
    if attack is None or state.node.pos != attack[1]:
        return NotImplemented
    source = _source(world, state)
    if source is None or elapsed_ms:
        return True
    target = _target(world, state, source, attack[0])
    chosen = state.evaluate(state.node.field('m_FA3ECC43'), watch=False)
    if target is None or not isinstance(chosen, tuple) or expr.Entity(target.entity) not in chosen:
        return True
    settings = state.node.field('m_modifyHealth')
    amount = expr.to_float(state.evaluate(settings['m_amount'], watch=False))
    if math.isfinite(amount) and amount > 0:
        fight = world.match.combat
        amount = expr.f32(amount)
        if fight.barriers and barriers.protect_body(fight, target, amount, attacker=source.entity):
            return True
        fight._damage(source, target, amount, False, None, fight.tick)
        if (state.instance.graph.index != CHARGE_GRAPH and not fight.vitals_of(target).dead
                and not fight._has_protection(target, combat.CRYO_STATES)):
            _push(source, target, state, settings)
    return True


def _push(source, target, state, settings):
    horizontal = expr.to_float(state.evaluate(settings['m_E33EFEE1'], watch=False))
    upward = expr.to_float(state.evaluate(settings['m_10399AE7'], watch=False))
    if not all(math.isfinite(value) and value >= 0 for value in (horizontal, upward)):
        return
    x, _, z = source.position
    dx, dz = target.feet[0] - x, target.feet[2] - z
    distance = math.hypot(dx, dz)
    if distance:
        dx, dz = dx / distance, dz / distance
    else:
        dx, _, dz = combat.aim(source.yaw, 0)
    mover = target.owner.mover
    vx, vy, vz = mover.velocity
    mover.velocity = (expr.f32(vx + horizontal * dx), expr.f32(vy + upward),
                      expr.f32(vz + horizontal * dz))
    if upward > 0 and not mover.flags_word & IN_AIR:
        mover.flags_word |= FELL
        mover.air_ticks = 0
