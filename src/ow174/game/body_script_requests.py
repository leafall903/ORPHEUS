"""Scoped requests into actual live recipient VMs using the shared PlayScript queue.

Research adapter for the original Uppercut call. It does not add a force,
timer-only stun, second movement host or a replacement correction transport.
"""
from ow174.game.script import graph, nodes, play as requests, play_queue

UPPERCUT_CALL = (0x02E000000000012F, 0x0DD8, 22)
UPPERCUT_HELPER = 0x0E7D


def npc_mover(match, bot):
    """Existing authoritative impulse/control mover, never a factory fallback."""
    mover = bot.control_mover if bot.control_mover is not None else bot.impulse_mover
    if mover is None or match.combat.knockbacks.active.get(bot.entity) is not mover:
        return None
    if bot.control_mover is not None and match.combat.knockbacks._controlled_receiver(bot) is None:
        return None
    return mover


def retire(component):
    """Retire the scoped externally played helper before its body is detached."""
    for instance in list(component.instances.values()):
        if instance.graph.index == UPPERCUT_HELPER:
            component.destroy(instance)


def queue_npc_velocity_changes(match, component, frame, source, changes):
    """Original helper scalars precede the bound launch in the existing queue."""
    from dataclasses import replace
    from ow174.game import velocity

    instance = component.instances.get(source[0])
    bot = next((b for b in match.bots if b.entity == component.entity and not b.dead
                and b.body_script is not None and b.body_script.component is component), None)
    mover = npc_mover(match, bot) if bot is not None else None
    if instance is None or instance.graph.index != UPPERCUT_HELPER or mover is None:
        return
    launches = [row for row in mover.velocity_changes if row.frame == frame + 1 and row.index == -1]
    if len(launches) != 1:
        return
    # Like the existing B.O.B. victim reset, use the accepted launch's key.
    # Numeric and vector requests occupy distinct subarrays of this one queue.
    for _slot, channel, op, operand, priority in changes:
        if 0 <= channel <= 3 and not isinstance(operand, tuple):
            change = velocity.numeric(frame + 1, channel, op, operand)
            velocity.enqueue(mover.velocity_changes, replace(change, key=launches[0].key, priority=priority))


def _pending_helpers(component):
    from ow174.game.script import runtime

    return [i for i in component.instances.values() if i.graph.index == UPPERCUT_HELPER
            and any(event.kind == runtime.ENTRY for event in i.queue)]


def activate_npc(match, bot):
    """Flush an already queued request only after canonical force bound its mover."""
    script = bot.body_script
    component = getattr(script, 'component', None)
    if (component is not None and npc_mover(match, bot) is not None
            and getattr(component.world, 'match', None) is match
            and component.fault is None and not component.owner_dead
            and _pending_helpers(component)):
        from ow174.game import bob_victim

        # Initialize the same authoritative pose inputs that advance_live
        # already samples. The helper watches actual airborne transitions.
        bob_victim.sample(bot, component, initialize=True)
        component.run_now()


def discard_unaccepted_npc(component):
    """A request left pending after its caller phase had no accepted force."""
    for instance in _pending_helpers(component):
        component.destroy(instance)


def play(world, instance, node):
    if (world.hero, instance.graph.index, node.pos) != UPPERCUT_CALL:
        return NotImplemented
    match = world.match
    if (match is None or instance.component.strict or instance.component.entity != world.body
            or instance.component.instances.get(instance.id) is not instance
            or instance.stopped or instance.teardown):
        return False
    caller = next((p for p in match.players if p.body == world.body and p.has_body
                   and not p.gone and p.body_script is not None
                   and p.body_script.component is instance.component
                   and not p.body_script.retired), None)
    if caller is None or match.combat.is_dead(caller):
        return False
    resource = requests.resolve_resource(instance, node)
    definition = graph.graph(UPPERCUT_HELPER)
    if (resource is None or definition is None
            or resource.guid != (graph.GRAPH_TYPE << 48) | UPPERCUT_HELPER):
        return False
    request = requests.resolve_request(instance, node, resource,
        context_provider=lambda: requests.InvocationContext(world.body, world.body, instance.source_entity()))
    if request is None:
        return True
    for entity in request.targets:
        npc = False
        owner = next((p for p in match.players if p.body == entity and p.has_body
                      and not p.gone and p.body_script is not None
                      and not p.body_script.retired), None)
        if owner is None:
            owner = next((bot for bot in match.bots if bot.entity == entity and not bot.dead
                          and bot.body_script is not None), None)
            npc = owner is not None
        if owner is None or match.combat.is_dead(owner):
            continue
        target = next((t for t in match.combat._targets() if t.owner is owner), None)
        if target is None or match.combat._control_immune(target):
            continue
        component = owner.body_script.component
        if (world._body_component(entity) is not component or component.fault is not None
                or component.owner_dead or component.strict
                or getattr(component.world, 'match', None) is not match):
            continue
        if npc and (getattr(component.world, 'component', None) is not component
                    or match.combat.junkrat.rooted(entity, match.tick)):
            continue
        if any(isinstance(state, nodes.MoveTo) and state.active
               for active in component.instances.values() for state in active.states.values()):
            # The common receiver cannot arbitrate this path yet. Do not run
            # half of a reset/control sequence after its force was rejected.
            continue
        queue = play_queue.Queue(component, lambda guid: play_queue.Load('ready', definition)
                                 if guid == resource.guid else None)
        queue.submit(request)
        if queue.pending:
            raise ValueError('Loaded body helper left pending PlayScript work')
        if not npc:
            component.run_now()
        # NPC target collection precedes the damage/force callback. Its actual
        # VM queues these entries; accepted canonical admission flushes them
        # after binding the one physical mover, before its next pose.
    return True
