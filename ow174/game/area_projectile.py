"""Reconstructed ballistic flight and defense interception for deployed areas."""

import math
from dataclasses import dataclass

from ow174.game import defenses
from ow174.game.script import expr

FRAME_MS = 16


@dataclass
class Flight:
    owner: object
    body: int
    position: tuple
    velocity: tuple
    gravity: float
    left: float
    last_tick: int
    shot: object = None
    flown: float = 0.0
    direct: object = None


class BallisticFlights:
    def __init__(self, combat):
        self.combat = combat
        self.flights: list[Flight] = []

    def launch(self, player, state, shot_frame, origin, direction, *, activation, snapshot, direct=None):
        speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        lifetime = expr.to_float(state.evaluate(state.node.field("m_projectileLifetime"), watch=False))
        motion = state.node.field("m_projectileMotions")[0]
        gravity = expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))
        shot = self.combat.projectiles.fired(
            player, state, shot_frame, self.projectile, lifetime, origin, direction,
            activation=activation, snapshot=snapshot,
        )
        self.flights.append(Flight(player, player.body, origin, tuple(speed * d for d in direction),
                                     gravity, lifetime, self.combat.tick, shot, direct=direct))

    def retire(self, body):
        for flight in tuple(self.flights):
            if flight.body == body:
                self.end_flight(flight)

    def end_flight(self, flight):
        self.flights.remove(flight)
        if flight.shot is not None:
            self.combat.projectiles.flown(flight.shot, flight.flown)

    def contact(self, flight, direction, length):
        from ow174.game.combat import ray_world

        wall = ray_world(self.combat._collision(), flight.position, direction, length)
        piece = self.combat.walls.cast(flight.position, direction, length)
        if piece is not None and (wall is None or piece[0] < wall):
            return piece[0], (piece[0], piece[1], None)
        return (wall if wall is not None else math.inf), None

    def fly(self, flight, tick):
        # Fixed16ms chords preserve the same path through scheduler gaps.
        for at in range(flight.last_tick + 1, tick + 1):
            seconds = min(FRAME_MS / 1000, flight.left)
            if self.chord(flight, seconds, at):
                return
            flight.last_tick = at
            if flight.left <= 1e-9:
                self.end_flight(flight)  # Lifetime expiry in empty space creates no floor area.
                return

    def chord(self, flight, seconds, at, reflections=0):
        """Sweep one ballistic chord, including reflected remainder in this tick."""
        destination = tuple(
            p + v * seconds - (flight.gravity * seconds**2 / 2 if i == 1 else 0)
            for i, (p, v) in enumerate(zip(flight.position, flight.velocity, strict=True))
        )
        delta = tuple(b - a for a, b in zip(flight.position, destination, strict=True))
        length = math.sqrt(sum(d * d for d in delta))
        direction = tuple(d / length for d in delta) if length else (0.0, -1.0, 0.0)
        from ow174.game.combat import aim

        fight = self.combat
        distance, victim = self.contact(flight, direction, length)
        gate = defenses.projectile_gate(
            flight.position, direction, min(length, distance), fight._enemies(flight.owner), self.projectile,
        )
        if gate is not None and gate[0] < distance:
            contact, defender, mode = gate
            spent = seconds * contact / max(length, 1e-9)
            flight.flown += spent
            flight.left -= spent
            if flight.shot is not None:
                fight.projectiles.absorbed(flight.shot)
                flight.shot = None
            if mode == 1 and flight.direct is not None:
                defenses.grasp_absorbed(defender.owner, flight.direct.amount)
            if mode != 0 or reflections >= 8 or flight.left <= 1e-9:
                self.end_flight(flight)
                return True
            owner = defender.owner
            velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * spent,
                        flight.velocity[2])
            facing = aim(round(owner.mover.yaw), round(owner.mover.pitch))
            flight.position = tuple(p + d * contact + f * 0.001
                                    for p, d, f in zip(flight.position, direction, facing, strict=True))
            flight.owner, flight.body = owner, owner.body
            flight.velocity = tuple(math.sqrt(sum(v * v for v in velocity)) * d for d in facing)
            return self.chord(flight, seconds - spent, at, reflections + 1)
        if distance <= length:
            flight.flown += seconds * distance / max(length, 1e-9)
            point = tuple(p + d * distance for p, d in zip(flight.position, direction, strict=True))
            self.land(flight, point, at, victim)
            return True
        flight.position = destination
        flight.velocity = (flight.velocity[0], flight.velocity[1] - flight.gravity * seconds,
                           flight.velocity[2])
        flight.flown += seconds
        flight.left -= seconds
        return False
