"""Delivered0044/0045 pulse clocks over the combined body's collision services.

Damage34, five pulses/second, root radius/duration and per-target scheduling
are retained from the Reaper delivery. Surface poses and visibility remain
the combined host's reconstructed policies, not native effect attachment.
"""

import math

from ow174.game import (
    barrier, barriers, contacts, deflection, mei_wall, nano, photon_barrier,
    reaper, reinhardt_barrier, sigma_barrier, world_bubbles,
)
from ow174.game.collision import MOVER


def _key(fight, kind, actor):
    if kind == "legacy":
        owner = next((p for p in fight.match.players if p.body == actor), None)
        script = getattr(owner, "body_script", None)
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x95), None) if script else None
        phase = instance.states.get(17) if instance else None
        return (actor, phase.counter, phase.start) if phase is not None else (actor,)
    if kind == "brigitte":
        return (actor.instance.component.entity,)
    if isinstance(actor, reinhardt_barrier.RaisedBarrier):
        phase = actor.instance.states.get(17)
        return (actor.body, phase.counter, phase.start) if phase is not None else (actor.body,)
    return (getattr(actor, "entity", getattr(actor, "body", None)),)


def _mesh_point(scene, local):
    points = []
    for index, mask in enumerate(scene.masks):
        if mask & MOVER:
            start = index * 13
            corners = [tuple(scene.data[start + k:start + k + 3]) for k in (0, 3, 6)]
            points.append(contacts.closest_on_triangle(*corners, local))
    return min(points, key=lambda p: math.dist(p, local), default=None)


def _transform_point(scene, origin, centre, basis):
    delta = tuple(a - b for a, b in zip(origin, centre, strict=True))
    local = tuple(contacts.dot(delta, axis) for axis in basis)
    point = _mesh_point(scene, local)
    if point is not None:
        return tuple(centre[k] + sum(point[j] * basis[j][k] for j in range(3)) for k in range(3))
    return None


def _surfaces(fight, player, origin, radius):
    """Yield typed shield contacts; never manufacture another shield health pool."""
    from ow174.game.combat import HitDef

    scene = barrier.mesh()
    legacy = set(fight.barrier.children) if scene is not None else set()
    for actor in fight.barriers.enemies(player):
        if isinstance(actor, reinhardt_barrier.RaisedBarrier):
            if actor.body in legacy:
                continue
            # This native-mesh adapter supplies visible nearest surface samples.
            samples = []
            def sample(point):
                samples.append(point)
                return True
            list(actor.surface_distances(origin, radius, sample))
            point = min(samples, key=lambda p: math.dist(origin, p), default=None)
        else:
            centre = actor.centre()
            extent = actor.radius if isinstance(actor, world_bubbles.FixedBubble) else barriers.RADIUS
            distance = math.dist(origin, centre)
            # Fixed world shells retain their exit surface for an inside origin.
            if isinstance(actor, world_bubbles.FixedBubble):
                point = tuple(centre[k] - (centre[k] - origin[k]) * extent / distance
                              for k in range(3)) if distance else (centre[0], centre[1], centre[2] + extent)
            else:
                point = tuple(centre[k] - (centre[k] - origin[k]) * extent / distance
                              for k in range(3)) if distance > extent else origin
        if point is not None:
            yield ("combined", actor, None), point
    if scene is not None:
        for target in fight._enemies(player):
            owner = target.owner
            if target.entity not in legacy or getattr(getattr(owner, "hero", None), "guid", None) != barrier.REINHARDT:
                continue
            script = owner.body_script
            instance = next((i for i in script.component.instances.values() if i.graph.index == 0x95), None) if script else None
            phase = instance.states.get(17) if instance else None
            if phase is None or not phase.active or fight.is_stunned(owner):
                continue
            remaining = fight.barrier.health.get(target.entity, instance.vars[587].value())
            if remaining <= 0:
                continue
            axes = deflection.basis(owner.mover.yaw, 0)
            centre = tuple(a + b for a, b in zip(fight._eye(owner), axes[2], strict=True))
            point = _transform_point(scene, origin, centre, axes)
            if point is not None:
                yield ("legacy", target.entity, remaining), point
    for manager in (fight.orisa_barriers, fight.photon_barriers):
        if manager.geometry is None:
            continue
        for actor in manager.opponents(player):
            yield ("orisa" if manager is fight.orisa_barriers else "photon", actor, None), manager.closest(actor, origin)
    for actor in fight.sigma_barriers.values():
        if (not actor.active or actor.vitals.dead or actor.owner is player
                or (not fight.match.game_map.free_for_all and actor.owner.team == player.team)):
            continue
        point = _transform_point(actor.geometry, origin, actor.position, actor.basis)
        if point is not None:
            yield ("sigma", actor, None), point
    # Preserve the combined Brigitte face policy and its terminal health sentinel.
    blast = HitDef(amount=1.0, rings=((radius, 1.0),))
    for actor, _ in fight.brigitte.blast_barriers(player, origin, blast):
        yield ("brigitte", actor, None), None


