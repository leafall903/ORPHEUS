"""Orisa body birth epochs, independent of input replay admission anchors."""

import math

from ow174.game import projectile_clock

HERO = 0x02E000000000013E
GRAPHS = (0x0BD4, 0x0CCB, 0x0BD7, 0x0CF5)
FRAME_MS = 16


def view_world_tick(script, command, quanta):
    """Original viewed-body query time; keep owner HIT/event frames separate."""
    world_birth, owner_birth = script.orisa_birth
    at = world_birth + (command.frame - owner_birth) * script.frame_ms / FRAME_MS
    return at - command.view_delay(quanta) * 1000 / FRAME_MS


def event_tick(script, event_ms):
    tick, frame = script.orisa_birth
    return tick + math.floor((event_ms - frame * script.frame_ms) / FRAME_MS)


def impact_tick(rocket, fallback):
    if rocket.started_tick is None:
        return fallback
    # Round a contact to its enclosing world tick, tolerating one sum-rounding ulp.
    at = rocket.started_tick + rocket.flown * 1000 / FRAME_MS
    return math.ceil(math.nextafter(at, -math.inf))


def advance(fight, rocket):
    if (getattr(getattr(rocket.shooter, "hero", None), "guid", None) != HERO
            or rocket.source_graph not in GRAPHS or rocket.last_tick is None):
        return False
    projectile_clock.advance_accepted(fight, rocket)
    return True
