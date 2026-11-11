"""Moira Biotic Orb movement and aura from the reviewed 104319 resources."""

import math
from dataclasses import dataclass

from ow174.game import moira, sigma_grasp, tracer
from ow174.game.body_protection import active as protection
from ow174.game.script import expr

HEAL_ORB = 0x0400000000001E60
DAMAGE_ORB = 0x0400000000001E61
ORB_SLOW = 0.275
DAMAGE_ORB_TAGS = (0x0D80000000002DFD, 0x0D80000000002F99, 0x0D80000000004D29, 0x0D80000000004910)
REFLECTED_DAMAGE_ORB_TAGS = (*DAMAGE_ORB_TAGS[:2], 0x0D800000000019C4, *DAMAGE_ORB_TAGS[2:])


@dataclass
class Orb:
    shooter: object
    body: int
    launch_body: int
    team: int
    position: tuple
    direction: tuple
    speed: float
    left: float
    energy: float
    healing: bool
    last_tick: int
    shot: object
    absorption_amount: float
    flown: float = 0.0
    bounces: int = 0


def launch(fight, player, state, definition, speed, lifetime, origin, direction, shot):
    from ow174.game import combat

    if (getattr(getattr(player, "hero", None), "guid", None) != moira.HERO
            or state.instance.graph.index != 0x10E4 or definition not in (HEAL_ORB, DAMAGE_ORB)):
        return False
    healing = definition == HEAL_ORB
    component = state.instance.component
    # 10FF/4 and 1100/13 snapshot the launch owner's energy multiplier.
    key, base = (12618, 300.0) if healing else (12619, 200.0)
    slot = component.entity_var(key, create=False)
    scalar = slot.value() if slot is not None else None
    if scalar is None:
        scalar = component.world.ruleset(key)
    energy = expr.f32(base * expr.to_float(scalar))
    energy = max(0.0, energy) if math.isfinite(energy) else 0.0
    direct = combat.hit_def(state, state.node.field("m_6396149F"))
    amount = max(0.0, direct.amount) if direct is not None else 0.0
    fight.moira_orbs.append(Orb(player, player.body, player.body, player.team, origin, direction,
                               speed, lifetime, energy, healing, fight.tick, shot, amount))
    return True


def _orb_targets(fight, orb):
    from ow174.game import combat

    radius = 5.0 if orb.healing else 4.0
    for target in fight._targets():
        vitals = fight.vitals_of(target)
        if vitals.dead:
            continue
        friendly = target.owner is orb.shooter or (
            not fight.match.game_map.free_for_all and target.team == orb.team)
        if orb.healing != friendly:
            continue
        if orb.healing and vitals.total >= vitals.maximum - combat.EPSILON:
            continue
        if not orb.healing and (tracer.recalling(target.owner) or protection(target.owner)):
            continue
        a, b = target.segment()
        if combat.segment_distance(orb.position, a, b) - target.radius > radius:
            continue
        centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = combat._sub(centre, orb.position)
        distance = math.hypot(*delta)
        if distance > combat.EPSILON:
            direction = tuple(axis / distance for axis in delta)
            wall = combat.ray_world(fight._collision(), orb.position, direction, distance)
            if ((wall is not None and wall < distance)
                    or fight.walls.cast(orb.position, direction, distance) is not None):
                continue
        yield target


