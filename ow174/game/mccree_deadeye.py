"""Host targets for the authored 104319 Deadeye state graphs."""

import math

from ow174.game.script import expr, graph, runtime
from ow174.game.weapon_spread import MCCREE

ROOT, TARGET_EFFECT = 0x022F, 0x01EE
REAPPLY = 0x024000000000000F


def _owner(world, instance):
    match = world.match
    if match is None or match.ended or world.hero != MCCREE or instance.component.entity != world.body:
        return None
    return next((player for player in match.players if player.body == world.body
                 and player.spawned and not player.gone and player.client is not None
                 and not match.combat.is_dead(player)), None)


def _target_point(target):
    """Use the eye height clipped to the capsule's upper axis endpoint."""
    _, upper = target.segment()
    return target.feet[0], min(target.feet[1] + target.eye, upper[1]), target.feet[2]


def _visible(fight, origin, target):
    from ow174.game.combat import capsule_visible

    a, b = target.segment()
    return capsule_visible(fight.match.collision_world, origin, a, b, target.radius)


def targets(world, state):
    if state.instance.graph.index != ROOT or state.node.pos not in (26, 86) or world.hero != MCCREE:
        return NotImplemented
    owner = _owner(world, state.instance)
    if owner is None:
        return ()
    if graph.graph(TARGET_EFFECT) is None:
        expr.warn_once("deadeye_effect", "Deadeye requires external target-effect graph 01EE")
        return ()
    from ow174.game.combat import aim

    fight = world.match.combat
    origin = fight._eye(owner)
    forward = aim(owner.mover.yaw, owner.mover.pitch)
    reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    # Interpret the authored angle as the full cone. Only the second query requires visibility.
    angle = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
    threshold = math.cos(math.radians(angle * 0.5))
    found = []
    for target in fight._enemies(owner):
        offset = tuple(b - a for a, b in zip(origin, _target_point(target), strict=True))
        distance = math.hypot(*offset)
        if not 0 < distance <= reach:
            continue
        if sum(a * b for a, b in zip(forward, offset, strict=True)) < threshold * distance:
            continue
        if state.node.pos == 26 or _visible(fight, origin, target):
            found.append((distance, target.entity))
    return tuple(expr.Entity(entity) for _, entity in sorted(found))


def effects(world, state):
    if state.instance.graph.index != ROOT or state.node.pos != 11 or world.hero != MCCREE:
        return NotImplemented
    owner = _owner(world, state.instance)
    definition = graph.graph(TARGET_EFFECT)
    if owner is None or definition is None:
        state.clear_children()
        return True
    component = state.instance.component
    live = {target.entity for target in world.match.combat._enemies(owner)}
    slot = component.entity_var(2551, create=False)
    selected = slot.value() if slot is not None else ()
    selected = selected if isinstance(selected, tuple) else ()
    desired = {expr.to_entity(value) for value in selected} & live
    for entity in tuple(state.children):
        if entity not in desired:
            child = component.instances.get(state.children.pop(entity))
            if child is not None:
                component.destroy(child)
    for value in selected:
        entity = expr.to_entity(value)
        if entity not in desired:
            continue
        child = component.instances.get(state.children.get(entity))
        if child is None:
            child = component.create(definition, values={(expr.INSTANCE, 536): expr.Entity(entity)})
            child.set_source_entity(owner.body)
            state.children[entity] = child.id
        runtime.send_instance_message(child, REAPPLY, {92: expr.Entity(entity)}, owner.body)
    return True


def native(world, cfg, instance):
    if (instance is None or instance.graph.index != TARGET_EFFECT
            or cfg.get("$") != "STU_9B7648C3" or world.hero != MCCREE):
        return NotImplemented
    owner = _owner(world, instance)
    if owner is None:
        return 0.0
    fight = world.match.combat
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    target = next((target for target in fight._enemies(owner) if target.entity == entity), None)
    # Bind the aggregate getter to the authoritative health, armour and shield pools.
    return expr.f32(fight.vitals_of(target).total) if target is not None else 0.0


def shot_aim(fight, shooter, state):
    if (shooter.hero.guid != MCCREE or shooter.body_script is None or state.node.pos != 14
            or state.instance.component is not shooter.body_script.component):
        return None
    slot = state.instance.vars.get(536)
    entity = expr.to_entity(slot.value()) if slot is not None else 0
    visible = state.instance.component.entity_var(5874, create=False)
    if visible is None or expr.Entity(entity) not in (visible.value() or ()):
        return None
    target = next((target for target in fight._enemies(shooter) if target.entity == entity), None)
    origin = fight._eye(shooter)
    if target is None or not _visible(fight, origin, target):
        return None
    offset = tuple(b - a for a, b in zip(origin, _target_point(target), strict=True))
    distance = math.hypot(*offset)
    if distance <= 0:
        return None
    return origin, tuple(value / distance for value in offset)
