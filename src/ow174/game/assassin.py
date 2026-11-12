"""Manual1157 pin bridge; target context, contact and damage clock are reconstructed."""

import math

from ow174.game import immunity
from ow174.game.script import expr, runtime, message_state, play_queue, scopes, voice_stimulus
from ow174.game.script.graph import graph

HERO, GRAPH, BUTTON = 0x02E00000000001BB, 0x1157, 36
VICTIM_GRAPH, PIN_MESSAGE = 0x1177, 0x02400000000008FE
PIN_EFFECT = 0x0580000000001177
PIN_RANGE = 3.0  # manual contact policy, not recovered native acquisition distance


def manual(world):
    return getattr(world, "hero", None) == HERO and world.controller_components is not None


def scoped(world, instance):
    return (world.hero == HERO and world.controller_components is not None
            and instance is not None and instance.graph.index == GRAPH)


def bind(world, component, target, *, dying=False):
    """Retire the old pin before a stop, interruption or target/body replacement."""
    if (getattr(world, 'hero', None) != HERO or getattr(world, 'controller_components', None) is None
            or (world.manual_target == target and not dying)):
        return
    world.manual_target = target
    if world.match is not None:
        world.match.combat.release_pin(component.entity)
    for instance in list(component.instances.values()):
        if instance.graph.index != GRAPH:
            continue
        # Ending the pin queues the ordinary release animation. A corpse
        # takes the death pose instead, even if it was already releasing.
        positions = (23, 12, 31) if target is not None or dying else (23, 12)
        for position in positions:
            node = instance.graph.node(position)
            state = instance.states.get(node.state) if node is not None else None
            if state is not None:
                if dying:
                    instance.drop_begins([state])
                state.end(False)
        instance.write((expr.INSTANCE, 536), 0)
    component.edges([(36, False)])


def native(world, cfg, instance):
    if not scoped(world, instance) or cfg.get('$') not in {'STU_DB742151', 'STU_9B7648C3'}:
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


def contact(match, source, target):
    from ow174.game import npc_navigation
    from ow174.game.mover import mover_data

    data = mover_data(source.body)
    return (not match.combat.junkrat.rooted(source.entity, match.tick)
            and npc_navigation.pin_contact(match.collision_world, source.position, target, data)
            and npc_navigation.teammate_clear(match, source, source.position, target.feet, data))


def attach_victim(source, target):
    """Use the authored 1157/45 graph and 1157/18 message for the active pin."""
    script = getattr(target.owner, 'body_script', None)
    definition = graph(VICTIM_GRAPH) if script is not None else None
    if definition is None or script.component.entity != target.entity:
        return
    component = script.component
    victim_script = component.play_loaded_graph(definition, source=source, instigator=source, policy=1)
    component.run_now()
    if runtime.send_instance_message(victim_script, PIN_MESSAGE, {}, source):
        # A confirmed manual pin starts immediately. The receiver's waiting
        # branch otherwise aborts its release animation into the graph's
        # self-stop on the first 08FE, before the pin animation can start.
        release = victim_script.node_state(definition.node(4))
        if release is not None and release.active:
            victim_script.exit_subgraph(release.node, 'm_subgraphPlug')
            victim_script.teardown = True
            try:
                release.end(False)
            finally:
                victim_script.teardown = False
        component.run_now()
    else:
        component.destroy(victim_script)


def detach_victim(match, source, body):
    actor = next((bot for bot in match.bots if bot.entity == source), None)
    context = getattr(getattr(actor, 'body_script', None), 'control_context', None)
    if context is not None:
        retire_pin_effect(context.target_host, source)
    for player in match.players:
        script = player.body_script
        if script is None or script.component.entity != body:
            continue
        component = script.component
        for instance in list(component.instances.values()):
            if instance.graph.index == VICTIM_GRAPH and instance.source_entity() == source:
                component.destroy(instance)
        break
# Wraith Form, Fade, and Cryo-Freeze protect against the pin itself.
PIN_EXCLUDED_PHASES = frozenset((0x0020, 0x0F7C, 0x05FE))


