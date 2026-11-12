"""Scoped Cassidy Roll/primary bridges, reviewed against parent's uncommitted work.

Native graph supplies direction, speed15,0.4s lifetime, refill and cooldown.
Yaw basis, authoritative velocity sampling and horizontal end cap are explicit
reconstructions. General velocity/property services and client acceptance stay open.
"""
import math

from ow174.game.script import expr

HERO = 0x02E0000000000042


def owner(world, instance):
    if (world.hero != HERO or world.match is None or world.match.ended
            or instance.graph.index != 0x1F0 or instance.component.entity != world.body):
        return None
    return next((p for p in world.match.players if p.body == world.body and p.spawned and not p.gone
                 and p.client is not None and not world.match.combat.is_dead(p)), None)


def velocity_action(world, instance, node):
    if world.hero != HERO or instance.graph.index != 0x1F0 or node.pos not in (8, 11, 17):
        return NotImplemented
    actor = owner(world, instance)
    if actor is None or world.match.combat.is_stunned(actor):
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
        # Original C32CCA..C32D22 stores a one-frame desired-minus-sampled delta.
        delta = tuple(expr.f32(v-before) for v, before in zip(value, actor.mover.velocity, strict=True))
        change = 'delta', delta
    world.velocity_requests.append((instance.component.frame + 1, *change))
    return True


def apply_velocity(actor, frame, *, controlled=False):
    script = actor.body_script
    if script is None:
        return
    world = script.component.world
    if controlled or world.body != actor.body or actor.match.combat.is_dead(actor):
        world.velocity_requests.clear()
        return
    pending = []
    for at, operation, value in world.velocity_requests:
        if at > frame:
            pending.append((at, operation, value))
            continue
        vx, vy, vz = actor.mover.velocity
        if operation == 'delta':
            actor.mover.velocity = tuple(expr.f32(v+d) for v, d in zip((vx, vy, vz), value, strict=True))
        else:
            speed = math.hypot(vx, vz)
            if speed > value:
                actor.mover.velocity = (vx*value/speed, vy, vz*value/speed)
    world.velocity_requests[:] = pending


def native(world, cfg, instance):
    if instance is None or cfg.get('$') not in {'STU_021D01CD', 'STU_66F3B89E', 'STU_309B0570'}:
        return NotImplemented
    actor = owner(world, instance)
    if actor is None or expr.to_entity(instance.evaluate(cfg.get('m_entity'))) != world.body:
        return NotImplemented
    angle = actor.mover.yaw*math.tau/65536
    if cfg['$'] == 'STU_309B0570':
        return expr.Vec3(*actor.mover.velocity)
    if cfg['$'] == 'STU_021D01CD':
        return expr.Vec3(expr.f32(math.sin(angle)), 0.0, expr.f32(math.cos(angle)))
    return expr.Vec3(expr.f32(math.cos(angle)), 0.0, expr.f32(-math.sin(angle)))


def critical_default(state):
    """Declared owned01D2/primary31 implicit2x policy, not a native global default."""
    instance = getattr(state, 'instance', None)
    if instance is not None:
        world = instance.component.world
        if (getattr(world, 'hero', None) == HERO
                and getattr(world, 'body', None) == instance.component.entity
                and instance.graph.index == 0x1D2 and state.node.pos == 31):
            return 2.0
    return 1.0
