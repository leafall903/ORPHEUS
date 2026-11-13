"""0259's presentation inputs for the reconstructed Match body host.

The original graph owns chasing and the BF/C0 mappings. This host supplies
body poses; its hardpoint 0001 policy uses the existing combat capsule centre,
not an animated model skeleton. Native animated hardpoint fidelity is pending.
"""

import math

from ow174.game.script import expr

HERO = 0x02E000000000006E
GRAPH = 0x0259


def relative_angles(target, view):
    """CCC8E0: view minus target yaw/pitch, wrapped and expressed in degrees."""
    if not isinstance(target, expr.Vec3) or not isinstance(view, expr.Vec3):
        return expr.Vec3(0.0, 0.0, 0.0)
    angles = []
    for vector in (target, view):
        length = math.sqrt(sum(axis * axis for axis in vector))
        if not math.isfinite(length) or length < 1.0842021724855044e-19:
            return expr.Vec3(0.0, 0.0, 0.0)
        x, y, z = (expr.f32(axis / length) for axis in vector)
        angles.append((expr.f32(math.atan2(x, z)), expr.f32(math.asin(max(-1, min(1, -y))))))
    result = []
    for before, after in zip(*angles, strict=True):
        delta = expr.f32(after - before)
        if delta > math.pi:
            delta = expr.f32(delta - expr.f32(2 * math.pi))
        elif delta < -math.pi:
            delta = expr.f32(delta + expr.f32(2 * math.pi))
        result.append(expr.f32(delta * expr.f32(180 / math.pi)))
    return expr.Vec3(*result, 0.0)


def native(host, cfg, instance):
    if (host.hero != HERO or host.match is None or instance is None
            or instance.graph.index != GRAPH or instance.component.strict):
        return NotImplemented
    cls = cfg.get('$')
    if cls == 'STU_9B5656C5':
        return relative_angles(instance.evaluate(cfg.get('m_5E57229B')),
                               instance.evaluate(cfg.get('m_794397AC')))
    if cls not in ('STU_FC99C77E', 'STU_21A13145', 'STU_2200A66D'):
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
    if cls in ('STU_21A13145', 'STU_2200A66D'):
        owner = host.match.script_player_body(entity, host.body)
        if owner is None:
            return expr.EVALUATION_FAILED
        from ow174.game.combat import Combat, aim

        return expr.Vec3(*(Combat._eye(host.match.combat, owner) if cls == 'STU_21A13145'
                           else aim(owner.mover.yaw, owner.mover.pitch)))
    if expr.guid_of((cfg.get('m_hardPoint') or {}).get('m_hardPoint')) != 0x0DC0000000000001:
        return NotImplemented
    target = next((target for target in host.match.combat._targets() if target.entity == entity), None)
    if target is None:
        return expr.EVALUATION_FAILED
    return expr.Vec3(target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
