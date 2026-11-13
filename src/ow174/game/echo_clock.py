"""Echo's owner view cursor mapped to world target history by the actual body birth."""


def view_world_tick(script, command, quanta):
    from ow174.game.combat import TICK

    world, owner = script.echo_birth
    elapsed_ms = (command.frame - owner) * script.frame_ms
    delay_ms = command.view_delay(quanta) * 1e6 * script.frame_ms / quanta
    return world + (elapsed_ms - delay_ms) / (TICK * 1000)
