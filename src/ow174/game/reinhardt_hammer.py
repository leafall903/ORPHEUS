"""Delivered0007 swing contacts adapted to existing canonical actors/pools.

Authored capsule queries, mesh contact and shell contact are retained. This
module owns no actors, pools or movement; shared managers retain lifecycle.
"""
import math

from ow174.game import contacts, reinhardt_barrier, sigma_barrier, world_bubbles
from ow174.game.collision import MOVER

def overlaps_frame(triangles, position, axes, a, b, radius, *, visible=None):
    """Intersect a melee capsule with local triangles in a known world pose."""
    points = tuple(tuple(sum((p[k] - position[k]) * axis[k] for k in range(3)) for axis in axes)
                   for p in (a, b))
    low = tuple(min(points[0][k], points[1][k]) - radius for k in range(3))
    high = tuple(max(points[0][k], points[1][k]) + radius for k in range(3))
    for triangle in triangles:
        corners = tuple(tuple(triangle[n:n + 3]) for n in (0, 3, 6))
        if any(max(v[k] for v in corners) < low[k] or min(v[k] for v in corners) > high[k]
               for k in range(3)):
            continue
        distance = contacts.gjk(corners, points, reach=radius)
        if distance.distance <= radius:
            point = tuple(position[k] + sum(axes[n][k] * distance.point_a[n] for n in range(3))
                          for k in range(3))
            if visible is None or visible(point):
                return True
    return False


def shell_overlap(actor, a, b, radius, visible):
    centre, shell_radius = actor.point, actor.radius
    offset = tuple(x - y for x, y in zip(a, centre, strict=True))
    segment = tuple(y - x for x, y in zip(a, b, strict=True))
    length2 = sum(axis * axis for axis in segment)
    share = -sum(x * y for x, y in zip(offset, segment, strict=True)) / length2 if length2 else 0
    point = tuple(x + max(0.0, min(1.0, share)) * y for x, y in zip(a, segment, strict=True))
    if math.dist(point, centre) > shell_radius + radius:
        return False
    far = max((a, b), key=lambda p: math.dist(p, centre))
    if math.dist(far, centre) < shell_radius - radius:
        return False
    candidates = (point, a, b)
    for candidate in candidates:
        distance = math.dist(candidate, centre)
        if distance and abs(distance - shell_radius) <= radius:
            shell = tuple(c + (p - c) * shell_radius / distance
                          for c, p in zip(centre, candidate, strict=True))
            if visible(shell):
                return True
    length = math.sqrt(length2)
    if not length:
        return False
    direction = tuple(axis / length for axis in segment)
    at = world_bubbles.shell(a, direction, centre, shell_radius, length)
    if at is None or at > length:
        return False
    return visible(tuple(x + at * y for x, y in zip(a, direction, strict=True)))


