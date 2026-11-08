"""Reconstructed Graviton field combat; native actor/aura hosting remains separate.

104319: 0260/node4 duration4, node5 radius6; 00A3.053 refers to0261,
whose node2 supplies5 continuous damage and node15 contains the scalar3.
Interpreting scalar3 as pull speed and using current capsule/LOS acquisition are
explicit host policies, not a native force solver or immunity implementation.
"""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph

FIELD_ENTITY = 0x04000000000003E2
DAMAGE_PER_SECOND = 5.0  # target-build0261/node2 ModifyHealth.m_amount
PULL_SPEED = 3.0  # reconstructed interpretation of0261/node15.m_43B917C6
LOADOUT = 0x0B9000000000005B


@dataclass
class Field:
    shooter: object
    source_entity: int
    team: int
    position: tuple
    last_tick: int
    end_tick: int
    radius: float


class Gravitons:
    def __init__(self, combat):
        self.combat = combat
        self.fields: list[Field] = []
        self.npc_movers: dict[int, Mover] = {}

    def landed(self, rocket, point) -> None:
        if (rocket.spawn != FIELD_ENTITY or getattr(rocket.shooter, "gone", False)
                or self.combat.match.ended):
            return
        duration = float(graph.graph(0x0260).node(4).field("m_value.m_value"))
        radius = float(graph.graph(0x0260).node(5).field("m_value.m_value"))
        ticks = math.ceil(duration / 0.016)
        self.fields.append(Field(rocket.shooter, rocket.source_entity, rocket.team,
                                 tuple(point), self.combat.tick, self.combat.tick + ticks, radius))

    def includes(self, field: Field, target) -> bool:
        from ow174.game.combat import ray_world, segment_distance

        if (self.combat.match.ended or getattr(field.shooter, "gone", False)
                or target.owner is field.shooter or self.combat.vitals_of(target).dead
                or (not self.combat.match.game_map.free_for_all and target.team == field.team)):
            return False
        a, b = target.segment()
        if segment_distance(field.position, a, b) - target.radius > field.radius:
            return False
        point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(v - o for v, o in zip(point, field.position, strict=True))
        distance = math.sqrt(sum(v * v for v in delta))
        if distance <= 1e-9:
            return True
        wall = ray_world(self.combat._collision(), field.position,
                         tuple(v / distance for v in delta), distance)
        return wall is None or wall >= distance

    def movement_for(self, player):
        """A current enemy field overrides ordinary player movement while it lasts."""
        target = next((t for t in self.combat._targets() if t.owner is player), None)
        if target is None:
            return None
        for field in self.fields:
            if self.combat.tick < field.end_tick and self.includes(field, target):
                x, y, z = field.position
                return (x, y - target.eye, z), PULL_SPEED
        return None

    def update(self, tick: int) -> None:
        live_npcs = {bot.entity for bot in self.combat.match.bots if not bot.dead}
        moved = set()
        for field in list(self.fields):
            if getattr(field.shooter, "gone", False) or self.combat.match.ended:
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            duration = max(0, until - field.last_tick) * 0.016
            field.last_tick = max(field.last_tick, until)
            if duration > 0:
                for target in self.combat._targets():
                    if not self.includes(field, target):
                        continue
                    self.combat._damage(field.shooter, target, DAMAGE_PER_SECOND * duration,
                                        False, LOADOUT, tick, world_tick=tick,
                                        source_entity=field.source_entity,
                                        armour_scalar=1.0)  # Blizzard2019-03-19: Graviton DOT bypasses armour
                    if (isinstance(target.owner, bots.Bot) and not self.combat.vitals_of(target).dead
                            and target.entity not in moved):
                        bot = target.owner
                        moved.add(bot.entity)
                        mover = self.npc_movers.get(bot.entity)
                        if mover is None:
                            mover = self.npc_movers[bot.entity] = Mover(
                                bot.position, int(bot.yaw), self.combat._collision(), mover_data(bot.body))
                        else:
                            mover.position[:] = bot.position
                        x, y, z = field.position
                        mover.step_pose(Command(tick, yaw=int(bot.yaw)), duration, tick,
                                        move_to=((x, y - target.eye, z), PULL_SPEED))
                        bot.position, bot.velocity = tuple(mover.position), mover.velocity
                        bot.moved_tick = tick
            if tick >= field.end_tick:
                self.fields.remove(field)
        if not self.fields:
            live_npcs.clear()
        for entity in list(self.npc_movers):
            if entity not in live_npcs:
                del self.npc_movers[entity]
