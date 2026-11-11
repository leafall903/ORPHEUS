"""Authored Pulse Bomb parameters and server-side impact fuse."""

import math
from dataclasses import dataclass

from ow174.game import phase_defenses, reinhardt_barrier
from ow174.game.script import expr

HERO = 0x02E0000000000003
GRAPH = 0x0034
ATTRIBUTE = 0x0F4800000000015D
DEFAULT_DAMAGE = 350.0
EPSILON = 1e-9


def resource_float(world, cfg, instance):
    """Resolve only the verified default resource on this live Tracer body."""
    resource = cfg.get("m_DBF61E9A")
    if (world.hero == HERO and instance.component.entity == world.body
            and isinstance(resource, dict) and resource.get("$") == "STU_D9BDAA46"
            and expr.guid_of(resource.get("m_DBF61E9A")) == ATTRIBUTE
            and instance.evaluate(cfg.get("m_entity")) == expr.Entity(world.body)):
        return DEFAULT_DAMAGE
    return NotImplemented


def is_pulse(shooter, state):
    return (getattr(getattr(shooter, "hero", None), "guid", None) == HERO
            and state.instance.graph.index == GRAPH and state.node.pos == 1)


def launch_direction(state, forward):
    """Apply the authored vertical launch bias before projectile speed."""
    launch = state.node.field("m_5EC1EB38") or {}
    config = launch.get("m_22CE7F30")
    if config is None:
        return forward
    # D5ECB4 through D5ECEA, as verified by the shared projectile launch probe.
    lift = expr.f32(expr.to_float(state.evaluate(config, watch=False)))
    x, y, z = (expr.f32(value) for value in forward)
    y = expr.f32(y + lift)
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    if squared < 1.1754943508222875e-36:
        return (-0.0, -1.0, -0.0)
    inverse = 1 / math.sqrt(squared)
    return tuple(expr.f32(value * inverse) for value in (x, y, z))



DEFINITION = 0x0400000000000045
LOADOUT = 0x0B90000000000003


@dataclass
class ImpactFuse:
    duration: float
    stuck: bool = False
    body: int = 0
    barrier: int = 0
    pillar: int = 0
    offset: tuple = (0., 0., 0.)
    wall: bool = False


def _anchor(fight, barrier):
    if barrier.component.world.hero == reinhardt_barrier.ZARYA:
        target = next((t for t in fight._targets() if t.entity == barrier.protected_body), None)
        return (target.feet, None) if target is not None else (None, None)
    return reinhardt_barrier.pose(barrier)


def stick(fight, rocket, distance, duration, *, target=None, barrier=None, pillar=None):
    from ow174.game.combat import _along, _dot, _sub

    fuse = rocket.pulse
    elapsed = min(duration, distance / rocket.speed)
    rocket.position = _along(rocket.position, rocket.direction, distance)
    rocket.flown += elapsed
    rocket.left = min(max(0., rocket.left - elapsed), fuse.duration)
    fuse.stuck = True
    fuse.wall = target is None
    amount = rocket.direct.amount * rocket.damage_scale
    if barrier is not None:
        origin, axes = _anchor(fight, barrier)
        if origin is not None:
            fuse.barrier = barrier.entity
            delta = _sub(rocket.position, origin)
            fuse.offset = tuple(_dot(delta, axis) for axis in axes) if axes else delta
        reinhardt_barrier.damage(barrier, amount, attacker=rocket.source_body)
    elif pillar is not None:
        fuse.pillar = pillar.entity
        fight.walls.damage(pillar, rocket.shooter, amount, fight.tick)
    elif target is not None:
        fuse.body = target.entity
        fuse.offset = _sub(rocket.position, target.feet)
        if not reinhardt_barrier.protect_body(fight, target, amount, attacker=rocket.source_body):
            fight._damage(rocket.shooter, target, amount, False, LOADOUT, fight.tick,
                          world_tick=fight.tick, tags=rocket.direct.tags, source_body=rocket.source_body,
                          source_scaled=True)
    advance(fight, rocket, duration - elapsed)


def advance(fight, rocket, duration):
    from ow174.game.combat import _along

    fuse = rocket.pulse
    barrier = None
    if fuse.barrier:
        barrier = next((b for b in reinhardt_barrier.enemy_barriers(fight, rocket.shooter, team=rocket.team)
                        if b.entity == fuse.barrier), None)
        if barrier is not None:
            origin, axes = _anchor(fight, barrier)
            if origin is not None:
                delta = (tuple(sum(axis[i] * value for axis, value in zip(axes, fuse.offset, strict=True))
                               for i in range(3)) if axes else fuse.offset)
                rocket.position = _along(origin, delta, 1.)
        else:
            fuse.barrier = 0
    if fuse.body:
        target = next((t for t in fight._targets() if t.entity == fuse.body), None)
        if target is None or fight.vitals_of(target).dead or phase_defenses.active(target.owner):
            fuse.body = 0
        else:
            rocket.position = _along(target.feet, fuse.offset, 1.)
    spent = min(duration, rocket.left)
    rocket.flown += spent
    rocket.left -= spent
    if rocket.left > EPSILON:
        return
    fight._landed(rocket)
    shielded = None
    blast = rocket.blast
    amount = blast.amount * blast.splash(0.) * rocket.damage_scale if blast is not None else 0.
    if barrier is not None:
        reinhardt_barrier.damage(barrier, amount, attacker=rocket.source_body)
        if barrier.component.world.hero == reinhardt_barrier.ZARYA:
            shielded, barrier = barrier.protected_body, None
    pillar = fight.walls.pieces.get(fuse.pillar)
    fight._explode(rocket, rocket.position, wall=fuse.wall, shielded=shielded,
                   impact_barrier=barrier, impact_pillar=pillar)
    if pillar is not None:
        fight.walls.damage(pillar, rocket.shooter, amount, fight.tick)
