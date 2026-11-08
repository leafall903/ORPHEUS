"""Explicit reconstructed interruption and debug-charge gate for Hanzo's manual cast.

0101's54FEB21C phases are not standard Ability states. Flashbang cancellation
must also retire their cast states; otherwise the delayed volley survives the
control window. This host policy is not native224E execution or immunity logic.
The same phase predicate stops debug refill; it does not supply native charge rates.
"""

HANZO = 0x02E0000000000005


def _active_casts(script):
    if script is None or getattr(script, "hero", None) != HANZO:
        return
    for instance in script.component.instances.values():
        if instance.graph.index == 0x0101 and any(
            state.active and state.node.pos in (29, 37) for state in instance.states.values()
        ):
            yield instance


def casting(script):
    """The reconstructed1s/1.2s phases; inspect allocated states without evaluating the VM."""
    return any(True for _instance in _active_casts(script))


def interrupt(script):
    if script is None or getattr(script, "hero", None) != HANZO:
        return
    progress = script.world.hanzo_climb
    if progress is not None:
        progress.active = False
        script.component.set(1204, False)
    for instance in list(_active_casts(script)):
        # Preserve the permanent button/availability/animation guards. Abort the
        # current cast's phases, stacks, presentation and timers through State.end.
        for state in list(instance.states.values()):
            if state.active and state.node.pos not in (1, 2, 54):
                state.end(False)
