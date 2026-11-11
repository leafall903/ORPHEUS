"""Authored Brigitte shield and Bash state consumers."""

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import barrier_pet, defenses, orisa_deployable, sigma_barrier, winston_barrier, zarya_barrier
from ow174.game.contacts import closest_on_triangle
from ow174.game.defense_geometry import AttachedMesh, DefenseGeometryError, attached_meshes
from ow174.game.script import expr, graph, nodes, play
from ow174.game.script.control_effect import apply_stun


HERO = 0x02E0000000000195
GRAPH = 0x0EE3
LOADOUT = 0x0B900000000001DB
RECHARGE_DELAY = 2  # 0EE4 Wait state 18
RECHARGE_RATE = 85  # 0EE4 ModifyHealth state 20 base rate
MODEL_SHA256 = "fd9303448daa02a128b1ec613f915f2396b90028743c6f02c48e3066a6fc6fb2"
SHIELD_NODE = 0xAC4


def configured_geometry():
    path = os.environ.get("ORPHEUS_BRIGITTE_SHIELD_MODEL")
    if not path:
        return None
    meshes = attached_meshes(Path(path).read_bytes())
    if len(meshes) != 1 or meshes[0].node != SHIELD_NODE or meshes[0].source_sha256 != MODEL_SHA256:
        raise DefenseGeometryError("Brigitte shield model must match the verified 104319 asset")
    return meshes[0]


@lru_cache(maxsize=2)
def _ray_world(mesh):
    return mesh.world()


@dataclass
class Barrier:
    owner: object
    instance: object
    health: float
    maximum: float
    last: float
    recharge_at: float
    geometry: AttachedMesh | None = None

    def raised(self):
        ability = self.instance.states.get(30)
        return (self.health > 1 and ability is not None and ability.active
                and bool(ability.flags & nodes.Ability.RUNNING))

    def update(self, now):
        if self.raised():
            self.recharge_at = now + RECHARGE_DELAY
        elif now > self.recharge_at and self.health < self.maximum:
            elapsed = max(0, now - max(self.last, self.recharge_at))
            self.set_health(min(self.maximum, self.health + elapsed * RECHARGE_RATE))
        self.last = now

    def set_health(self, health):
        self.health = expr.f32(health)
        state = self.instance.states.get(32)
        if state is not None and state.active:
            state.dependency_changed()
            self.instance.component.run_now()

    def damage(self, amount):
        if amount <= 0 or not self.raised():
            return
        self.recharge_at = self.owner.match.combat.now + RECHARGE_DELAY
        self.set_health(max(1, self.health - amount))
        if self.health <= 1:
            self.instance.states[30].stop()
            self.instance.component.run_now()

    def face(self):
        mover = self.owner.mover
        right, up, normal = sigma_barrier.view_basis(mover.yaw, mover.pitch)
        x, y, z = mover.position
        centre = (x + normal[0] * 0.5, y + 1 + normal[1] * 0.5, z + normal[2] * 0.5)
        return centre, right, up, normal

    def ray(self, origin, direction, reach):
        """Intersect the frontal shield at the authored owner offset."""
        from ow174.game.combat import _along, _dot, _sub

        centre, right, up, normal = self.face()
        facing = _dot(direction, normal)
        if facing >= -1e-9:
            return None
        if self.geometry is not None:
            axes = right, up, normal
            delta = _sub(origin, centre)
            local = tuple(_dot(delta, axis) for axis in axes)
            toward = tuple(_dot(direction, axis) for axis in axes)
            hit = _ray_world(self.geometry).cast_ray(local, toward, reach)
            return hit.distance if hit is not None else None
        distance = _dot(_sub(centre, origin), normal) / facing
        if not -1e-6 <= distance <= reach + 1e-9:
            return None
        offset = _sub(_along(origin, direction, distance), centre)
        if abs(_dot(offset, right)) <= 1 and abs(_dot(offset, up)) <= 1.1:
            return min(reach, max(0.0, distance))
        return None

    def closest_front(self, point):
        from ow174.game.combat import _along, _dot, _sub

        centre, right, up, normal = self.face()
        offset = _sub(point, centre)
        if self.geometry is not None:
            axes = right, up, normal
            local = tuple(_dot(offset, axis) for axis in axes)
            mesh = self.geometry
            nearest = min(
                (closest_on_triangle(mesh.vertices[a], mesh.vertices[b], mesh.vertices[c], local)
                 for a, b, c, *_ in mesh.triangles),
                key=lambda vertex: math.dist(vertex, local),
            )
            if local[2] < nearest[2] - 1e-6:
                return None
            return tuple(value + sum(coord * axis[index] for coord, axis in zip(nearest, axes, strict=True))
                         for index, value in enumerate(centre))
        if _dot(offset, normal) < 0:
            return None
        x = max(-1, min(1, _dot(offset, right)))
        y = max(-1.1, min(1.1, _dot(offset, up)))
        return _along(_along(centre, right, x), up, y)


