"""Clock mapping for the reconstructed authored Fire Strike only."""


def clock_origin(fight, owner):
    """Capture the existing constructor epoch while this source body is current."""
    script = owner.body_script
    epoch = fight.knockbacks.body_epochs.get(owner.body)
    if (script is None or epoch is None or epoch[0] is not script or epoch[1] is None
            or script.hero != 0x02E0000000000007):
        return None
    return (*epoch[1], script.frame_ms)


def launch_tick(fight, owner, frame):
    """Map the volley frame using the existing immutable body-constructor pair.

    Input batching must not replace this epoch with the latest admitted pose.
    Missing epochs retain the existing unresolved path rather than inventing an
    offset; the normal Match body constructor always registers this pair.
    """
    from ow174.game.combat import TICK

    origin = clock_origin(fight, owner)
    if origin is None:
        return None
    tick, birth, frame_ms = origin
    quantum = round(TICK * 1000)
    elapsed = (frame - birth) * frame_ms
    return tick + (elapsed + quantum - 1) // quantum


def view_world_tick(fight, owner, command):
    """Hammer target history uses world time; owner notices keep their frame."""
    from ow174.game.combat import TICK

    origin = clock_origin(fight, owner)
    if origin is None:
        return None
    tick, birth, frame_ms = origin
    return tick + ((command.frame - birth) * frame_ms
                   - command.view_delay(owner.quanta) * 1000) / (TICK * 1000)


def advance(fight, rocket):
    """Opt only the already qualified Fire Strike flight into physical-tick replay."""
    if (rocket.source_graph != 0x00E2 or rocket.definition != 0x04000000000001EA
            or type(rocket.last_tick) is not int or rocket.shot is None
            or rocket.shot.clock_origin is None):
        return False
    from ow174.game import projectile_clock

    projectile_clock.advance_accepted(fight, rocket)
    if rocket in fight.rockets:
        observe_live(fight, rocket.shot)
    return True


def observe_live(fight, shot):
    """Register a newly joined viewer against the same still-live Fire Strike."""
    owner = shot.player
    script = owner.body_script
    if (owner.body != shot.body or script is None or script.retired or not shot.observers
            or not shot.authoritative or shot.definition != 0x04000000000001EA):
        return
    instance = script.component.instances.get(shot.instance)
    state = (instance.states.get(shot.state)
             if instance is not None and instance.graph.index == 0x00E2 else None)
    if state is None:
        return
    viewers = [viewer for viewer in fight.match.players if viewer.client is not None and viewer.spawned
               and not viewer.gone and not any(
                   delivery.viewer is viewer and delivery.client is viewer.client
                   for delivery in shot.observers)]
    if not viewers:
        return
    original = shot.observers[0].record
    deadline = min(delivery.deadline for delivery in shot.observers)
    if fight.tick >= deadline:
        return
    fight.projectiles._show_others(
        shot, state, original.origin, original.direction, None,
        viewers=viewers, original_record=original, deadline=deadline,
    )