def victim(world, state):
    match = world.match
    if match is None or match.ended:
        return None
    fight = match.combat
    source = next((b for b in match.bots if b.entity == state.instance.component.entity), None)
    if (source is None or source.dead or not source.firing or fight.is_stunned(source)
            or source.body_script is None or source.body_script.component is not state.instance.component):
        return None
    selector = state.node.field('m_FA3ECC43')
    if selector is None and state.node.pos == 23:
        selector = {'$': 'STUConfigVarDynamic', 'm_identifier': 536, 'm_60DB8F99': 0}
    entity = expr.to_entity(state.evaluate(selector, watch=False))
    if entity != world.manual_target:
        return None
    target = next((t for t in fight._enemies(source) if t.entity == entity), None)
    if (target is None or getattr(target.owner, 'client', None) is None or target.owner.gone
            or target.owner.entity != source.attack_target):
        return None
    if (fight._invulnerable(target.owner, phases=PIN_EXCLUDED_PHASES)
            or immunity.control_immune(target.owner, target.entity)
            or not contact(match, source, target)):
        return None
    return source, target


def modify_health(world, state, elapsed_ms):
    context = getattr(world, 'control_context', None)
    if context is not None and context.target_host is not None:
        return controller_modify_health(world, state, elapsed_ms)
    if not scoped(world, state.instance) or state.node.pos not in (15, 25):
        return NotImplemented
    pair = victim(world, state)
    if pair is None:
        end_health(world, state, force=True)
        return True
    source, target = pair
    fight = world.match.combat
    if fight.barriers.for_target(target.owner) is not None:
        fight.release_pin(source.entity)
    elif fight.pins.get(source.entity) != target.entity:
        fight.pins[source.entity] = target.entity
        fight._interrupt(target)
        attach_victim(source.entity, target)
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


def refresh_control(world, component):
    context = getattr(world, 'control_context', None)
    if context is not None and context.target_host is not None:
        controller_refresh_control(world, component)
        return
    if (getattr(world, 'hero', None) != HERO or world.match is None
            or world.controller_components is None):
        return
    fight = world.match.combat
    if fight.junkrat.rooted(component.entity, fight.tick):
        fight.release_pin(component.entity)
        return
    for instance in component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        node = instance.graph.node(23)
        pin = instance.states.get(node.state) if node is not None else None
        if pin is None or not pin.active or pin.ending:
            continue
        pair = victim(world, pin)
        if pair is None:
            continue
        source, target = pair
        if fight.barriers.for_target(target.owner) is not None:
            continue
        if fight.pins.get(source.entity) != target.entity:
            fight.release_pin(source.entity)
            fight.pins[source.entity] = target.entity
            fight._interrupt(target)
            attach_victim(source.entity, target)
        return
    fight.release_pin(component.entity)


def rescue_with_melee(fight, rescuer, target):
    """A sandbox teammate's quick melee interrupts the active pin."""
    actor = target.owner
    victim = fight.pins.get(target.entity)
    if victim is None or fight.match.game_map.free_for_all or fight.vitals_of(target).dead:
        return
    if not any(player.body == victim and player is not rescuer and player.team == rescuer.team
               and not player.gone for player in fight.match.players):
        return
    script = getattr(actor, "body_script", None)
    if script is None or actor.hero != HERO or actor.spawner.get("manual_spawn") is not True:
        return
    for instance in script.component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        recovery = instance.graph.node(37)
        if recovery is None:
            return
        duration = expr.to_float(instance.evaluate(recovery.field("m_timeout")))
        if not math.isfinite(duration) or duration <= 0:
            return
        from ow174.game import combat

        deadline = fight.tick + math.ceil(duration / combat.TICK)
        fight.stuns[target.entity] = max(deadline, fight.stuns.get(target.entity, 0))
        fight._interrupt(target)
        return


def recovery_duration(world, state):
    """Declared recovery timing from the paired authored Wait37, not clip metadata.

    Anim31 has no explicit duration. Restrict this compatibility service to its
    actual alias561; the2-second enclosing recovery wait supplies our policy.
    """
    if not scoped(world, state.instance) or state.node.pos != 31:
        return NotImplemented
    alias = state.evaluate(state.node.field('m_11E0A658'), watch=False)
    if alias != expr.Asset(0x0C80000000000561):
        return NotImplemented
    wait = state.instance.graph.node(37)
    if wait is None or wait.cls != 'STUStatescriptStateWait':
        return NotImplemented
    return expr.to_float(state.evaluate(wait.field('m_timeout'), watch=False))


