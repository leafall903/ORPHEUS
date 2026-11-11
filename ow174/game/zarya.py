"""Zarya's authored primary damage state with reconstructed beam collision."""

import math

from ow174.game.commands import FIRE
from ow174.game.script import expr
from ow174.game.script.combat_pose import active_pose


def modify_health(world, state, elapsed_ms):
    instance = state.instance
    if instance.graph.index != 0x0241 or state.node.pos != 28:
        return NotImplemented
    pose = active_pose.get()
    if pose is None or pose[0] is not world:
        return True
    _, player, command = pose
    fight = world.match.combat
    if (world.match.collision_world is None or fight.is_dead(player)
            or fight.is_stunned(player) or not command.buttons & FIRE
            or instance.component.frame != command.frame):
        return True

    ray = instance.graph.node(81)
    reach = expr.to_float(state.evaluate(ray.field("m_F5732403.m_6A79BD98"), watch=False))
    radius = expr.to_float(state.evaluate(ray.field("m_57DAE1B5"), watch=False))
    target, shield = fight.beam_target(player, command, reach, radius)

    # Publish the ray output beneath Stack176 so ModifyHealth28 reads its authored target.
    selected = expr.Entity(target.entity) if target is not None else None
    instance.write(expr.dynamic(ray.field("m_target.m_692DFF72")), selected)
    settings = state.node.field("m_modifyHealth")
    amount = expr.to_float(state.evaluate(settings["m_amount"], watch=False))
    damage = amount * min(elapsed_ms, player.body_script.frame_ms) / 1000
    if not math.isfinite(damage) or damage <= 0:
        return True
    if target is not None and state.evaluate(state.node.field("m_FA3ECC43"), watch=False) == selected:
        loadout = state.evaluate(settings["m_loadout"], watch=False)
        fight._damage(player, target, expr.f32(damage), False, loadout.guid, command.frame)
    elif shield is not None:
        fight.barriers.damage(shield, expr.f32(damage), player.body)
    return True
