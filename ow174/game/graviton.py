"""104319 Graviton Surge combat host adapted from the Zarya lane.

Ballistic contact, capsule/LOS admission, and interpreting 0261/15 as a
pull speed are reconstructed. The host advances on the combat clock.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, defenses, reinhardt_charge
from ow174.game.area_projectile import BallisticFlights
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph

PROJECTILE = 0x04000000000008CE
LOADOUT = 0x0B9000000000005B
DAMAGE_PER_SECOND = 5.0  # 0261/2
PULL_SPEED = 3.0  # 0261/15


@dataclass
class Field:
    owner: object
    body: int
    team: int
    position: tuple
    radius: float
    last_tick: int
    end_tick: int


class Gravitons(BallisticFlights):
    projectile = PROJECTILE

    def __init__(self, combat):
        super().__init__(combat)
        self.fields: list[Field] = []
        self.pulled: set[int] = set()
        self.npc_movers: dict[int, Mover] = {}

    def fired(self, player, state, shot_frame, origin, direction, *, activation, snapshot):
        if (state.instance.graph.index, state.node.pos) != (0x025F, 1):
            return False
        from ow174.game.combat import hit_def

        direct = hit_def(state, state.node.field("m_6396149F"))
        self.launch(player, state, shot_frame, origin, direction, activation=activation,
                    snapshot=snapshot, direct=direct)
        return True

    def contact(self, flight, direction, length):
        from ow174.game.combat import ray_capsule

        distance, hit = super().contact(flight, direction, length)
        fight = self.combat
        for target in fight._enemies(flight.owner):
            a, b = target.segment()
            body = ray_capsule(flight.position, direction, a, b, target.radius)
            if body is not None and body <= length and body < distance:
                distance, hit = body, target
        cover = fight._barrier_contact(flight.owner, flight.position, direction, min(distance, length))
        if cover is not None and cover[0] < distance:
            distance, hit = cover[0], cover
        return distance, hit

    def land(self, flight, point, tick, victim=None):
        from ow174.game.combat import TICK, Target

        self.end_flight(flight)
        fight = self.combat
        direct = flight.direct
        if isinstance(victim, Target):
            fight._damage(flight.owner, victim, direct.amount, False, direct.loadout, tick, world_tick=tick)
        elif victim is not None:
            # Barrier contact is represented by the original damage tuple.
            fight._damage_barrier(victim, direct.amount, shooter=flight.owner)
        profile = graph.graph(0x0260)
        duration = profile.node(4).field("m_value.m_value")
        radius = profile.node(5).field("m_value.m_value")
        self.fields.append(Field(flight.owner, flight.body, flight.owner.team, point,
                                 radius, tick, tick + math.ceil(duration / TICK)))

    def retire(self, body):
        super().retire(body)
        self.fields[:] = [field for field in self.fields if field.body != body]
        self.npc_movers.pop(body, None)

    def includes(self, field, target):
        from ow174.game.combat import capsule_visible, segment_distance

        if (target.owner is field.owner or self.combat.vitals_of(target).dead
                or defenses.protection(target.owner)[0]
                or (not self.combat.match.game_map.free_for_all and target.team == field.team)):
            return False
        a, b = target.segment()
        return (segment_distance(field.position, a, b) - target.radius <= field.radius
                and capsule_visible(self.combat._collision(), field.position, a, b, target.radius,
                                    walls=self.combat.walls))

    def update(self, tick, dt):
        fight = self.combat
        for flight in tuple(self.flights):
            self.fly(flight, tick)
        moved = set()
        targets = fight._targets() if self.fields else ()
        for field in tuple(self.fields):
            if field.owner.gone or fight.match.ended:
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            elapsed = max(0, until - field.last_tick) * dt
            if elapsed:
                field.last_tick = until
                for target in targets:
                    if not self.includes(field, target):
                        continue
                    fight._damage(field.owner, target, elapsed * DAMAGE_PER_SECOND, False, LOADOUT,
                                  tick, world_tick=tick)
                    if (tick < field.end_tick and target.entity not in moved
                            and not fight.vitals_of(target).dead and not defenses.control_immune(target.owner)
                            and not fight.hooks.controlled(target.entity)
                            and not fight.traps.rooted(target.entity, tick)
                            and target.entity not in fight.pins.values()
                            and not reinhardt_charge.pinned(fight, target.entity)):
                        self.pull(field, target, tick, elapsed)
                        moved.add(target.entity)
            if tick >= field.end_tick:
                self.fields.remove(field)

        released = self.pulled - moved
        if released:
            for player in fight.match.players:
                if player.body in released and fight.hooks.player_pose_ticks.get(player.body) != tick:
                    player.mover.velocity = (0.0, 0.0, 0.0)
                    player.mover.driven = False
            for bot in fight.match.bots:
                if bot.entity in released:
                    bot.velocity = (0.0, 0.0, 0.0)
            for entity in released:
                self.npc_movers.pop(entity, None)
        self.pulled = moved

    def pull(self, field, target, tick, elapsed):
        fight = self.combat
        owner = target.owner
        fight.impulses.retire(target.entity, forget=False)
        x, y, z = field.position
        destination = (x, y - target.eye, z)
        if isinstance(owner, bots.Bot):
            mover = self.npc_movers.get(target.entity)
            if mover is None:
                mover = self.npc_movers[target.entity] = Mover(
                    owner.position, int(owner.yaw), fight._collision(), mover_data(owner.body),
                )
            else:
                mover.position[:] = owner.position
            mover.step_pose(Command(tick, yaw=int(owner.yaw)), elapsed, tick,
                            move_to=(destination, PULL_SPEED))
            owner.position, owner.velocity = tuple(mover.position), mover.velocity
            owner.moved_tick = tick
        else:
            owner.mover._move_to(elapsed, destination, PULL_SPEED)
            fight.revise_world_pose(target.entity, owner.mover.position)
