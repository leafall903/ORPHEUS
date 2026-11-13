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
    return True
