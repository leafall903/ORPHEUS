"""Scoped manual Assassin health delivery for authored graph 1157."""

import math

from ow174.game.script import expr, graph, message_state, play_queue, scopes, voice_stimulus

PIN_RANGE = 3.0  # Reconstructed manual contact policy.
HERO = 0x02E00000000001BB
RELEASE_SECONDS = 1.4666667  # Manifest-verified 508A.006 header.
PIN_EFFECT = 0x0580000000001177


def effect_host(world, instance, node):
    """Resolve the explicitly provisioned controller for the authored pin request."""
    context = world.control_context
    if (world.hero != HERO or context is None or context.retired
            or instance.component is not context.body_component
            or instance.graph.index != 0x1157 or node.pos != 45):
        return None
    host = context.target_host
    if host is None or host.retired or host.component.fault is not None:
        return None
    component = host.component
    inputs = host.filter_inputs
    services = component.world
    if (inputs is None or inputs.retired or inputs.owner != component.entity
            or services.linked_target_sample(component.entity) is NotImplemented
            or inputs.has_component(51)):
        return None
    registry = services.subgraph_scopes(component.entity)
    voice = services.voice_context(component.entity)
    messages = services.message_state_context(component.entity, component=component)
    if ((registry is not None and type(registry) not in (scopes.Registry, scopes.ForwardHost))
            or (type(registry) is scopes.ForwardHost and registry.component is not component)
            or (voice is not None and (type(voice) is not voice_stimulus.Context
                                       or voice.retired or voice.component is not component))
            or type(messages) is not message_state.Context or messages.component is not component
            or messages.retired or messages.host.manager.retired):
        return None
    return host


def submit_pin_effect(world, instance, node, request):
    host = effect_host(world, instance, node)
    if (host is None or request.resource.guid != PIN_EFFECT
            or request.targets != (host.component.entity,)
            or request.context.instigator != instance.component.entity
            or (request.policy, request.wire_flag) != (0, 0)):
        return False
    definition = graph.graph(PIN_EFFECT & 0xFFFFFFFFFFFF)
    if definition is None:
        return False
    host.validate_namespace()
    queue = play_queue.Queue(host.component, lambda guid: play_queue.Load("ready", definition))
    queue.submit(request)
    host.validate_namespace()
    return True


def retire_pin_effect(host, source):
    """Reconstructed manual release policy for submitted effects."""
    if host is None or host.retired or host.component.fault is not None:
        return
    component = host.component
    for instance in tuple(component.instances.values()):
        if (instance.graph.index == (PIN_EFFECT & 0xFFFFFFFFFFFF) and instance.auto_retire
                and instance.cached_source_entity == source):
            component.destroy(instance)


def release_duration(world, state):
    """Use the authored release clip as the reconstructed manual completion clock."""
    context = world.control_context
    if (context is None or context.retired or state.instance.component is not context.body_component
            or state.instance.graph.index != 0x1157 or state.node.pos != 31):
        return None
    alias = state.node.field("m_11E0A658")
    category = state.node.field("m_22326315")
    if (expr.guid_of((alias or {}).get("m_animAlias")) == 0x0C80000000000561
            and expr.guid_of((category or {}).get("m_category")) == 0x0280000000000082):
        return RELEASE_SECONDS
    return None


def _pin_instance(bot):
    if bot.hero != HERO or bot.body_script is None:
        return None
    return next((i for i in bot.body_script.component.instances.values()
                 if i.graph.index == 0x1157), None)


def pin_button(bot):
    """Read the authored logical input from the mounted pin graph."""
    instance = _pin_instance(bot)
    if instance is None:
        return None
    value = instance.graph.node(3).field("m_logicalButton")
    if value and value.get("$") == "STUConfigVarLogicalButton":
        return value["m_logicalButton"]
    return None