def barrier_health(world, instance, cfg):
    if (instance.component.strict or instance.component.owner_dead or world.match is None
            or world.hero != HERO or instance.component.entity != world.body or instance.graph.index != GRAPH
            or cfg.get("$") != "STU_9B7648C3"
            or (cfg.get("m_entity") or {}).get("m_identifier") != 13214):
        return NotImplemented
    combat = world.match.combat
    barrier = combat.brigitte_barriers.get(world.body)
    if barrier is None:
        player = next((p for p in world.match.players if p.has_body and not p.gone and p.body == world.body), None)
        maximum = instance.vars.get(587)
        if player is None or maximum is None:
            return NotImplemented
        health = expr.to_float(maximum.value())
        barrier = Barrier(player, instance, health, health, combat.now, combat.now, combat.brigitte_geometry)
        combat.brigitte_barriers[world.body] = barrier
    return barrier.health


def colliders(combat, body, team):
    return tuple(barrier for entity, barrier in combat.brigitte_barriers.items()
                 if entity != body and barrier.raised() and not combat.is_dead(barrier.owner)
                 and not barrier.owner.gone and barrier.owner.body_script is not None
                 and barrier.owner.body_script.component is barrier.instance.component
                 and (combat.match.game_map.free_for_all or barrier.owner.team != team))


def update(combat):
    for barrier in tuple(combat.brigitte_barriers.values()):
        script = barrier.owner.body_script
        if (script is not None and script.component is barrier.instance.component
                and not combat.is_dead(barrier.owner)):
            barrier.update(combat.now)


def _graph(player):
    script = player.body_script
    if (player.hero.guid != HERO or script is None or script.component.strict
            or script.component.owner_dead):
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)


def _path_colliders(combat, player, enemies):
    body, team = player.body, player.team
    held = barrier_pet.active_colliders(combat, enemies)
    placed = (winston_barrier.colliders(combat, body, team) + orisa_deployable.colliders(combat, body, team)
              + sigma_barrier.colliders(combat, body, team) + colliders(combat, body, team))
    return held, placed


def movement_limit(combat, player):
    instance = _graph(player)
    dash = instance.states.get(14) if instance is not None else None
    if dash is None or not dash.active or dash.ending:
        return None
    x, y, z = player.mover.position
    origin = x, y + 1, z

    def limit(direction, reach):
        from ow174.game.combat import deployable_contact, ray_capsule

        enemies = combat._enemies(player)
        held, deployables = _path_colliders(combat, player, enemies)
        contacts = [barrier_pet.contact(held, origin, direction, reach),
                    deployable_contact(deployables, origin, direction, reach),
                    combat.walls.cast(origin, direction, reach)]
        distance = min((hit[0] for hit in contacts if hit is not None), default=reach)
        for target in enemies:
            if defenses.phase(target.owner)[0] and not defenses.transcendence(target.owner):
                hit = ray_capsule(origin, direction, *target.segment(), target.radius)
                if hit is not None:
                    distance = min(distance, hit)
        if distance < reach:
            instance.write((graph.INSTANCE, 488), True)
            dash.try_complete()
        return distance

    return limit


