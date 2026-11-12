"""Original0016 exposure queries over the existing, settled combat geometry.

Prediction must not synchronize hosts, advance a VM, allocate an actor or spend
health. The returned actors and services are the same objects used by combat.
"""

import math
from functools import partial

from ow174.game import contacts, cryo, deflection, ice_wall, mei_wall, reinhardt_barrier, sigma_barrier
from ow174.game.collision import MOVER


def _world_triangles(world):
    for index, mask in enumerate(world.masks):
        if mask & MOVER:
            base = index * 13
            yield tuple(tuple(world.data[base + k:base + k + 3]) for k in (0, 3, 6))


def _mesh_closest(position, axes, triangles, point):
    delta = contacts.sub(point, position)
    local = tuple(contacts.dot(delta, axis) for axis in axes)
    closest = min((contacts.closest_on_triangle(*corners, local) for corners in triangles),
                  key=lambda p: math.dist(p, local), default=None)
    if closest is None:
        return None
    return tuple(position[k] + sum(axis[k] * value for axis, value in zip(axes, closest, strict=True))
                 for k in range(3))


def _sphere_closest(actor, point):
    delta = contacts.sub(point, actor.point)
    distance = math.hypot(*delta)
    direction = tuple(value / distance for value in delta) if distance > 1e-8 else (0., 0., 1.)
    return tuple(a + actor.radius * b for a, b in zip(actor.point, direction, strict=True))


def _shields(fight, shooter):
    """Yield current actors/services and their pure ray/closest-point readers."""
    for actor in fight.photon_barriers.opponents(shooter):
        service = fight.photon_barriers
        yield actor, service, partial(service.contact, actor), partial(service.closest, actor)

    service = fight.barriers.reinhardt
    native_bodies = set(service.actors)
    for actor in service.actors.values():
        if (not actor.active or actor.health <= 0 or actor.owner is shooter
                or actor.body != actor.owner.body
                or (not fight.match.game_map.free_for_all and actor.owner.team == shooter.team)):
            continue
        position, axes = reinhardt_barrier.frame(
            actor.geometry, *actor.pose())
        triangles = tuple(tuple(tuple(t[k:k + 3]) for k in (0, 3, 6))
                          for t in actor.geometry.triangles)
        yield actor, service, actor.trace, partial(_mesh_closest, position, axes, triangles)

    # The existing selected native-field path has a different attachment from
    # the raised actor. Read that geometry only when it has no raised actor.
    service = fight.barrier
    if service.model_sha256 is not None:
        from ow174.game import barrier
        mesh = barrier.mesh()
        for owner in fight.match.players:
            if (owner.body in native_bodies or owner is shooter or owner.body not in service.children
                    or service.health.get(owner.body, 0) <= 0 or owner.body_script is None
                    or (not fight.match.game_map.free_for_all and owner.team == shooter.team)):
                continue
            instance = next((i for i in owner.body_script.component.instances.values()
                             if i.graph.index == 0x95), None)
            state = instance.states.get(17) if instance is not None else None
            if state is None or not state.active or state.node.pos != 51:
                continue
            axes = deflection.basis(owner.mover.yaw, 0)
            position = tuple(a + b for a, b in zip(fight._eye(owner), axes[2], strict=True))
            triangles = tuple(_world_triangles(mesh))
            def trace(origin, direction, reach, owner=owner, service=service):
                return next((distance for distance, body, _ in service.intersections(
                    shooter, origin, direction, reach, include_endpoint=True) if body == owner.body), None)
            yield owner, service, trace, partial(_mesh_closest, position, axes, triangles)

    service = fight.barriers.world
    for actor in service.enemies(shooter):
        yield actor, service, partial(service.trace, shooter, bubbles=(actor,)), partial(_sphere_closest, actor)

    service = fight.orisa_barriers
    if service.geometry is not None:
        for actor in service.opponents(shooter):
            yield actor, service, partial(service.geometry.cast, actor.position, actor.yaw), partial(service.closest, actor)

    # sigma_barrier.enemies() updates its host and VM. Prediction instead reads
    # the exact geometry/pose already settled by the sole combat update.
    for actor in fight.sigma_barriers.values():
        if (not actor.active or actor.vitals.total <= 0 or actor.owner is shooter
                or actor.owner_body != actor.owner.body or actor.instance.component.owner_dead
                or (not fight.match.game_map.free_for_all and actor.owner.team == shooter.team)):
            continue
        triangles = tuple(_world_triangles(actor.geometry))
        yield actor, sigma_barrier, actor.intersection, partial(
            _mesh_closest, actor.position, actor.basis, triangles)


