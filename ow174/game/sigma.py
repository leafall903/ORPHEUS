"""Reconstructed Sigma combat services over the extracted build-104319 graphs.

Grasp's box and absorption amount output come from 1D02 state1. The server's
box transform, interception ordering and nominal damage accounting are rebuilt;
this adapter is not proof of native engine services or client presentation.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

from ow174.game import assassin, bots, dva_matrix, genji, props, winston_jump, winston_tesla
from ow174.game.collision import MOVER, TEAM0, TEAM1, World
from ow174.game.script import expr, graph
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E000000000023B
GRASP = 0x1D02
# The 104319 003 definitions carrying 0145, excluded by 1D02 node 7.
GRASP_EXCLUDED_PROJECTILES = frozenset(0x0400000000000000 | index for index in (
    0x03DC, 0x068A, 0x072D, 0x08BE, 0x11A9, 0x1575, 0x15E9, 0x1846, 0x18B7,
    0x1C70, 0x1CCB, 0x1FF1, 0x2578, 0x2658, 0x2931, 0x29A8, 0x3900, 0x3B1F,
))
SHIELD_TAG = 0x5847
NOT_HANDLED = object()
BARRIER_GRAPH = 0x1B55
BARRIER_DEFINITION = 0x0400000000002D25
FLUX = 0x1E41
FLUX_EFFECT = 0x1DBC
ACCRETION = 0x1BBB
# Authored damage loadouts of 187F, 1BBB and quick melee0043.
HYPERSPHERE_LOADOUT = 0x0B9000000000028C
ACCRETION_LOADOUT = 0x0B9000000000028A
ANA_DAMAGE_LOADOUTS = {0x0B900000000000E1, 0x0B900000000000E2}  # Sleep Dart and Biotic Grenade
DAMAGE_CHARGE_LOADOUTS = {HYPERSPHERE_LOADOUT, ACCRETION_LOADOUT, 0x0B900000000002F5}
WINSTON_CHARGE_LOADOUTS = {winston_tesla.LOADOUT, winston_jump.LOADOUT}
# Stock cost 1960 from body graph 004B, with the historical OW1 rate of 5 points/s.
PASSIVE_FULL_CHARGE_SECONDS = 1960 / 5
SHIELD_RECOVERY_DELAY = 3.0
SHIELD_RECOVERY_RATE = 30.0
BODY_PROTECTION = {
    0x02E00000000000DD: (0x05FE, 14, "cryo"),
    0x02E0000000000002: (0x0020, 8, "phase"),
    0x02E00000000001A2: (0x0F7C, 7, "phase"),
}


def body_protection(owner):
    """Read the live protection stack of a supported player ability."""
    if isinstance(owner, bots.Bot):
        return None
    protection = BODY_PROTECTION.get(owner.hero.guid)
    script = owner.body_script
    if protection is None or script is None:
        return None
    component = script.component
    if component.strict or component.entity != owner.body:
        return None
    flag = component.entity_var(2575, create=False)
    if flag is None or not expr.truthy(flag.value()):
        return None
    graph_index, state_index, mode = protection
    for instance in component.instances.values():
        if instance.graph.index == graph_index:
            state = instance.states.get(state_index)
            if state is not None and state.active:
                return mode
    return None


def fortify(owner):
    """Find the active Orisa Fortify combat filter on the current player body."""
    if isinstance(owner, bots.Bot) or owner.hero.guid != 0x02E000000000013E:
        return None
    script = owner.body_script
    if script is None or script.component.strict or script.component.entity != owner.body:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == 0x0CF4:
            state = instance.states.get(4)
            if state is not None and state.active:
                return state
    return None


def halt_owner(rocket):
    owner = rocket.shooter
    if rocket.halt_instance is None or owner.body != rocket.owner_body or owner.body_script is None:
        return None
    return owner.body_script.component.instances.get(rocket.halt_instance)


def halt_flight(world, instance):
    if world.match is None or instance.component.strict or instance.graph.index != 0x0CCB:
        return None
    return next((r for r in world.match.combat.rockets if halt_owner(r) is instance), None)


def halt_native(world, cfg, instance):
    if instance.graph.index != 0x0CCB or instance.component.strict or world.match is None:
        return NotImplemented
    kind = cfg.get("$")
    if kind == "STU_E72F6348":
        return halt_flight(world, instance) is not None
    if kind == "STU_2B2E2F6D" and halt_flight(world, instance) is not None:
        return expr.Entity(world.body)
    return NotImplemented


def trigger_halt(instance, node):
    if instance.graph.index != 0x0CCB or node.pos != 26:
        return False
    rocket = halt_flight(instance.component.world, instance)
    if rocket is None or rocket.halt_at is not None:
        return False
    from ow174.game.combat import TICK

    rocket.halt_at = instance.component.world.match.tick + math.ceil(0.4 / TICK)
    return True


def halt_pull(fight, rocket):
    """Reconstruct the 0CCC radial pull over exposed player capsules."""
    from ow174.game.combat import TICK, segment_distance

    for target in fight._enemies(rocket.shooter):
        owner = target.owner
        if (isinstance(owner, bots.Bot) or body_protection(owner) or fortify(owner) is not None
                or particle_barrier(owner) is not None):
            continue
        if (segment_distance(rocket.position, *target.segment()) - target.radius > 5
                or not fight._unobstructed(rocket.position, target)
                or not fight._blast_passes_barriers(rocket.shooter, rocket.position, target, None)):
            continue
        x, y, z = rocket.position
        goal = (x, y - target.height / 2, z)
        # 0CCE starts its slow branch after the 0.25s wait and retires at 0.65s.
        pull_end = fight.tick + math.ceil(0.25 / TICK)
        fight.halt_pulls[target.entity] = goal, pull_end
        fight.halt_slows[target.entity] = pull_end, fight.tick + math.ceil(0.65 / TICK)


def particle_barrier(owner):
    """Select the first live barrier in the reconstructed contact order."""
    return next(particle_barriers(owner), None)


def particle_barriers(owner):
    """Yield the personal and projected barrier activations protecting this body."""
    if isinstance(owner, bots.Bot):
        return
    match = owner.match
    for caster in match.players:
        if caster.hero.guid != 0x02E0000000000068 or not match._shown(caster):
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
    from ow174.game.combat import aim

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
                or fight.vitals_of(target).dead or particle_barrier(target.owner) is not None):
            continue
        a, b = target.segment()
        nearest = (a[0], max(a[1], min(b[1], origin[1])), a[2])
        delta = tuple(end - start for start, end in zip(origin, nearest, strict=True))
        length = math.sqrt(sum(value * value for value in delta))
        facing = sum(axis * value for axis, value in zip(direction, delta, strict=True))
        if 0 < length <= reach and facing >= cosine * length and fight._unobstructed(origin, target):
            found.append((-facing / length, length, target.entity))
    return (min(found)[2],) if found else ()


def damage_particle_barrier(pet, vitals, amount, attacker):
    """Consume the scoped 0256 barrier pool, with one damage event per impact."""
    activation = particle_barrier_key(pet)
    health = vitals.particle_barriers.get(activation)
    if health is None:
        node = graph.graph(0x0256).node(0)
        health = expr.to_float(pet.evaluate(node.field("m_amount"), watch=False))
    remaining = max(0.0, health - amount)
    vitals.particle_barriers[activation] = remaining
    absorbed = health - remaining
    component = pet.instance.component
    energy = next((i for i in component.instances.values() if i.graph.index == 0x0242), None)
    if absorbed > 0 and energy is not None:
        from ow174.game.script import runtime

        # 0256 node 10 sends absorbed damage and the attacker to 0242 node 5.
        params = {0x2A: absorbed, 0x12: expr.Entity(attacker)}
        if runtime.send_instance_message(energy, 0x0240000000000146, params, component.entity):
            component.run_now()
    if remaining == 0.0:
        del vitals.particle_barriers[activation]
        pet.end(False)
        component.run_now()
    return absorbed


def release_phased_flux_target(player):
    """End an existing lift when an OW1 phase ability takes effect."""
    if body_protection(player) != "phase":
        return
    component = player.body_script.component
    for instance in tuple(component.instances.values()):
        if instance.graph.index == FLUX_EFFECT:
            component.destroy(instance)


def delay_base_shields(owner, vitals, tick, tick_seconds):
    if not isinstance(owner, bots.Bot) and owner.hero.guid == HERO:
        vitals.shield_recovery_tick = tick + SHIELD_RECOVERY_DELAY / tick_seconds


def recover_base_shields(fight, tick_seconds):
    """Recover Sigma's ordinary shield pool on the reconstructed combat clock."""
    if fight.match.ended:
        return
    for player in fight.match.players:
        if not fight.match._shown(player) or player.hero.guid != HERO:
            continue
        vitals = fight.vitals.get(player.body)
        if vitals is None or vitals.dead:
            continue
        since = vitals.shield_recovery_tick
        if since is None or fight.tick <= since:
            continue
        missing = vitals.most[SHIELDS_POOL] - vitals.now[SHIELDS_POOL]
        restored = min(missing, (fight.tick - since) * tick_seconds * SHIELD_RECOVERY_RATE)
        vitals.shield_recovery_tick = None if restored == missing else fight.tick
        if restored > 0:
            vitals.now[SHIELDS_POOL] += restored
            vitals.changed.add(SHIELDS_POOL)
            fight._sync_body_health(player, vitals)


