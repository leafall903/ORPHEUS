"""Self-body direction queries used by Lúcio's authored Soundwave."""

import math

from ow174.game.script import expr

HERO = 0x02E0000000000079
GRAPH = 0x0776
LOOK = "STU_2200A66D"
FORWARD = "STU_021D01CD"
LIMIT = "STU_0762B33A"


def query(world, instance, cfg):
    """Scope the direction evaluator to the live owner's 0776 graph."""
    kind = cfg.get("$")
    if (kind not in (LOOK, FORWARD, LIMIT) or instance is None
            or instance.graph.index != GRAPH or instance.component.strict or world.match is None):
        return NotImplemented
    component = instance.component
    player = next((owner for owner in world.match.players if owner.has_body and not owner.gone
                   and owner.body == world.body == component.entity
                   and owner.body_script is not None and owner.body_script.component is component), None)
    if player is None or component.owner_dead or instance.stopped or instance.teardown:
        return expr.EVALUATION_FAILED
    if kind == LIMIT:
        return _limited_direction(instance, cfg)
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body):
        return NotImplemented
    yaw = player.mover.yaw * math.tau / 65536
    pitch = player.mover.pitch * math.tau / 65536 if kind == LOOK else 0.0
    return expr.Vec3(expr.f32(math.sin(yaw) * math.cos(pitch)), expr.f32(-math.sin(pitch)),
                     expr.f32(math.cos(yaw) * math.cos(pitch)))


def _limited_direction(instance, cfg):
    # CEB820 limits yaw and pitch independently about the authored reference.
    yaw_limit = instance.evaluate(cfg.get("m_5023B9B0"))
    pitch_limit = instance.evaluate(cfg.get("m_E26E5E09"))
    if (yaw_limit, pitch_limit) != (90, 25):
        return NotImplemented
    angles = []
    for field in ("m_32B00625", "m_3661BC45"):
        vector = instance.evaluate(cfg.get(field))
        if not isinstance(vector, expr.Vec3) or not all(map(math.isfinite, vector)):
            return expr.EVALUATION_FAILED
        length = math.hypot(*vector)
        if length * length < expr.EPSILON:
            return expr.EVALUATION_FAILED
        x, y, z = vector
        angles.append((math.atan2(x, z), math.asin(max(-1.0, min(1.0, -y / length)))))
    clamped = []
    for desired, reference, degrees in zip(*angles, (yaw_limit, pitch_limit), strict=True):
        delta = (desired - reference + math.pi) % math.tau - math.pi
        limit = math.radians(degrees)
        clamped.append(reference + max(-limit, min(limit, delta)))
    yaw, pitch = clamped
    return expr.Vec3(expr.f32(math.sin(yaw) * math.cos(pitch)), expr.f32(-math.sin(pitch)),
                     expr.f32(math.cos(yaw) * math.cos(pitch)))
