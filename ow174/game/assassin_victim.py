"""Scoped1157/8FE -> Genji1177 host; native playback/attachment remain open.

Persisted sender identity supplies late receiver arrival and active/idle phases.
Policy is explicit: one current invocation per manual source, channel1, no filter
or parameter rows. Generic native receiver roles/priority/overlap are not inferred.
1177 authors intro0.55s/release1s; the existing Anim timer executes these.
Animation selection, attachment and visual playback need separate acceptance.
"""

from dataclasses import dataclass

from ow174.game.script import expr, graph, play, play_queue, runtime

GENJI = 0x02E0000000000029
ASSASSIN = 0x02E00000000001BB
VICTIM_GRAPH = 0x1177
MESSAGE = 0x8FE


@dataclass
class Lease:
    source: int
    source_instance: int
    sender_state: int
    activation: int
    target: int
    source_component: runtime.Component
    target_component: runtime.Component
    instance: int = 0


def owner(fight, lease):
    return next((p for p in fight.match.players if p.body == lease.target and p.has_body
                 and not p.gone and p.body_script is not None and not p.body_script.retired
                 and p.body_script.component is lease.target_component
                 and p.body_script.component.fault is None), None)


def sender_present(fight, lease):
    source = next((b for b in fight.match.bots if b.entity == lease.source), None)
    if (source is None or source.dead or source.body_script is None
            or source.body_script.component is not lease.source_component
            or lease.source_component.fault is not None or lease.source_component.owner_dead):
        return False
    instance = source.body_script.component.instances.get(lease.source_instance)
    state = instance.states.get(lease.sender_state) if instance is not None else None
    if state is None or not state.active or state.counter != lease.activation:
        return False
    target = state.node.field("m_target") or {}
    return (instance.graph.index == 0x1157 and state.node.pos == 18
            and target.get("$") == "STU_9B213168"
            and expr.to_entity(state.evaluate(target.get("m_targets"), watch=False)) == lease.target)


def retire(fight, lease):
    if fight.assassin_victims.get(lease.source) is lease:
        del fight.assassin_victims[lease.source]
    component = lease.target_component
    if lease.instance and component.fault is None:
        instance = component.instances.get(lease.instance)
        if instance is not None and instance.graph.index == VICTIM_GRAPH:
            component.destroy(instance)


def retire_target(fight, body):
    for lease in list(fight.assassin_victims.values()):
        if lease.target == body:
            retire(fight, lease)


def invocation(world, instance):
    if (world.hero != GENJI or world.match is None or instance.graph.index != VICTIM_GRAPH):
        return None
    return next((lease for lease in world.match.combat.assassin_victims.values()
                 if lease.target == world.body and lease.instance in (0, instance.id)
                 and lease.target_component is instance.component
                 and instance.source_entity() == lease.source), None)


def play_script(world, instance, node):
    if (world.match is None or world.match.ended or world.hero != ASSASSIN
            or world.controller_components is None or instance.graph.index != 0x1157 or node.pos != 45
            or instance.stopped or instance.component.owner_dead or instance.component.fault is not None
            or instance.component.instances.get(instance.id) is not instance):
        return
    resource = play.resolve_resource(instance, node)
    definition = graph.graph(VICTIM_GRAPH)
    if resource is None or resource.guid != 0x0580000000001177 or definition is None:
        return
    request = play.resolve_request(instance, node, resource, context_provider=lambda:
                                   play.InvocationContext(instance.component.entity,
                                                          instance.component.entity,
                                                          instance.source_entity()))
    if request is None or len(request.targets) != 1:
        return
    fight = world.match.combat
    source = next((b for b in world.match.bots if b.entity == instance.component.entity), None)
    player = next((p for p in world.match.players if p.body == request.targets[0]), None)
    if (source is None or source.dead or source.body_script is None
            or source.body_script.component is not instance.component
            or player is None or player.hero.guid != GENJI or not player.has_body
            or player.gone or player.body_script is None or player.body_script.retired
            or player.body_script.component.fault is not None
            or fight.is_dead(player)):
        return
    sender = instance.states.get(instance.graph.node(18).state)
    if sender is None or not sender.active:
        return
    lease = Lease(source.entity, instance.id, sender.index, sender.counter, player.body,
                  instance.component, player.body_script.component)
    if not sender_present(fight, lease):
        return
    old = fight.assassin_victims.get(source.entity)
    if old is not None:
        if (old.source_component is lease.source_component
                and (old.source_instance, old.activation, old.target)
                == (lease.source_instance, lease.activation, lease.target)):
            return
        retire(fight, old)  # explicit manual restart replaces this source's prior release instance
    component = player.body_script.component
    before = set(component.instances)
    fight.assassin_victims[source.entity] = lease  # receiver may begin during ready dispatch
    try:
        queue = play_queue.Queue(component, lambda guid: play_queue.Load("ready", definition))
        queue.submit(request)
        created = [i for key, i in component.instances.items() if key not in before
                   and i.graph.index == VICTIM_GRAPH]
        if queue.pending or len(created) != 1:
            raise ValueError("ready victim invocation did not create one script")
        lease.instance = created[0].id
    except Exception:
        if component.fault is None:
            for key, created in list(component.instances.items()):
                if key not in before and created.graph.index == VICTIM_GRAPH:
                    component.destroy(created)
        retire(fight, lease)
        raise


def receiver(world, state):
    lease = invocation(world, state.instance)
    if lease is None or state.node.pos != 17:
        return NotImplemented
    present = sender_present(world.match.combat, lease)
    first = state.message_key != MESSAGE
    if first:
        state.message_key, state.message_flags = MESSAGE, 1
        state.message_sender = 0
        state.received = False
        state.ticks()
    if first or present != state.received:
        previous = "m_452862E6" if state.received else "m_33DA5163"
        if not first:
            state.instance.exit_subgraph(state.node, previous)
        state.received = present
        state.message_sender = lease.source if present else 0
        state.instance.follow(state.node, "m_452862E6" if present else "m_33DA5163", state)
    return True


def health(world, cfg, instance):
    if invocation(world, instance) is None or cfg.get("$") != "STU_9B7648C3":
        return NotImplemented
    if expr.to_entity(instance.evaluate(cfg.get("m_entity"))) != world.body:
        return NotImplemented
    slot = instance.component.entity_var(253, create=False)
    return expr.f32(slot.value()) if slot is not None else 0.0



def stop_script(world, instance, node):
    lease = invocation(world, instance)
    if lease is None or node.pos not in (15, 18):
        return NotImplemented
    retire(world.match.combat, lease)
    return True


def controlled(fight, body):
    for lease in fight.assassin_victims.values():
        if lease.target != body:
            continue
        player = owner(fight, lease)
        instance = player.body_script.component.instances.get(lease.instance) if player is not None else None
        state = (instance.states.get(instance.graph.node(16).state)
                 if instance is not None and instance.graph.index == VICTIM_GRAPH else None)
        if state is not None and state.active:  #1177 node16 owns the control scope
            return True
    return False


def advance(fight):
    for lease in list(fight.assassin_victims.values()):
        player = owner(fight, lease)
        if player is None or fight.is_dead(player):
            retire(fight, lease)
            continue
        component = player.body_script.component
        instance = component.instances.get(lease.instance)
        if instance is None:
            retire(fight, lease)
            continue
        state = instance.states.get(instance.graph.node(17).state)
        if state is not None and state.active:
            receiver(component.world, state)
            component.run_now()
