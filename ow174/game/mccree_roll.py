"""104319 Combat Roll velocity requests over the collision-aware host mover."""

import math

from ow174.game.mccree_fan import HERO
from ow174.game.script import expr

ROLL = 0x01F0


def _roll_owner(world, instance):
    if (world.hero != HERO or world.match is None or world.match.ended
            or instance.graph.index != ROLL or instance.component.entity != world.body):
        return None
    return next((p for p in world.match.players if p.body == world.body and p.spawned and not p.gone
                 and p.client is not None and not world.match.combat.is_dead(p)), None)


def velocity_action(world, instance, node):
    """Scoped01F0 bridge; next-frame delta/cap timing follows D2B995/C32CCA."""
    if world.hero != HERO or instance.graph.index != ROLL or node.pos not in (8, 11, 17):
        return NotImplemented
    owner = _roll_owner(world, instance)
    if owner is None or world.match.combat.is_stunned(owner):
        return True
    rows = node.field('m_BC5E91CF')
    expected = 'STU_748FC65A' if node.pos == 17 else 'STU_95A10547'
    field = 'm_A9561CA0' if node.pos == 17 else 'm_CFEC5F76'
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get('$') != expected or rows[0].get(field) != 1
            or rows[0].get('m_priority') is not None):
        return NotImplemented
    value = instance.evaluate(rows[0].get('m_9B7A63EA'))
    if node.pos == 17:
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return NotImplemented
        change = 'cap', float(value)
    else:
        if not isinstance(value, expr.Vec3) or not all(math.isfinite(v) for v in value):
            return NotImplemented
        # C32D22 stores desired minus sampled velocity, for exactly one frame.
        delta = tuple(expr.f32(v - before) for v, before in zip(value, owner.mover.velocity, strict=True))
        change = 'delta', delta
    world.velocity_requests.append((instance.component.frame + 1, *change))
    return True


def apply_velocity(owner, frame, *, controlled=False):
    """Consume each request once, before the existing collision-aware posed move."""
    script = owner.body_script
    if script is None or script.hero != HERO:
        return
    world = script.world
    if controlled or world.body != owner.body or owner.match.combat.is_dead(owner):
        world.velocity_requests.clear()
        return
    pending = []
    for at, operation, value in world.velocity_requests:
        if at > frame:
            pending.append((at, operation, value))
            continue
        vx, vy, vz = owner.mover.velocity
        if operation == 'delta':
            owner.mover.velocity = tuple(expr.f32(v + d) for v, d in zip((vx, vy, vz), value, strict=True))
        else:
            # Reconstructed horizontal-speed clamp; full native phase ordering is open.
            speed = math.hypot(vx, vz)
            if speed > value:
                owner.mover.velocity = (vx * value / speed, vy, vz * value / speed)
    world.velocity_requests[:] = pending


def native(world, cfg, instance):
    if instance is not None and cfg.get('$') in {'STU_021D01CD', 'STU_66F3B89E', 'STU_309B0570'}:
        owner = _roll_owner(world, instance)
        if owner is not None and expr.to_entity(instance.evaluate(cfg.get('m_entity'))) == world.body:
            angle = owner.mover.yaw * math.tau / 65536
            if cfg['$'] == 'STU_309B0570':
                return expr.Vec3(*owner.mover.velocity)
            if cfg['$'] == 'STU_021D01CD':
                return expr.Vec3(expr.f32(math.sin(angle)), 0.0, expr.f32(math.cos(angle)))
            return expr.Vec3(expr.f32(math.cos(angle)), 0.0, expr.f32(-math.sin(angle)))
    return NotImplemented


def clear(script):
    if script is not None and script.hero == HERO:
        script.world.velocity_requests.clear()
