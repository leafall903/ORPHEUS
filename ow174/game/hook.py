"""World-frame host for Roadhog's authored 01C8 launch and 01C9 pull."""

import math
from dataclasses import dataclass

from ow174.game import bots, fortify, mei, phase_defenses, reinhardt_barrier
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, nodes

HERO = 0x02E0000000000040
CAST = 0x01C8
PULL = 0x01C9


@dataclass
class Flight:
    owner: object
    body: int
    team: int
    instance: object
    origin: tuple
    direction: tuple
    reach: float
    speed: float
    radius: float
    damage: object
    damage_scale: float
    pull_speed: float
    separation: float
    gap: float
    recovery: float
    limit: float
    last_tick: int
    launch_tick: int
    travelled: float = 0.0
    target: object = None
    caught_at: int | None = None
    arrived_at: int | None = None
    npc_mover: Mover | None = None


class Hooks:
    def __init__(self, combat):
        self.combat = combat
        self.flights: dict[int, Flight] = {}
        self.seen: dict[tuple[int, int], int] = {}

    def command(self, player, command):
        if command is None or player.hero.guid != HERO:
            return
        from ow174.game import combat

        for instance in player.body_script.component.instances.values():
            if instance.graph.index != CAST:
                continue
            launch = next((s for s in instance.states.values() if s.node.pos == 12 and s.active), None)
            if launch is None:
                continue
            key = player.body, instance.id
            if self.seen.get(key) == launch.counter:
                continue
            self.seen[key] = launch.counter
            pull_graph = graph.graph(PULL)
            if pull_graph is None:
                return
            move = pull_graph.node(0)
            pull_speed = move.field("m_speed.m_expression.m_D99EF254[0]")
            separation = move.field("m_865F3D01.m_configVars[2].m_expression.m_D99EF254[0]")
            gap = move.field("m_865F3D01.m_configVars[2].m_configVars[0].m_expression.m_D99EF254[0]")
            recovery = pull_graph.node(7).field("m_timeout.m_value")
            limit = pull_graph.node(5).field("m_timeout.m_value")
            modify = instance.graph.node(19).field("m_modifyHealth")
            direct = combat.hit_def(launch, {"m_modifyHealth": modify})
            reach = expr.to_float(instance.find_var(expr.INSTANCE, 1648).value())
            speed = expr.to_float(launch.evaluate(instance.graph.node(136).field("m_9B0385B4"), watch=False))
            radius = expr.to_float(launch.evaluate(instance.graph.node(17).field("m_radius"), watch=False))
            scalars = (reach, speed, pull_speed, separation, gap, limit, recovery, radius)
            if direct is None or any(type(value) not in (int, float) or not math.isfinite(value)
                                     for value in scalars):
                return
            if min(reach, speed, pull_speed, separation, limit) <= 0 or min(gap, recovery, radius) < 0:
                return
            fight = self.combat
            self.cancel_owner(player.body)
            self.flights[player.body] = Flight(
                player, player.body, player.team, instance, fight._eye(player), combat.aim(*command.aim()),
                reach, speed, radius, direct, fight.outgoing_scale(player.body), pull_speed,
                separation, gap, recovery, limit, fight.tick, fight.tick,
            )

    def controlled(self, entity):
        return any(flight.target is not None and flight.target.entity == entity
                   for flight in self.flights.values())

    def cancel_owner(self, entity):
        flight = self.flights.pop(entity, None)
        if flight is None or flight.target is None:
            return
        owner = flight.target.owner
        if isinstance(owner, bots.Bot):
            owner.velocity = (0.0, 0.0, 0.0)
            owner.moved_tick = self.combat.tick
            if owner.dva_mover is not None:
                owner.dva_mover.position[:] = owner.position
                owner.dva_mover.velocity = owner.velocity
                owner.dva_mover.body = None
        else:
            owner.mover.driven = False
            owner.mover.velocity = (0.0, 0.0, 0.0)

    def retire(self, entity):
        for body, flight in tuple(self.flights.items()):
            if body == entity or (flight.target is not None and flight.target.entity == entity):
                self.cancel_owner(body)
        self.seen = {key: value for key, value in self.seen.items() if key[0] != entity}

    def replay_until(self, previous, tick_seconds):
        return max((flight.launch_tick + math.ceil(
            (flight.reach / flight.speed + flight.limit) / tick_seconds,
        ) + 1 for flight in self.flights.values()), default=previous)

    @staticmethod
    def destination(flight, feet):
        x, y, z = flight.owner.mover.position
        dx, dz = feet[0] - x, feet[2] - z
        length = math.hypot(dx, dz)
        if length < 1e-9:
            dx, _, dz = flight.direction
            length = math.hypot(dx, dz)
        scale = flight.separation / max(length, 1e-9)
        return x + dx * scale, y, z + dz * scale

    def update(self, tick):
        if not self.flights:
            return
        from ow174.game import combat

        fight = self.combat
        for body, flight in tuple(self.flights.items()):
            if self.flights.get(body) is not flight:
                continue
            if (fight.match.ended or flight.owner.gone or flight.owner.body != body
                    or fight.is_dead(flight.owner) or fight.is_stunned(flight.owner, tick=tick)
                    or body in fight.hacks):
                self.cancel_owner(body)
                continue
            elapsed = (tick - flight.last_tick) * combat.TICK
            if elapsed <= 0:
                continue
            flight.last_tick = tick
            if flight.target is None:
                self._sweep(flight, elapsed)
            else:
                self._pull(flight, elapsed)

    def _sweep(self, flight, elapsed):
        from ow174.game import combat, heavy_assault, sniper_special

        fight = self.combat
        ability = next((s for s in flight.instance.states.values() if s.node.pos == 11), None)
        if ability is None or not ability.flags & nodes.Ability.RUNNING:
            self.cancel_owner(flight.body)
            return
        reach = min(flight.reach - flight.travelled, elapsed * flight.speed)
        origin = combat._along(flight.origin, flight.direction, flight.travelled)
        wall = combat.ray_world(fight._line_collision(), origin, flight.direction, reach)
        limit = reach if wall is None else wall
        barrier = reinhardt_barrier.ray_hit(
            fight, flight.owner, origin, flight.direction, limit, include_bubbles=False, team=flight.team,
        )
        if barrier is not None:
            limit = min(limit, barrier[0])
        screens = tuple(b for b in reinhardt_barrier.enemy_barriers(fight, flight.owner, team=flight.team)
                        if b.mesh)
        target = None
        for candidate in fight._enemies(flight.owner, team=flight.team):
            a, b = candidate.segment()
            distance = combat.ray_capsule(
                origin, flight.direction, a, b, candidate.radius + flight.radius,
            )
            if (distance is not None and distance <= reach
                    and (distance < limit or (distance == limit and target is None
                                              and wall is None and barrier is None))
                    and combat.capsule_visible(fight._line_collision(), origin, a, b, candidate.radius,
                                                barriers=screens)):
                limit, target = distance, candidate
        flight.travelled += limit
        amount = flight.damage.amount * flight.damage_scale
        if target is None:
            if barrier is not None and (wall is None or barrier[0] < wall):
                reinhardt_barrier.damage(barrier[1], amount, attacker=flight.body)
                self.cancel_owner(flight.body)
            elif wall is not None or flight.travelled >= flight.reach - combat.EPSILON:
                self.cancel_owner(flight.body)
            return
        if reinhardt_barrier.protect_body(fight, target, amount, attacker=flight.body):
            self.cancel_owner(flight.body)
            return
        fight._damage(flight.owner, target, amount, False, flight.damage.loadout, fight.tick,
                      world_tick=fight.tick, tags=flight.damage.tags, source_scaled=True)
        if (target.is_device or mei.protected(target.owner) or fight.vitals_of(target).dead
                or fortify.active(fight, target.owner)
                or self.controlled(target.entity) or target.entity in fight.pins.values()):
            self.cancel_owner(flight.body)
            return
        self.cancel_owner(target.entity)
        flight.target, flight.caught_at = target, fight.tick
        own_radius = flight.owner.mover.data.stand_radius
        flight.separation = max(flight.separation, own_radius + target.radius + flight.gap)
        if isinstance(target.owner, bots.Bot):
            heavy_assault.interrupt(target.owner)
            sniper_special.interrupt(target.owner)
            flight.npc_mover = Mover(target.feet, round(target.owner.yaw), fight._collision(),
                                     mover_data(target.owner.body))
        else:
            fight.gravitons.release_player(target.owner)
        fight._interrupt(target)

    def _pull(self, flight, elapsed):
        from ow174.game import combat

        fight = self.combat
        target = next((t for t in fight._targets() if t.entity == flight.target.entity), None)
        if (target is None or fight.vitals_of(target).dead or phase_defenses.active(target.owner)
                or mei.protected(target.owner)
                or fortify.active(fight, target.owner)
                or (fight.tick - flight.caught_at) * combat.TICK >= flight.limit):
            self.cancel_owner(flight.body)
            return
        origin = fight._eye(flight.owner)
        centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = combat._sub(centre, origin)
        length = math.hypot(*delta)
        if length > target.radius:
            direction = tuple(axis / length for axis in delta)
            reach = length - target.radius
            if (combat.ray_world(fight._line_collision(), origin, direction, reach) is not None
                    or reinhardt_barrier.body_bubble(fight, target.owner) is not None
                    or reinhardt_barrier.ray_hit(fight, flight.owner, origin, direction, reach,
                                                 include_bubbles=False, team=flight.team) is not None):
                self.cancel_owner(flight.body)
                return
        destination = self.destination(flight, target.feet)
        owner = target.owner
        if flight.arrived_at is None:
            if flight.npc_mover is not None:
                mover = flight.npc_mover
                mover.step_pose(Command(fight.tick, yaw=round(owner.yaw)), elapsed, fight.tick,
                                move_to=(destination, flight.pull_speed))
                owner.position, owner.velocity = tuple(mover.position), mover.velocity
                owner.moved_tick = fight.tick
                feet = owner.position
            else:
                owner.mover.driven = True
                owner.mover._move_to(elapsed, destination, flight.pull_speed)
                feet = tuple(owner.mover.position)
            history = fight.history.get(target.entity)
            if history and history[-1][0] == fight.tick:
                frame, _, height, eye = history[-1]
                history[-1] = frame, feet, height, eye
            if math.dist(feet, destination) < 0.15:
                flight.arrived_at = fight.tick
        if (flight.arrived_at is not None
                and (fight.tick - flight.arrived_at) * combat.TICK >= flight.recovery):
            self.cancel_owner(flight.body)