def damage_charge_script(fight, shooter, target, loadout):
    """Select the live current owner of an eligible hosted damage loadout."""
    if (loadout not in DAMAGE_CHARGE_LOADOUTS and loadout not in WINSTON_CHARGE_LOADOUTS
            and loadout not in ANA_DAMAGE_LOADOUTS and loadout != dva_matrix.BOOSTERS_LOADOUT):
        return None
    if (isinstance(shooter, bots.Bot)
            or shooter is target.owner or fight.match.ended or not fight.match._shown(shooter)
            or fight.is_dead(shooter)):
        return None
    if isinstance(target.owner, bots.Bot) and target.owner.spawner.get("summoned"):
        return None  # Deployed objects have health, but are not charge-bearing combatants.
    reflected_damage = (shooter.hero.guid == genji.HERO
                        and (loadout == HYPERSPHERE_LOADOUT or loadout in ANA_DAMAGE_LOADOUTS))
    ana_damage = shooter.hero.guid == 0x02E000000000013B and loadout in ANA_DAMAGE_LOADOUTS
    winston_damage = shooter.hero.guid == winston_tesla.HERO and loadout in WINSTON_CHARGE_LOADOUTS
    sigma_damage = shooter.hero.guid == HERO and loadout in DAMAGE_CHARGE_LOADOUTS
    booster_damage = shooter.hero.body == dva_matrix.MECH and loadout == dva_matrix.BOOSTERS_LOADOUT
    if not (sigma_damage or reflected_damage or ana_damage or winston_damage or booster_damage):
        return None
    if not fight.match.game_map.free_for_all and shooter.team == target.team:
        return None
    script = shooter.body_script
    if script is None or script.component.strict or script.component.entity != shooter.body:
        return None
    return script


def accretion_launch(state, forward):
    """Resolve the authored acceleration and launch bias of 1BBB node 9."""
    if state.instance.graph.index != ACCRETION or state.node.pos != 9:
        return None
    motions = state.node.field("m_projectileMotions") or []
    if len(motions) != 1 or motions[0].get("$") != "STU_E1A5B579":
        return None
    gravity = expr.to_float(state.evaluate(motions[0]["m_599AB8D7"], watch=False))
    launch = state.node.field("m_5EC1EB38")
    lift = expr.to_float(state.evaluate(launch["m_22CE7F30"], watch=False))
    # D5ECB4-D5ECEA adds the bias to Y before normalizing.
    x, y, z = (expr.f32(axis) for axis in forward)
    y = expr.f32(y + lift)
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    direction = (tuple(expr.f32(axis / math.sqrt(squared)) for axis in (x, y, z))
                 if squared >= 1.1754943508222875e-36 else (-0.0, -1.0, -0.0))
    return gravity, direction


def hypersphere_motion(state):
    """Read both stock Hypersphere launch rows for the scoped sphere host."""
    node = state.node
    if state.instance.graph.index != 0x187F or node.pos not in (45, 56):
        return None
    bounce = node.field("m_E365DB20") or {}
    motions = node.field("m_projectileMotions") or ()
    if (bounce.get("$") != "STU_3FE1A93B" or len(motions) != 1
            or motions[0].get("$") != "STU_E1A5B579"):
        return None

    def value(row, key):
        return expr.to_float(state.evaluate(row.get(key), watch=False))

    hit = node.field("m_6396149F")
    return (value(motions[0], "m_599AB8D7"),
            value(hit, "m_57DAE1B5"), value(hit, "m_1FFD8312"),
            expr.to_int(state.evaluate(bounce.get("m_360294F0"), watch=False)),
            value(bounce, "m_0D20DAFD"), value(bounce, "m_C29D35E8"))


