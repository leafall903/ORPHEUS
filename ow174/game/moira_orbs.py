"""Biotic Orb flight and aura driven by the 104319 volley and orb graphs."""

import math
from dataclasses import dataclass

from ow174.game import moira_beams, sigma
from ow174.game.script import expr, graph

HEAL_ORB, DAMAGE_ORB = 0x0400000000001E60, 0x0400000000001E61
VOLLEYS = {20: HEAL_ORB, 21: DAMAGE_ORB}


@dataclass
class Orb:
    shooter: object
    source_body: int  # Last launch or reflection body, even if the shooter respawns.
    definition: int
    position: tuple
    direction: tuple
    speed: float
    left: float
    energy: float
    radius: float
    rate: float
    slow: float
    loadout: int
    absorption: float
    last_tick: int | None = None
    shot: object = None
    flown: float = 0.0
    bounces: int = 0

    @property
    def healing(self):
        return self.definition == HEAL_ORB


def launch(player, state, definition, origin, direction, speed, lifetime, direct):
    if (player.hero.guid != moira_beams.HERO or state.instance.graph.index != 0x10E4
            or not isinstance(definition, expr.Asset) or VOLLEYS.get(state.node.pos) != definition.guid):
        return None
    healing = definition.guid == HEAL_ORB
    root, aura, budget_node, scalar_key, slow_node = (
        (0x10FF, 0x1102, 4, 12618, 10) if healing else (0x1100, 0x1101, 13, 12619, 8)
    )
    control, operation = graph.graph(root), graph.graph(aura)
    if control is None or operation is None:
        raise ValueError("Biotic Orb requires its external control and aura graphs")
    component = state.instance.component
    slot = component.entity_var(scalar_key, create=False)
    scalar = slot.value() if slot is not None else None
    if scalar is None:
        scalar = component.world.ruleset(scalar_key)
    budget, = control.node(budget_node).field("m_value.m_expression.m_D99EF254")
    rate, = (row["m_value"]["m_value"] for row in control.node(2).field("m_params")
             if row["m_B5051BCE"] == "0x0A4000000000002A")
    radius = control.node(1).field("m_radius.m_value")
    slow = control.node(slow_node).field("m_67E8FF48.m_value")
    loadout = graph.guid(operation.node(0).field("m_modifyHealth.m_loadout.m_loadout"))
    if loadout is None:
        raise ValueError("Biotic Orb aura requires its authored loadout")
    energy = expr.f32(budget * expr.to_float(scalar))
    if not math.isfinite(energy):
        raise ValueError("Biotic Orb budget must be finite")
    return Orb(player, player.body, definition.guid, origin, direction, speed, lifetime, max(0.0, energy),
               radius, rate, slow, loadout, max(0.0, direct.amount) if direct is not None else 0.0)


def _targets(fight, orb):
    from ow174.game.combat import EPSILON, _sub, ray_world, segment_distance

    source = orb.shooter
    candidates = fight._targets() if orb.healing else fight._enemies(source)
    for target in candidates:
        vitals = fight.vitals_of(target)
        if vitals.dead or sigma.body_protection(target.owner):
            continue
        if orb.healing:
            friendly = target.owner is source or (
                not fight.match.game_map.free_for_all and target.team == source.team
            )
            if not friendly or sum(vitals.now.values()) >= sum(vitals.most.values()) - EPSILON:
                continue
        if segment_distance(orb.position, *target.segment()) - target.radius > orb.radius:
            continue
        x, y, z = target.feet
        centre = x, y + target.height / 2, z
        distance = math.dist(centre, orb.position)
        if distance > EPSILON:
            direction = tuple(value / distance for value in _sub(centre, orb.position))
            if ray_world(fight._collision(), orb.position, direction, distance) is not None:
                continue
        yield target


def _retire(fight, orb, *, absorbed=False):
    fight.moira_orbs.remove(orb)
    if orb.shot is not None:
        if absorbed:
            fight.projectiles.absorbed(orb.shot)
        else:
            fight.projectiles.flown(orb.shot, orb.flown)
        orb.shot = None


