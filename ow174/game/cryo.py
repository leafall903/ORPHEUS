"""Scoped 090B actor and 0623 script hosting for Mei Cryo-Freeze."""
from dataclasses import dataclass

from ow174.game.script import expr, graph, play, play_queue, runtime

HERO = 0x02E00000000000DD
GRAPH = 0x05FE
ENTITY = 0x040000000000090B
EFFECT = 0x0623
FIRST_ENTITY = 0xC2000001


@dataclass
class Ice:
    entity: int
    owner: object
    body: int
    pet: runtime.State
    activation: int
    component: runtime.Component


def create(world, state):
    if ((getattr(world, 'hero', None), state.instance.graph.index, state.index) != (HERO, GRAPH, 11)
            or graph.guid(state.node.field('m_entityDefinition.m_entityDef')) != ENTITY
            or getattr(world, 'match', None) is None or graph.graph(EFFECT) is None):
        return 0
    fight = world.match.combat
    owner = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if (owner is None or owner.gone or owner.body_script is None or owner.body_script.retired
            or owner.body_script.component is not state.instance.component or fight.is_dead(owner)):
        return 0

    from ow174.game.script.driver import BodyWorld

    occupied = ({p.body for p in world.match.players} | {b.entity for b in world.match.bots}
                | set(fight.cryo_actors))
    while fight.next_cryo in occupied:
        fight.next_cryo += 1
    entity = fight.next_cryo
    fight.next_cryo += 1
    child_world = BodyWorld(HERO, world.mode, entity)
    child_world.match = world.match
    source = state.instance.component
    child = runtime.Component(entity, source.frame_ms, child_world)
    child.frame, child.now = source.frame, source.now
    fight.cryo_actors[entity] = Ice(entity, owner, owner.body, state, state.counter, child)
    return entity


def live(fight, ice):
    owner, pet = ice.owner, ice.pet
    script = owner.body_script
    return (owner.has_body and not owner.gone and owner.body == ice.body and script is not None
            and not script.retired and not fight.is_dead(owner) and script.component is pet.instance.component
            and not pet.instance.stopped and not pet.instance.teardown and pet.active
            and pet.counter == ice.activation and pet.pet_entity == ice.entity)


def retire(fight, entity):
    ice = fight.cryo_actors.pop(entity, None)
    if ice is not None:
        for instance in list(ice.component.instances.values()):
            ice.component.destroy(instance)


def retire_owner(fight, owner):
    for ice in list(fight.cryo_actors.values()):
        if ice.owner is owner:
            retire(fight, ice.entity)


def advance(fight, tick):
    for ice in list(fight.cryo_actors.values()):
        if not live(fight, ice):
            retire(fight, ice.entity)
        elif tick > ice.component.frame:
            ice.component.run_frame(tick)
        else:
            ice.component.run_now()


def play_effect(world, instance, node):
    if (world.hero, instance.graph.index, node.pos) != (HERO, GRAPH, 22) or world.match is None:
        return False
    resource = play.resolve_resource(instance, node)
    model = graph.graph(EFFECT)
    if resource is None or resource.guid != (0x0580000000000000 | EFFECT) or model is None:
        return False
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None or len(request.targets) != 1:
        return False
    fight = world.match.combat
    ice = fight.cryo_actors.get(request.targets[0])
    if (ice is None or ice.body != world.body or ice.pet.instance is not instance or not live(fight, ice)):
        return False
    child = ice.component
    child.frame, child.now = instance.component.frame, instance.component.now
    queue = play_queue.Queue(child, lambda guid: play_queue.Load("ready", model))
    queue.submit(request)
    if queue.pending:
        raise ValueError("ready Cryo effect left pending work")
    child.run_now()
    return True


def heal(world, state, elapsed_ms):
    """Distribute the selected authored 05FE self-heal budget over active VM time."""
    instance = state.instance
    if (world.hero, instance.graph.index, state.node.pos) != (HERO, GRAPH, 27) or world.match is None:
        return NotImplemented
    if elapsed_ms <= 0 or instance.teardown:
        return True
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == world.body), None)
    if (target is None or target.owner.body_script is None or target.owner.body_script.retired
            or target.owner.body_script.component is not instance.component
            or state.evaluate(state.node.field("m_FA3ECC43"), watch=False) != expr.Entity(world.body)):
        return True
    duration = round(expr.to_float(state.evaluate(state.node.field("m_95F1FB0B"), watch=False)) * 1000)
    if duration <= 0:
        return True
    elapsed = max(0, state.now - state.start)
    applied = min(duration, elapsed) - min(duration, max(0, elapsed - elapsed_ms))
    budget = expr.to_float(state.evaluate(state.node.field("m_modifyHealth.m_amount"), watch=False))
    fight._heal_pools(target.owner, fight.vitals_of(target), budget * applied / duration)
    return True