def holds_pin(bot, target):
    """Keep manual input held for the body already contacted by this pin."""
    instance = _pin_instance(bot)
    if instance is None or instance.stopped or instance.teardown:
        return False
    pin = instance.states.get(instance.graph.node(23).state)
    contact = instance.states.get(instance.graph.node(25).state)
    return bool(pin is not None and pin.active and not pin.ending
                and contact is not None and contact.start >= pin.start
                and contact.target_body == target.body
                and expr.to_entity(instance.vars[536].value()) == target.entity)


def selected_controller(context):
    if context is None or context.retired:
        return 0
    root = context.controller_component
    slot = root.entity_var(1650, create=False) if root is not None and root.fault is None else None
    return expr.to_entity(slot.value()) if slot is not None else 0


def target_player(world, instance, controller):
    context = world.control_context
    if (world.match is None or context is None or context.retired
            or instance.graph.index != 0x1157 or instance.component is not context.body_component
            or instance.component.world is not world or instance.stopped or instance.teardown
            or instance.component.instances.get(instance.id) is not instance
            or controller != context.target_entity):
        return None
    host = context.target_host
    if (host is None or host.retired or host.component.fault is not None
            or host.component.entity != controller):
        return None
    return next((p for p in world.match.players if p.entity == controller
                 and p.controller_vm is host and p.spawned and p.has_body and not p.gone), None)


def target_health(world, cfg, instance):
    if cfg.get("$") != "STU_9B7648C3" or instance.graph.index != 0x1157:
        return NotImplemented
    controller = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    player = target_player(world, instance, controller)
    if player is None:
        return NotImplemented
    fight = world.match.combat
    target = next((item for item in fight._targets() if item.owner is player), None)
    return expr.f32(fight.vitals_of(target).total) if target is not None else 0.0


def modify_health(world, state, elapsed_ms):
    instance = state.instance
    component = instance.component
    context = world.control_context
    if (instance.graph.index != 0x1157 or state.node.pos not in (15, 25)
            or context is None or component is not context.body_component):
        return NotImplemented
    match = world.match
    fight = match.combat
    source = next((bot for bot in match.bots if bot.entity == component.entity), None)
    controller = expr.to_entity(state.evaluate(state.node.field("m_FA3ECC43"), watch=False))
    settings = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(settings.get("m_amount"), watch=False))
    mode = expr.to_int(state.evaluate(state.node.field("m_95F1FB0B"), watch=False))
    if mode != (1 if state.node.pos == 15 else 0) or not math.isfinite(amount) or amount < 0:
        return NotImplemented
    if mode == 0 and selected_controller(context) != controller:
        return False
    player = target_player(world, instance, controller)
    if (match.ended or context.retired or source is None or source.dead or not source.firing
            or not source.attack_input or fight.is_stunned(source) or source.body_script is None
            or source.body_script.component is not component or instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance or component.fault is not None
            or controller != source.attack_target or player is None or player.client is None):
        return False
    target = next((item for item in fight._enemies(source) if item.owner is player), None)
    if target is None or state.target_body not in (0, target.entity):
        return False
    pin = instance.states.get(instance.graph.node(23).state)
    if pin is None or not pin.active or pin.ending:
        return False
    if mode == 1 and not state.target_body:
        # The delayed rate belongs to the same body hit by this pin's entry.
        contact = instance.states.get(instance.graph.node(25).state)
        if contact is None or contact.start < pin.start or contact.target_body != target.entity:
            return False
    from ow174.game import combat

    origin = (source.position[0], source.position[1] + combat.EYE, source.position[2])
    a, b = target.segment()
    if (math.dist(origin, fight._eye(player)) > PIN_RANGE or match.collision_world is None
            or not combat.capsule_visible(
                match.collision_world, origin, a, b, target.radius, ice_walls=fight.ice_walls)):
        return False
    state.target_body = target.entity
    # Reconstructed clock. Treat node15's amount as a rate and node25's as
    # an entry amount, following the manual pin policy.
    damage = amount * elapsed_ms / 1000 if mode == 1 else amount
    if damage:
        fight._damage(source, target, expr.f32(damage), False, None, fight.tick)
    return mode == 1
