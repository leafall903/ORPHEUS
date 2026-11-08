"""Mercy's authored self-healing state, consumed by authoritative combat.

0071/node89 compares owner health253 with maximum1762; node91 waits one
second, then node79 starts ModifyHealth/state2. Damage message0011 aborts
that branch through node90. These bindings are extracted 104319 data.
Applying the positive amount per second is reconstructed server behavior;
this does not implement the other ModifyHealth forms or healing modifiers.
"""

from ow174.game.script import expr, runtime

MERCY = 0x02E0000000000004
DAMAGED = 0x0240000000000011
GRAPH, STATE = 0x0071, 2


def report(script) -> float:
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
    return total


def damaged(script, sender: int) -> None:
    """Restart the existing wait through the actual graph's damage entry.

    The regeneration entry consumes no parameters. Full engine damage-message
    parameters and other hero receivers are deliberately outside this binding.
    """
    if script.hero != MERCY:
        return
    for instance in list(script.component.instances.values()):
        if instance.graph.index == GRAPH:
            runtime.send_instance_message(instance, DAMAGED, {}, sender)
    script.component.run_now()
