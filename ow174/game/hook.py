"""Build 104319 Chain Hook flight and pull driven by the authored 01C8 launch."""

import math
from dataclasses import dataclass

from ow174.game import bots, defenses, zarya_barrier
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph

ROADHOG = 0x02E0000000000040
GRAPH = 0x01C8
LAUNCH_NODES = (12, 135, 136)


@dataclass
class Flight:
    owner: object
    body: int
    origin: tuple
    direction: tuple
    reach: float
    speed: float
    radius: float
    damage: object
    pull_speed: float
    separation: float
    gap: float
    recovery: float
    limit: float
    last_tick: int
    travelled: float = 0.0
    target: int | None = None
    caught_at: float = 0.0
    arrived_at: float | None = None
    npc_mover: Mover | None = None


class Hooks:
    def __init__(self, fight):
        self.fight = fight
        self.flights: dict[int, Flight] = {}
        self.seen: dict[tuple[int, int], int] = {}

    def command(self, player, command):
        from ow174.game.combat import aim, hit_def

        for instance in player.body_script.component.instances.values():
            if instance.graph.index != GRAPH:
                continue
            launch = next((s for s in instance.states.values() if s.node.pos == 12 and s.active), None)
            key = (player.body, instance.id)
            if launch is None or self.seen.get(key) == launch.counter:
                continue
            self.seen[key] = launch.counter
            pull_graph = graph.graph(0x01C9)
            if pull_graph is None:
                continue
            # Reuse the 01C9 scalar extraction and relative pull policy of the Roadhog host.
            move = pull_graph.node(0)
            pull = move.field("m_speed")["m_expression"]["m_D99EF254"][0]
            offset = move.field("m_865F3D01")["m_configVars"][2]
            separation = offset["m_expression"]["m_D99EF254"][0]
            gap = offset["m_configVars"][0]["m_expression"]["m_D99EF254"][0]
            recovery = pull_graph.node(7).field("m_timeout")["m_value"]
            limit = pull_graph.node(5).field("m_timeout")["m_value"]
            damage = hit_def(launch, {"m_modifyHealth": instance.graph.node(19).field("m_modifyHealth")})
            reach = expr.to_float(instance.find_var(0, 1648).value())
            speed = expr.to_float(launch.evaluate(instance.graph.node(136).field("m_9B0385B4"), watch=False))
            radius = expr.to_float(launch.evaluate(instance.graph.node(17).field("m_radius"), watch=False))
            if min(reach, speed, pull, separation, limit) <= 0 or radius < 0:
                continue
            self.cancel_owner(player.body)
            self.flights[player.body] = Flight(
                player, player.body, self.fight._eye(player), aim(*command.aim()), reach, speed, radius,
                damage, pull, separation, gap, recovery, limit, self.fight.tick,
            )

    def pulling(self, entity):
        return any(flight.target == entity for flight in self.flights.values())

    def cancel_owner(self, body):
        flight = self.flights.pop(body, None)
        if flight is None or flight.target is None:
            return
        fight = self.fight
        fight.controls.pop((flight.target, body, "hook"), None)
        target = fight._target_entity(flight.target)
        if target is not None and not fight.pinned(target.entity):
            actor = target.owner
            if isinstance(actor, bots.Bot):
                actor.velocity = (0.0, 0.0, 0.0)
                actor.moved_tick = fight.tick
            else:
                actor.mover.velocity = (0.0, 0.0, 0.0)
                actor.mover.driven = False

    def retire(self, entity):
        for body, flight in tuple(self.flights.items()):
            if body == entity or flight.target == entity:
                self.cancel_owner(body)
        self.seen = {key: counter for key, counter in self.seen.items() if key[0] != entity}

    def update(self, tick):
        from ow174.game.combat import TICK

        fight = self.fight
        for body, flight in tuple(self.flights.items()):
            if self.flights.get(body) is not flight or tick <= flight.last_tick:
                continue
            source = fight._target_entity(body)
            if source is None or source.owner is not flight.owner or fight.controlled(body):
                self.cancel_owner(body)
                continue
            elapsed = (tick - flight.last_tick) * TICK
            flight.last_tick = tick
            if flight.target is None:
                self.sweep(flight, source, elapsed)
            else:
                self.pull(flight, elapsed)

    def sweep(self, flight, source, elapsed):
        from ow174.game.combat import _along, ray_capsule, ray_world

        fight = self.fight
        step = min(flight.reach - flight.travelled, flight.speed * elapsed)
        origin = _along(flight.origin, flight.direction, flight.travelled)
        best = None
        for target in fight._enemies(flight.owner):
            distance = ray_capsule(origin, flight.direction, *target.segment(), target.radius + flight.radius)
            if distance is not None and distance <= step and (best is None or distance < best[0]):
                best = distance, target
        limit = best[0] if best else step
        wall = ray_world(fight._collision(), origin, flight.direction, limit)
        ice = fight.walls.cast(origin, flight.direction, limit, radius=flight.radius)
        cover = min((value for value in (wall, ice[0] if ice else None) if value is not None), default=None)
        shield = fight._barrier_contact(flight.owner, origin, flight.direction,
                                       limit if cover is None else cover, flight.radius)
        if shield is not None and (cover is None or shield[0] < cover):
            fight._damage_barrier(shield, flight.damage.amount, shooter=flight.owner)
            self.cancel_owner(flight.body)
            return
        if cover is not None:
            self.cancel_owner(flight.body)
            return
        if best is None:
            flight.travelled += step
            if flight.travelled >= flight.reach:
                self.cancel_owner(flight.body)
            return
        distance, target = best
        flight.travelled += distance
        dealt = fight._damage(flight.owner, target, flight.damage.amount, False, flight.damage.loadout,
                              fight.tick, world_tick=fight.tick, tags=flight.damage.tags)
        if (not dealt or fight.vitals_of(target).dead or defenses.control_immune(target.owner)
                or fight.pinned(target.entity) or self.pulling(target.entity)):
            self.cancel_owner(flight.body)
            return
        flight.target, flight.caught_at = target.entity, fight.now
        flight.separation = max(flight.separation, source.radius + target.radius + flight.gap)
        actor = target.owner
        fight.knockbacks.pop(target.entity, None)
        if isinstance(actor, bots.Bot):
            flight.npc_mover = Mover(target.feet, round(actor.yaw), fight._collision(), mover_data(actor.body))
        else:
            actor.mover.impulse_active = False
            actor.mover.velocity = (0.0, 0.0, 0.0)
        fight.control(target, flight.body, "hook", flight.limit)

    def pull(self, flight, elapsed):
        from ow174.game.combat import _sub, ray_world

        fight = self.fight
        target = fight._target_entity(flight.target)
        if (target is None or fight.now - flight.caught_at >= flight.limit
                or (not fight.match.game_map.free_for_all and target.team == flight.owner.team)
                or defenses.control_immune(target.owner) or fight.pinned(target.entity)
                or zarya_barrier.active(fight, target.entity) is not None):
            self.cancel_owner(flight.body)
            return
        origin = fight._eye(flight.owner)
        x, y, z = target.feet
        centre = (x, y + target.height / 2, z)
        delta = _sub(centre, origin)
        length = math.hypot(*delta)
        if length > target.radius:
            direction = tuple(axis / length for axis in delta)
            reach = length - target.radius
            if (ray_world(fight._collision(), origin, direction, reach) is not None
                    or fight.walls.blocked(origin, centre)
                    or fight._barrier_contact(flight.owner, origin, direction, reach) is not None):
                self.cancel_owner(flight.body)
                return
        if flight.arrived_at is not None:
            if fight.now - flight.arrived_at >= flight.recovery:
                self.cancel_owner(flight.body)
            return
        actor = target.owner
        mover = flight.npc_mover if flight.npc_mover is not None else actor.mover
        ox, oy, oz = flight.owner.mover.position
        dx, dz = x - ox, z - oz
        distance = math.hypot(dx, dz)
        if distance < 1e-9:
            dx, dz = flight.direction[0], flight.direction[2]
            distance = max(math.hypot(dx, dz), 1e-9)
        scale = flight.separation / distance
        destination = ox + dx * scale, oy, oz + dz * scale
        mover._move_to(elapsed, destination, flight.pull_speed)
        if flight.npc_mover is not None:
            actor.position, actor.velocity = tuple(mover.position), mover.velocity
            actor.last_tick = actor.moved_tick = fight.tick
        history = fight.history.get(target.entity)
        if history:
            frame, _, height, eye = history[-1]
            history[-1] = frame, tuple(mover.position), height, eye
        if math.dist(mover.position, destination) < 0.15:
            mover.velocity = (0.0, 0.0, 0.0)
            flight.arrived_at = fight.now