def _axes(yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return ((c, 0., -s), (0., 1., 0.), (s, 0., c))


def _flat(triangles):
    return tuple(tuple(v for point in triangle for v in point) for triangle in triangles)


def _wall_clear(fight, origin, point):
    # Delivered endpoint exclusion: a pillar's own visible face is hittable.
    from ow174.game.mei_wall import EPS
    delta = tuple(b - a for a, b in zip(origin, point, strict=True))
    length = math.sqrt(sum(v * v for v in delta))
    if length < EPS:
        return True
    direction = tuple(v / length for v in delta)
    start = tuple(a + EPS * d for a, d in zip(origin, direction, strict=True))
    return fight.walls.cast(start, direction, max(0., length - 2 * EPS)) is None


def surfaces(fight, player, volumes, amount, seen):
    """One visible contact per existing surface and swing, including late entry."""
    from ow174.game.combat import capsule_visible
    from ow174.game import nano

    eye = fight._eye(player)
    amount *= nano.offense(fight, player.body) * fight.orisa.superchargers.scale(player)

    def visible(point):
        return (_wall_clear(fight, eye, point)
                and capsule_visible(fight._collision(), eye, point, point, 0.))

    def key(body, actor):
        return ("surface", actor)

    def mesh_hit(triangles, position, axes):
        return any(overlaps_frame(triangles, position, axes, a, b, radius, visible=visible)
                   for a, b, radius in volumes)

    for actor in fight.barriers.enemies(player):
        # Particle barriers retain their body interception policy in _damage.
        if isinstance(actor, reinhardt_barrier.RaisedBarrier):
            token = key(actor.body, actor.entity)
            feet, yaw, pitch, radius, height = actor.pose()
            position, axes = reinhardt_barrier.frame(actor.geometry, feet, yaw, pitch, radius, height)
            hit = token not in seen and mesh_hit(actor.geometry.triangles, position, axes)
        elif isinstance(actor, world_bubbles.FixedBubble):
            token = key(actor.source_body, actor.body)
            hit = token not in seen and any(shell_overlap(actor, a, b, radius, visible)
                                            for a, b, radius in volumes)
        else:
            continue
        if hit:
            fight.barriers.damage(actor, amount, player)
            seen.add(token)

    corners = ((-1., -1.1, 0.), (1., -1.1, 0.), (1., 1.1, 0.), (-1., 1.1, 0.))
    triangles = (corners[0]+corners[1]+corners[2], corners[0]+corners[2]+corners[3])
    for shield, centre, normal, right, up in fight.brigitte._barrier_faces(player):
        body = shield.instance.component.entity
        token = ("surface", body, fight.replacement_ticks.get(body))
        if token not in seen and mesh_hit(triangles, centre, (right, up, normal)):
            fight.brigitte.damage_barrier(shield, amount)
            seen.add(token)

    for actor in sigma_barrier.enemies(fight, player):
        token = key(actor.owner_body, actor.entity)
        triangles = tuple(tuple(actor.geometry._triangle(i)[:9]) for i, mask in enumerate(actor.geometry.masks)
                          if mask & MOVER)
        if token not in seen and mesh_hit(triangles, actor.position, actor.basis):
            sigma_barrier.damage_barrier(fight, actor, amount)
            seen.add(token)

    for manager in (fight.orisa_barriers, fight.photon_barriers):
        if manager.geometry is None:
            if manager is fight.photon_barriers:
                for actor in manager.opponents(player):
                    token = key(actor.source_body, actor.body)
                    if token not in seen and any(visible(point) for a,b,radius in volumes
                            for point in manager.capsule_contacts(actor,a,b,radius)):
                        manager.damage(actor,amount)
                        seen.add(token)
            continue
        for actor in manager.opponents(player):
            token = key(actor.source_body, actor.body)
            if token not in seen and mesh_hit(_flat(manager.geometry.triangles), actor.position, _axes(actor.yaw)):
                manager.damage(actor, amount)
                seen.add(token)


def objects(fight, player, volumes, amount, seen):
    """Delivered turret/device and pillar consumers over their current managers."""
    from ow174.game.combat import capsule_visible
    from ow174.game import mei_wall, nano

    eye = fight._eye(player)
    amount *= nano.offense(fight, player.body) * fight.orisa.superchargers.scale(player)

    def visible(point):
        return (_wall_clear(fight, eye, point)
                and capsule_visible(fight._collision(), eye, point, point, 0.))

    manager = fight.orisa.superchargers
    if manager.geometry is not None:
        for device in tuple(manager.opponents(player)):
            token = ("supercharger", device.entity)
            if token not in seen and any(overlaps_frame(_flat(manager.geometry.triangles), device.position,
                    _axes(device.yaw), a, b, radius, visible=visible) for a, b, radius in volumes):
                seen.add(token)
                manager.damage(device, amount)

    # Body visibility is decided before a swing can remove an occluding pillar.
    for piece in tuple(fight.walls.pieces.values()):
        token = ("ice_wall", piece.entity)
        body_token = ("body", (piece.entity, fight.replacement_ticks.get(piece.entity)))
        if token in seen or body_token in seen:
            continue
        for a, b, radius in volumes:
            length = math.dist(a, b)
            direction = tuple((y - x) / length for x, y in zip(a, b, strict=True)) if length else (0., 0., 0.)
            if mei_wall.ray(piece, a, direction, max(length, 2 * mei_wall.EPS), radius=radius, height=radius) is None:
                continue
            point = mei_wall.closest(piece, eye)
            if visible(point):
                seen.add(token)
                fight.walls.damage(piece, player, amount)
            break
