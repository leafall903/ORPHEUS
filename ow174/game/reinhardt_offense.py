"""Scoped authored Rocket Hammer and Fire Strike world contacts."""

import math

from ow174.game import contacts, knockback, mei_wall, reinhardt_barrier, shot_guards
from ow174.game.collision import MOVER
from ow174.game.script import expr

HERO = reinhardt_barrier.HERO
HAMMER = 0x0048
SWINGS = {3: (1, 8), 4: (2, 9)}
FIRE_STRIKE = 0x00E2
FIRE_STRIKE_ENTITY = 0x04000000000001EA
FIRE_STRIKE_LOADOUT = 0x0B90000000000029
SHATTER_LOADOUT = 0x0B90000000000027


def enter(world, state):
    if (world.hero != HERO or state.instance.component.strict
            or (state.instance.graph.index, state.index) != (FIRE_STRIKE, 5)):
        return
    if all(state.node.field(key) is None for key in (
        "m_ED97A97C", "m_7C8AD485", "m_C7A42E2A", "m_F8CE386A", "m_C46403A5",
    )):
        state.request_finish()


def physical_buttons(buttons):
    return buttons | bool(buttons & 0x800)


def segment_pair_distance(a, b, c, d):
    from ow174.game.combat import EPSILON, _along, _dot, _sub, segment_distance

    u, v, w = _sub(b, a), _sub(d, c), _sub(a, c)
    aa, bb, cc = _dot(u, u), _dot(u, v), _dot(v, v)
    dd, ee = _dot(u, w), _dot(v, w)
    candidates = [segment_distance(point, c, d) for point in (a, b)]
    candidates.extend(segment_distance(point, a, b) for point in (c, d))
    determinant = aa * cc - bb * bb
    if determinant > EPSILON * aa * cc:
        s, t = (bb * ee - cc * dd) / determinant, (aa * ee - bb * dd) / determinant
        if 0. <= s <= 1. and 0. <= t <= 1.:
            candidates.append(math.dist(_along(a, u, s), _along(c, v, t)))
    return min(candidates)


def _swing_volumes(instance, indices, feet, basis):
    def transform(point):
        return tuple(feet[i] + sum(axis[i] * value for axis, value in zip(basis, point, strict=True))
                     for i in range(3))

    for index in indices:
        state = instance.states.get(index)
        if state is None or not state.active:
            continue
        a, b = (state.evaluate(state.node.field(key), watch=False)
                for key in ("m_05BE62DD", "m_B3A77CD0"))
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        if isinstance(a, expr.Vec3) and isinstance(b, expr.Vec3) and radius > 0:
            yield transform(a), transform(b), radius


def _hammer_force(fight, state, modify, player, target):
    from ow174.game.combat import _sub

    rings = modify.get("m_knockbackRings") or ()
    if len(rings) != 1:
        return
    ring, = rings
    origin = state.evaluate(modify.get("m_724CBA57"), watch=False)
    point = tuple(origin) if isinstance(origin, expr.Vec3) else tuple(player.mover.position)
    radius = expr.to_float(state.evaluate(ring["m_radius"], watch=False))
    speed = expr.to_float(state.evaluate(ring["m_9102DA3F"], watch=False))
    if radius > 0 and speed > 0 and math.dist(point, target.feet) <= radius:
        fight.impulses.hit(target, _sub(target.feet, point), 0.,
                           knockback.Force(speed, speed, 0., 0., ((radius, 1., 0., 1.),)))