def projectile_knockback(state, modify):
    """Read the impulse strengths and per-axis rings for the supported impact graphs."""
    launch = state.instance.graph.index, state.node.pos
    if launch not in (
        (ACCRETION, 9), (0x187F, 45), (0x187F, 56), (0x010A, 15), (0x010A, 21), (0x00FE, 74), (0x00FE, 75),
        (dva_matrix.BOOSTERS, 16),
    ):
        return None

    def value(cfg):
        return expr.to_float(state.evaluate(cfg, watch=False))

    horizontal, vertical = (value(modify.get(key)) for key in ("m_E33EFEE1", "m_583C208B"))
    rings = []
    for row in modify.get("m_knockbackRings") or ():
        rings.append((value(row["m_radius"]), value(row.get("m_579D7CFF")),
                      value(row.get("m_10399AE7"))))
    if not rings or not all(math.isfinite(x) for x in (horizontal, vertical)):
        return None
    return horizontal, vertical, tuple(rings)


def apply_impact_impulse(fight, direction, target, knockback, point=None, distance=0.0, *, strength=1.0):
    """Reconstruct horizontal and upward impulses using the authored values."""
    if (not strength or knockback is None or fight.vitals_of(target).dead or body_protection(target.owner)
            or fortify(target.owner) is not None):
        return
    horizontal, vertical, rings = knockback
    dx, _, dz = direction
    if point is not None:
        dx, dz = target.feet[0] - point[0], target.feet[2] - point[2]
    length = math.hypot(dx, dz)
    scale_xz = scale_y = 0.0
    previous_radius = 0.0
    previous_xz, previous_y = rings[0][1:]
    for radius, ring_xz, ring_y in rings:
        if distance <= radius:
            width = radius - previous_radius
            share = (distance - previous_radius) / width if width > 0 else 0
            scale_xz = previous_xz + (ring_xz - previous_xz) * share
            scale_y = previous_y + (ring_y - previous_y) * share
            break
        previous_radius, previous_xz, previous_y = radius, ring_xz, ring_y
    if not (horizontal * scale_xz or vertical * scale_y):
        return
    owner = target.owner
    if isinstance(owner, bots.Bot):
        if owner.spawner.get("manual_spawn") is not True:
            return
        match = fight.match
        category = MOVER if match.game_map.free_for_all else (TEAM0, TEAM1)[owner.team]
        mover = owner.ensure_effect_mover(match.collision_world, category, match.tick)
    else:
        mover = owner.mover
    lateral = horizontal * scale_xz / length if length else 0.0
    mover.add_external_impulse((
        dx * lateral * strength, vertical * scale_y * strength, dz * lateral * strength,
    ))


def accretion_hit(fight, rocket, target, dealt):
    """Feed the graph's tagged damage entry after an actual Accretion contact."""
    source = rocket.accretion_source
    shooter = rocket.shooter
    if (source is None or source[0] != shooter.body
            or shooter.body_script is None or dealt <= 0 or fight.vitals_of(target).dead):
        return
    component = shooter.body_script.component
    instance = component.instances.get(source[1])
    if instance is None or instance.graph.index != ACCRETION or component.strict:
        return
    from ow174.game.script import runtime

    hit = instance.graph.node(9).field("m_6396149F")["m_modifyHealth"]
    tags = tuple(instance.evaluate(row["m_EE3055BD"]) for row in hit["m_148F3152"])
    message = expr.guid_of(instance.graph.node(17).field("m_gameMessage")["m_gameMessage"])
    runtime.send_instance_message(instance, message, {0x15: expr.Entity(target.entity), 0x13: dealt,
                                                      0x3D: tags}, shooter.body)
    component.run_now()


def accretion_native(world, cfg, instance):
    if (instance.component.strict or instance.graph.index != ACCRETION or world.match is None
            or world.hero != HERO or instance.component.entity != world.body):
        return NotImplemented
    caster = next((p for p in world.match.players if world.match._shown(p) and p.body == world.body), None)
    if caster is None:
        return NotImplemented
    if cfg == instance.graph.node(16).field("m_condition"):
        entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
        return any(target.entity == entity for target in world.match.combat._enemies(caster))
    if cfg == instance.graph.node(19).field("m_value"):
        return expr.Vec3(*caster.mover.position)
    return NotImplemented


class _AccretionControlContext(expr.Context):
    """Read the target's actual gate variables without creating an effect instance."""

    def __init__(self, component, overrides):
        self.component = component
        self.overrides = {identifier & 0xFFFF: value for identifier, value in overrides}

    def var(self, scope, identifier):
        if scope == graph.INSTANCE:
            return self.overrides.get(identifier)
        variable = self.component.entity_var(identifier, create=False)
        return variable.value() if variable is not None else None

    def would_stack_to_top(self, var, priority):
        from ow174.game.script.runtime import would_stack_to_top

        if var is None or var[0] != graph.ENTITY:
            return False
        variable = self.component.entity_var(var[1], create=False)
        return variable is None or would_stack_to_top(variable, priority)


def play_accretion_control(world, instance, node):
    """Translate the scoped 224E request to the host's existing control clock."""
    if (instance.component.strict or instance.graph.index != ACCRETION or node.pos != 18
            or instance.component.entity != world.body):
        return False
    from ow174.game.script import play

    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | 0x224E):
        return False
    duration = dict(resource.overrides).get(0x0D800000000000D7)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return False
    entity = expr.to_entity(instance.evaluate(node.field("m_targets")))
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == entity), None)
    if target is None or fight.vitals_of(target).dead:
        return True
    script = target.owner.body_script
    definition = graph.graph(0x224E)
    if (definition is None or script is None or script.component.entity != entity
            or script.component.strict or script.component.owner_dead or script.component.fault is not None):
        return True
    context = _AccretionControlContext(script.component, resource.overrides)
    for pos in (4, 122):
        if not expr.truthy(expr.evaluate(definition.node(pos).field("m_condition"), context)):
            return True
    # The host retains the longer deadline and only interrupts again when
    # this request extends it. Native 224E's queue and status-filter services
    # are separate from these two target gates.
    seconds = expr.to_int(expr.f32(duration * 1000)) / 1000
    fight._apply_stun(target, seconds, extend_only=True)
    return True


