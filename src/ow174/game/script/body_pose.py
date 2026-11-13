"""Owned-body angle query shared by explicitly qualified authored callers."""

from ow174.game.script import expr


def owned_pitch(world, cfg, instance, *, current_pose=False):
    """104319 CC8200 -> D1C220 -> C61F70, in degrees; no angle fallback."""
    if (cfg.get('$') != 'STU_40AC5AE4' or instance is None
            or instance.component.strict or world.match is None
            or instance.component.entity != world.body):
        return NotImplemented
    if instance.evaluate(cfg.get('m_entity')) != expr.Entity(world.body):
        return NotImplemented
    player = world.match.script_player_body(world.body, world.body)
    if (player is None or player.body_script is None
            or player.body_script.component is not instance.component):
        return NotImplemented
    if current_pose and (player.body_script.retired
                         or not player.mover.history
                         or player.mover.history[-1].frame != instance.component.frame):
        return NotImplemented
    units = player.mover.pitch
    if type(units) is not int or not -32768 <= units <= 32767:
        return NotImplemented
    # Native C61F70 and D1C972 round these two multiplications separately.
    radians = expr.f32(units * 9.58738019107841e-05)
    return expr.f32(radians * 57.295780181884766)