def sweep_surfaces(fight, player, eye, volumes, amount, hits):
    from ow174.game.combat import capsule_visible
    from ow174.game.winston_combat import Dome

    for barrier in reinhardt_barrier.enemy_barriers(fight, player):
        key = ("mesh", barrier.entity)
        if key in hits:
            continue
        if isinstance(barrier, Dome):
            contacts = (barrier.capsule_contact(a, b, radius) for a, b, radius in volumes)
            if any(capsule_visible(fight._line_collision(), eye, point, point, 0.)
                   for point in contacts if point is not None):
                hits.add(key)
                reinhardt_barrier.damage(barrier, amount, attacker=player.body)
            continue
        if not barrier.mesh:
            continue
        anchor, axes = reinhardt_barrier.pose(barrier)

        def world(point, anchor=anchor, axes=axes):
            return tuple(anchor[k] + sum(point[j] * axes[j][k] for j in range(3)) for k in range(3))

        # The authored sweep is reconstructed as two capsules against the mesh.
        for triangle in barrier.mesh:
            triangle = tuple(world(point) for point in triangle)
            if (any(_capsule_triangle(a, b, radius, triangle) for a, b, radius in volumes)
                    and any(capsule_visible(fight._line_collision(), eye, point, point, 0.)
                            for point in triangle)):
                hits.add(key)
                reinhardt_barrier.damage(barrier, amount, attacker=player.body)
                break
    for piece in tuple(fight.walls.pieces.values()):
        key = ("wall", piece.entity)
        if key in hits:
            continue
        for a, b, radius in volumes:
            centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
            if any(math.dist(point, closest) <= radius and fight.walls.reaches(piece, eye, closest)
                   for point in (a, centre, b) if (closest := mei_wall.closest(piece, point)) is not None):
                hits.add(key)
                fight.walls.damage(piece, player, amount, fight.tick)
                break


def _capsule_triangle(a, b, radius, triangle):
    from ow174.game.barrier_geometry import ray_mesh

    delta = tuple(y - x for x, y in zip(a, b, strict=True))
    length = math.hypot(*delta)
    if length > 0 and ray_mesh((triangle,), a, tuple(v / length for v in delta), length) is not None:
        return True
    return (any(math.dist(point, contacts.closest_on_triangle(*triangle, point)) <= radius
                for point in (a, b))
            or any(segment_pair_distance(a, b, c, d) <= radius
                   for c, d in zip(triangle, (*triangle[1:], triangle[0]), strict=True)))


def hammer(fight, player, command, script):
    from ow174.game.combat import capsule_visible, damage_tags
    from ow174.game.sigma_barrier import view_basis

    instance = next((item for item in script.component.instances.values()
                     if item.graph.index == HAMMER), None)
    if instance is None:
        return
    feet, eye = tuple(player.mover.position), fight._eye(player)
    basis = view_basis(*command.aim())
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    for index, queries in SWINGS.items():
        state = instance.states.get(index)
        key = player.body, index
        if state is None or not state.active:
            fight.hammer_swings.pop(key, None)
            continue
        activation = instance.id, state.counter, state.start
        stored, hits = fight.hammer_swings.get(key, (None, set()))
        if stored != activation:
            hits = set()
            fight.hammer_swings[key] = activation, hits
        volumes = tuple(_swing_volumes(instance, queries, feet, basis))
        if not volumes:
            continue
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
        amount *= fight.outgoing_scale(player.body)
        loadout = state.evaluate(modify["m_loadout"], watch=False)
        loadout = loadout.guid if isinstance(loadout, expr.Asset) else None
        tags = damage_tags(state, modify)
        sweep_surfaces(fight, player, eye, volumes, amount, hits)
        for target in fight._enemies(player):
            victim = ("body", target.entity, fight.replacement_ticks.get(target.entity))
            shown = fight.seen(target, view_tick)
            if victim in hits or shown is None:
                continue
            a, b = shown.segment()
            if (not any(segment_pair_distance(c, d, a, b) <= r + shown.radius for c, d, r in volumes)
                    or not capsule_visible(fight._line_collision(), eye, a, b, shown.radius)):
                continue
            hits.add(victim)
            if (shot_guards.parries_melee(fight, shown, feet, tags)
                    or reinhardt_barrier.protect_body(fight, target, amount, attacker=player.body)):
                continue
            before = fight.vitals_of(target).total
            fight._damage(player, target, amount, False, loadout, command.frame, tags=tags,
                          source_body=player.body, source_scaled=True)
            if fight.vitals_of(target).total < before:
                _hammer_force(fight, state, modify, player, target)


def strike_radius(state):
    return max(expr.to_float(state.evaluate(state.node.field(f"m_6396149F.{key}"), watch=False))
               for key in ("m_57DAE1B5", "m_61CD1F5B"))


