"""Reconstructed Graviton field combat; native actor/aura hosting remains separate.

104319: 0260/node4 duration4, node5 radius6; 00A3.053 refers to0261,
whose node2 supplies5 continuous damage and node15 contains the scalar3.
Interpreting scalar3 as pull speed and using current capsule/LOS acquisition are
explicit host policies, not a native force solver or immunity implementation.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, defenses, heavy_charge, phase, reinhardt_charge, sigma_flux
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
    offense_scale: float = 1.0


class Gravitons:
    def __init__(self, combat):
        self.combat = combat
        self.fields: list[Field] = []
        self.npc_movers: dict[int, Mover] = {}
        self.player_pulls: set[int] = set()

    def clock_ticks(self, previous: int, tick: int, flights: list[range]) -> set[int]:
        ticks = {at for field in self.fields
                 for at in range(max(previous, field.last_tick) + 1, min(tick, field.end_tick) + 1)}
        if flights:
            # Reserve the possible lifetime of a field that lands during this update.
            duration = self.duration_ticks()
            for flight in flights:
                ticks.update(range(flight[0] + 1, min(tick, flight[-1] + duration) + 1))
        return ticks

    @staticmethod
    def duration_ticks() -> int:
        duration = float(graph.graph(0x0260).node(4).field("m_value.m_value"))
        return math.ceil(duration / 0.016)

    def landed(self, rocket, point) -> None:
        if (rocket.spawn != FIELD_ENTITY or getattr(rocket.shooter, "gone", False)
                or self.combat.match.ended):
            return
        radius = float(graph.graph(0x0260).node(5).field("m_value.m_value"))
        ticks = self.duration_ticks()
        self.fields.append(Field(rocket.shooter, rocket.source_entity, rocket.team,
                                 tuple(point), self.combat.tick, self.combat.tick + ticks, radius,
                                 rocket.offense_scale))

    def includes(self, field: Field, target, *, tick=None) -> bool:
        from ow174.game.combat import segment_distance

        if (self.combat.match.ended or getattr(field.shooter, "gone", False)
                or target.owner is field.shooter or self.combat.vitals_of(target).dead
                or phase.protection(target.owner, tick=tick) is not None
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
        wall = self.combat.cover_ray(field.position, tuple(v / distance for v in delta), distance, tick=tick)
        return wall is None or wall >= distance

    def pulls(self, field: Field, target, *, tick=None) -> bool:
        at = self.combat.tick if tick is None else tick
        return (at < field.end_tick and defenses.fortify_state(target.owner, tick=tick) is None
                and self.includes(field, target, tick=tick))

    def movement_for(self, actor):
        """Return the active enemy pull for a player or NPC."""
        target = next((t for t in self.combat._targets() if t.owner is actor), None)
        if target is None:
            return None
        for field in self.fields:
            if self.pulls(field, target):
                x, y, z = field.position
                return (x, y - target.eye, z), PULL_SPEED
        return None

    def release_player(self, player) -> None:
        if player.body in self.player_pulls:
            self.player_pulls.remove(player.body)
            vertical = player.mover.velocity[1] if sigma_flux.has_effect(player) else 0.0
            player.mover.velocity = (0.0, vertical, 0.0)
            player.mover.driven = False

    @staticmethod
    def _pull_horizontally(mover, destination, duration, blockers):
        # Flux owns vertical motion. Sweep sideways without replacing its physics state.
        x, _, z = destination
        velocity, fall, driven = mover.velocity, mover.fall, mover.driven
        mover._move_to(duration, (x, mover.position[1], z), PULL_SPEED, blockers)
        vx, _, vz = mover.velocity
        mover.velocity, mover.fall, mover.driven = velocity, fall, driven
        return vx, velocity[1], vz

    def update(self, tick: int) -> None:
        live_npcs = {bot.entity: bot for bot in self.combat.match.bots if not bot.dead}
        moved = set()
        player_pulls = set()
        advanced = False
        blockers = phase.cryo_blockers(self.combat.match.players, tick=tick) if self.fields else ()
        for field in list(self.fields):
            if getattr(field.shooter, "gone", False) or self.combat.match.ended:
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            first = field.last_tick + 1
            if first <= until:
                field.last_tick = until
                advanced = True
                for target in self.combat._targets():
                    # A sparse update can see a body that did not exist for the whole field interval.
                    first_live = max(first, self.combat.replacement_ticks.get(target.entity, first))
                    if first_live > until or not self.includes(field, target, tick=tick):
                        continue
                    duration = (until - first_live + 1) * 0.016
                    damage = DAMAGE_PER_SECOND * duration * field.offense_scale
                    self.combat._damage(field.shooter, target, damage,
                                        False, LOADOUT, tick, world_tick=tick,
                                        source_entity=field.source_entity,
                                        ultimate_damage=True,
                                        source_scaled=True,
                                        armour_scalar=1.0)  # Blizzard2019-03-19: Graviton DOT bypasses armour
                    npc = isinstance(target.owner, bots.Bot)
                    if (self.combat.vitals_of(target).dead or target.entity in moved
                            or tick >= field.end_tick
                            or defenses.fortify_state(target.owner, tick=tick) is not None
                            or reinhardt_charge.pinned(self.combat, target.entity)):
                        continue
                    moved.add(target.entity)
                    x, y, z = field.position
                    destination = (x, y - target.eye, z)
                    if npc:
                        bot = target.owner
                        if bot.entity in self.combat.charge_contacts and bot.attack_action == "charge":
                            heavy_charge.finish(self.combat, bot, heavy_charge.instance_of(bot))
                        if bot.impulse_mover is not None and sigma_flux.has_effect(bot):
                            mover = bot.impulse_mover
                            bot.velocity = self._pull_horizontally(mover, destination, duration, blockers)
                            bot.position = tuple(mover.position)
                            bot.moved_tick = tick
                            continue
                        bot.impulse_mover = None
                        mover = self.npc_movers.get(bot.entity)
                        if mover is None:
                            mover = self.npc_movers[bot.entity] = Mover(
                                bot.position, int(bot.yaw), self.combat._collision(), mover_data(bot.body))
                        else:
                            mover.position[:] = bot.position
                        mover.step_pose(Command(tick, yaw=int(bot.yaw)), duration, tick,
                                        move_to=(destination, PULL_SPEED), extra_capsules=blockers)
                        bot.position, bot.velocity = tuple(mover.position), mover.velocity
                        bot.moved_tick = tick
                    else:
                        player = target.owner
                        player_pulls.add(target.entity)
                        reinhardt_charge.stop(self.combat, player)
                        self.combat.stop_genji_strike(player)
                        self.combat.stop_boosters(player)
                        self.combat.winston_jump.cancel(player.body)
                        mover = player.mover
                        if sigma_flux.has_effect(player):
                            self._pull_horizontally(mover, destination, duration, blockers)
                        else:
                            mover.driven = True
                            mover._move_to(duration, destination, PULL_SPEED, blockers)
                        history = self.combat.history.get(target.entity)
                        if history:
                            frame, _, height, eye = history[-1]
                            history[-1] = frame, tuple(mover.position), height, eye
            if tick >= field.end_tick:
                ready = self.combat.zarya_charge_ready.get(field.source_entity)
                if ready is not None:
                    # Player commands can deal damage after this update in the field's final tick.
                    self.combat.zarya_charge_ready[field.source_entity] = max(ready, field.end_tick + 1)
                self.fields.remove(field)
        if advanced or not self.fields:
            released = self.player_pulls - player_pulls
            if released:
                for player in self.combat.match.players:
                    if player.body in released:
                        self.release_player(player)
            self.player_pulls = player_pulls
        for entity, mover in tuple(self.npc_movers.items()):
            bot = live_npcs.get(entity)
            if bot is None:
                del self.npc_movers[entity]
            elif (advanced or not self.fields) and entity not in moved:
                if (bot.impulse_mover is None and bot.position == tuple(mover.position)
                        and bot.velocity == mover.velocity):
                    bot.velocity = (0.0, 0.0, 0.0)
                del self.npc_movers[entity]