def _contact(fight, player, origin, direction, reach):
    contact = fight._barrier_contact(player, origin, direction, reach, bubbles=True)
    nearest = None
    if contact is not None:
        distance, remaining, actor = contact
        if type(actor) is int:
            kind = "legacy"
        elif isinstance(actor, sigma_barrier.Barrier):
            kind = "sigma"
        elif isinstance(actor, photon_barrier.Shield):
            kind = "photon"
        elif actor in fight.orisa_barriers.active.values():
            kind = "orisa"
        else:
            kind = "combined"
        nearest = distance, (kind, actor, remaining)
    raised = fight.brigitte.barrier_ray(player, origin, direction, reach)
    if raised is not None and (nearest is None or raised[0] < nearest[0]):
        nearest = raised[0], ("brigitte", raised[1], None)
    return nearest


def _damage_shield(fight, player, shield, amount):
    kind, actor, remaining = shield
    if kind == "legacy":
        remaining = fight.barrier.health.get(actor, remaining)
        fight.barrier.damage(actor, remaining, amount)
        return fight.barrier.health.get(actor, 0) > 0
    if kind == "sigma":
        sigma_barrier.damage_barrier(fight, actor, amount)
        return actor.active and not actor.vitals.dead
    if kind == "orisa":
        fight.orisa_barriers.damage(actor, amount)
    elif kind == "photon":
        fight.photon_barriers.damage(actor, amount)
    elif kind == "brigitte":
        fight.brigitte.damage_barrier(actor, amount)
        return actor.health > 1
    else:
        fight.barriers.damage(actor, amount, player)
    return actor.health > 0


