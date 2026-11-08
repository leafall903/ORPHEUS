"""Reconstructed Pulse Bomb physics and impact fuse for build104319.

350 is the manifest-verified015D.130 A0E5F3ED default. The actual0034 graph
provides speed15, gravity30, lifetime5, contact5, blast rings2/4 and fuse-field1.
Interpreting F969FA7A as the impact fuse, sphere flight/sticking, attachment
offsets, Recall detachment and explosion LOS are reconstructed server policies.
"""

import math
from dataclasses import dataclass

from ow174.game import contacts
from ow174.game.collision import MOVER
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


@dataclass
class Bomb:
    shooter: object
    source: int
    position: tuple
    velocity: tuple
    gravity: float
    radius: float
    life_left: float
    fuse: float
    direct: object
    blast: object
    shot: object
    last_tick: int | None = None
    age: float = 0.0
    stuck: bool = False
    attached: int | None = None
    offset: tuple = (0.0, 0.0, 0.0)
    fuse_left: float = 0.0


class PulseBombs:
    def __init__(self, combat):
        self.combat = combat
        self.bombs: list[Bomb] = []

    def launch(self, shooter, state, origin, direction, speed, lifetime, direct, blast, shot):
        def number(cfg, default):
            value = state.evaluate(cfg, watch=False) if cfg is not None else None
            return expr.to_float(value) if value is not None else default

        motions = state.node.field("m_projectileMotions") or ()
        gravity = next((number(m.get("m_599AB8D7"), 0.0) for m in motions
                        if isinstance(m, dict) and m.get("$") == "STU_E1A5B579"), 0.0)
        config = state.node.field("m_55E8761C") or {}
        hit = state.node.field("m_6396149F") or {}
        bomb = Bomb(shooter, shooter.body, origin, tuple(v * speed for v in direction),
                    gravity, number(hit.get("m_57DAE1B5"), 0.0), lifetime,
                    number(config.get("m_F969FA7A"), 1.0), direct, blast, shot)
        self.bombs.append(bomb)
        return bomb

    def retire(self, source):
        for bomb in list(self.bombs):
            if bomb.source == source:
                self._finish(bomb)

    def update(self, tick):
        from ow174.game.combat import TICK

        for bomb in list(self.bombs):
            if bomb.shooter.body != bomb.source or not bomb.shooter.has_body:
                self._finish(bomb)
                continue
            elapsed = 1 if bomb.last_tick is None else tick - bomb.last_tick
            if elapsed <= 0:
                continue
            bomb.last_tick = tick
            # A bomb lives at most its authored lifetime; a large scheduler gap
            # needs only that bounded number of16-ms physics steps.
            steps = min(elapsed, math.ceil(bomb.life_left / TICK) + 1)
            for at in range(tick - elapsed + 1, tick - elapsed + steps + 1):
                if bomb not in self.bombs:
                    break
                self._advance(bomb, TICK, at)

    def _anchor(self, bomb):
        from ow174.game import tracer

        if bomb.attached is None:
            return
        target = next((t for t in self.combat._targets() if t.entity == bomb.attached), None)
        if target is None or tracer.recalling(target.owner):
            bomb.attached = None  # keep the last world position; do not rewind with Recall
            return
        bomb.position = tuple(axis + delta for axis, delta in zip(target.feet, bomb.offset, strict=True))

    def _advance(self, bomb, duration, tick):
        from ow174.game.combat import ray_capsule

        duration = min(duration, bomb.life_left)
        if duration <= EPSILON:
            if bomb.life_left <= EPSILON or (bomb.stuck and bomb.fuse_left <= EPSILON):
                self._explode(bomb, tick)
            return
        if bomb.stuck:
            self._anchor(bomb)
            spent = min(duration, bomb.fuse_left)
            bomb.age += spent
            bomb.life_left -= spent
            bomb.fuse_left -= spent
            if bomb.fuse_left <= EPSILON or bomb.life_left <= EPSILON:
                self._explode(bomb, tick)
            return
        vx, vy, vz = bomb.velocity
        delta = (vx * duration, vy * duration - 0.5 * bomb.gravity * duration ** 2, vz * duration)
        length = math.hypot(*delta)
        direction = tuple(axis / length for axis in delta) if length > EPSILON else (0.0, 0.0, 0.0)
        end = tuple(axis + change for axis, change in zip(bomb.position, delta, strict=True))
        best, struck = 1.0, None
        contact = False
        for target in self.combat._enemies(bomb.shooter):
            a, b = target.segment()
            distance = ray_capsule(bomb.position, direction, a, b, target.radius + bomb.radius)
            if distance is not None and distance <= length and distance / max(length, EPSILON) <= best:
                best, struck, contact = distance / max(length, EPSILON), target, True
        wall = contacts.scene_sweep(self.combat._collision().engine, bomb.position, end,
                                    ((0.0, 0.0, 0.0),), bomb.radius, MOVER)
        if wall is not None and wall[0] <= best:
            best, struck, contact = wall[0], None, True
        spent = duration * best
        bomb.position = tuple(axis + change * best
                              for axis, change in zip(bomb.position, delta, strict=True))
        bomb.velocity = (vx, vy - bomb.gravity * spent, vz)
        bomb.age += spent
        bomb.life_left -= spent
        if contact:
            bomb.stuck = True
            bomb.fuse_left = max(0.0, bomb.fuse)
            if struck is not None:
                bomb.attached = struck.entity
                bomb.offset = tuple(axis - feet
                                    for axis, feet in zip(bomb.position, struck.feet, strict=True))
                if bomb.direct is not None:
                    self.combat._damage(bomb.shooter, struck, bomb.direct.amount, False,
                                        bomb.direct.loadout, tick, world_tick=tick)
            self._advance(bomb, duration - spent, tick)
        elif bomb.life_left <= EPSILON:
            self._explode(bomb, tick)

    def _explode(self, bomb, tick):
        from ow174.game.combat import ray_world, segment_distance

        self._anchor(bomb)
        self._finish(bomb)
        blast = bomb.blast
        if blast is None or blast.amount <= 0:
            return
        targets = self.combat._enemies(bomb.shooter)
        targets += [t for t in self.combat._targets() if t.entity == bomb.source]
        for target in targets:
            a, b = target.segment()
            distance = max(0.0, segment_distance(bomb.position, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar <= 0:
                continue
            dy = b[1] - a[1]
            share = max(0.0, min(1.0, (bomb.position[1] - a[1]) / max(dy, EPSILON)))
            centre = (a[0], a[1] + share * dy, a[2])
            delta = tuple(axis - old for axis, old in zip(centre, bomb.position, strict=True))
            length = math.hypot(*delta)
            direction = tuple(axis / length for axis in delta) if length > EPSILON else (0.0, 0.0, 0.0)
            wall = ray_world(self.combat._collision(), bomb.position, direction, distance)
            if wall is not None and wall + EPSILON < distance:
                continue
            self.combat._damage(bomb.shooter, target, blast.amount * scalar, False,
                                blast.loadout, tick, world_tick=tick)

    def _finish(self, bomb):
        if bomb in self.bombs:
            self.bombs.remove(bomb)
        if bomb.shot is not None:
            self.combat.projectiles.flown(bomb.shot, bomb.age)
