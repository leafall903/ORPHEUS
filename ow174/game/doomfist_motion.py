"""Selected Doomfist aim queries and self-launch rows for build 104319."""

import math

from ow174.game import rocket_punch
from ow174.game.script import expr

LAUNCH_NODES = {0x0DD8: 1, 0x0B27: 75}


def direction_query(world, cfg, instance):
    if (instance is None or instance.graph.index not in LAUNCH_NODES or world.hero != rocket_punch.HERO
            or world.match is None or instance.stopped or instance.teardown):
        return NotImplemented
    cls = cfg.get("$")
    if cls not in ("STU_021D01CD", "STU_2200A66D", "STU_0762B33A"):
        return NotImplemented
    from ow174.game.combat import aim

    player = world.match.combat._cast_owner(instance)
    if (player is None or player.body != world.body or player.body_script is None
            or player.body_script.component is not instance.component):
        return NotImplemented
    if cls == "STU_0762B33A":
        yaw = instance.evaluate(cfg.get("m_5023B9B0"))
        pitch = instance.evaluate(cfg.get("m_E26E5E09"))
        if (yaw, pitch) != (90, 0):
            return NotImplemented
        desired = instance.evaluate(cfg.get("m_32B00625"))
        basis = instance.evaluate(cfg.get("m_3661BC45"))
        return expr.limit_direction(desired, basis, yaw, pitch)
    target = cfg.get("m_entity") or {}
    if target.get("$") != "STU_B5A0CAF0" or instance.evaluate(target) != expr.Entity(world.body):
        return NotImplemented
    pitch = player.mover.pitch if cls == "STU_2200A66D" else 0
    return expr.Vec3(*aim(player.mover.yaw, pitch))


def launch(fight, instance, node):
    if LAUNCH_NODES.get(instance.graph.index) != node.pos:
        return NotImplemented
    caster = fight._cast_owner(instance)
    if caster is None:
        return True
    value = instance.evaluate(node.field("m_BC5E91CF[2].m_9B7A63EA"))
    if isinstance(value, expr.Vec3) and all(map(math.isfinite, value)) and value.y > 0:
        # Selected first launch row. The sibling axis operations and later
        # velocity modifiers have separate native consumers.
        caster.mover.launch(value)
    return True
