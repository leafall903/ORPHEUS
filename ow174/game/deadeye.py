"""McCree Deadeye lock and target-effect bridge for 104319."""

import math

from ow174.game.mccree_fan import HERO
from ow174.game.script import expr, graph, runtime

ULTIMATE, TARGET_EFFECT = 0x022F, 0x01EE
REAPPLY = 0x024000000000000F


def _owner(world, state):
    match = world.match
    if match is None or match.ended or world.hero != HERO or state.instance.component.entity != world.body:
        return None
    return next((p for p in match.players if p.body == world.body and p.spawned and not p.gone
                 and p.client is not None and not match.combat.is_dead(p)), None)


def _living(match):
    return {t.entity: t for t in match.combat._targets() if not match.combat.vitals_of(t).dead}


def target_point(target):
    """Reconstruct hardpoint1 inside the actual capsule, including short NPCs."""
    _, upper = target.segment()
    return target.feet[0], min(target.feet[1] + target.eye, upper[1]), target.feet[2]


def targets(world, state):
    if state.instance.graph.index != ULTIMATE or state.node.pos not in (26, 86):
        return NotImplemented
    owner = _owner(world, state)
    if owner is None or world.match.collision_world is None:
        return ()
    if graph.graph(TARGET_EFFECT) is None:
        expr.warn_once('deadeye_effect', 'Deadeye requires external target-effect graph 01EE')
        return ()
    from ow174.game import combat

    fight = world.match.combat
    origin = fight._eye(owner)
    forward = combat.aim(owner.mover.yaw, owner.mover.pitch)
    reach = expr.to_float(state.evaluate(state.node.field('m_radius'), watch=False))
    # Reconstructed geometry: authored105 degrees is interpreted as the full cone.
    angle = expr.to_float(state.evaluate(state.node.field('m_A30B383E'), watch=False))
    threshold = math.cos(math.radians(angle * 0.5))
    found = []
    for target in fight._enemies(owner):
        point = target_point(target)
        offset = tuple(b - a for a, b in zip(origin, point, strict=True))
        distance = math.sqrt(sum(value * value for value in offset))
        if distance <= 0 or distance > reach:
            continue
        if sum(a * b for a, b in zip(forward, offset, strict=True)) / distance < threshold:
            continue
        a, b = target.segment()
        if state.node.pos == 26 or combat.capsule_visible(
                world.match.collision_world, origin, a, b, target.radius, walls=fight.walls):
            found.append((distance, target.entity))
    return tuple(entity for _, entity in sorted(found))


def effects(world, state):
    if state.instance.graph.index != ULTIMATE or state.node.pos != 11 or world.hero != HERO:
        return NotImplemented
    owner = _owner(world, state)
    definition = graph.graph(TARGET_EFFECT)
    if owner is None or definition is None:
        state.clear_children()
        return True
    component = state.instance.component
    live = _living(world.match)
    slot = component.entity_var(2551, create=False)
    selected = slot.value() if slot is not None else ()
    selected = selected if isinstance(selected, tuple) else ()
    desired = {expr.to_entity(value) for value in selected}
    for entity, number in list(state.children.items()):
        if entity not in live or entity not in desired:
            child = component.instances.get(number)
            if child is not None:
                component.destroy(child)
            del state.children[entity]
    for value in selected:
        entity = expr.to_entity(value)
        if entity not in live:
            continue
        child = component.instances.get(state.children.get(entity))
        if child is None:
            child = component.create(definition, values={(expr.INSTANCE, 536): expr.Entity(entity)})
            child.set_source_entity(owner.body)
            state.children[entity] = child.id
        runtime.send_instance_message(child, REAPPLY, {92: expr.Entity(entity)}, owner.body)
    return True


def native(world, cfg, instance):
    if (world.hero != HERO or world.match is None or instance is None or instance.graph.index != TARGET_EFFECT
            or cfg.get('$') != 'STU_9B7648C3'):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    target = _living(world.match).get(entity)
    # Reconstruct the native component51 aggregate getter from authoritative pools.
    return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0


def shot_aim(fight, shooter, state):
    """Use01EE's target536 direction instead of the command's crosshair direction."""
    from ow174.game import combat

    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component):
        return None
    slot = state.instance.vars.get(536)
    entity = expr.to_entity(slot.value()) if slot is not None else 0
    target = next((target for target in fight._enemies(shooter) if target.entity == entity), None)
    selected = state.instance.component.entity_var(5874, create=False)
    targets = selected.value() if selected is not None else ()
    if target is None or not isinstance(targets, tuple) or expr.Entity(entity) not in targets:
        return None
    origin = fight._eye(shooter)
    point = target_point(target)
    offset = tuple(b - a for a, b in zip(origin, point, strict=True))
    distance = math.sqrt(sum(value * value for value in offset))
    if distance <= 0:
        return None
    a, b = target.segment()
    if not combat.capsule_visible(fight._collision(), origin, a, b, target.radius, walls=fight.walls):
        return None
    return origin, tuple(value / distance for value in offset)