def recovering(world, component):
    """Delay a new manual down edge until old recovery has cleared its target."""
    for instance in component.instances.values():
        if scoped(world, instance):
            state = instance.states.get(23)  # actual node31 recovery animation
            if state is not None and state.active:
                return True
    return False


def release_victim(fight, entity):
    sources = {source for source, victim in fight.pins.items() if victim == entity}
    for bot in fight.match.bots:
        if bot.entity not in sources or bot.hero != HERO or bot.body_script is None:
            continue
        component = bot.body_script.component
        bind(component.world, component, None)
        bot.attack_input = False



def acquire(fight, bot, player):
    from ow174.game import npc_control

    script = bot.body_script
    if script is None or not manual(script.component.world):
        return False
    world = script.component.world
    if player is None or not player.has_body or not player.spawned:
        stop(bot)
        return False
    if world.manual_target not in (None, player.body):
        stop(bot)
        return False
    if world.manual_target is None and recovering(world, script.component):
        return False
    bot.attack_target = player.entity
    return npc_control.update_input(fight, bot)


def engage(match, bot, player):
    if match.ended or bot not in match.bots or player not in match.players:
        return False
    return acquire(match.combat, bot, player)


def selected_contact(fight, bot, entity):
    if bot.dead or fight.is_stunned(bot):
        return None
    target = next((target for target in fight._enemies(bot) if target.entity == entity), None)
    if (target is None or getattr(target.owner, "client", None) is None or target.owner.gone
            or not contact(fight.match, bot, target)):
        return None
    return target


def stop(bot):
    component = getattr(bot.body_script, "component", None)
    if component is not None and manual(component.world):
        bind(component.world, component, None)
        bot.attack_input = False
        bot.attack_target = None


def refresh(fight, bot):
    component = getattr(bot.body_script, "component", None)
    if component is not None and manual(component.world):
        refresh_control(component.world, component)


def retire_pin_effect(host, source):
    """Reconstructed manual release policy for submitted effects."""
    if host is None or host.retired or host.component.fault is not None:
        return
    component = host.component
    for instance in tuple(component.instances.values()):
        if (instance.graph.index == (PIN_EFFECT & 0xFFFFFFFFFFFF) and instance.auto_retire
                and instance.cached_source_entity == source):
            component.destroy(instance)


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
    definition = graph(PIN_EFFECT & 0xFFFFFFFFFFFF)
    if definition is None:
        return False
    host.validate_namespace()
    queue = play_queue.Queue(host.component, lambda guid: play_queue.Load("ready", definition))
    queue.submit(request)
    host.validate_namespace()
    return True


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


def controller_modify_health(world, state, elapsed_ms):
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
                match.collision_world, origin, a, b, target.radius, walls=fight.walls)):
        return False
    state.target_body = target.entity
    # Reconstructed clock. Treat node15's amount as a rate and node25's as
    # an entry amount, following the manual pin policy.
    damage = amount * elapsed_ms / 1000 if mode == 1 else amount
    if damage:
        if fight.pins.get(source.entity) != target.entity:
            fight.release_pin(source.entity)
            fight.pins[source.entity] = target.entity
            fight._interrupt(target)
        fight._damage(source, target, expr.f32(damage), False, None, fight.tick)
    return mode == 1


def controller_refresh_control(world, component):
    """Retain existing body-control bookkeeping without a second body1177."""
    context = world.control_context
    fight = world.match.combat
    instance = next((item for item in component.instances.values() if item.graph.index == GRAPH), None)
    pin = instance.states.get(instance.graph.node(23).state) if instance is not None else None
    player = target_player(world, instance, context.target_entity) if instance is not None else None
    contact = instance.states.get(instance.graph.node(25).state) if instance is not None else None
    if (pin is None or not pin.active or pin.ending or player is None
            or fight.junkrat.rooted(component.entity, fight.tick)
            or (contact is not None and contact.target_body not in (0, player.body))):
        fight.release_pin(component.entity)
        retire_pin_effect(context.target_host, component.entity)
