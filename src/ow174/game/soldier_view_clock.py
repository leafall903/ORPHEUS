"""Soldier target history uses world time; attack notices retain owner frames."""


def view_world_tick(script, command, quanta):
    """Translate once through the preceding accepted packet's pose anchor.

    Body creation supplies the fallback before any packet has been accepted.
    Keep fractional view time for target-history interpolation.
    """
    from ow174.game.combat import TICK

    world, owner = script.world_frame_anchor or script.soldier_birth
    elapsed_ms = (command.frame - owner) * script.frame_ms - command.view_delay(quanta) * 1000
    return world + elapsed_ms / round(TICK * 1000)
