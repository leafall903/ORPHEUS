"""Scoped 090B actor and 0623 script hosting for Mei Cryo-Freeze."""
from dataclasses import dataclass, field

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


@dataclass
class Healing:
    """World-clock healing and immunity for one authored activation."""
    activation: tuple
    started: float
    end: float
    exit_delay: float
    rate: float
    at: float
    accounted: float = 0.0
    statuses: list[tuple[float, float, float]] = field(default_factory=list)

    def note(self, at, blocked_until, boosted_until):
        if not self.statuses or self.statuses[-1][1:] != (blocked_until, boosted_until):
            self.statuses.append((at, blocked_until, boosted_until))

    def sample(self, through, tick, boost):
        cumulative = (min(through, self.end) - self.started) * self.rate
        amount = cumulative - self.accounted
        if amount <= 1e-9:
            return 0.0
        span = amount / self.rate
        count = max(1, round(span / tick))
        period = span / count
        total, status = 0.0, 0
        # A catch-up sample represents pulses at the end of their own frames.
        # Only an effective status at that pulse can change its value.
        for index in range(1, count + 1):
            pulse = self.at + index * period + 1e-9
            while status + 1 < len(self.statuses) and self.statuses[status + 1][0] <= pulse:
                status += 1
            _, blocked_until, boosted_until = self.statuses[status]
            if pulse >= blocked_until:
                total += boost if pulse < boosted_until else 1.0
        self.statuses[:] = self.statuses[status:]
        self.at += span
        self.accounted = cumulative
        return amount * total / count


def _active_state(owner, hero, graph, index):
    script = getattr(owner, "body_script", None)
    if (getattr(getattr(owner, "hero", None), "guid", None) != hero
            or script is None or script.retired or script.component.owner_dead
            or script.component.fault is not None):
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == graph:
            state = instance.states.get(index)
            if state is not None and state.active and not state.ending:
                return state
    return None


def active(actor):
    # The protection stack outlives the healing phase during the authored
    # quarter-second exit wait.
    state = _active_state(actor, HERO, GRAPH, 14)
    if state is None:
        return False
    flag = state.instance.component.entity_var(2575, create=False)
    return flag is not None and expr.truthy(flag.value())


def advance_entry(actor):
    state = _active_state(actor, HERO, GRAPH, 41)
    if (state is not None and not state.instance.component.strict
            and state.node.cls == "STU_54FEB21C"):
        state.request_finish()
        state.instance.update()


def healing_state(actor):
    script = getattr(actor, "body_script", None)
    if (getattr(getattr(actor, "hero", None), "guid", None) != HERO
            or script is None or script.retired):
        return None
    component = script.component
    if component.owner_dead or component.fault:
        return None
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    state = instance.states.get(13) if instance is not None else None
    return state if state is not None and state.counter else None


def activation(state):
    return state.instance.id, state.counter, state.start


def healing_inputs(state):
    instance = state.instance
    duration = expr.to_float(instance.find_var(0, 567).value())
    amount = expr.to_float(instance.component.entity_var(6578).value())
    return duration, amount


def exit_delay(state):
    node = state.instance.graph.state(19)
    return expr.to_float(state.evaluate(node.field("m_timeout"), watch=False))


def exit_frame(actor):
    """Frame containing the authored exit, before a long input gap skips it."""
    if _active_state(actor, HERO, GRAPH, 14) is None:
        return None
    state = healing_state(actor)
    if state is None:
        return None
    duration, _ = healing_inputs(state)
    ended = state.start + duration * 1000 if state.active else state.ended_at
    return int((ended + exit_delay(state) * 1000) // state.instance.component.frame_ms)


def healing_sample(actor):
    """Read the authored healing state's elapsed input-VM time."""
    state = healing_state(actor)
    if state is None:
        return None
    component = state.instance.component
    end = component.now if state.active else state.ended_at
    if end is None:
        return None
    duration, amount = healing_inputs(state)
    duration *= 1000.0
    if duration <= 0 or amount <= 0:
        return None
    elapsed = max(0.0, min(duration, end - state.start))
    # The authored float32 ChaseVar can finish within 1ms of its endpoint.
    if elapsed >= duration - 1.0:
        elapsed = duration
    return activation(state), amount * elapsed / duration, amount * 1000 / duration