def advance(fight, player, *, register=False):
    from ow174.game.combat import EPSILON, _dot, _sub, capsule_visible, ray_world, segment_distance

    state = reaper.blossom_state(player.body_script)
    channel = fight.reaper_blossoms.get(player.body)
    if state is None and channel is None:
        return
    if fight.is_dead(player) or (state is None and fight.now < channel["end"] - EPSILON):
        if channel is not None:
            channel["end"] = min(channel["end"], fight.now)
            channel["ended"] = True
            channel["targets"].clear()
        return
    activation = (state.instance.id, state.counter, state.start) if state is not None else channel["activation"]
    if channel is None or channel["activation"] != activation:
        if not register:
            return
        duration = float(state.evaluate(state.node.field("m_timeout"), watch=False))
        tracker = state.instance.states.get(5)
        if tracker is None:
            return
        radius = float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
        start = fight.now - max(0.0, (state.now - state.start) / 1000.0)
        channel = fight.reaper_blossoms[player.body] = {
            "activation": activation, "start": start, "end": start + duration,
            "radius": radius, "ended": False, "targets": {}, "last_tick": None,
        }
    if channel["ended"] or channel["last_tick"] == fight.tick:
        return
    channel["last_tick"] = fight.tick
    x, y, z = player.mover.position
    origin = x, y + 1.8, z
    available = {}
    for target in fight._enemies(player):
        a, b = target.segment()
        if segment_distance(origin, a, b) - target.radius > channel["radius"]:
            continue
        point = target.feet[0], (a[1] + b[1]) / 2, target.feet[2]
        delta = _sub(point, origin)
        distance = math.sqrt(_dot(delta, delta))
        direction = tuple(v / distance for v in delta) if distance else (0.0, 0.0, 1.0)
        wall = ray_world(fight._collision(), origin, direction, distance)
        reach = min(distance, channel["radius"], wall if wall is not None else distance)
        shield = _contact(fight, player, origin, direction, reach)
        pillar = fight.walls.cast(origin, direction, shield[0] if shield is not None else reach)
        clear = wall is None or wall >= distance
        # New-style pillar deployables remain blockers for legacy body capsules.
        clear = clear and capsule_visible(fight._collision(), origin, point, point, 0.0,
                                         blockers=tuple(t for t in fight._ice_wall_blockers() if t.entity != target.entity))
        if pillar is not None:
            if clear:
                available[target.entity] = None
        elif shield is not None:
            descriptor = shield[1]
            available[_key(fight, *descriptor[:2])] = descriptor
            if clear:
                available[target.entity] = None
        elif clear:
            available[target.entity] = target
    for descriptor, point in _surfaces(fight, player, origin, channel["radius"]):
        key = _key(fight, *descriptor[:2])
        if point is not None:
            distance = math.dist(point, origin)
            if distance > channel["radius"]:
                continue
            direction = tuple((point[k] - origin[k]) / distance for k in range(3)) if distance else (0, 0, 1)
            wall = ray_world(fight._collision(), origin, direction, distance)
            if wall is not None and wall < distance - EPSILON:
                continue
            if fight.walls.cast(origin, direction, distance) is not None:
                available.setdefault(key, None)
                continue
        available[key] = descriptor
    for piece in fight.walls.pieces.values():
        if (piece.owner == player.body or
                (not fight.match.game_map.free_for_all and piece.team == player.team)):
            continue
        surface = mei_wall.closest(piece, origin)
        distance = math.dist(surface, origin)
        if distance > channel["radius"] or not capsule_visible(fight._collision(), origin, surface, surface, 0.0):
            continue
        available[piece.entity] = None
        direction = tuple((surface[k] - origin[k]) / distance for k in range(3)) if distance else (0, 0, 1)
        blocked = fight.walls.cast(origin, direction, distance)
        if blocked is not None and blocked[1] is not piece and blocked[0] < distance - EPSILON:
            continue
        if _contact(fight, player, origin, direction, distance) is None:
            available[piece.entity] = ("pillar", piece, None)
    manager = fight.orisa.superchargers
    if manager.geometry is not None:
        for device in manager.opponents(player):
            surface = manager.closest(device, origin)
            distance = math.dist(surface, origin)
            direction = tuple((surface[k] - origin[k]) / distance for k in range(3)) if distance else (0, 0, 1)
            if (distance <= channel["radius"] and capsule_visible(fight._collision(), origin, surface, surface, 0., walls=fight.walls)
                    and _contact(fight, player, origin, direction, distance) is None):
                available[device.entity] = ("supercharger", device, None)
    boost = fight._damage_boost(player, frame=fight.tick)
    amount = reaper.BLOSSOM_DAMAGE * (boost[1] if boost is not None else 1.0)
    surface_amount = fight._team_damage(player, amount) * fight.orisa.superchargers.scale(player) * nano.offense(fight, player.body)
    clocks = channel["targets"]
    for key in list(clocks):
        if key not in available:
            del clocks[key]
    for key, target in available.items():
        if key not in clocks:
            if fight.now >= channel["end"]:
                continue
            clocks[key] = max(channel["start"], fight.now)
        due = clocks[key]
        while due <= fight.now + EPSILON and due < channel["end"] - EPSILON:
            due += reaper.BLOSSOM_INTERVAL
            if type(target) is tuple:
                kind, actor, _ = target
                if kind == "pillar":
                    fight.walls.damage(actor, player, surface_amount)
                    live = actor.entity in fight.walls.pieces
                elif kind == "supercharger":
                    manager.damage(actor, surface_amount)
                    live = actor.health > 0
                else:
                    live = _damage_shield(fight, player, target, surface_amount)
                if not live:
                    break
            elif target is not None:
                fight._damage(player, target, amount, False, reaper.BLOSSOM_LOADOUT,
                              fight.tick, world_tick=fight.tick, award_ult=False, boost_credit=boost)
                vitals = fight.vitals.get(target.entity)
                if vitals is None or vitals.dead or vitals.total <= 0:
                    break
        clocks[key] = due
    if fight.now >= channel["end"]:
        channel["ended"] = True
        clocks.clear()
