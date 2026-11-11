"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math

from ow174.game.script import expr

HERO, GRAPH = 0x02E00000000001BB, 0x1157
PIN_RANGE = 3.0  # manual contact policy, not recovered native acquisition distance


def scoped(world, instance):
    return (world.hero == HERO and world.controller_components is not None
            and instance is not None and not instance.component.strict and instance.graph.index == GRAPH)


def bind(world, component, target):
    """Retire the old pin before a stop, interruption or target/body replacement."""
    if (getattr(world, 'hero', None) != HERO or getattr(world, 'controller_components', None) is None
            or world.manual_target == target):
        return
    world.manual_target = target
    if world.match is not None:
        world.match.combat.pins.pop(component.entity, None)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # The release Anim31 has no resolved asset duration in this runtime.
        # A new explicit manual engagement interrupts it through authored exits.
        positions = (23, 12, 31) if target is not None else (23, 12)
        for position in positions:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(36, False)])


def native(world, cfg, instance):
    if not scoped(world, instance):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    if cfg.get('$') == 'STU_DB742151' and entity == instance.component.entity:
        # Explicit data-only possessor context; no synthetic controller entity is
        # sent to the client or aliased to an existing body's namespace.
        target = expr.Entity(world.manual_target) if world.manual_target is not None else None
        return expr.Map(((1650, target),))
    if cfg.get('$') == 'STU_9B7648C3' and entity is not None and world.match is not None:
        if entity not in (instance.component.entity, world.manual_target):
            return NotImplemented
        target = next((t for t in world.match.combat._targets() if t.entity == entity), None)
        return expr.f32(world.match.combat.vitals_of(target).total) if target is not None else 0.0
    return NotImplemented


def victim(world, state):
    match = world.match
    if match is None or match.ended:
        return None
    fight = match.combat
    source = next((b for b in match.bots if b.entity == state.instance.component.entity), None)
    if (source is None or source.dead or not source.firing or fight.is_stunned(source)
            or source.controlled or fight.punches.controlled(source.entity)
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return None
    entity = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if entity != world.manual_target:
        return None
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if (target is None or getattr(target.owner, 'client', None) is None or target.owner.gone
            or target.owner.entity != source.attack_target):
        return None
    from ow174.game import combat

    origin = (source.position[0], source.position[1] + combat.eye_height(source.body), source.position[2])
    point = fight._eye(target.owner)
    distance = math.dist(origin, point)
    a, b = target.segment()
    if distance > PIN_RANGE or not combat.capsule_visible(match.collision_world, origin, a, b, target.radius):
        return None
    return source, target


def modify_health(world, state, elapsed_ms):
    if not scoped(world, state.instance) or state.node.pos not in (15, 25):
        return NotImplemented
    pair = victim(world, state)
    if pair is None:
        end_health(world, state, force=True)
        return True
    source, target = pair
    fight = world.match.combat
    if fight.pins.get(source.entity) != target.entity:
        fight.pins[source.entity] = target.entity
        fight._interrupt(target)
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
    # Reconstructed state clock: mode1 is damage/second; default mode applies
    # once on entry. Original1157 supplies amounts, delay and scope lifetime.
    damage = amount * elapsed_ms / 1000.0 if mode == 1 else (amount if elapsed_ms == 0 else 0.0)
    if math.isfinite(damage) and damage > 0:
        fight._damage(source, target, expr.f32(damage), False, None, fight.tick)
    return True


def end_health(world, state, *, force=False):
    if not scoped(world, state.instance) or world.match is None:
        return
    node = state.instance.graph.node(23)
    pin = state.instance.states.get(node.state) if node is not None else None
    if force or pin is None or not pin.active or pin.ending:
        world.match.combat.pins.pop(state.instance.component.entity, None)


def refresh_control(world, component):
    if (getattr(world, 'hero', None) != HERO or world.match is None
            or component.entity not in world.match.combat.pins):
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(23)
            pin = instance.states.get(node.state) if node is not None else None
            if pin is not None and pin.active:
                return
    world.match.combat.pins.pop(component.entity, None)
