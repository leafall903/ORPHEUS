"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math

from ow174.game.script import expr

HERO, GRAPH = 0x02E00000000001BB, 0x1157
PIN_RANGE = 3.0  # manual contact policy, not recovered native acquisition distance


def scoped(world, instance):
    return (world.hero == HERO and world.controller_components is not None
            and instance is not None and instance.graph.index == GRAPH)


def bind(world, component, target):
    """Retire the old pin before a stop, interruption or target/body replacement."""
    if (getattr(world, 'hero', None) != HERO or getattr(world, 'controller_components', None) is None
            or world.manual_target == target):
        return
    world.manual_target = target
    world.rejected_pin_attempt = None
    if world.match is not None:
        world.match.combat.pins.pop(component.entity, None)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # A new explicit manual engagement may interrupt the release.
        # Ordinary held engagement waits for the timed animation exit.
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
    if cfg.get('$') == 'STU_9B7648C3' and world.match is not None:
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
            or fight.hooks.controlled(source.entity)
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return None
    entity = expr.to_entity(state.evaluate(state.node.field('m_FA3ECC43'), watch=False))
    if entity != world.manual_target:
        return None
    target = next((t for t in fight._targets() if t.entity == entity), None)
    if (target is None or fight.vitals_of(target).dead
            or (not match.game_map.free_for_all and target.team == source.team)
            or getattr(target.owner, 'client', None) is None or target.owner.gone
            or target.owner.entity != source.attack_target):
        return None
    from ow174.game import combat

    origin = (source.position[0], source.position[1] + combat.EYE, source.position[2])
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
    node = state.instance.graph.node(23)
    pin = state.instance.states.get(node.state) if node is not None else None
    attempt = (state.instance.id, pin.counter if pin is not None else state.counter)
    if world.rejected_pin_attempt == attempt:
        end_health(world, state, force=True)
        return True
    from ow174.game import defenses

    if defenses.control_immune(target.owner):
        # Reject control for this whole authored scope, not just this tick: an
        # immune contact must not acquire a victim later through its DPS phase.
        world.rejected_pin_attempt = attempt
        settings = state.node.field('m_modifyHealth') or {}
        mode = expr.to_int(state.evaluate(state.node.field('m_95F1FB0B'), watch=False))
        amount = expr.to_float(state.evaluate(settings.get('m_amount'), watch=False))
        # Reconstructed contact policy: a fortified body still takes its one
        # reduced contact hit; solid/ghost invulnerability rejects _damage.
        if mode == 0 and elapsed_ms == 0 and math.isfinite(amount) and amount > 0:
            fight._damage(source, target, expr.f32(amount), False, None, fight.tick)
        end_health(world, state, force=True)
        return True
    if fight.pins.get(source.entity) != target.entity:
        fight.pins[source.entity] = target.entity
        fight.impulses.retire(target.entity, forget=False)
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


def rescue_melee(fight, source, rescuer):
    """Route a damaging allied quick-melee rescue through the authored pin exit."""
    body = fight.pins.get(source.entity)
    if body is None or source.body_script is None or fight.match.game_map.free_for_all:
        return
    if not any(p.body == body and p.team == rescuer.team for p in fight.match.players):
        return
    component = source.body_script.component
    for instance in component.instances.values():
        if not scoped(component.world, instance):
            continue
        pin = instance.states.get(instance.graph.node(23).state)
        if pin is not None and pin.active:
            pin.end(False)
            component.run_now()
            refresh_control(component.world, component)
            return


def releasing(component):
    """Hold the manual attack input up through the authored release animation."""
    for instance in component.instances.values():
        if scoped(component.world, instance):
            node = instance.graph.node(31)
            release = instance.states.get(node.state) if node is not None else None
            return release is not None and release.active
    return False


def refresh_control(world, component):
    if (getattr(world, 'hero', None) != HERO or world.match is None
            or component.entity not in world.match.combat.pins):
        return
    for instance in component.instances.values():
        if instance.graph.index == GRAPH:
            node = instance.graph.node(23)
            pin = instance.states.get(node.state) if node is not None else None
            if pin is not None and pin.active:
                # Control spans the authored delay too: validate its recipient
                # independently of whether a ModifyHealth state is ticking.
                from ow174.game import defenses

                health_node = instance.graph.node(25)
                health = instance.states.get(health_node.state) if health_node is not None else None
                pair = victim(world, health) if health is not None else None
                if pair is None or defenses.control_immune(pair[1].owner):
                    world.rejected_pin_attempt = (instance.id, pin.counter)
                    world.match.combat.pins.pop(component.entity, None)
                return
    world.match.combat.pins.pop(component.entity, None)
