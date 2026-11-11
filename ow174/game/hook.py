"""Roadhog's 01C8 launch and 01C9 pull through the server collision and mover."""

import math
from dataclasses import dataclass

from ow174.game import bots, fortify, phase_defenses
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, nodes

HERO = 0x02E0000000000040
GRAPH = 0x01C8


@dataclass
class Flight:
    owner: object
    body: int
    instance: int
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
        self.flights = {}
        self.seen = {}

    def command(self, player, command):
        from ow174.game import combat

        if player.hero.guid != HERO:
            return
        for instance in player.body_script.component.instances.values():
            if instance.graph.index != GRAPH:
                continue
            launch = instance.states.get(instance.graph._by_pos[12].state)
            key = (player.body, instance.id)
            if launch is None or not launch.active or self.seen.get(key) == launch.counter:
                continue
            self.seen[key] = launch.counter
            definition = graph.graph(0x01C9)
            if definition is None:
                continue
            move = definition._by_pos[0]
            pull = move.field("m_speed")["m_expression"]["m_D99EF254"][0]
            offset = move.field("m_865F3D01")["m_configVars"][2]
            separation = offset["m_expression"]["m_D99EF254"][0]
            gap = offset["m_configVars"][0]["m_expression"]["m_D99EF254"][0]
            recovery = definition._by_pos[7].field("m_timeout")["m_value"]
            limit = definition._by_pos[5].field("m_timeout")["m_value"]
            damage = combat.hit_def(launch, {
                "m_modifyHealth": instance.graph._by_pos[19].field("m_modifyHealth"),
            })
            reach = expr.to_float(instance.find_var(0, 1648).value())
            speed = expr.to_float(launch.evaluate(
                instance.graph._by_pos[136].field("m_9B0385B4"), watch=False,
            ))
            radius = expr.to_float(launch.evaluate(instance.graph._by_pos[17].field("m_radius"), watch=False))
            if damage is None or min(reach, speed, pull, separation, limit) <= 0 or radius < 0:
                continue
            self.flights[player.body] = Flight(
                player, player.body, instance.id, self.fight._eye(player), combat.aim(*command.aim()),
                reach, speed, radius, damage, pull, separation, gap, recovery, limit, self.fight.tick,
            )

    def controlled(self, entity):
        return any(flight.target == entity for flight in self.flights.values())

    def cancel_owner(self, entity):
        flight = self.flights.pop(entity, None)
        if flight is not None and flight.npc_mover is not None:
            for bot in self.fight.match.bots:
                if bot.entity == flight.target:
                    bot.velocity = (0.0, 0.0, 0.0)
                    bot.moved_tick = self.fight.tick
                    break

    def release_target(self, entity):
        for body, flight in self.flights.items():
            if flight.target == entity:
                self.cancel_owner(body)
                return

    def retire(self, entity):
        self.cancel_owner(entity)
        self.release_target(entity)
        self.seen = {key: value for key, value in self.seen.items() if key[0] != entity}

    @staticmethod
    def destination(flight, feet):
        owner = flight.owner.mover.position
        dx, dz = feet[0] - owner[0], feet[2] - owner[2]
        length = math.hypot(dx, dz)
        if length < 1e-9:
            dx, dz = flight.direction[::2]
            length = math.hypot(dx, dz)
        scale = flight.separation / max(length, 1e-9)
        return owner[0] + dx * scale, owner[1], owner[2] + dz * scale

    def forced_move(self, player):
        for flight in self.flights.values():
            if flight.target == player.body and flight.arrived_at is None:
                return self.destination(flight, player.mover.position), flight.pull_speed
        return None

    def _catch(self, flight, source, elapsed):
        from ow174.game import combat

        fight = self.fight
        instance = flight.owner.body_script.component.instances.get(flight.instance)
        if instance is None:
            return False
        ability = instance.states.get(instance.graph._by_pos[11].state)
        if ability is None or not ability.flags & nodes.Ability.RUNNING:
            return False
        step = min(flight.reach - flight.travelled, flight.speed * elapsed)
        origin = combat._along(flight.origin, flight.direction, flight.travelled)
        wall = combat.ray_world(fight._collision(), origin, flight.direction, step)
        limit = step if wall is None else wall
        caught = None
        for target in fight._enemies(flight.owner):
            if self.controlled(target.entity) or target.entity in fight.pins.values():
                continue
            distance = combat.ray_capsule(origin, flight.direction, *target.segment(),
                                          target.radius + flight.radius)
            if distance is not None and distance <= limit:
                caught, limit = target, distance
        shield = fight._shield_trace(flight.owner, origin, flight.direction, limit)
        if shield is not None and (wall is None or shield[0] < wall):
            fight._shield_damage(flight.owner, shield[1], flight.damage.amount)
            return False
        if caught is None:
            flight.travelled += step
            return wall is None and flight.travelled < flight.reach
        if phase_defenses.phase(caught.owner)[0]:
            return False
        bubble = fight.bubbles.for_target(caught.owner)
        if bubble is not None:
            fight._shield_damage(flight.owner, bubble, flight.damage.amount)
            return False
        fight._damage(flight.owner, caught, flight.damage.amount, False, flight.damage.loadout,
                      fight.tick, world_tick=fight.tick)
        if fight.vitals_of(caught).dead or fortify.control_flag_active(caught.owner):
            return False
        flight.target, flight.caught_at = caught.entity, fight.now
        flight.separation = max(flight.separation, source.radius + caught.radius + flight.gap)
        fight._interrupt(caught)
        if isinstance(caught.owner, bots.Bot):
            bot = caught.owner
            flight.npc_mover = Mover(bot.position, round(bot.yaw), fight._collision(), mover_data(bot.body))
        return True

    def _pull(self, flight, target, elapsed):
        from ow174.game import combat

        fight = self.fight
        if (target is None or fight.vitals_of(target).dead or phase_defenses.phase(target.owner)[0]
                or fortify.control_flag_active(target.owner) or fight.now - flight.caught_at >= flight.limit
                or (not fight.match.game_map.free_for_all and target.team == flight.owner.team)):
            return False
        centre = combat._along(target.feet, (0, 1, 0), target.height / 2)
        eye = fight._eye(flight.owner)
        delta = combat._sub(centre, eye)
        length = math.sqrt(combat._dot(delta, delta))
        if length > target.radius:
            direction = tuple(axis / length for axis in delta)
            reach = length - target.radius
            if (combat.ray_world(fight._collision(), eye, direction, reach) is not None
                    or fight._shield_trace(flight.owner, eye, direction, reach) is not None):
                return False
        destination = self.destination(flight, target.feet)
        feet = target.feet
        if flight.npc_mover is not None and elapsed > 0 and flight.arrived_at is None:
            mover = flight.npc_mover
            mover.step_pose(Command(fight.tick), elapsed, fight.tick, move_to=(destination, flight.pull_speed))
            feet = target.owner.position = tuple(mover.position)
            target.owner.velocity = mover.velocity
            target.owner.moved_tick = fight.tick
        if flight.arrived_at is None and math.dist(feet, destination) < 0.15:
            flight.arrived_at = fight.now
        return flight.arrived_at is None or fight.now - flight.arrived_at < flight.recovery

    def update(self):
        from ow174.game.combat import TICK

        if not self.flights:
            return
        fight = self.fight
        targets = {target.entity: target for target in fight._targets()}
        for body, flight in list(self.flights.items()):
            if body not in self.flights or flight.last_tick >= fight.tick:
                continue
            source = targets.get(body)
            if (source is None or fight.vitals_of(source).dead
                    or fight.is_stunned(flight.owner) or fight.sombra_hacks.silenced(body, fight.tick)):
                self.cancel_owner(body)
                continue
            elapsed = (fight.tick - flight.last_tick) * TICK
            flight.last_tick = fight.tick
            if flight.target is None and not self._catch(flight, source, elapsed):
                self.cancel_owner(body)
            elif flight.target is not None and not self._pull(flight, targets.get(flight.target), elapsed):
                self.cancel_owner(body)