def play_flux_request(world, instance, node):
    """Deliver the authored request on each selected body's existing VM clock."""
    if (instance.component.strict or instance.graph.index != FLUX or node.pos != 38
            or instance.component.entity != world.body or world.match.ended):
        return False
    from ow174.game.script import play

    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | FLUX_EFFECT):
        return False
    definition = graph.graph(FLUX_EFFECT)
    if definition is None:
        return False
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None:
        return True
    fight = world.match.combat
    targets = {target.entity: target for target in fight._targets()}
    for entity in request.targets:
        target = targets.get(entity)
        if target is None or fight.vitals_of(target).dead or body_protection(target.owner):
            continue
        script = target.owner.body_script
        if script is None:
            continue
        receiver = script.component
        if (receiver.entity != entity or receiver.fault is not None or receiver.owner_dead
                or receiver.strict or getattr(receiver.world, "match", None) is not world.match):
            continue
        receiver.play_loaded_graph(
            definition, source=request.context.caller, instigator=request.context.instigator,
            params=request.params, overrides=resource.overrides, policy=request.policy,
        )
    return True


def _flux_source(match, instance):
    source = expr.to_entity(instance.context().var(graph.INSTANCE, 41))
    return next((p for p in match.players
                 if match._shown(p) and p.body == source and p.hero.guid == HERO), None)


def interrupt_flux_lift(fight, caster):
    """Release this cast's targets until 1DBC has committed their descent."""
    if isinstance(caster, bots.Bot) or caster.hero.guid != HERO:
        return
    for target in fight._targets():
        script = target.owner.body_script
        if script is None or script.component.strict:
            continue
        component = script.component
        for instance in tuple(component.instances.values()):
            if (instance.graph.index != FLUX_EFFECT
                    or expr.to_entity(instance.context().var(graph.INSTANCE, 41)) != caster.body):
                continue
            lift = instance.states.get(16)  # 1DBC node 33 owns the pre-descent phase
            if lift is None or lift.active or not lift.counter:
                component.destroy(instance)


def flux_effect_native(world, cfg, instance):
    """Bind 1DBC's source and maximum-health queries to the current Match bodies."""
    if instance.component.strict or instance.graph.index != FLUX_EFFECT or world.match is None:
        return NotImplemented
    if cfg.get("$") == "STU_E72F6348":
        condition = instance.graph.node(60).field("m_condition")
        if cfg == condition["m_configVars"][0]:
            return _flux_source(world.match, instance) is not None
    if cfg.get("$") == "STU_60146072":
        amount = instance.graph.node(14).field("m_modifyHealth")["m_amount"]
        if cfg == amount["m_configVars"][0]:
            fight = world.match.combat
            for target in fight._targets():
                if target.entity == instance.component.entity:
                    return expr.f32(fight.vitals_of(target).maximum)
    if cfg.get("$") == "STU_8CBDD81C":
        sample = instance.graph.node(57).field("m_143D4C5B")[0]["m_value"]
        if cfg == sample:
            return world.match.script_body_position(instance.component.entity, instance.component.entity)
    return NotImplemented


def sync_flux_airborne(component, mover):
    """Publish the target pose before 1DBC evaluates its airborne branches."""
    if component.strict:
        return
    var = component.entity_var(417, create=False)
    # Keep updating a true value through landing if the effect ends early.
    if ((var is not None and var.base is True)
            or any(i.graph.index == FLUX_EFFECT for i in component.instances.values())):
        component.set(417, mover is not None and mover.airborne)


def flux_velocity(instance, node):
    """Apply the authored target velocity rows before the next body pose."""
    component = instance.component
    match = getattr(component.world, "match", None)
    caster_action = instance.graph.index == FLUX and node.pos in (18, 124)
    target_action = instance.graph.index == FLUX_EFFECT and node.pos in (4, 38, 41, 47)
    if component.strict or match is None or not (caster_action or target_action):
        return False
    target = next((p for p in match.players if match._shown(p) and p.body == component.entity), None)
    if target is None and target_action:
        target = next((bot for bot in match.bots if bot.entity == component.entity
                       and bot.spawner.get("manual_spawn") is True and not bot.dead), None)
    if target is None or target.body_script is None or target.body_script.component is not component:
        return False
    if component.owner_dead or match.ended or (target_action and _flux_source(match, instance) is None):
        return True
    from ow174.game.mover import FELL

    # Scoped reconstruction of the rows used by Flux. Movement still passes
    # through the existing capsule collision, gravity and modifier integration.
    if isinstance(target, bots.Bot):
        mover = target.effect_mover
        if mover is None:
            if node.pos not in (38, 47):
                if node.pos == 4:
                    target.velocity = (0.0, 0.0, 0.0)
                return True
            category = MOVER if match.game_map.free_for_all else (TEAM0, TEAM1)[target.team]
            mover = target.ensure_effect_mover(match.collision_world, category, match.tick)
    else:
        mover = target.mover
    for row in node.field("m_BC5E91CF"):
        config = row["m_9B7A63EA"]
        value = instance.evaluate(config)
        if row["$"] == "STU_9AF0FA80" and value == 0:
            if row["m_A9561CA0"] == 0:
                mover.velocity = (0.0, 0.0, 0.0)
            elif caster_action and row["m_A9561CA0"] == 2:
                x, _, z = mover.velocity
                mover.velocity = (x, 0.0, z)
        elif row["$"] == "STU_4635C5E2" and row["m_CFEC5F76"] == 1 and isinstance(value, expr.Vec3):
            mover.velocity = tuple(expr.f32(a + b) for a, b in zip(mover.velocity, value, strict=True))
            if mover.velocity[1] > 0:
                mover.flags_word |= FELL
                if target_action and isinstance(target, bots.Bot):
                    from ow174.game import heavy_assault

                    if target.hero == assassin.HERO:
                        assassin.bind(component.world, component, None)
                    elif target.hero == heavy_assault.TROOPER:
                        heavy_assault.stop(component)
        elif row["$"] == "STU_748FC65A" and row["m_A9561CA0"] == 2 and type(value) in (int, float):
            x, y, z = mover.velocity
            # Native C32860 maps selector 2 to signed vertical field 34.
            # The 748FC65A row selects MINIMUM, so downward speed is preserved.
            mover.velocity = (x, expr.f32(min(y, value)), z)
        elif (caster_action and row["$"] == "STU_748FC65A" and row["m_A9561CA0"] == 0
              and type(value) in (int, float) and value >= 0):
            length = math.sqrt(sum(axis * axis for axis in mover.velocity))
            if length > value:
                mover.velocity = tuple(expr.f32(axis * value / length) for axis in mover.velocity)
    return True


def flux_caster_native(world, cfg, instance):
    """Use upright body up for the exact caster launch direction query."""
    if (instance.component.strict or instance.graph.index != FLUX or world.hero != HERO
            or cfg.get("$") != "STU_D32169F6" or instance.component.entity != world.body):
        return NotImplemented
    launch = instance.graph.node(18).field("m_BC5E91CF")[1]["m_9B7A63EA"]
    return expr.Vec3(0.0, 1.0, 0.0) if cfg == launch["m_configVars"][0] else NotImplemented


