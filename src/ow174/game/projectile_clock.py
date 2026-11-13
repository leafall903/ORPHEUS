"""Physical world-tick replay for one explicitly admitted projectile."""

TICK = 0.016


def advance_accepted(fight, rocket):
    """Call only after the hero/definition guard and a known integer last_tick.

    This advances one accepted flight using current collision targets. The
    caller owns historical target-pose policies and its actor input clock.
    """
    now, tick = fight.now, fight.tick
    try:
        for at in range(rocket.last_tick + 1, tick + 1):
            if rocket not in fight.rockets:
                break
            fight.now, fight.tick = now - (tick - at) * TICK, at
            fight._fly_current(rocket)
    finally:
        fight.now, fight.tick = now, tick
