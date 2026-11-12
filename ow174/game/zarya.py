"""Original0068 energy and charge policy on the existing single body state host."""
from ow174.game.script import nodes

ULT_PASSIVE_PER_SECOND = 5.0

HERO = 0x02E0000000000068


def ultimate_charge_ready_frame(script) -> int | None:
    """Use the cast's authored end wait even when input stalls the body VM."""
    ready = 0
    for ability in script.ultimates:
        if not (ability.flags & nodes.Ability.RUNNING):
            continue
        instance = ability.instance
        if instance.graph.index != 0x025F or ability.node.pos != 7:
            return None
        wait = instance.states.get(instance.graph.node(4).state)
        if not isinstance(wait, nodes.Wait) or not wait.active:
            return None
        ready = max(ready, (wait.start + wait.ms) // script.frame_ms + 1)
    return ready


def advance_energy(script, tick: int) -> None:
    from ow174.game.combat import TICK

    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == 0x0242), None)
    if instance is None:
        return
    wait = instance.states.get(instance.graph.node(7).state)
    deadline = getattr(wait, "world_deadline", None)
    if deadline is None or tick < deadline:
        return
    tick_ms = round(TICK * 1000)
    if wait.active:
        wait.request_finish()
        component.run_now()
        chase = instance.states.get(instance.graph.node(12).state)
        if chase is not None and chase.active:
            # The first elapsed step starts at the exact authored timeout.
            chase.world_used = deadline * tick_ms - (-wait.ms % tick_ms)
    chase = instance.states.get(instance.graph.node(12).state)
    if chase is not None and chase.active and not chase.reached:
        elapsed = tick * tick_ms - chase.world_used
        if elapsed > 0:
            chase.world_used += elapsed
            chase.advance_to(chase.last + elapsed)
            # Current ChaseVar already supplies the one arithmetic implementation.
            # Retain the delivered accepted-body cursor after external world work.
            chase.last = chase.clock()
            component.run_now()