def _move(fight, orb, distance, duration, *, intercept=True):
    from ow174.game.combat import EPSILON, _along, _dot

    total = distance
    for _ in range(8):
        wall = fight._collision().cast_ray(orb.position, orb.direction, distance)
        limit = wall.distance if wall is not None else distance
        contacts = []
        if intercept:
            matrix = fight._matrix_hit(orb, limit)
            deflect = sigma.deflect_intersection(fight, orb.shooter, orb.position, orb.direction, limit)
            grasp = sigma.grasp_intersection(fight, orb.shooter, orb.position, orb.direction, limit)
            contacts = [(at, kind) for at, kind in (
                (matrix, "matrix"), (deflect[0] if deflect else None, "deflect"),
                (grasp[0] if grasp else None, "grasp"),
            ) if at is not None and (wall is None or at < limit)]
        if contacts:
            contact, kind = min(contacts, key=lambda entry: entry[0])
            orb.position = _along(orb.position, orb.direction, contact)
            travelled = duration * (total - distance + contact) / total
            if kind != "deflect":
                if kind == "grasp":
                    sigma.absorbed(grasp[1], orb.absorption)
                orb.flown += travelled
                orb.left -= travelled
                _retire(fight, orb, absorbed=True)
                return True
            if orb.shot is not None:
                fight.projectiles.flown(orb.shot, orb.flown + travelled)
                orb.shot = None
            _, orb.shooter, orb.direction = deflect
            orb.source_body = orb.shooter.body
        elif wall is not None:
            contact = wall.distance
            orb.position = wall.point
            dot = _dot(orb.direction, wall.normal)
            orb.direction = tuple(d - 2 * dot * n for d, n in zip(orb.direction, wall.normal, strict=True))
            orb.bounces += 1
        else:
            orb.position = _along(orb.position, orb.direction, distance)
            return False
        distance -= contact
        step = min(1e-4, distance)
        orb.position = _along(orb.position, orb.direction, step)
        distance -= step
        if distance <= EPSILON:
            return False
    return False


def _apply_aura(fight, orb, duration):
    for target in _targets(fight, orb):
        if orb.energy <= 0:
            return
        vitals = fight.vitals_of(target)
        amount = min(orb.energy, orb.rate * duration)
        before = vitals.total
        if orb.healing:
            fight.heal_target(orb.shooter, target, amount, credit=target.owner is not orb.shooter)
        else:
            barrier = sigma.particle_barrier(target.owner)
            if barrier is not None:
                orb.energy -= sigma.damage_particle_barrier(barrier, vitals, amount, orb.source_body)
                continue
            fight._damage(orb.shooter, target, amount, False, orb.loadout, fight.tick,
                          world_tick=fight.tick, source_body=orb.source_body)
        orb.energy = max(0.0, orb.energy - abs(vitals.total - before))


def update(fight, *, endpoint):
    from ow174.game.combat import EPSILON, TICK

    for orb in tuple(fight.moira_orbs):
        if fight.match.ended or orb.shooter.gone or orb.energy <= EPSILON or orb.left <= EPSILON:
            _retire(fight, orb)
            continue
        if orb.last_tick is None and not endpoint:
            continue
        if orb.last_tick is not None and fight.tick <= orb.last_tick:
            continue
        if orb.last_tick is not None:
            omitted = min(orb.left, (fight.tick - orb.last_tick - 1) * TICK)
            if omitted > EPSILON:
                # No retained actor poses exist for this interval. Advance its
                # static flight and lifetime without inventing aura or defenses.
                _move(fight, orb, orb.speed * omitted, omitted, intercept=False)
                orb.flown += omitted
                orb.left -= omitted
                if orb.left <= EPSILON:
                    _retire(fight, orb)
                    continue
        orb.last_tick = fight.tick
        duration = min(TICK, orb.left)
        speed = orb.speed * (orb.slow if next(_targets(fight, orb), None) is not None else 1.0)
        if _move(fight, orb, speed * duration, duration):
            continue
        _apply_aura(fight, orb, duration)
        orb.flown += duration
        orb.left = max(0.0, orb.left - duration)
        if orb.left <= EPSILON or orb.energy <= EPSILON:
            _retire(fight, orb)
