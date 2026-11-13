"""Advance Ana's accepted projectile events on their body birth clock."""

from ow174.game import projectile_clock

FLIGHTS = {
    (0x0B94, 0x04000000000015B7),
    (0x0BDD, 0x04000000000015E8),
    (0x0B97, 0x0400000000001426),
}


def advance(fight, rocket):
    if (rocket.source_graph, rocket.definition) not in FLIGHTS or rocket.last_tick is None:
        return False
    projectile_clock.advance_accepted(fight, rocket)
    return True


def view_world_tick(script, command, quanta):
    """Map Ana's owner view time into target-history world ticks without rounding."""
    world, owner = script.ana_birth
    elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(quanta) * 1000
    return world + elapsed_ms / round(projectile_clock.TICK * 1000)
