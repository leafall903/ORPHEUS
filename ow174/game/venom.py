"""Widowmaker's authoritative mine/poison reconstruction for build104319.

Numbers come from 0056 (throw), 0057 (mine), 0073 (poison), and projectile
00AF's m_A0D912A2=-30. The server simulation/attachment/target-filter semantics
are reconstructed. Networked deployed-mine presentation and the complete native
per-target graph host remain unimplemented; this module does not claim them.
"""

import math
from dataclasses import dataclass
from functools import cached_property

from ow174.game.script import expr, graph

HERO = 0x02E000000000000A
FRAME_SECONDS = 0.016
GRAVITY = -30.0
DEFINITION = 0x04000000000000AF


@dataclass
class Flight:
    owner: object
    body: int
    position: tuple
    velocity: tuple
    remaining: float
    shot: object
    team: int | None = None
    matrix_absorbable: bool | None = None


@dataclass
class Mine:
    number: int
    owner: object
    body: int
    position: tuple
    ready: int
    health: float
    wall: int | None = None


@dataclass
class Poison:
    owner: object
    body: int
    target: int
    remaining: float
    rate: float
    pending: float = 0.0


class _PoisonAmount(expr.Context):
    def __init__(self, duration):
        super().__init__()
        self.duration = duration

    def var(self, scope, var):
        return {(graph.INSTANCE, 215): self.duration, (graph.ENTITY, 11844): 1}.get((scope, var))


def _literal(graph_id, state_id, field):
    config = graph.graph(graph_id).states[state_id].field(field)
    value = expr.evaluate(config, expr.Context())
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"mine needs numeric {graph_id:04X}/{state_id}/{field}")
    return float(value)


