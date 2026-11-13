"""Opt Hanzo's admitted arrows into physical event replay."""

from dataclasses import replace

from ow174.game import projectile_clock

FLIGHTS = {
    (0x00B9, 0x0400000000000144),
    (0x00B9, 0x040000000000020B),
    (0x00B9, 0x040000000000270F),
    (0x00B9, 0x0400000000000149),
    (0x00B9, 0x040000000000020D),
    (0x0101, 0x040000000000034F),
}


HISTORY_ARROWS = {(0x00B9, 0x0400000000000144), (0x00B9, 0x0400000000000149)}

def targets_at_impact(fight, rocket, candidates):
    """Use retained physical-tick poses for the proved delayed Hanzo direct hits."""
    if ((rocket.source_graph, rocket.definition) not in HISTORY_ARROWS
            or rocket.shot is None or rocket.shot.clock_origin is None
            or rocket.reflected or fight.tick >= fight.match.tick):
        return candidates
    return [replace(shown, construct=target.construct) for target in candidates
            if (shown := fight.seen(target, fight.tick)) is not None]

def clock_origin(script):
    # Hanzo's anchor is fixed at this body's birth; capture it before a switch
    # can replace the player's current BodyScript.
    if script.hero != 0x02E0000000000005 or script.world_frame_anchor is None:
        return None
    return (*script.world_frame_anchor, script.frame_ms)


def advance(fight, rocket):
    if (rocket.source_graph, rocket.definition) not in FLIGHTS or rocket.last_tick is None:
        return False
    projectile_clock.advance_accepted(fight, rocket)
    return True


def confirmation_components(shot):
    """0149/020D own a native Pet; bind aliases before their launch event arrives."""
    if shot.definition not in (0x0400000000000149, 0x040000000000020D):
        return {}
    if shot.filter_bits is None:
        return {}  # no declared player filter snapshot; do not invent NPC affiliation
    return {72: [shot.body], 26: [shot.filter_bits]}