def flux_flight_data(player):
    """Expose the authored flight mover tuning while the graph owns node 35."""
    script = player.body_script
    if script is None or player.hero.guid != HERO or script.component.strict:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index != FLUX:
            continue
        state = instance.states.get(13)
        if state is not None and state.active:
            cfg = state.node.field("m_BC5E91CF")[1]["m_9B7A63EA"]
            return replace(player.mover.data, run_forward=cfg["m_CFD3E635"], run_backward=cfg["m_61D6B4E9"],
                           run_strafe=cfg["m_19452F15"], accel_air=cfg["m_E631BA84"],
                           decel_air_base=cfg["m_142DA8D4"], decel_air_base_speed=cfg["m_031FE7EE"],
                           decel_air_per_speed=cfg["m_8895A9C8"])
    return None


def flux_modify_health(world, state, elapsed_ms):
    """Apply the authored entry hit and finish a slam after ground contact."""
    instance = state.instance
    match = world.match
    if (instance.component.strict or instance.graph.index != FLUX_EFFECT
            or state.node.pos not in (12, 14) or match is None):
        return NotImplemented
    slam = state.node.pos == 14
    caster = _flux_source(match, instance)
    if caster is None or match.ended or instance.component.owner_dead:
        instance.component.destroy(instance)
        return True
    if elapsed_ms and not slam:
        return True
    if instance.evaluate(state.node.field("m_FA3ECC43")) != expr.Entity(instance.component.entity):
        return True
    fight = match.combat
    target = next((t for t in fight._enemies(caster) if t.entity == instance.component.entity), None)
    if target is not None:
        if slam:
            owner = target.owner
            mover = owner.effect_mover if isinstance(owner, bots.Bot) else owner.mover
            if mover is not None and mover.airborne:
                return True
        config = state.node.field("m_modifyHealth")
        amount = expr.to_float(instance.evaluate(config["m_amount"]))
        asset = instance.evaluate(config["m_loadout"])
        loadout = asset.guid if isinstance(asset, expr.Asset) else None
        fight._damage(caster, target, amount, False, loadout, match.tick)
        if fight.vitals_of(target).dead:
            instance.component.destroy(instance)
    if slam and state.active:
        from ow174.game.script.runtime import FINISH

        state.request(FINISH)
    return True


def flux_targets(world, state):
    """Scoped reconstructed 1E41/node37 query over current Match body capsules.

    Authored origin/depth/aperture feed the native-backed Volume kernel. Upright
    axes, living hostile Match membership, nearest-capsule static LOS and stable
    distance/ID ordering are server policies. Native index/mask/tag population,
    target ages, barrier LOS and special immunity admission remain unproven;
    this bridge does not supply native filter contexts or alias controllers.
    """
    instance = state.instance
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.component.entity != world.body or instance.graph.index != FLUX
            or state.node.pos != 37):
        return NotImplemented
    match = world.match
    player = next((p for p in match.players if p.has_body and p.body == world.body), None)
    if (match.ended or instance.component.owner_dead or player is None or not player.spawned
            or player.client is None or match.combat.is_dead(player)):
        return ()
    from ow174.game.combat import capsule_visible
    from ow174.game.script.target_volume import Volume

    origin = state.evaluate(state.node.field("m_05BE62DD"), watch=False)
    sight = state.evaluate(state.node.field("m_D59188D5"), watch=False)
    depth = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    aperture = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    if (not isinstance(origin, expr.Vec3) or not isinstance(sight, expr.Vec3)
            or not all(math.isfinite(v) for v in (*origin, *sight, depth, aperture))
            or depth <= 0 or not 0 <= aperture < 180):
        return ()
    # C9A430's parallel volume: extent = depth * tan(half aperture).
    half = expr.f32(math.radians(aperture) * 0.5)
    cosine = expr.f32(math.cos(half))
    extent = expr.f32(depth * expr.f32(math.tan(half)))
    volume = Volume(tuple(origin), (0, 1, 0), (cosine, cosine), (extent, extent),
                    (0, 0), (1, 1), depth, parallel=True)
    found = []
    for target in match.combat._enemies(player):
        # This capsule bridge hosts player and PvE movement, not fixed summons.
        if isinstance(target.owner, bots.Bot) and target.owner.spawner.get("summoned"):
            continue
        if body_protection(target.owner):
            continue
        accepted, distance = volume.capsule(target.feet, target.radius, target.height)
        if accepted and capsule_visible(match.combat._collision(), tuple(sight),
                                        *target.segment(), target.radius):
            found.append((distance, target.entity))
    return tuple(entity for _distance, entity in sorted(found))


def place_flux(world, state, *, confirm):
    """Reconstruct the static-ground placement boundary of1E41/node23.

    Native4C96470A refreshCB6BA0 writes the position/valid outputs before
    following success188 or failure190 on confirmation. The actual authored
    range is evaluated here. Static view-ray placement, upward-surface policy
    and ordering against the mover are reconstructed; native shape/filter,
    dynamic surfaces and client prediction are not established by this host.
    """
    instance = state.instance
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != FLUX or state.node.pos != 23):
        return NotImplemented
    return place_on_static_ground(world, state, confirm=confirm)


def place_on_static_ground(world, state, *, confirm):
    instance = state.instance
    combat = world.match.combat
    player = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (not state.active or player is None or instance.component.owner_dead or world.match.ended):
        return True
    reach = expr.to_float(state.evaluate(state.node.field("m_6386E7B4"), watch=False))
    direction = view_basis(player.mover.yaw, player.mover.pitch)[2]
    hit = combat._collision().cast_ray(combat._eye(player), direction, reach)
    valid = hit is not None and hit.normal[1] > 1e-6
    if valid:
        # Static world triangles have no dynamic entity/bone transform. Explicit
        # Entity0 is known absence, rather than an unimplemented query's null.
        point = expr.Vec3(*(expr.f32(value) for value in hit.point))
        instance.write(expr.lvalue(state.node.field("m_30710B76")), point)
        instance.write(expr.lvalue(state.node.field("m_1549F0F9")), expr.Entity(0))
        instance.write(expr.lvalue(state.node.field("m_2FECD1E3")), expr.Entity(0))
    instance.write(expr.lvalue(state.node.field("m_2339572B")), valid)
    if confirm:
        instance.follow(state.node, "m_F6955E81" if valid else "m_425337C6", state)
    return True


