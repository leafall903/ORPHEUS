"""Host health and energy for the actual Zarya Particle Barrier Pet states."""

import math

from ow174.game import bots
from ow174.game.script import expr, graph


def active(owner):
    """Select the first live barrier in the reconstructed contact order."""
    return next(particle_barriers(owner), None)


def particle_barriers(owner):
    """Yield the personal and projected barrier activations protecting this body."""
    if isinstance(owner, bots.Bot):
        return
    match = owner.match
    for caster in match.players:
        if caster.hero.guid != 0x02E0000000000068 or not match._shown(caster) or match.combat.is_dead(caster):
            continue
        if caster is not owner and (match.game_map.free_for_all or caster.team != owner.team):
            continue
        script = caster.body_script
        if script is None or script.component.strict or script.component.entity != caster.body:
            continue
        personal = caster is owner
        for instance in script.component.instances.values():
            if instance.graph.index != (0x0244 if personal else 0x025C):
                continue
            pet = instance.states.get(2 if personal else 10)
            if pet is not None and pet.active and (
                personal or expr.to_entity(instance.context().var(graph.INSTANCE, 536)) == owner.body
            ):
                yield pet


def particle_barrier_key(pet):
    return (pet.instance.component.entity, pet.instance.id, pet.counter)


def projected_barrier_targets(world, state):
    """Resolve the stock projected-barrier reticle and cast queries."""
    instance = state.instance
    match = world.match
    if (match is None or instance.component.strict or instance.graph.index != 0x025C
            or state.node.pos not in (11, 18)):
        return NotImplemented
    if match.game_map.free_for_all:
        return ()
    from ow174.game.combat import aim, capsule_visible

    caster = next((p for p in match.players if match._shown(p) and p.body == world.body), None)
    if caster is None or match.combat.is_dead(caster):
        return ()
    fight = match.combat
    origin = fight._eye(caster)
    direction = aim(caster.mover.yaw, caster.mover.pitch)
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    multiplier = instance.component.entity_var(9595, create=False)
    aperture = (10.0 if multiplier is None or multiplier.value() is None else
                expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False)))
    cosine = math.cos(math.radians(aperture))
    found = []
    for target in fight._targets():
        if (isinstance(target.owner, bots.Bot) or target.owner is caster or target.team != caster.team
                or fight.vitals_of(target).dead or active(target.owner) is not None):
            continue
        a, b = target.segment()
        nearest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        delta = tuple(end - start for start, end in zip(origin, nearest, strict=True))
        length = math.sqrt(sum(value * value for value in delta))
        facing = sum(axis * value for axis, value in zip(direction, delta, strict=True))
        if (0 < length <= reach and facing >= cosine * length
                and capsule_visible(fight._collision(), origin, a, b, target.radius)):
            found.append((-facing / length, length, target.entity))
    return (min(found)[2],) if found else ()


def damage_particle_barrier(pet, vitals, amount, attacker):
    """Consume the scoped 0256 barrier pool, with one damage event per impact."""
    activation = particle_barrier_key(pet)
    health = barrier_health(pet, vitals)
    remaining = max(0.0, health - amount)
    vitals.particle_barriers[activation] = remaining
    absorbed = health - remaining
    component = pet.instance.component
    energy = next((i for i in component.instances.values() if i.graph.index == 0x0242), None)
    if absorbed > 0 and attacker and energy is not None:
        from ow174.game.script import runtime

        # 0256 node 10 sends absorbed damage and the attacker to 0242 node 5.
        params = {0x2A: absorbed, 0x12: expr.Entity(attacker)}
        if runtime.send_instance_message(energy, 0x0240000000000146, params, component.entity):
            component.run_now()
    if remaining == 0.0:
        del vitals.particle_barriers[activation]
        pet.end(False)
        component.run_now()


def barrier_health(pet, vitals):
    stored = vitals.particle_barriers.get(particle_barrier_key(pet))
    if stored is not None:
        return stored
    node = graph.graph(0x0256).node(0)
    return expr.to_float(pet.evaluate(node.field("m_amount"), watch=False))