def cover_ray(fight, origin, direction, reach, *, exclude=None):
    from ow174.game.combat import ray_capsule, ray_world

    nearest = ray_world(fight._collision(), origin, direction, reach)
    ice = fight.walls.cast(origin, direction, reach)
    if ice is not None and (nearest is None or ice[0] < nearest):
        nearest = ice[0]
    for target in fight._targets():
        if target.owner is exclude or target.owner not in fight.match.players or not cryo.active(target.owner):
            continue
        at = ray_capsule(origin, direction, *target.segment(), target.radius)
        if at is not None and at <= reach and (nearest is None or at < nearest):
            nearest = at
    return nearest


def visible_target(fight, origin, target):
    from ow174.game.combat import EPSILON
    from ow174.game.symmetra_turrets import _capsule_ray

    direction, reach = _capsule_ray(origin, target)
    if reach <= EPSILON:
        return True
    wall = cover_ray(fight, origin, direction, reach)
    return wall is None or wall >= reach - EPSILON


def trace_barrier(fight, shooter, origin, direction, limit):
    from ow174.game.combat import _source_entity

    hits = []
    for actor, service, trace, _closest in _shields(fight, shooter):
        hit = trace(origin, direction, limit)
        distance = hit[0] if isinstance(hit, tuple) else hit
        if distance is not None:
            hits.append((distance, actor, service))
    ice = fight.walls.cast(origin, direction, limit)
    if ice is not None:
        distance, piece, _ = ice
        if (piece.owner != _source_entity(shooter)
                and (fight.match.game_map.free_for_all or piece.team != shooter.team)):
            hits.append((distance, piece, fight.walls))
    return min(hits, key=lambda hit: hit[0], default=None)


def splash_targets(fight, rocket, point, visible_from, *, targets=None):
    from ow174.game.combat import segment_distance
    from ow174.game.symmetra_turrets import _capsule_ray

    blast = rocket.blast
    if blast is None:
        return
    if targets is None:
        targets = (t for t in fight._enemies(rocket.shooter, team=rocket.team)
                   if getattr(t.owner, "definition", None) != ice_wall.PILLAR)
    for target in targets:
        distance = max(0.0, segment_distance(point, *target.segment()) - target.radius)
        scalar = blast.splash(distance)
        if scalar <= 0 or not visible_target(fight, visible_from, target):
            continue
        direction, reach = _capsule_ray(visible_from, target)
        if trace_barrier(fight, rocket.shooter, visible_from, direction, reach) is None:
            yield target, blast.amount * scalar


def splash_barriers(fight, rocket, point, visible_from, impact):
    from ow174.game.combat import _source_entity

    blast = rocket.blast
    if blast is None:
        return
    reach = blast.rings[-1][0] if blast.rings else 0
    contacts = [(actor, service, closest(point)) for actor, service, _trace, closest in _shields(fight, rocket.shooter)]
    contacts.extend((piece, fight.walls, mei_wall.closest(piece, point))
                    for piece in fight.walls.pieces.values()
                    if piece.owner != _source_entity(rocket.shooter)
                    and (fight.match.game_map.free_for_all or piece.team != rocket.team))
    for actor, service, contact in contacts:
        if contact is None or (impact is not None and actor is impact[0] and service is impact[1]):
            continue
        distance = math.dist(point, contact)
        scalar = blast.splash(distance)
        if distance > reach or scalar <= 0:
            continue
        delta = tuple(a - b for a, b in zip(contact, visible_from, strict=True))
        length = math.hypot(*delta)
        if length > 1e-6:
            direction = tuple(value / length for value in delta)
            limit = length - 1e-6
            if cover_ray(fight, visible_from, direction, limit) is not None or trace_barrier(
                    fight, rocket.shooter, visible_from, direction, limit) is not None:
                continue
        yield actor, service, blast.amount * scalar