def flux_position_query(world, cfg, instance):
    """Static absent-entity arm of C5148EFE/CEB190: preserve the supplied point.

    D16C80's absent-object branchD173E4 writes an identity transform. Dynamic
    entity/bone transforms and the full native lookup context remain unhandled.
    """
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != FLUX or cfg.get("$") != "STU_C5148EFE"
            or cfg.get("m_bone") is not None):
        return NOT_HANDLED
    entity, offset = cfg.get("m_entity"), cfg.get("m_BD631144")
    if (expr.dynamic(entity) != (graph.INSTANCE, 1883)
            or expr.dynamic(offset) != (graph.INSTANCE, 2478)):
        return NOT_HANDLED
    if instance.evaluate(entity) != expr.Entity(0):
        return NOT_HANDLED
    point = instance.evaluate(offset)
    return point if isinstance(point, expr.Vec3) else expr.EVALUATION_FAILED


def flux_actor_point_query(world, cfg, instance):
    """1E41/node37's candidate point, not a candidate entity-ID mapping.

    CE47D0/D1A410 writes a Vec3. Its actor arm uses C5E3C0's transform and
    D9F3F0's height along local up. Match bodies are upright; the existing
    Combat pose/eye input is the declared host binding. Native transform/height
    storage, non-actor fallback and complete target filters remain unverified.
    """
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.component.entity != world.body or instance.graph.index != FLUX
            or cfg.get("$") != "STU_21A13145"):
        return NOT_HANDLED
    expected = instance.graph.node(37).field("m_48837057")
    if cfg != expected:
        return NOT_HANDLED
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    target = next((target for target in world.match.combat._targets() if target.entity == entity), None)
    if target is None:
        return expr.EVALUATION_FAILED
    x, y, z = target.feet
    return expr.Vec3(expr.f32(x), expr.f32(expr.f32(y) + expr.f32(target.eye)), expr.f32(z))


@cache
def barrier_geometry(path: str):
    """Load the verified external C015 physics mesh, including its dynamic holder.

    No asset data is bundled in source and no rectangle fallback is fabricated.
    The sidecar is produced by the manifest-verifying local export step.
    """
    source = Path(path)
    record = json.loads(source.with_name("model-integrity.json").read_text(encoding="utf-8-sig"))
    raw = source.read_bytes()
    if (record.get("guid") != "00000000C015.00C" or record.get("build") != 104319
            or record.get("bytes") != len(raw)
            or record.get("sha256") != hashlib.sha256(raw).hexdigest()):
        raise ValueError("Barrier model does not match its verified 104319 input record")
    shapes = props.model_shapes(raw, 0xFFFF, include_dynamic=True)
    triangles, masks = [], []
    for shape in shapes:
        if shape[0] != "mesh":
            raise ValueError("unsupported Barrier physics shape")
        _kind, _mask, vertices, faces = shape
        for a, b, c, _w0, _w1, _w2, mask, _surface in faces:
            triangles.append((*vertices[a], *vertices[b], *vertices[c]))
            masks.append(mask)
    if not triangles:
        raise ValueError("Barrier model has no physics triangles")
    return World(triangles, masks=masks, name="Sigma C015 dynamic physics mesh")


def view_basis(yaw, pitch):
    yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
    sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
    return ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))


def barrier_recharge_rate(instance):
    """Reconstruct1A2A/node50's owner-body ruleset read, not its native query chain.

    The graph supplies both80HP/s and the591A ruleset identifier. Resolve the
    owning body's declared override/default, as the permissive host does for
    other ruleset inputs. Native3832D36C/CCFAC0 has its own context/fallback path.
    """
    config = graph.graph(0x1A2A).node(50).field("m_modifyHealth")["m_amount"]
    key = config["m_configVars"][0]["m_value"]["m_expression"]["m_dynamicVars"][0]["m_identifier"]
    override = instance.component.entity_var(key, create=False)
    scale = override.value() if override is not None else None
    if scale is None:
        scale = instance.component.world.ruleset(key)
    base = config["m_expression"]["m_D99EF254"][0]
    rate = expr.to_float(base) * expr.to_float(scale)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError("invalid Barrier recharge rate")
    return rate


@dataclass
class Barrier:
    persists_after_death = False

    entity: int
    owner_body: int
    owner: object
    instance: object
    vitals: object
    geometry: World
    position: tuple
    basis: tuple
    last: float
    phase: int = 1
    active: bool = False
    child_request_seen: bool = False
    recharge_at: float | None = None

    def update(self, combat):
        component = self.instance.component
        wanted = expr.to_int(component.entity_var(19777).value())
        if component.owner_dead or self.owner.body != self.owner_body or combat.match.ended:
            self.active = False
            return
        if wanted in (2, 3) and self.phase not in (2, 3):
            self.basis = view_basis(self.owner.mover.yaw, self.owner.mover.pitch)
            forward = self.basis[2]
            eye = combat._eye(self.owner)
            offset = 0.9
            if combat._collision().cast_ray(eye, forward, offset) is not None:
                # Start on the owner's side so the forward probe sees the wall.
                offset = 0.0
            self.position = tuple(a + b * offset for a, b in zip(eye, forward, strict=True))
            self.last = combat.now
        elapsed = max(0.0, combat.now - self.last)
        if wanted == 1:
            if self.phase != 1:
                wait = graph.graph(0x1A2A).node(45).field("m_timeout")
                delay = expr.to_float(self.instance.evaluate(wait))
                self.recharge_at = combat.now + delay
            if self.recharge_at is not None and self.child_request_seen:
                seconds = max(0.0, combat.now - max(self.last, self.recharge_at))
                if seconds:
                    self.vitals.heal(seconds * barrier_recharge_rate(self.instance))
        else:
            self.recharge_at = None
        self.last = max(self.last, combat.now)
        self.phase = wanted
        self.active = wanted in (2, 3) and self.vitals.total > 0 and self.child_request_seen
        if self.active and wanted == 2 and elapsed:
            # The actual child movement node supplies16.5m/s and its forward
            # wall probe supplies0.9m. Ordering/placement are reconstructed.
            child = graph.graph(0x1A2A)
            speed = expr.to_float(self.instance.evaluate(child.node(22).field("m_speed")))
            clearance = expr.to_float(self.instance.evaluate(child.node(24).field("m_castLength")))
            step = speed * elapsed
            hit = combat._collision().cast_ray(self.position, self.basis[2], step + clearance)
            if hit is not None:
                step = max(0.0, hit.distance - clearance)
            self.position = tuple(a + b * step for a, b in zip(self.position, self.basis[2], strict=True))

    def retire(self):
        self.active = False
        if self.instance.graph.index != BARRIER_GRAPH:
            return
        state = self.instance.states.get(4)
        if state is not None and state.pet_entity == self.entity and not self.instance.stopped:
            state.pet_entity = None
            self.instance.write(expr.lvalue(state.node.field("m_B3019338")), expr.Entity(0))

    def melee_contact(self, centre, radius, visible):
        return False

    def break_at(self, now):
        self.active = False

    def intersection(self, origin, direction, reach):
        delta = tuple(a - b for a, b in zip(origin, self.position, strict=True))
        point = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in self.basis)
        vector = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in self.basis)
        hit = self.geometry.cast_ray(point, vector, reach)
        return hit.distance if hit is not None else None


