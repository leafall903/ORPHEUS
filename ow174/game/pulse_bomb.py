"""Reconstructed Pulse Bomb physics and impact fuse for build104319.

350 is the manifest-verified015D.130 A0E5F3ED default. The actual0034 graph
provides speed15, gravity30, lifetime5, contact5, blast rings2/4 and fuse-field1.
Interpreting F969FA7A as the impact fuse, sphere flight/sticking, attachment
offsets, Recall detachment and explosion LOS are reconstructed server policies.
Physical Deflect uses the same explicit frontal capsule policy as hitscan, with
Pulse-specific admission; original target-tag filters and reflected visuals are open.
"""

import math
from dataclasses import dataclass

from ow174.game import (
    body_protection,
    contacts,
    mei_wall,
    sigma_barrier,
    sigma_grasp,
    sigma_hyperspheres,
    zarya_barrier,
)
from ow174.game.collision import MOVER
from ow174.game.script import expr
from ow174.game.winston_barrier import Bubble

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
    attached_shield: int | None = None
    attached_bubble: Bubble | None = None
    attached_projected: int | None = None
    attached_orisa: int | None = None
    attached_supercharger: int | None = None
    attached_particle: tuple[int, int, int] | None = None
    attached_pillar: int | None = None


class PulseBombs:
    def __init__(self, combat):
        self.combat = combat
        self.bombs: list[Bomb] = []

    def launch(self, shooter, state, origin, direction, speed, lifetime, direct, blast, shot,
               *, definition=None):
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
                    number(config.get("m_F969FA7A"), 1.0), direct, blast, shot, definition=definition)
        self.bombs.append(bomb)
        return bomb

    def retire(self, source):
        for bomb in list(self.bombs):
            if bomb.source == source:
                self._finish(bomb)

    def update(self, tick, *, poses=None):
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
                self._advance(bomb, TICK, at, poses=poses)

    def _anchor(self, bomb, tick, poses):
        from ow174.game import tracer

        if bomb.attached_pillar not in self.combat.walls.pieces:
            bomb.attached_pillar = None
        if bomb.attached_orisa not in self.combat.orisa_barriers.active:
            bomb.attached_orisa = None
        if bomb.attached_supercharger not in self.combat.superchargers.active:
            bomb.attached_supercharger = None
        if bomb.attached_projected is not None:
            projected = self.combat.sigma_barriers.get(bomb.attached_projected)
            if projected is None or not projected.active:
                bomb.attached_projected = None
            else:
                bomb.position = tuple(projected.position[k] + sum(axis[k] * value
                                      for axis, value in zip(projected.basis, bomb.offset, strict=True))
                                      for k in range(3))
        if bomb.attached_bubble is not None:
            live = self.combat.winston_barriers.snapshot()
            if not any(bubble is bomb.attached_bubble for bubble in live):
                bomb.attached_bubble = None
        if bomb.attached_shield is not None:
            shield = self.combat.reinhardt_barriers
            owner = next((p for p in self.combat.match.players if p.body == bomb.attached_shield), None)
            state = shield.sync(owner) if owner is not None else None
            if state is None or not state.active:
                bomb.attached_shield = None
            else:
                origin, axes = shield.pose(owner)
                bomb.position = tuple(origin[k] + sum(axis[k] * value
                                      for axis, value in zip(axes, bomb.offset, strict=True))
                                      for k in range(3))
        if bomb.attached is None:
            return
        target = next((t for t in self.combat._targets() if t.entity == bomb.attached), None)
        if target is not None:
            target, recall = self.combat.sample_impact_pose(target, tick, poses)
            recalling = tracer.recalling(target.owner) if recall is None else recall
        if target is None or recalling or body_protection.active(target.owner):
            bomb.attached = None  # keep the last world position; do not rewind with Recall
            bomb.attached_particle = None
            return
        if bomb.attached_particle is not None:
            active = zarya_barrier.particle_barriers(target.owner)
            if not any(zarya_barrier.particle_barrier_key(pet) == bomb.attached_particle for pet in active):
                bomb.attached = None
                bomb.attached_particle = None
                return
        bomb.position = tuple(axis + delta for axis, delta in zip(target.feet, bomb.offset, strict=True))

    def _advance(self, bomb, duration, tick, *, poses, reflections=0):
        from ow174.game.combat import ray_capsule

        duration = min(duration, bomb.life_left)
        if duration <= EPSILON:
            if bomb.life_left <= EPSILON or (bomb.stuck and bomb.fuse_left <= EPSILON):
                self._explode(bomb, tick, poses)
            return
        if bomb.stuck:
            self._anchor(bomb, tick, poses)
            spent = min(duration, bomb.fuse_left)
            bomb.age += spent
            bomb.life_left -= spent
            bomb.fuse_left -= spent
            if bomb.fuse_left <= EPSILON or bomb.life_left <= EPSILON:
                self._explode(bomb, tick, poses)
            return
        vx, vy, vz = bomb.velocity
        delta = (vx * duration, vy * duration - 0.5 * bomb.gravity * duration ** 2, vz * duration)
        length = math.hypot(*delta)
        direction = tuple(axis / length for axis in delta) if length > EPSILON else (0.0, 0.0, 0.0)
        end = tuple(axis + change for axis, change in zip(bomb.position, delta, strict=True))
        best, struck = 1.0, None
        contact = False
        for target in self.combat._enemies(bomb.shooter, poses=poses, tick=tick):
            a, b = target.segment()
            distance = ray_capsule(bomb.position, direction, a, b, target.radius + bomb.radius)
            if distance is not None and distance <= length and distance / max(length, EPSILON) <= best:
                best, struck, contact = distance / max(length, EPSILON), target, True
        wall = contacts.scene_sweep(self.combat._collision().engine, bomb.position, end,
                                    ((0.0, 0.0, 0.0),), bomb.radius, MOVER)
        if wall is not None and wall[0] <= best:
            best, struck, contact = wall[0], None, True
        shield = bubble_hit = projected_hit = orisa_hit = device_hit = pillar = None
        if length > EPSILON:
            stop = length * best
            pillar = self.combat.walls.cast(bomb.position, direction, stop, radius=bomb.radius)
            if pillar is not None and (not contact or pillar[0] < stop):
                stop = pillar[0]
                best, struck, contact = stop / length, None, True
            else:
                pillar = None
            shield = self.combat.reinhardt_barriers.first(
                bomb.shooter, bomb.position, direction, stop, radius=bomb.radius,
            )
            if shield is not None and (not contact or shield[0] < stop):
                stop = shield[0]
                best, struck, contact = stop / length, None, True
                pillar = None
            else:
                shield = None
            bubble_hit = self.combat.winston_barriers.first(
                bomb.shooter, bomb.position, direction, stop, radius=bomb.radius,
            )
            if bubble_hit is not None and (not contact or bubble_hit[0] < stop):
                stop = bubble_hit[0]
                best, struck, contact = stop / length, None, True
                shield = pillar = None
            else:
                bubble_hit = None
            projected_hit = sigma_barrier.barrier_intersection(
                self.combat, bomb.shooter, bomb.position, direction, stop, radius=bomb.radius,
            )
            if projected_hit is not None and (not contact or projected_hit[0] < stop):
                stop = projected_hit[0]
                best, struck, contact = stop / length, None, True
                shield = bubble_hit = pillar = None
            else:
                projected_hit = None
            orisa_hit = self.combat.orisa_barriers.first(
                bomb.shooter, bomb.position, direction, stop, radius=bomb.radius,
            )
            if orisa_hit is not None and (not contact or orisa_hit[0] < stop):
                stop = orisa_hit[0]
                best, struck, contact = stop / length, None, True
                shield = bubble_hit = projected_hit = pillar = None
            else:
                orisa_hit = None
            device_hit = self.combat.superchargers.first(
                bomb.shooter, bomb.position, direction, stop, radius=bomb.radius,
            )
            if device_hit is not None and (not contact or device_hit[0] < stop):
                stop = device_hit[0]
                best, struck, contact = stop / length, None, True
                shield = bubble_hit = projected_hit = orisa_hit = pillar = None
            else:
                device_hit = None
            absorbed = self.combat._matrix_hit(
                bomb.shooter, bomb.position, direction, stop, bomb.definition, poses=poses, tick=tick,
            )
            grasp_limit = min(stop, absorbed) if absorbed is not None else stop
            grasp = sigma_grasp.grasp_intersection(
                self.combat, bomb.shooter, bomb.position, direction, grasp_limit, poses=poses, tick=tick,
            )
            if grasp is not None and (grasp[0] < grasp_limit or (not contact and absorbed is None)):
                amount = (bomb.direct.amount if bomb.direct is not None else 0)
                amount += bomb.blast.amount if bomb.blast is not None else 0
                sigma_grasp.absorbed(grasp[1], amount)
                self._finish(bomb, absorbed=True)
                return
            if absorbed is not None and (not contact or absorbed < stop):
                self._finish(bomb, absorbed=True)
                return
        spent = duration * best
        bomb.position = tuple(axis + change * best
                              for axis, change in zip(bomb.position, delta, strict=True))
        bomb.velocity = (vx, vy - bomb.gravity * spent, vz)
        bomb.age += spent
        bomb.life_left -= spent
        if contact:
            # This manager contains only actual0034 Pulse Bombs. Admission is a
            # scoped server reconstruction, not an inference from damage tag003BF
            # (which Pulse does not carry) or native TargetTag0145 semantics.
            outgoing = self.combat.pulse_deflect_aim(struck, direction, tick, poses)
            if outgoing is not None:
                if reflections >= 8:
                    self._finish(bomb)  # bound zero-time overlapping deflectors
                    return
                if bomb.shot is not None:
                    self.combat.projectiles._end(bomb.shot, tick, False)
                    bomb.shot = None  # old prediction cannot represent the new owner
                bomb.shooter = struck.owner
                bomb.source = struck.entity
                bomb.direct = self.combat.scale_outgoing_hit(bomb.shooter, bomb.direct)
                bomb.blast = self.combat.scale_outgoing_hit(bomb.shooter, bomb.blast)
                speed = math.hypot(*bomb.velocity)
                bomb.velocity = tuple(axis * speed for axis in outgoing)
                # Keep gravity, age and remaining life. Contact/fuse begins only
                # on a later non-deflected collision, including a wall or floor.
                self._advance(bomb, duration - spent, tick, poses=poses, reflections=reflections + 1)
                return
            bomb.stuck = True
            bomb.fuse_left = max(0.0, bomb.fuse)
            if pillar is not None:
                piece = pillar[1]
                bomb.attached_pillar = piece.entity
                if bomb.direct is not None:
                    self.combat.walls.damage(piece, bomb.shooter, bomb.direct.amount)
            if shield is not None:
                _, owner, state = shield
                manager = self.combat.reinhardt_barriers
                origin, axes = manager.pose(owner)
                bomb.attached_shield = owner.body
                bomb.offset = tuple(sum((bomb.position[k] - origin[k]) * axis[k] for k in range(3))
                                    for axis in axes)
                if bomb.direct is not None:
                    manager.damage(owner, state, bomb.direct.amount)
            if projected_hit is not None:
                projected = projected_hit[1]
                bomb.attached_projected = projected.entity
                bomb.offset = tuple(sum((bomb.position[k] - projected.position[k]) * axis[k]
                                        for k in range(3)) for axis in projected.basis)
                if bomb.direct is not None:
                    sigma_barrier.damage_barrier(projected, bomb.direct.amount)
            if orisa_hit is not None:
                barrier = orisa_hit[1]
                bomb.attached_orisa = barrier.entity
                if bomb.direct is not None:
                    self.combat.orisa_barriers.damage(barrier, bomb.direct.amount)
            if device_hit is not None:
                device = device_hit[1]
                bomb.attached_supercharger = device.entity
                if bomb.direct is not None:
                    self.combat.superchargers.damage(device, bomb.direct.amount)
            if bubble_hit is not None:
                bomb.attached_bubble = bubble_hit[1]
                if bomb.direct is not None:
                    bomb.attached_bubble.damage(bomb.direct.amount)
            if struck is not None:
                bomb.attached = struck.entity
                particle = zarya_barrier.active(struck.owner)
                if particle is not None:
                    bomb.attached_particle = zarya_barrier.particle_barrier_key(particle)
                bomb.offset = tuple(axis - feet
                                    for axis, feet in zip(bomb.position, struck.feet, strict=True))
                if bomb.direct is not None:
                    _, recall = self.combat.sample_impact_pose(struck, tick, poses)
                    self.combat._damage(bomb.shooter, struck, bomb.direct.amount, False,
                                        bomb.direct.loadout, tick, world_tick=tick, tags=bomb.direct.tags,
                                        source_scaled=True,
                                        recall_protected=recall)
            self._advance(bomb, duration - spent, tick, poses=poses)
        elif bomb.life_left <= EPSILON:
            self._explode(bomb, tick, poses)

    def _explode(self, bomb, tick, poses):
        from ow174.game.combat import capsule_visible, segment_distance

        self._anchor(bomb, tick, poses)
        self._finish(bomb)
        blast = bomb.blast
        if blast is None or blast.amount <= 0:
            return
        targets = self.combat._enemies(bomb.shooter, poses=poses, tick=tick)
        targets += [self.combat.sample_impact_pose(t, tick, poses)[0]
                    for t in self.combat._targets() if t.entity == bomb.source]
        manager = self.combat.reinhardt_barriers
        collision = self.combat._collision()
        radius = blast.rings[-1][0] if blast.rings else 0.0
        walls = self.combat.walls

        def visible(point, entity):
            if not capsule_visible(collision, bomb.position, point, point, 0, walls=walls):
                return False
            delta = tuple(axis - old for axis, old in zip(point, bomb.position, strict=True))
            distance = math.hypot(*delta)
            if distance <= EPSILON:
                return True
            direction = tuple(axis / distance for axis in delta)
            cover = sigma_hyperspheres.barrier_contact(
                self.combat, bomb.shooter, bomb.position, direction, distance, excluded=(entity,),
            )
            return cover is None or cover[0] + EPSILON >= distance

        pillars = []
        for piece in walls.pieces.values():
            if piece.entity == bomb.attached_pillar:
                pillars.append((piece, blast.amount))
                continue
            surface = mei_wall.closest(piece, bomb.position)
            delta = tuple(axis - old for axis, old in zip(surface, bomb.position, strict=True))
            distance = math.hypot(*delta)
            amount = blast.amount * blast.splash(distance)
            if amount > 0 and visible(surface, piece.entity):
                pillars.append((piece, amount))

        projected = {actor.entity: (actor, blast.amount * blast.splash(distance))
                     for actor, distance in sigma_barrier.within(
                         self.combat, bomb.shooter, bomb.position, radius, visible=visible,
                     )}
        if bomb.attached_projected is not None:
            actor = self.combat.sigma_barriers[bomb.attached_projected]
            projected[actor.entity] = (actor, blast.amount)
        orisa = self.combat.orisa_barriers
        deployed = {actor.entity: (actor, blast.amount * blast.splash(distance))
                    for actor, distance in orisa.within(bomb.shooter, bomb.position, radius, visible=visible)}
        if bomb.attached_orisa is not None:
            actor = orisa.active[bomb.attached_orisa]
            deployed[actor.entity] = (actor, blast.amount)
        superchargers = self.combat.superchargers
        devices = {
            device.entity: (device, blast.amount * blast.splash(distance))
            for device, distance in superchargers.within(bomb.shooter, bomb.position, radius, visible=visible)
        }
        if bomb.attached_supercharger is not None:
            device = superchargers.active[bomb.attached_supercharger]
            devices[device.entity] = (device, blast.amount)
        bubbles = {actor.body: (actor, blast.amount * blast.splash(distance))
                   for actor, distance in self.combat.winston_barriers.within(
                       bomb.shooter, bomb.position, radius, visible=visible,
                   )}
        if bomb.attached_bubble is not None:
            bubble = bomb.attached_bubble
            bubbles[bubble.body] = (bubble, blast.amount)
        shields = {owner.body: (owner, state, blast.amount * blast.splash(distance))
                   for owner, state, distance in manager.within(
                       bomb.shooter, bomb.position, radius,
                       visible=visible,
                   )}
        if bomb.attached_shield is not None:
            for owner, state in manager.enemies(bomb.shooter):
                if owner.body == bomb.attached_shield:
                    shields[owner.body] = (owner, state, blast.amount)
                    break
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
            if not capsule_visible(collision, bomb.position, a, b, target.radius, walls=walls):
                continue
            shield = manager.first(bomb.shooter, bomb.position, direction, distance)
            limit = min(distance, shield[0]) if shield is not None else distance
            bubble_hit = self.combat.winston_barriers.first(bomb.shooter, bomb.position, direction, limit)
            if bubble_hit is not None:
                limit = min(limit, bubble_hit[0])
            projected_hit = sigma_barrier.barrier_intersection(
                self.combat, bomb.shooter, bomb.position, direction, limit,
            )
            projected_limit = min(limit, projected_hit[0]) if projected_hit is not None else limit
            orisa_hit = orisa.first(bomb.shooter, bomb.position, direction, projected_limit)
            if orisa_hit is not None and orisa_hit[0] < projected_limit:
                surface, actor = orisa_hit
                deployed.setdefault(actor.entity, (actor, blast.amount * blast.splash(surface)))
                continue
            if projected_hit is not None and projected_hit[0] < limit:
                surface, actor = projected_hit
                projected.setdefault(actor.entity, (actor, blast.amount * blast.splash(surface)))
                continue
            limit = min(distance, shield[0]) if shield is not None else distance
            if bubble_hit is not None and bubble_hit[0] < limit:
                surface, bubble = bubble_hit
                bubbles.setdefault(bubble.body, (bubble, blast.amount * blast.splash(surface)))
                continue
            if shield is not None and shield[0] < distance:
                distance_to_shield, owner, state = shield
                amount = blast.amount * blast.splash(distance_to_shield)
                shields.setdefault(owner.body, (owner, state, amount))
                continue
            self.combat._damage(bomb.shooter, target, blast.amount * scalar, False,
                                blast.loadout, tick, world_tick=tick, tags=blast.tags, source_scaled=True,
                                recall_protected=self.combat.sample_impact_pose(target, tick, poses)[1])
        for piece, amount in pillars:
            walls.damage(piece, bomb.shooter, amount)
        for owner, state, amount in shields.values():
            manager.damage(owner, state, amount)
        for bubble, amount in bubbles.values():
            bubble.damage(amount)
        for actor, amount in projected.values():
            sigma_barrier.damage_barrier(actor, amount)
        for actor, amount in deployed.values():
            orisa.damage(actor, amount)
        for device, amount in devices.values():
            if amount > 0:
                superchargers.damage(device, amount)

    def _finish(self, bomb, *, absorbed=False):
        if bomb in self.bombs:
            self.bombs.remove(bomb)
        if bomb.shot is not None:
            if absorbed:
                self.combat.projectiles.absorbed(bomb.shot)
            else:
                self.combat.projectiles.flown(bomb.shot, bomb.age)
