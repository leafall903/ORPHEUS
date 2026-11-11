"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math

from ow174.game.script import expr, graph, runtime

HERO, GRAPH = 0x02E00000000001BB, 0x1157
VICTIM_GRAPH, PIN_MESSAGE = 0x1177, 0x02400000000008FE
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
    if world.match is not None:
        world.match.combat.release_pin(component.entity)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # A new explicit manual engagement interrupts an old release through
        # its authored exits.
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


def attach_victim(source, player):
    """Start the victim graph referenced by 1157 and deliver its pin message."""
    script = player.body_script
    definition = graph.graph(VICTIM_GRAPH) if script is not None else None
    if definition is None or script.component.entity != player.body:
        return
    component = script.component
    release_node = definition.node(4)
    for prior in list(component.instances.values()):
        if prior.graph.index != VICTIM_GRAPH:
            continue
        release = prior.states.get(release_node.state) if release_node is not None else None
        if release is None or not release.active:
            return  # Concurrent pins share one active victim presentation.
        component.destroy(prior)
    instance = component.play_loaded_graph(definition, source=source, instigator=source, policy=1)
    component.run_now()
    if instance.id not in component.instances:
        return
    if not runtime.send_instance_message(instance, PIN_MESSAGE, {}, source):
        component.destroy(instance)
        return
    # The compatibility receiver enters the waiting branch before the first
    # packet. Its temporary release animation must not self-stop the graph
    # when the confirmed pin arrives and the receiver exits that branch.
    release = instance.states.get(release_node.state) if release_node is not None else None
    if release is not None and release.active:
        instance.exit_subgraph(release.node, 'm_subgraphPlug')
        instance.teardown = True
        try:
            release.end(False)
        finally:
            instance.teardown = False
    component.run_now()


def detach_victim(match, source, body, *, animate=False, next_source=None):
    for player in match.players:
        script = player.body_script
        if script is None or script.component.entity != body:
            continue
        component = script.component
        for instance in list(component.instances.values()):
            if (instance.graph.index != VICTIM_GRAPH
                    or (source is not None and instance.source_entity() != source)):
                continue
            if next_source is not None:
                instance.set_source_entity(next_source)
                continue
            if animate and _begin_release(instance):
                continue
            component.destroy(instance)
        break


def _begin_release(instance):
    scope_node = instance.graph.node(16)
    release_node = instance.graph.node(4)
    if scope_node is None or release_node is None:
        return False
    scope = instance.states.get(scope_node.state)
    release = instance.node_state(release_node)
    if scope is None or not scope.active or release is None:
        return False
    scope.end(False)
    release.request(runtime.BEGIN)
    instance.component.run_now()
    return True


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
    if (target is None or getattr(target.owner, 'client', None) is None or target.owner.gone
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
    if fight.pins.get(source.entity) != target.entity:
        fight.release_pin(source.entity)
        first = target.entity not in fight.pins.values()
        fight.pins[source.entity] = target.entity
        if first:
            fight._record_command_control(target, PIN_MESSAGE, None)
            fight._interrupt(target.owner, impact_frame=fight.tick)
            # Install the victim graph at the pin boundary so its stack
            # cannot block commands that still precede the pin.
            script = target.owner.body_script
            if script is not None and script.frame >= fight.tick - 1:
                attach_victim(source.entity, target.owner)
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
        world.match.combat.release_pin(state.instance.component.entity)


def rescue_melee(fight, source, rescuer):
    """End the authored pin scope when an ally lands a quick melee hit."""
    body = fight.pins.get(source.entity)
    if body is None or source.body_script is None:
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
                return
    world.match.combat.release_pin(component.entity)