class Mines:
    def __init__(self, combat):
        self.combat = combat
        self.flights: list[Flight] = []
        self.mines: list[Mine] = []
        self.poison: list[Poison] = []
        self.next_number = 1  # server-local handle, never a guessed network entity ID
        self.last_tick = None

    @cached_property
    def properties(self):
        poison_graph = graph.graph(0x73)
        duration = float(expr.evaluate(poison_graph.node(4).field("m_value"), expr.Context()))
        total = float(expr.evaluate(
            poison_graph.states[0].field("m_modifyHealth.m_amount"), _PoisonAmount(duration),
        ))
        loadout = expr.evaluate(poison_graph.states[0].field("m_modifyHealth.m_loadout"), expr.Context())
        return {
            "arm": math.ceil(_literal(0x57, 1, "m_timeout") / FRAME_SECONDS),
            "health": _literal(0x57, 3, "m_amount"),
            "trigger": _literal(0x57, 2, "m_radius"),
            "radius": _literal(0x57, 0, "m_radius"),
            "duration": duration, "rate": total / duration, "loadout": loadout.guid,
        }

    def throw(self, owner, state, origin, direction, shot):
        if (owner.hero.guid != HERO or state.instance.graph.index != 0x56 or state.index != 0):
            return False
        self.mines[:] = [mine for mine in self.mines if mine.body != owner.body]
        speed = float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        lifetime = float(state.evaluate(state.node.field("m_projectileLifetime"), watch=False))
        self.flights.append(Flight(
            owner, owner.body, tuple(origin), tuple(axis * speed for axis in direction), lifetime, shot,
            team=owner.team, matrix_absorbable=(DEFINITION in self.combat.matrix_definitions
                if self.combat.matrix_definitions is not None else
                self.combat.projectile_tags.admitted(DEFINITION)),
        ))
        return True

    def retire(self, body):
        self.flights[:] = [flight for flight in self.flights if flight.body != body]
        self.mines[:] = [mine for mine in self.mines if mine.body != body]
        self.poison[:] = [poison for poison in self.poison if poison.body != body and poison.target != body]

    def _shield_hit(self, shooter, origin, direction, reach, *, team=None):
        """Use the combined server's existing barrier managers, never old duplicates."""
        from ow174.game import barriers, reinhardt_barrier, sigma_barrier

        fight = self.combat
        hits = (
            fight._barrier_hit(shooter, origin, direction, reach),
            fight.barriers.intercept(shooter, origin, direction, reach, include_authored_mesh=False),
            barriers.ray_hit(fight, shooter, origin, direction, reach),
            reinhardt_barrier.intersection(fight, shooter, origin, direction, reach),
            sigma_barrier.barrier_intersection(fight, shooter, origin, direction, reach),
            fight.brigitte.barrier_ray(shooter, origin, direction, reach),
        )
        return min((hit for hit in hits if hit is not None), key=lambda hit: hit[0], default=None)

    def _visible(self, owner, origin, target):
        # Use the middle of the actual target capsule for the reconstructed LOS.
        a, b = target.segment()
        centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
        delta = tuple(x - y for x, y in zip(centre, origin, strict=True))
        distance = math.sqrt(sum(x * x for x in delta))
        if distance <= 1e-9:
            return True
        direction = tuple(x / distance for x in delta)
        reach = max(0.0, distance - target.radius)
        hit = self.combat._collision().cast_ray(origin, direction, reach)
        return ((hit is None or hit.distance >= reach)
                and self.combat.walls.cast(origin, direction, reach) is None
                and self._shield_hit(owner, origin, direction, reach) is None)

    def _near(self, mine, target, radius):
        from ow174.game.combat import segment_distance

        return segment_distance(mine.position, *target.segment()) - target.radius <= radius

    def update(self, tick):
        if self.last_tick is not None and tick <= self.last_tick:
            return
        first = tick if self.last_tick is None else max(self.last_tick + 1, tick - 624)
        self.last_tick = tick
        fight = self.combat
        now, current_tick = fight.now, fight.tick
        try:
            for frame in range(first, tick + 1):
                # Resolve defenses and damage at each retained mine/poison tick.
                fight.now, fight.tick = now - (tick - frame) * FRAME_SECONDS, frame
                self._step(frame)
        finally:
            fight.now, fight.tick = now, current_tick

    def _step(self, tick):
        if not (self.flights or self.mines or self.poison):
            return
        fight = self.combat
        for flight in list(self.flights):
            if flight.owner.body != flight.body or not flight.owner.has_body:
                self.flights.remove(flight)
                continue
            duration = min(FRAME_SECONDS, flight.remaining)
            for _ in range(8):  # bounded same-frame transfers; native ordering remains open
                if duration <= 1e-9:
                    break
                displacement = [axis * duration for axis in flight.velocity]
                displacement[1] += 0.5 * GRAVITY * duration * duration
                length = math.sqrt(sum(axis * axis for axis in displacement))
                direction = tuple(axis / length for axis in displacement) if length else (0.0, 0.0, 0.0)
                hit = fight._collision().cast_ray(flight.position, direction, length) if length else None
                hit, wall = fight.walls.contact(flight.position, direction, length, hit)
                boundary = hit.distance if hit else length
                shield = self._shield_hit(
                    flight.owner, flight.position, direction, boundary, team=flight.team,
                ) if length else None
                if shield is not None and (hit is None or shield[0] < hit.distance):
                    boundary = shield[0]
                else:
                    shield = None
                blocked = hit is not None or shield is not None
                defense = (fight._deflector(
                    flight.owner, DEFINITION, flight.position, direction, boundary,
                    absorption_admitted=flight.matrix_absorbable,
                ) if length else None)
                reflected = (defense[0], defense[1]) if defense is not None and not defense[3] else None
                absorbed = (defense[0], defense[4]) if defense is not None and defense[3] else None
                if (reflected is not None
                        and (not blocked or reflected[0] + 1e-9 < boundary)
                        and (absorbed is None or reflected[0] < absorbed[0])):
                    distance, target = reflected
                    used = duration * distance / length
                    point = tuple(
                        x + axis * distance for x, axis in zip(flight.position, direction, strict=True)
                    )
                    # Same selected aim-direction transfer as other reflected flights.
                    from ow174.game.combat import aim

                    outgoing = aim(target.owner.mover.yaw, target.owner.mover.pitch)
                    incoming = (flight.velocity[0], flight.velocity[1] + GRAVITY * used, flight.velocity[2])
                    speed = math.sqrt(sum(axis * axis for axis in incoming))
                    flight.position = tuple(x + axis * 1e-4 for x, axis in zip(point, outgoing, strict=True))
                    flight.velocity = tuple(axis * speed for axis in outgoing)
                    flight.remaining -= used
                    duration -= used
                    if flight.shot is not None:
                        fight.projectiles.absorbed(flight.shot)
                        flight.shot = None
                    flight.owner, flight.body, flight.team = target.owner, target.entity, target.team
                    continue
                if absorbed is not None and (not blocked or absorbed[0] + 1e-9 < boundary):
                    # Non-damaging thrown00AF has nominal0, not its future poison amount.
                    if absorbed[1] is not None:
                        fight._absorbed(absorbed[1], 0.0, flight.body)
                    self.flights.remove(flight)
                    if flight.shot is not None:
                        fight.projectiles.absorbed(flight.shot)
                    break
                if shield is not None:
                    # Explicit host policy: no dynamic shield attachment or impact damage for00AF.
                    if flight.shot is not None:
                        fight.projectiles._end(flight.shot, tick, explode=False)
                    self.flights.remove(flight)
                    break
                if hit is not None:
                    position = tuple(
                        x + normal * 0.02 for x, normal in zip(hit.point, hit.normal, strict=True)
                    )
                    mine = Mine(
                        self.next_number, flight.owner, flight.body, position,
                        tick + self.properties["arm"], self.properties["health"],
                        wall.entity if wall is not None else None,
                    )
                    self.next_number += 1
                    self.mines[:] = [old for old in self.mines if old.body != flight.body]
                    self.mines.append(mine)
                    self.flights.remove(flight)
                    if flight.shot is not None:
                        fight.projectiles._end(flight.shot, tick, explode=False)
                    break
                flight.position = tuple(
                    x + step for x, step in zip(flight.position, displacement, strict=True)
                )
                flight.velocity = (
                    flight.velocity[0], flight.velocity[1] + GRAVITY * duration, flight.velocity[2],
                )
                flight.remaining -= duration
                if flight.remaining <= 1e-9:
                    self.flights.remove(flight)
                    if flight.shot is not None:
                        fight.projectiles._end(flight.shot, tick, explode=False)
                break
            else:
                self.flights.remove(flight)
                if flight.shot is not None:
                    fight.projectiles.absorbed(flight.shot)

        for mine in list(self.mines):
            if mine.owner.body != mine.body or not mine.owner.has_body:
                self.mines.remove(mine)
                continue
            if tick < mine.ready:
                continue
            enemies = [
                target for target in fight._enemies(mine.owner) if not fight._invulnerable(target.owner)
            ]
            trigger = self.properties["trigger"]
            if not any(self._near(mine, target, trigger) and self._visible(mine.owner, mine.position, target)
                       for target in enemies):
                continue
            self.mines.remove(mine)
            radius = self.properties["radius"]
            duration = self.properties["duration"]
            for target in enemies:
                if self._near(mine, target, radius) and self._visible(mine.owner, mine.position, target):
                    self.poison[:] = [
                        p for p in self.poison if (p.body, p.target) != (mine.body, target.entity)
                    ]
                    self.poison.append(Poison(
                        mine.owner, mine.body, target.entity, duration, self.properties["rate"],
                    ))

        targets = {target.entity: target for target in fight._targets()}
        for poison in list(self.poison):
            target = targets.get(poison.target)
            if (target is None or poison.owner.body != poison.body or fight.vitals_of(target).dead):
                self.poison.remove(poison)
                continue
            duration = min(FRAME_SECONDS, poison.remaining)
            poison.pending += poison.rate * duration
            poison.remaining -= duration
            # The hit notice carries an integer. Reconstruct integer damage
            # pulses with a fractional accumulator instead of reporting every
            # sub-point simulation increment as one whole point to the client.
            amount = float(math.floor(poison.pending + 1e-9))
            if poison.remaining <= 1e-9:
                amount = poison.pending
            if amount > 1e-9:
                fight._damage(
                    poison.owner, target, amount, False,
                    self.properties["loadout"], tick, world_tick=tick,
                )
                poison.pending = max(0.0, poison.pending - amount)
            if poison.remaining <= 1e-9:
                self.poison.remove(poison)

    def poisoned_by(self, viewer, entity):
        return any(p.owner is viewer and p.target == entity for p in self.poison)

    def intersect(self, shooter, origin, direction, reach):
        from ow174.game.combat import ray_capsule

        found = []
        for mine in self.mines:
            if mine.owner is shooter:
                continue
            if not self.combat.match.game_map.free_for_all and mine.owner.team == shooter.team:
                continue
            distance = ray_capsule(origin, direction, mine.position, mine.position, 0.19)
            if distance is not None and distance <= reach:
                found.append((distance, mine))
        return min(found, key=lambda item: item[0]) if found else None

    def melee(self, shooter, origin, centre, radius, amount):
        """Use the host's reconstructed melee sphere/solid LOS for deployed mines.

        This has no network hit entity and does not claim native deployable tags.
        """
        from ow174.game.combat import capsule_visible

        if amount <= 0:
            return
        for mine in list(self.mines):
            if mine.owner is shooter:
                continue
            if not self.combat.match.game_map.free_for_all and mine.owner.team == shooter.team:
                continue
            if math.dist(centre, mine.position) > radius + 0.19:
                continue
            if not capsule_visible(self.combat._collision(), origin, mine.position, mine.position, 0.19):
                continue
            self.damage(mine, amount)

    def damage(self, mine, amount):
        if mine not in self.mines or amount <= 0:
            return
        mine.health -= amount
        if mine.health <= 0:
            self.mines.remove(mine)

    def explode(self, shooter, point, blast, *, visible_from):
        """Selected host splash geometry; native deployable filters remain open."""
        from ow174.game.combat import capsule_visible

        for mine in list(self.mines):
            if mine.owner is shooter:
                continue
            if not self.combat.match.game_map.free_for_all and mine.owner.team == shooter.team:
                continue
            distance = max(0.0, math.dist(point, mine.position) - 0.19)
            scalar = blast.splash(distance)
            if (scalar > 0 and not self.combat.walls.blocked(visible_from, mine.position)
                    and capsule_visible(
                        self.combat._collision(), visible_from, mine.position, mine.position, 0.19,
                    )):
                self.damage(mine, blast.amount * scalar)
