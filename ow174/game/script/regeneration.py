"""Mercy's authored self-healing state, consumed by authoritative combat.

0071/node89 compares owner health253 with maximum1762; node91 waits one
second, then node79 starts ModifyHealth/state2. Damage message0011 aborts
that branch through node90. These bindings are extracted 104319 data.
Applying the positive amount per second and retained healing-filter history are
reconstructed server behavior; other ModifyHealth forms use their own hosts.
"""

from ow174.game.script import expr, runtime

MERCY = 0x02E0000000000004
DAMAGED = 0x0240000000000011
GRAPH, STATE = 0x0071, 2
DAMAGE_WAIT = 29  #0071/node91


def report(script, frame: int) -> float:
    """Accumulate only frames actually simulated, including retained input gaps."""
    if script.hero != MERCY or script.component.owner_dead:
        return 0.0
    total = 0.0
    for instance in script.component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        state = instance.states.get(STATE)
        if state is None or not state.active:
            continue
        modify = state.node.field("m_modifyHealth") or {}
        amount = expr.to_float(state.evaluate(modify.get("m_amount"), watch=False))
        total += max(0.0, amount) * script.frame_ms / 1000.0
    match = script.world.match
    if total <= 0 or match is None:
        return total
    if frame >= match.combat.tick:
        target = next((t for t in match.combat._targets() if t.entity == script.entity), None)
        return total * match.combat.healing.factor(target) if target is not None else 0.0
    for tick, scale in reversed(getattr(script.world, "passive_healing_scales", ())):
        if tick <= frame:
            return total * scale
    return total


def damaged(script, sender: int, tick: int) -> None:
    """Restart the existing wait through the actual graph's damage entry.

    The regeneration entry consumes no parameters. Full engine damage-message
    parameters and other hero receivers are deliberately outside this binding.
    """
    if script.hero != MERCY:
        return
    component = script.component
    instances = [instance for instance in component.instances.values() if instance.graph.index == GRAPH]
    first_new_event = component.seq.next_value
    for instance in instances:
        runtime.send_instance_message(instance, DAMAGED, {}, sender)
    component.run_now()

    # World impacts can reach a body before its commands for that tick. Run
    # the authored abort immediately, but anchor its new wait no earlier than
    # the end of the last completed world frame.
    offset = tick * script.frame_ms - 1 - component.now
    if offset <= 0:
        return
    for instance in instances:
        for event in instance.queue:
            if (event.seq >= first_new_event and event.kind == runtime.TIMER
                    and event.state.index == DAMAGE_WAIT):
                event.state.start += offset
                event.time += offset
                instance.queue.sort(key=runtime.Event.key)
                break


def record_healing_scale(script, frame: int, scale: float) -> None:
    """Keep only effective scale changes, keyed by the world frame."""
    history = getattr(script.world, "passive_healing_scales", ())
    if history and history[-1][0] == frame:
        history = history[:-1]
    if scale != (history[-1][1] if history else 1.0):
        history += ((frame, scale),)
    if history:
        script.world.passive_healing_scales = history
    else:
        vars(script.world).pop("passive_healing_scales", None)


def prune_healing_scales(script) -> None:
    history = getattr(script.world, "passive_healing_scales", ())
    for index in range(len(history) - 1, -1, -1):
        tick, scale = history[index]
        if tick <= script.frame:
            start = index + 1 if scale == 1.0 else index
            remaining = history[start:]
            if remaining:
                script.world.passive_healing_scales = remaining
            else:
                del script.world.passive_healing_scales
            break
