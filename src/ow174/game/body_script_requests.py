"""Scoped requests into actual live recipient VMs using the shared PlayScript queue.

Research adapter for the original Uppercut call. It does not add a force,
timer-only stun, second movement host or a replacement correction transport.
"""
from ow174.game.script import graph, nodes, play as requests, play_queue

UPPERCUT_CALL = (0x02E000000000012F, 0x0DD8, 22)
UPPERCUT_HELPER = 0x0E7D


def retire(component):
    """Retire the scoped externally played helper before its body is detached."""
    for instance in list(component.instances.values()):
        if instance.graph.index == UPPERCUT_HELPER:
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
        owner = next((p for p in match.players if p.body == entity and p.has_body
                      and not p.gone and p.body_script is not None
                      and not p.body_script.retired), None)
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
        component.run_now()
    return True
