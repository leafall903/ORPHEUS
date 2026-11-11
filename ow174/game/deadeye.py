"""Scoped host for McCree's authored per-target Deadeye effect."""

import math

from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000042
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
            or cfg.get("$") != "STU_9B7648C3"):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    target = _living(world.match).get(entity)
    return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0


def shot_aim(fight, shooter, state):
    """Use the target effect's authored body identity to aim a normal hitscan ray."""
    from ow174.game import combat

    if (shooter.hero.guid != HERO or shooter.body_script is None
            or state.instance.component is not shooter.body_script.component):
        return None
    slot = state.instance.vars.get(536)
    entity = expr.to_entity(slot.value()) if slot is not None else 0
    selected = state.instance.component.entity_var(5874, create=False)
    if selected is None or expr.Entity(entity) not in (selected.value() or ()):
        return None
    target = next((t for t in fight._enemies(shooter) if t.entity == entity), None)
    if target is None or not fight.visible_for_acquisition(target, shooter.mover.position):
        return None
    origin = fight._eye(shooter)
    lower, upper = target.segment()
    if not combat.capsule_visible(fight.match.collision_world, origin, lower, upper, target.radius):
        return None
    x, y, z = target.feet
    point = (x, min(y + target.eye, upper[1]), z)
    offset = tuple(b - a for a, b in zip(origin, point, strict=True))
    distance = math.hypot(*offset)
    return (origin, tuple(value / distance for value in offset)) if distance > 0 else None