def barrier_gap_buttons(script, buttons):
    """Preserve a confirmed continuous deployment across reconstructed command frames."""
    from ow174.game.commands import SECONDARY

    if (script is None or script.hero != HERO or script.component.strict
            or not script.buttons & buttons & SECONDARY):
        return 0
    phase = script.component.entity_var(19777, create=False)
    return SECONDARY if phase is not None and phase.value() == 2 else 0


def create_barrier(combat, world, state):
    if (state.instance.component.strict or world.hero != HERO
            or state.instance.graph.index != BARRIER_GRAPH or state.node.state != 4):
        return NotImplemented
    definition = state.evaluate(state.node.field("m_entityDefinition"), watch=False)
    if definition != expr.Asset(BARRIER_DEFINITION):
        return NotImplemented
    path = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
    if not path:
        return NotImplemented  # explicit missing external input, never a placeholder shape
    owner = next((p for p in combat.match.players if p.has_body and p.body == world.body), None)
    if owner is None:
        return NotImplemented
    from ow174.game.combat import Vitals

    geometry = barrier_geometry(path)
    entity = combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000:
        raise ValueError("Barrier entity allocator exhausted")
    combat.next_bot += 1
    amount = expr.to_float(state.instance.vars[587].value())
    basis = view_basis(owner.mover.yaw, owner.mover.pitch)
    combat.barriers[entity] = Barrier(entity, world.body, owner, state.instance, Vitals(entity, amount),
                                     geometry, combat._eye(owner), basis, combat.now)
    return entity


def barrier_health_query(world, cfg, instance):
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != BARRIER_GRAPH or cfg.get("$") != "STU_9B7648C3"):
        return NOT_HANDLED
    selector = cfg.get("m_entity")
    if not isinstance(selector, dict) or selector.get("$") != "STUConfigVarDynamic":
        return NOT_HANDLED
    if selector.get("m_identifier") != 512 or selector.get("m_60DB8F99") != graph.ENTITY:
        return NOT_HANDLED
    entity = expr.to_entity(instance.evaluate(selector))
    barrier = world.match.combat.barriers.get(entity)
    return barrier.vitals.total if barrier is not None else expr.EVALUATION_FAILED


def play_barrier_request(combat, body, instance, node):
    """Bind the actual self-owned Pet request to its reconstructed actor service.

    The1A2A graph's native engine execution is not claimed; its health, motion
    constants and owner phase inputs drive this explicit service.
    """
    if instance.component.strict or instance.graph.index != BARRIER_GRAPH or node.pos != 8:
        return False
    resource = instance.evaluate(node.field("m_script"))
    target = expr.to_entity(instance.evaluate(node.field("m_targets")))
    barrier = combat.barriers.get(target)
    if (resource != expr.Asset(0x0580000000001A2A) or barrier is None
            or barrier.owner_body != body or barrier.instance is not instance):
        return False
    barrier.child_request_seen = True
    return True


def retire_barrier(combat, barrier):
    del combat.barriers[barrier.entity]
    barrier.retire()


def update_barriers(combat):
    from ow174.game import photon_barrier, reinhardt_barrier, winston_barrier

    reinhardt_barrier.mount_active(combat)
    for barrier in tuple(combat.barriers.values()):
        owner_script_ended = barrier.instance.stopped or barrier.instance.component.owner_dead
        replaced = barrier.owner.body != barrier.owner_body
        if ((replaced and not getattr(barrier, "persists_after_respawn", False)) or combat.match.ended
                or (owner_script_ended and not barrier.persists_after_death)):
            retire_barrier(combat, barrier)
            continue
        barrier.update(combat)
        if isinstance(barrier, (winston_barrier.Bubble, photon_barrier.Wall)) and not barrier.active:
            retire_barrier(combat, barrier)


def hit_barrier_with_melee(combat, shooter, eye, centre, radius, amount):
    from ow174.game.combat import capsule_visible

    def visible(point):
        return capsule_visible(combat._collision(), eye, point, point, 0.0)

    for barrier in hostile_barriers(combat, shooter):
        if barrier.melee_contact(centre, radius, visible):
            damage_barrier(combat, barrier, amount)


def barrier_intersection(combat, shooter, origin, direction, reach):
    return min(barrier_intersections(combat, shooter, origin, direction, reach),
               key=lambda hit: hit[0], default=None)


def hostile_barriers(combat, shooter):
    for barrier in combat.barriers.values():
        barrier.update(combat)
        if (barrier.active and barrier.owner is not shooter
                and (combat.match.game_map.free_for_all or barrier.owner.team != shooter.team)):
            yield barrier


def barrier_intersections(combat, shooter, origin, direction, reach):
    for barrier in hostile_barriers(combat, shooter):
        distance = barrier.intersection(origin, direction, reach)
        if distance is not None:
            yield distance, barrier


def damage_barrier(combat, barrier, amount):
    dealt = barrier.vitals.damage(amount)
    if barrier.vitals.total <= 0:
        barrier.break_at(combat.now)
    return dealt


def barrier_owner_query(world, cfg, instance):
    """Reconstruct only 1B55's self->component43 owner->existence predicate.

    F7262B36/CDBDE0 calls D15520, which reads component43 and BD42D0. The
    server's current Player.body/entity relation supplies that declared body
    possession. This is not a generic namespace/owner-query engine adapter.
    """
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index != 0x1B55):
        return NOT_HANDLED
    cls = cfg.get("$")
    source = cfg.get("m_801B298F") if cls == "STU_F7262B36" else cfg.get("m_entity")
    if cls == "STU_E72F6348":
        if not isinstance(source, dict) or source.get("$") != "STU_F7262B36":
            return NOT_HANDLED
        source = source.get("m_801B298F")
    elif cls != "STU_F7262B36":
        return NOT_HANDLED
    if not isinstance(source, dict) or source.get("$") != "STU_B5A0CAF0":
        return NOT_HANDLED
    if instance.component.entity != world.body:
        return NOT_HANDLED
    owner = next((player for player in world.match.players
                  if player.has_body and player.body == world.body), None)
    if cls == "STU_E72F6348":
        return owner is not None and not world.match.ended
    if owner is None or world.match.ended:
        return expr.EVALUATION_FAILED  # native object lookup failure, not a fabricated controller
    return expr.Entity(owner.entity)


