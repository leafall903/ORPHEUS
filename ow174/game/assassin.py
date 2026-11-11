"""Manual1157 target/pin bridge; contact and damage clock are reconstructed.

This supplies explicit sandbox context, never a native controller/component
predicate. Autonomous targeting, native collision and animation rules are open.
"""

import math

from ow174.game.script import expr, runtime

HERO, GRAPH = 0x02E00000000001BB, 0x1157
PIN_RANGE = 3.0  # manual contact policy, not a recovered native range


def scoped(world, instance):
    return (world.hero == HERO and world.manual_control
            and instance is not None and instance.graph.index == GRAPH)


def bind(world, component, target):
    """Release old control before an explicit stop or target/body replacement."""
    if (world.hero != HERO or not world.manual_control or world.manual_target == target):
        return
    world.manual_target = target
    if world.match is not None:
        world.match.combat.pins.pop(component.entity, None)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # Release Anim31 has no resolved duration. A new manual engagement
        # interrupts it through its authored exit rather than inventing a timer.
        for position in ((23, 12, 31) if target is not None else (23, 12)):
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(36, False)])


def native(world, cfg, instance):
    if not scoped(world, instance) or cfg.get('$') not in ('STU_DB742151', 'STU_9B7648C3'):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    if cfg.get('$') == 'STU_DB742151' and entity == instance.component.entity:
        # Data-only possessor map: no synthetic entity or replicated component.
        target = expr.Entity(world.manual_target) if world.manual_target is not None else None
        return expr.Map(((1650, target),))
    if cfg.get('$') == 'STU_9B7648C3' and world.match is not None:
        # Self health remains BotWorld's authoritative OWNER_HEALTH input,
        # including bootstrap before this bot joins match.bots.
        if world.manual_target is None or entity != world.manual_target:
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
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return None
    entity = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if entity != world.manual_target:
        return None
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if (target is None or fight.vitals_of(target).dead or getattr(target.owner, 'client', None) is None
            or target.owner.gone or target.owner.entity != source.attack_target):
        return None
    from ow174.game import combat, defenses

    # Scoped host policy, not recovered general native pin immunity.
    if defenses.invulnerable(target.owner) or defenses.fortify_scale(target.owner) is not None:
        return None
    origin = (source.position[0], source.position[1] + combat.EYE, source.position[2])
    a, b = target.segment()
    if (math.dist(origin, fight._eye(target.owner)) > PIN_RANGE
            or not combat.capsule_visible(match.collision_world, origin, a, b, target.radius)):
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
    settings = state.node.field('m_modifyHealth') or {}
    amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
    # Resource amounts, delay and state lifetime; reconstructed mode1 rate clock.
    damage = amount * elapsed_ms / 1000.0 if mode == 1 else (amount if elapsed_ms == 0 else 0.0)
    if math.isfinite(damage) and damage > 0:
        if fight.zarya_barriers.intercept_target(source, target.owner, damage):
            # The same engagement cannot apply its delayed rate after a blocked
            # contact, even if that contact exhausted the shield.
            end_health(world, state, force=True)
            for position in (23, 12):
                node = state.instance.graph.node(position)
                scope = state.instance.states.get(node.state)
                if scope is not None and scope.active:
                    scope.request(runtime.ABORT)
            return True
        if fight.pins.get(source.entity) != target.entity:
            fight.pins[source.entity] = target.entity
            fight._interrupt(target)
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
    if world.hero != HERO or world.match is None or component.entity not in world.match.combat.pins:
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(23)
            pin = instance.states.get(node.state) if node is not None else None
            if pin is not None and pin.active and not pin.ending:
                return
    world.match.combat.pins.pop(component.entity, None)