def fly_strike(fight, rocket, duration, returns):
    from ow174.game.combat import EPSILON, _along, _dot, ray_capsule

    origin, direction = rocket.position, rocket.direction
    reach = rocket.speed * duration
    contact = contacts.scene_sweep(fight._collision().engine, origin, _along(origin, direction, reach),
                                   ((0., 0., 0.),), rocket.body_radius, MOVER)
    wall = contact[0] * reach if contact is not None and _dot(direction, contact[2]) < -EPSILON else None
    ice = fight.walls.cast(origin, direction, reach if wall is None else wall, radius=rocket.body_radius)
    if ice is not None:
        wall = ice[0]
    limit = reach if wall is None else wall
    guard = fight._projectile_guard(rocket, limit, returns < shot_guards.MAX_RETURNS,
                                    margin=rocket.body_radius)
    if guard is not None:
        limit = guard[0]
    contacts_at = []
    for barrier in reinhardt_barrier.enemy_barriers(fight, rocket.shooter, team=rocket.team):
        if not barrier.mesh:
            continue
        at = reinhardt_barrier.mesh_ray(barrier, origin, direction, limit)
        if at is not None:
            contacts_at.append((at, 0, barrier.entity, ("mesh", barrier.entity), barrier))
    for target in fight._enemies(rocket.shooter, team=rocket.team):
        a, b = target.segment()
        at = (target.ray(origin, direction, limit) if target.is_device
              else ray_capsule(origin, direction, a, b, target.radius + rocket.body_radius))
        if at is not None and at <= limit:
            key = ("body", target.entity, fight.replacement_ticks.get(target.entity))
            contacts_at.append((at, 1, target.entity, key, target))
    amount = rocket.direct.amount * rocket.damage_scale
    for at, kind, _, key, target in sorted(contacts_at):
        if key in rocket.strike_hits or ((wall is not None or guard is not None) and at >= limit):
            continue
        rocket.strike_hits.add(key)
        if not kind:
            reinhardt_barrier.damage(target, amount, attacker=rocket.source_body)
        elif not reinhardt_barrier.protect_body(fight, target, amount, attacker=rocket.source_body):
            fight._damage(rocket.shooter, target, amount, False, rocket.direct.loadout, fight.tick,
                          world_tick=fight.tick, tags=rocket.direct.tags, source_body=rocket.source_body,
                          source_scaled=True)
    travelled = limit / rocket.speed
    rocket.position = _along(origin, direction, limit)
    rocket.flown += travelled
    rocket.left = max(0., rocket.left - travelled)
    if guard is not None:
        _, state, owner, direction = guard
        if rocket.shot is not None:
            fight.projectiles._end(rocket.shot, fight.tick, explode=False)
            rocket.shot = None
        if state.instance.graph.index != 0x03A3:
            fight._landed(rocket)
            if state.instance.graph.index == 0x1D02:
                shot_guards.absorb(state, amount, rocket.source_body)
            return
        rocket.shooter, rocket.team, rocket.source_body = owner.owner, owner.team, owner.entity
        rocket.damage_scale = fight.outgoing_scale(owner.entity)
        rocket.direction = direction
        rocket.position = _along(rocket.position, direction, .0001)
        if duration > travelled and rocket.left > EPSILON:
            fight._fly_step(rocket, duration - travelled, returns + 1)
        elif rocket.left <= EPSILON:
            fight._landed(rocket)
        return
    if ice is not None:
        fight.walls.damage(ice[1], rocket.shooter, amount, fight.tick)
    if wall is not None or rocket.left <= EPSILON:
        fight._landed(rocket)


def native(world, cfg, instance):
    if (world.match is None or world.hero != HERO or instance is None or instance.component.strict
            or instance.graph.index not in (HAMMER, 0x00BE)):
        return NotImplemented
    cls = cfg.get("$")
    if cls not in ("STU_71A87E92", "STU_021D01CD", "STU_C8C4C20D", "STU_66F3B89E", "STU_2200A66D"):
        return NotImplemented
    actor = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if actor is None or (instance.graph.index == HAMMER
                         and instance.evaluate(cfg.get("m_entity")) != expr.Entity(world.body)):
        return NotImplemented
    yaw = actor.mover.yaw * math.tau / 65536
    if cls == "STU_71A87E92":
        return expr.Vec3(*actor.mover.position)
    if cls == "STU_021D01CD":
        return expr.Vec3(math.sin(yaw), 0., math.cos(yaw))
    if cls == "STU_C8C4C20D":
        return actor.mover.yaw * 360. / 65536
    if instance.graph.index == HAMMER:
        if cls == "STU_66F3B89E":
            return expr.Vec3(math.cos(yaw), 0., -math.sin(yaw))
        from ow174.game.combat import aim

        return expr.Vec3(*aim(actor.mover.yaw, actor.mover.pitch))
    return NotImplemented