@dataclass
class ShieldDecay:
    starts: float
    last: float
    rate: float


def ray_box(origin, direction, low, high, reach):
    """First nonnegative intersection with a local axis-aligned box, or None."""
    start, stop = 0.0, reach
    for point, vector, minimum, maximum in zip(origin, direction, low, high, strict=True):
        if abs(vector) < 1e-9:
            if point < minimum or point > maximum:
                return None
            continue
        enter, leave = (minimum - point) / vector, (maximum - point) / vector
        start, stop = max(start, min(enter, leave)), min(stop, max(enter, leave))
        if start > stop:
            return None
    return start


def deflect_intersection(combat, shooter, origin, direction, reach):
    """Find the nearest active Genji box in the scoped front-facing host."""
    best = None
    for target in combat._enemies(shooter):
        actor = target.owner
        if isinstance(actor, bots.Bot) or actor.hero.guid != 0x02E0000000000029:
            continue
        script = actor.body_script
        if script is None or script.component.owner_dead or script.component.strict:
            continue
        basis = view_basis(actor.mover.yaw, actor.mover.pitch)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        if local_direction[2] >= 0:
            continue
        eye = combat._eye(actor)
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        for instance in script.component.instances.values():
            if instance.graph.index != 0x03A3:
                continue
            state = instance.states.get(10)
            if state is None or not state.active or state.node.cls != "STUStatescriptStateDeflectProjectiles":
                continue
            for box in state.node.field("m_boxes") or ():
                a = state.evaluate(box.get("m_CBA118B2"), watch=False)
                b = state.evaluate(box.get("m_AEF67365"), watch=False)
                if not isinstance(a, expr.Vec3) or not isinstance(b, expr.Vec3):
                    continue
                low = tuple(map(min, zip(a, b, strict=True)))
                high = tuple(map(max, zip(a, b, strict=True)))
                hit = ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, actor, basis[2]
    return best


def grasp_intersection(combat, shooter, origin, direction, reach):
    """Nearest live enemy Grasp box before the caller's nearer body/wall bound.

    The local box is reconstructed as view-oriented and eye-relative. Bounds
    themselves are evaluated from the actual active State, including body scale.
    """
    best = None
    for target in combat._enemies(shooter):
        actor = target.owner
        npc = isinstance(actor, bots.Bot)
        hero = actor.hero if npc else actor.hero.guid
        script = actor.body_script
        if hero != HERO or script is None or script.component.owner_dead:
            continue
        yaw, pitch = (actor.yaw, 0) if npc else (actor.mover.yaw, actor.mover.pitch)
        yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
        eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for instance in script.component.instances.values():
            if instance.graph.index != GRASP:
                continue
            state = instance.states.get(1)
            if state is None or not state.active:
                continue
            for box in state.node.field("m_boxes") or []:
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                    continue
                hit = ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, state
    return best


def absorbed(state, amount):
    """Feed the actual 1D02 absorption output and its authored accumulator plug.

    6584 is the per-hit amount; the authored node32 adds it to21312. Do not grant
    health or bypass the authored end/PlayScript path here. The declared
    reconstructed shield request is handled separately; native pool services
    and original-client presentation remain open.
    """
    state.instance.write((graph.INSTANCE, 6584), float(amount))
    state.instance.follow(state.node, "m_160B5DBE", state)
    state.instance.component.run_now()


def grant_shields(combat, entity, instance, node):
    """Explicit reconstruction of 1D02/node34's self-only 1D03 shield request.

    Permissive bodies otherwise skip PlayScript. Consume its actual amount/cap
    arguments and authored decay fields; do not pretend the native engine host
    or 1D03 HealthPool State has executed. Native pool queries, cap stacking and
    presentation still need comparison with the original engine.
    """
    if instance.graph.index != GRASP or node.pos != 34:
        return False
    resource = instance.evaluate(node.field("m_script"))
    target = instance.evaluate(node.field("m_targets"))
    if resource != expr.Asset(0x0580000000001D03) or target != expr.Entity(entity):
        return False
    owner = next((target for target in combat._targets() if target.entity == entity), None)
    if owner is None:
        return True
    script = owner.owner.body_script
    if instance.component.entity != entity or script is None or script.component is not instance.component:
        return False
    absorption = instance.states.get(1)
    if absorption is None or absorption.counter == 0:
        return True
    activation = instance.id, absorption.counter
    if combat.grasp_grants.get(entity) == activation:
        return True
    combat.grasp_grants[entity] = activation
    vitals = combat.vitals_of(owner)
    if vitals.dead:
        return True
    params = {graph.guid(entry["m_B5051BCE"]): expr.to_float(instance.evaluate(entry["m_value"]))
              for entry in node.field("m_params") or []}
    amount, limit = params[0x0A4000000000002A], params[0x0A400000000003D1]
    delay = expr.to_float(instance.evaluate(instance.graph.node(71).field("m_timeout")))
    modify = instance.graph.node(70).field("m_modifyHealth")
    rate = expr.to_float(instance.evaluate(modify["m_amount"]))
    if vitals.grant_temporary_shields(SHIELD_TAG, amount, limit) > 0:
        now = instance.component.now / 1000
        combat.grasp_shields[entity] = ShieldDecay(now + delay, now, rate)
        combat._sync_body_health(owner.owner, vitals)
    return True


def update_shields(combat):
    """Elapsed-time decay without hit notices/healing credit or base shield loss."""
    owners = {target.entity: target.owner for target in combat._targets()}
    for entity, decay in list(combat.grasp_shields.items()):
        vitals = combat.vitals.get(entity)
        if vitals is None or vitals.dead or entity not in owners:
            del combat.grasp_shields[entity]
            continue
        duration = max(0.0, combat.now - max(decay.last, decay.starts))
        decay.last = max(decay.last, combat.now)
        if vitals.decay_temporary_shields(SHIELD_TAG, duration * decay.rate) > 0:
            combat._sync_body_health(owners[entity], vitals)
        if vitals.temporary_shields[SHIELD_TAG].now <= 0:
            del combat.grasp_shields[entity]