def _orb_move(fight, orb, distance, duration):
    """Point wall/Genji sweep with bounded contacts and preserved flight budgets.

    Genji contact reuses his lane's declared capsule/frontal Deflect admission.
    It transfers both health operations and body attribution, retires the old
    prediction, and preserves remaining life/energy. Native boxes/filters and
    reflected client motion remain separate acceptance questions.
    """
    from ow174.game import combat

    total = distance
    for _ in range(8):
        hit = fight._collision().cast_ray(orb.position, orb.direction, distance)
        wall_distance, normal = (hit.distance, hit.normal) if hit is not None else (distance, None)
        pillar = fight.walls.cast(orb.position, orb.direction, distance)
        if (pillar is not None and any(pillar[2])
                and (normal is None or pillar[0] < wall_distance)):
            wall_distance, _, normal = pillar
        limit = min(distance, wall_distance)
        defender = None
        for target in fight._enemies(orb.shooter):
            if not fight._genji_deflects(target, orb.direction):
                continue
            contact = combat.ray_capsule(orb.position, orb.direction, *target.segment(), target.radius)
            if contact is not None and contact < limit:
                limit, defender = contact, target
        grasp = sigma_grasp.grasp_intersection(fight, orb.shooter, orb.position, orb.direction, limit)
        matrix = fight._matrix_hit(
            orb.shooter, orb.position, orb.direction,
            min(limit, grasp[0]) if grasp is not None else limit,
            HEAL_ORB if orb.healing else DAMAGE_ORB,
        )
        if matrix is not None and matrix < limit and (grasp is None or matrix < grasp[0]):
            orb.position = combat._along(orb.position, orb.direction, matrix)
            fraction = (total - distance + matrix) / total if total > 0 else 0
            orb.flown += duration * fraction
            orb.left = max(0.0, orb.left - duration * fraction)
            if orb.shot is not None:
                fight.projectiles.absorbed(orb.shot)
                orb.shot = None
            return True
        if grasp is not None and grasp[0] < limit:
            distance_to, state = grasp
            orb.position = combat._along(orb.position, orb.direction, distance_to)
            fraction = (total - distance + distance_to) / total if total > 0 else 0
            orb.flown += duration * fraction
            orb.left = max(0.0, orb.left - duration * fraction)
            sigma_grasp.absorbed(state, orb.absorption_amount)
            if orb.shot is not None:
                fight.projectiles.absorbed(orb.shot)
                orb.shot = None
            return True
        if defender is not None:
            orb.position = combat._along(orb.position, orb.direction, limit)
            if orb.shot is not None:
                fraction = (total - distance + limit) / total if total > 0 else 0
                fight.projectiles.flown(orb.shot, orb.flown + duration * fraction)
                orb.shot = None
            orb.shooter, orb.body, orb.team = defender.owner, defender.entity, defender.team
            orb.direction = combat.aim(defender.owner.mover.yaw, defender.owner.mover.pitch)
            distance = max(0.0, distance - limit)
            step = min(1e-4, distance)
            orb.position = combat._along(orb.position, orb.direction, step)
            distance -= step
            if distance <= combat.EPSILON:
                return
            continue
        if normal is None:
            orb.position = combat._along(orb.position, orb.direction, distance)
            return
        orb.position = combat._along(orb.position, orb.direction, wall_distance)
        dot = combat._dot(orb.direction, normal)
        reflected = tuple(d - 2 * dot * n for d, n in zip(orb.direction, normal, strict=True))
        norm = math.sqrt(combat._dot(reflected, reflected))
        if norm <= combat.EPSILON:
            return
        orb.direction = tuple(v / norm for v in reflected)
        orb.bounces += 1
        distance = max(0.0, distance - wall_distance)
        step = min(1e-4, distance)
        orb.position = combat._along(orb.position, orb.direction, step)
        distance -= step
        if distance <= combat.EPSILON:
            return


def update(fight):
    from ow174.game import combat

    for orb in tuple(fight.moira_orbs):
        elapsed = max(0, fight.tick - orb.last_tick) * combat.TICK
        orb.last_tick = fight.tick
        absorbed = False
        while elapsed > combat.EPSILON and orb.left > combat.EPSILON and orb.energy > combat.EPSILON:
            dt = min(combat.TICK, elapsed, orb.left)
            slow = ORB_SLOW if next(_orb_targets(fight, orb), None) is not None else 1.0
            if _orb_move(fight, orb, orb.speed * slow * dt, dt):
                absorbed = True
                break
            tags = REFLECTED_DAMAGE_ORB_TAGS if orb.body != orb.launch_body else DAMAGE_ORB_TAGS
            for target in _orb_targets(fight, orb):
                amount = min(orb.energy, (65.0 if orb.healing else 50.0) * dt)
                if orb.healing:
                    spent = fight._heal(orb.shooter, target, amount, limit=orb.energy)
                else:
                    spent = fight._damage(orb.shooter, target, amount, False, 0x0B9000000000016D,
                                          fight.tick, world_tick=fight.tick, tags=tags, limit=orb.energy)
                orb.energy = max(0.0, orb.energy - spent)
                if orb.energy <= combat.EPSILON:
                    orb.energy = 0.0
                    break
            orb.flown += dt
            orb.left = max(0.0, orb.left - dt)
            elapsed -= dt
        if absorbed or orb.left <= combat.EPSILON or orb.energy <= combat.EPSILON:
            fight.moira_orbs.remove(orb)
            if orb.shot is not None:
                fight.projectiles.flown(orb.shot, orb.flown)
