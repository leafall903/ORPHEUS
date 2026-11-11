"""Reconstructed Pulse Bomb physics and impact fuse for build104319.

The 015D.130 A0E5F3ED default is 350. The actual0034 graph
provides speed15, gravity30, lifetime5, contact5, blast rings2/4 and fuse-field1.
Interpreting F969FA7A as the impact fuse, sphere flight/sticking, attachment
offsets, Recall detachment and explosion LOS are reconstructed server policies.
Physical Deflect uses the shared frontal capsule policy.
"""

import math
from dataclasses import dataclass

from ow174.game import brigitte_shield, contacts, rein_barrier, sigma_barrier
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
    if (world.hero == HERO and instance.graph.index == GRAPH and instance.component.entity == world.body
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
    definition: int | None = None
    attached_shield: tuple[str, int] | None = None


class PulseBombs:
    def __init__(self, combat):
        self.combat = combat
        self.bombs: list[Bomb] = []

    def launch(self, shooter, state, origin, direction, speed, lifetime, direct, blast, shot,
               *, definition, frame):
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
                    number(config.get("m_F969FA7A"), 1.0), direct, blast, shot,
                    last_tick=max(frame, self.combat.tick), definition=definition)
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


    @staticmethod
    def _shield_key(actor):
        if isinstance(actor, rein_barrier.Contact):
            return "rein", actor.instance.component.entity
        if isinstance(actor, brigitte_shield.Contact):
            return "brigitte", actor.shield.instance.component.entity
        if isinstance(actor, sigma_barrier.Barrier):
            return "sigma", actor.entity
        return None

    def _shield_surface(self, bomb):
        kind, entity = bomb.attached_shield
        fight = self.combat
        if kind == "sigma":
            barrier = fight.sigma_barriers.actors.get(entity)
            if barrier is not None and barrier.active:
                return barrier.position, barrier.basis, barrier
        if kind == "rein":
            for instance, centre, basis, _, _ in rein_barrier._surfaces(fight, bomb.shooter):
                if instance.component.entity == entity:
                    return centre, basis, rein_barrier.Contact(instance, fight.now)
        if kind == "brigitte":
            for shield, centre, normal, right, up in fight.brigitte_shields.faces(bomb.shooter):
                if shield.instance.component.entity == entity:
                    contact = brigitte_shield.Contact(fight.brigitte_shields, shield)
                    return centre, (normal, right, up), contact
        return None

    def _anchor(self, bomb):
        if bomb.attached_shield is not None:
            surface = self._shield_surface(bomb)
            if surface is None:
                bomb.attached_shield = None
            else:
                centre, basis, _ = surface
                bomb.position = tuple(centre[i] + sum(axis[i] * offset for axis, offset
                                      in zip(basis, bomb.offset, strict=True)) for i in range(3))
        if bomb.attached is None:
            return
        target = next((t for t in self.combat._targets() if t.entity == bomb.attached), None)
        if (target is None or self.combat.vitals_of(target).dead
                or self.combat.invulnerable(target.owner)):
            bomb.attached = None
            return
        bomb.position = tuple(x + y for x, y in zip(target.feet, bomb.offset, strict=True))

    def _advance(self, bomb, duration, tick):
        from ow174.game.combat import aim, ray_capsule

        duration = min(duration, bomb.life_left)
        if bomb.stuck:
            self._anchor(bomb)
            spent = min(duration, bomb.fuse_left)
            bomb.fuse_left -= spent
            bomb.life_left -= spent
            bomb.age += spent
            if bomb.fuse_left <= EPSILON or bomb.life_left <= EPSILON:
                self._explode(bomb, tick)
            return
        vx, vy, vz = bomb.velocity
        delta = (vx * duration, vy * duration - .5 * bomb.gravity * duration ** 2, vz * duration)
        length = math.hypot(*delta)
        if length <= EPSILON:
            bomb.life_left -= duration
            bomb.age += duration
            if bomb.life_left <= EPSILON:
                self._explode(bomb, tick)
            return
        direction = tuple(axis / length for axis in delta)
        end = tuple(x + y for x, y in zip(bomb.position, delta, strict=True))
        stop, struck = length, None
        for target in self.combat._enemies(bomb.shooter):
            a, b = target.segment()
            distance = ray_capsule(bomb.position, direction, a, b, target.radius + bomb.radius)
            if distance is not None and distance < stop:
                stop, struck = distance, target
        wall = contacts.scene_sweep(self.combat._collision().engine, bomb.position, end,
                                    ((0.0, 0.0, 0.0),), bomb.radius, MOVER)
        ice = self.combat.walls.cast(bomb.position, direction, stop)
        wall_dist = min(wall[0] * length if wall is not None else math.inf,
                        ice[0] if ice is not None else math.inf)
        hit_wall = wall_dist <= stop
        if hit_wall:
            stop, struck = wall_dist, None
        shield = self.combat._shield_hit(bomb.shooter, bomb.position, direction, stop, radius=bomb.radius)
        shield_distance = shield[0] if shield is not None else math.inf
        if shield_distance < stop or (not hit_wall and abs(shield_distance - stop) <= EPSILON):
            stop, struck, hit_wall = min(stop, shield_distance), None, False
        else:
            shield = None
        absorbed = self.combat._absorption_hit(bomb.shooter, bomb.position, direction, stop, bomb.definition)
        if absorbed is not None and absorbed[0] < stop:
            amount = (bomb.direct.amount if bomb.direct else 0) + (bomb.blast.amount if bomb.blast else 0)
            self.combat._absorbed(absorbed, amount)
            bomb.age += duration * absorbed[0] / length
            self._finish(bomb, absorbed=True)
            return
        spent = duration * stop / length
        bomb.position = tuple(x + y * stop for x, y in zip(bomb.position, direction, strict=True))
        bomb.velocity = (vx, vy - bomb.gravity * spent, vz)
        bomb.age += spent
        bomb.life_left -= spent
        if struck is not None and self.combat._genji_deflects(struck, direction):
            if bomb.shot is not None:
                self.combat.projectiles.absorbed(bomb.shot)
                bomb.shot = None
            bomb.shooter, bomb.source = struck.owner, struck.entity
            outgoing = aim(struck.owner.mover.yaw, struck.owner.mover.pitch)
            speed = math.hypot(*bomb.velocity)
            bomb.velocity = tuple(axis * speed for axis in outgoing)
            bomb.position = tuple(x + y * .001 for x, y in zip(bomb.position, outgoing, strict=True))
            return
        if hit_wall or shield is not None or struck is not None:
            bomb.stuck = True
            bomb.fuse_left = max(0.0, bomb.fuse)
            # Keep visibility and interception rays on the incoming side of a surface.
            if hit_wall or shield is not None:
                bomb.position = tuple(x - y * .001 for x, y in zip(bomb.position, direction, strict=True))
            if shield is not None:
                actor = shield[1]
                bomb.attached_shield = self._shield_key(actor)
                surface = self._shield_surface(bomb) if bomb.attached_shield is not None else None
                if surface is not None:
                    centre, basis, _ = surface
                    delta = tuple(x - y for x, y in zip(bomb.position, centre, strict=True))
                    bomb.offset = tuple(
                        sum(x * y for x, y in zip(delta, axis, strict=True)) for axis in basis
                    )
                else:
                    bomb.attached_shield = None
                if bomb.direct is not None:
                    actor.damage(bomb.direct.amount)
            if struck is not None:
                bomb.attached = struck.entity
                bomb.offset = tuple(x - y for x, y in zip(bomb.position, struck.feet, strict=True))
                if bomb.direct is not None:
                    self.combat._damage(bomb.shooter, struck, bomb.direct.amount, False,
                                        bomb.direct.loadout, tick, world_tick=tick, effect=bomb.direct.effect)
            self._advance(bomb, duration - spent, tick)
        elif bomb.life_left <= EPSILON:
            self._explode(bomb, tick)

    def _explode(self, bomb, tick):
        from ow174.game.combat import capsule_visible, segment_distance

        self._finish(bomb)
        blast = bomb.blast
        if blast is None or blast.amount <= 0:
            return
        fight = self.combat
        fight.venom.explode(bomb.shooter, bomb.position, blast, visible_from=bomb.position)
        targets = fight._enemies(bomb.shooter)
        targets += [t for t in fight._targets() if t.entity == bomb.source]
        blocked = {}
        if bomb.attached_shield is not None:
            surface = self._shield_surface(bomb)
            if surface is not None:
                blocked[bomb.attached_shield] = (surface[2], blast.amount)
        for target in targets:
            a, b = target.segment()
            distance = max(0.0, segment_distance(bomb.position, a, b) - target.radius)
            scalar = blast.splash(distance)
            if scalar <= 0 or not capsule_visible(fight._collision(), bomb.position, a, b, target.radius):
                continue
            y = max(a[1], min(b[1], bomb.position[1]))
            centre = (a[0], y, a[2])
            if fight.walls.blocked(bomb.position, centre):
                continue
            delta = tuple(x - y for x, y in zip(centre, bomb.position, strict=True))
            length = math.hypot(*delta)
            if length > EPSILON:
                direction = tuple(axis / length for axis in delta)
                shield = fight._shield_hit(bomb.shooter, bomb.position, direction, distance)
                if shield is not None:
                    at, actor = shield
                    amount = blast.amount * blast.splash(at)
                    key = self._shield_key(actor) or id(actor)
                    if key not in blocked or amount > blocked[key][1]:
                        blocked[key] = (actor, amount)
                    continue
            fight._damage(bomb.shooter, target, blast.amount * scalar, False, blast.loadout, tick,
                          world_tick=tick, effect=blast.effect)
        for actor, amount in blocked.values():
            actor.damage(amount)
        fight.walls.splash(bomb.shooter, bomb.position, blast, bomb.position)

    def _finish(self, bomb, *, absorbed=False):
        if bomb in self.bombs:
            self.bombs.remove(bomb)
        if bomb.shot is not None:
            if absorbed:
                self.combat.projectiles.absorbed(bomb.shot)
            else:
                self.combat.projectiles.flown(bomb.shot, bomb.age)
