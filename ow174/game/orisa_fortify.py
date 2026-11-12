"""Delivered Fortify pose-anchor/idle clock adapter for the combined server.

The source carries these methods in its Orisa barrier manager. Keep the same
Fortify algorithm without installing a second, obsolete barrier implementation.
"""
from ow174.game.script import runtime

HERO = 0x02E000000000013E


def historical_pending(owner, tick):
    script = getattr(owner, "body_script", None)
    return (tick is not None and getattr(script, "orisa_fortify_pending", False)
            and tick < owner.match.tick)


def rebase_fortify(fight, tick):
    from ow174.game.combat import TICK, _active_fortify
    from ow174.game.projectiles import MAX_LEASE_FRAME
    from ow174.game.script.nodes import ChaseVar

    for owner in fight.match.players:
        script = owner.body_script
        if (script is None or not getattr(script, "orisa_fortify_pending", False)
                or script.orisa_pose_anchor is None):
            continue
        phase = _active_fortify(owner)
        if phase is None:
            continue
        anchor_tick, anchor_frame = script.orisa_pose_anchor
        elapsed_ms = max(0, tick - anchor_tick) * round(TICK * 1000)
        target = min(MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
        shift = (target - script.frame) * script.frame_ms
        if shift <= 0:
            continue
        instance = phase.instance
        # Preserve the new cast's lifetime without moving any older shield throw.
        states = {state for state in instance.states.values() if state.active and state.start >= phase.start}
        for state in states:
            state.start += shift
            if isinstance(state, ChaseVar):
                state.last += shift
        for event in instance.queue:
            if event.state in states:
                event.time += shift
        instance.queue.sort(key=runtime.Event.key)


def advance_idle(fight, tick):
    from ow174.game.combat import TICK, _active_fortify
    from ow174.game.projectiles import MAX_LEASE_FRAME
    from ow174.game.script.driver import MAX_GAP

    for owner in fight.match.players:
        if _active_fortify(owner) is None:
            continue
        script = owner.body_script
        if script.orisa_pose_anchor is None:
            continue
        anchor_tick, anchor_frame = script.orisa_pose_anchor
        elapsed_ms = max(0, tick - anchor_tick) * round(TICK * 1000)
        target = min(MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
        while script.frame < target and owner.body_script is script:
            if _active_fortify(owner) is None:
                break
            previous = script.frame
            owner._advance_body_gap(min(target, previous + MAX_GAP) + 1)
            if script.frame <= previous:
                break
