"""Scoped combat protection for the authored Cryo-Freeze lifetime."""

from dataclasses import dataclass, field

from ow174.game.script import expr

HERO = 0x02E00000000000DD
GRAPH = 0x05FE


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
    match = getattr(actor, "match", None)
    if match is not None:
        fight = match.combat
        phase = fight.cryo_samples.get(actor.body)
        heal = state.instance.states.get(13)
        if phase is not None and heal is not None and phase.activation == activation(heal):
            if fight.now + 1e-9 >= phase.end + phase.exit_delay:
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