def prepare(combat, player, command):
    """Feed the live enemy contact to 0EE3's existing TrackTargets output."""
    from ow174.game.combat import _dot, _sub, aim, segment_distance

    instance = _graph(player)
    tracker = instance.states.get(12) if instance is not None else None
    if tracker is None or not tracker.active:
        return
    radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
    angle = expr.to_float(tracker.evaluate(tracker.node.field("m_A30B383E"), watch=False))
    offset = tracker.evaluate(tracker.node.field("m_05BE62DD"), watch=False)
    if not isinstance(offset, expr.Vec3) or radius <= 0:
        return
    yaw, pitch = command.aim()
    right, up, forward = sigma_barrier.view_basis(yaw, 0)
    origin = tuple(feet + offset.x * r + offset.y * u + offset.z * f
                   for feet, r, u, f in zip(player.mover.position, right, up, forward, strict=True))
    heading = aim(yaw, pitch)
    cos_half_angle = math.cos(math.radians(angle / 2))
    enemies = combat._enemies(player)
    attached = zarya_barrier.colliders(combat, player.body, player.team)
    protected = {barrier.body for barrier in attached}
    held, deployables = _path_colliders(combat, player, enemies)
    found = []
    for target in enemies:
        a, b = target.segment()
        if segment_distance(origin, a, b) - target.radius > radius:
            continue
        centre = (a[0], min(b[1], max(a[1], origin[1])), a[2])
        delta = _sub(centre, origin)
        distance = math.hypot(*delta)
        if distance > 1e-9:
            toward = tuple(axis / distance for axis in delta)
            if _dot(heading, toward) < cos_half_angle or not combat._blast_path_clear(
                    origin, toward, distance - target.radius, held, deployables, None):
                continue
        found.append((distance, target.entity))
    chosen = min(found, default=None)
    if chosen is not None and chosen[1] in protected:
        # A projected barrier occupies the target's body query and suppresses control.
        chosen = None
    selected = (expr.Entity(chosen[1]),) if chosen is not None else ()
    instance.write(expr.lvalue(tracker.node.field("m_8A2905A0")), selected)


def consume(combat, player, command):
    """Consume the actual single-target ModifyHealth activation exactly once."""
    from ow174.game.combat import aim, damage_tags

    instance = _graph(player)
    state = instance.states.get(10) if instance is not None else None
    if state is None or not state.active or state.ending:
        return
    modify = state.node.field("m_modifyHealth")
    selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
    horizontal = expr.to_float(state.evaluate(modify["m_E33EFEE1"], watch=False))
    vertical = expr.to_float(state.evaluate(modify["m_583C208B"], watch=False))
    loadout = state.evaluate(modify["m_loadout"], watch=False)
    tags = damage_tags(state, modify)
    resource = play.resolve_resource(instance, instance.graph.node(13))
    state.request_finish()
    instance.component.run_now()
    dash = instance.states.get(14)
    if dash is not None and dash.active:
        dash.try_complete()
        instance.component.run_now()
    if not isinstance(selected, tuple) or not isinstance(loadout, expr.Asset):
        return
    for target in combat._enemies(player):
        if expr.Entity(target.entity) not in selected:
            continue
        previous = combat.vitals_of(target).total
        combat._damage(player, target, amount, False, loadout.guid, command.frame, tags=tags)
        if combat.vitals_of(target).total < previous and apply_stun(combat, target, resource):
            dx, _, dz = aim(command.aim()[0], 0)
            combat._apply_impulse(target, (dx * horizontal, vertical, dz * horizontal), carry=True)
