"""Scoped build-104319 Doomfist aim and first self-launch rows."""

import math

from ow174.game import rocket_punch
from ow174.game.script import expr

LAUNCH_NODES = {0x0DD8: 1, 0x0B27: 75}


def caster_of(fight, instance):
    if instance.component.strict or instance.component.owner_dead or instance.stopped or instance.teardown:
        return None
    return next((p for p in fight.match.players if p.has_body and p.spawned and not p.gone
                 and p.body == instance.component.entity and p.hero.guid == rocket_punch.HERO
                 and p.body_script is not None and not p.body_script.retired
                 and p.body_script.component is instance.component and not fight.is_dead(p)), None)


def direction_query(world, cfg, instance):
    if instance.graph.index not in LAUNCH_NODES or world.hero != rocket_punch.HERO or world.match is None:
        return NotImplemented
    cls = cfg.get("$")
    if cls not in ("STU_021D01CD", "STU_2200A66D", "STU_0762B33A"):
        return NotImplemented
    from ow174.game.combat import aim

    caster = caster_of(world.match.combat, instance)
    if caster is None or caster.body != world.body:
        return NotImplemented
    if cls == "STU_0762B33A":
        if (instance.evaluate(cfg.get("m_5023B9B0")), instance.evaluate(cfg.get("m_E26E5E09"))) != (90, 0):
            return NotImplemented
        desired = instance.evaluate(cfg.get("m_32B00625"))
        basis = instance.evaluate(cfg.get("m_3661BC45"))
        if not isinstance(desired, expr.Vec3) or not isinstance(basis, expr.Vec3):
            return NotImplemented
        yaw = math.atan2(basis.x, basis.z)
        desired_yaw = math.atan2(desired.x, desired.z)
        delta = (desired_yaw - yaw + math.pi) % math.tau - math.pi
        yaw += max(-math.pi / 2, min(math.pi / 2, delta))
        length = math.hypot(*basis)
        if not length:
            return NotImplemented
        vertical = basis.y / length
        horizontal = math.hypot(basis.x, basis.z) / length
        return expr.Vec3(*(expr.f32(v) for v in (math.sin(yaw) * horizontal, vertical,
                                                math.cos(yaw) * horizontal)))
    target = cfg.get("m_entity") or {}
    if target.get("$") != "STU_B5A0CAF0" or instance.evaluate(target) != expr.Entity(world.body):
        return NotImplemented
    pitch = caster.mover.pitch if cls == "STU_2200A66D" else 0
    return expr.Vec3(*aim(caster.mover.yaw, pitch))


def launch(fight, instance, node):
    if LAUNCH_NODES.get(instance.graph.index) != node.pos or instance.component.strict:
        return NotImplemented
    caster = caster_of(fight, instance)
    if caster is not None:
        value = instance.evaluate(node.field("m_BC5E91CF[2].m_9B7A63EA"))
        if isinstance(value, expr.Vec3) and all(map(math.isfinite, value)) and value.y > 0:
            caster.mover.launch(value)
    return True
