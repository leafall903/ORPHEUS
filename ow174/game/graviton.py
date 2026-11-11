"""Host the 03E2 field using 0260/0261 values and reconstructed pull motion."""

import math
from dataclasses import dataclass

from ow174.game import bots, fortify, mei, phase_defenses, reinhardt_barrier
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data

FIELD_ENTITY = 0x04000000000003E2
LOADOUT = 0x0B9000000000005B
DURATION, RADIUS = 4.0, 6.0  # 0260 nodes 4 and 5
DAMAGE_PER_SECOND = 5.0  # 0261 node 2
PULL_SPEED = 3.0  # speed interpretation of 0261 node 15's scalar


@dataclass
class Field:
    shooter: object
    source_body: int
    team: int
    position: tuple
    last_tick: int
    end_tick: int
    radius: float
    damage_scale: float = 1.0


class Gravitons:
    def __init__(self, combat):
        self.combat = combat
        self.fields: list[Field] = []
        self.npc_movers: dict[int, Mover] = {}
        self.player_pulls: set[int] = set()

    def landed(self, rocket, point, tick_seconds):
        if rocket.shooter.gone or self.combat.match.ended:
            return
        tick = self.combat.tick
        self.fields.append(Field(
            rocket.shooter, rocket.source_body, rocket.team, point, tick,
            tick + math.ceil(DURATION / tick_seconds), RADIUS, rocket.damage_scale,
        ))

    def includes(self, field, target):
        from ow174.game.combat import capsule_visible, segment_distance

        if (target.owner is field.shooter or target.is_device
                or self.combat.vitals_of(target).dead or phase_defenses.active(target.owner)
                or mei.protected(target.owner)
                or (not self.combat.match.game_map.free_for_all and target.team == field.team)):
            return False
        a, b = target.segment()
        return (segment_distance(field.position, a, b) - target.radius <= field.radius
                and capsule_visible(self.combat._line_collision(), field.position, a, b, target.radius))

    def pulling(self, player):
        return player.body in self.player_pulls

    def release_player(self, player):
        if player.body in self.player_pulls:
            self.player_pulls.remove(player.body)
            player.mover.driven = False
            player.mover.velocity = (0.0, 0.0, 0.0)

    def departed(self, player):
        from ow174.game.combat import TICK

        fight = self.combat
        for rocket in tuple(fight.rockets):
            if rocket.shooter is player and rocket.spawn == FIELD_ENTITY:
                fight._landed(rocket)
        if any(field.shooter is player for field in self.fields):
            self.update(fight.tick, TICK)

    def update(self, tick, tick_seconds):
        if not self.fields and not self.player_pulls:
            return
        fight = self.combat
        moved = set()
        players = set()
        npcs = set()
        for field in tuple(self.fields):
            if fight.match.ended or field.shooter.gone:
                self.fields.remove(field)
                continue
            end = min(tick, field.end_tick)
            damage_per_tick = DAMAGE_PER_SECOND * tick_seconds * field.damage_scale
            elapsed = max(0, end - field.last_tick)
            duration = elapsed * tick_seconds
            field.last_tick = end
            for target in fight._targets():
                if not self.includes(field, target):
                    continue
                remaining = elapsed
                # The tick that breaks a bubble is absorbed, even during catch-up.
                while remaining and reinhardt_barrier.protect_body(
                        fight, target, damage_per_tick, attacker=field.source_body):
                    remaining -= 1
                if remaining:
                    fight._damage(field.shooter, target, damage_per_tick * remaining, False, LOADOUT, tick,
                                  world_tick=tick, source_body=field.source_body, source_scaled=True)
                if fight.vitals_of(target).dead:
                    players.discard(target.entity)
                    npcs.discard(target.entity)
                    continue
                if (tick >= field.end_tick or target.entity in moved or target.entity in fight.pins.values()
                        or fight.hooks.controlled(target.entity) or fortify.active(fight, target.owner)):
                    continue
                moved.add(target.entity)
                x, y, z = field.position
                destination = (x, y - target.eye, z)
                owner = target.owner
                if isinstance(owner, bots.Bot):
                    npcs.add(target.entity)
                    if not duration:
                        continue
                    mover = self.npc_movers.get(target.entity)
                    if mover is None:
                        mover = self.npc_movers[target.entity] = Mover(
                            owner.position, int(owner.yaw), fight._collision(), mover_data(owner.body),
                        )
                    else:
                        mover.position[:] = owner.position
                    mover.step_pose(Command(tick, yaw=int(owner.yaw)), duration, tick,
                                    move_to=(destination, PULL_SPEED))
                    owner.position, owner.velocity = tuple(mover.position), mover.velocity
                    owner.moved_tick = tick
                else:
                    players.add(target.entity)
                    if duration:
                        owner.mover.driven = True
                        owner.mover._move_to(duration, destination, PULL_SPEED)
                history = fight.history.get(target.entity)
                if duration and history and history[-1][0] == tick:
                    frame, _, height, eye = history[-1]
                    position = owner.position if isinstance(owner, bots.Bot) else tuple(owner.mover.position)
                    history[-1] = frame, position, height, eye
            if tick >= field.end_tick:
                self.fields.remove(field)
        released = (self.player_pulls | moved) - players
        for player in fight.match.players:
            if player.body in released:
                player.mover.driven = False
                player.mover.velocity = (0.0, 0.0, 0.0)
        self.player_pulls = players
        released_npcs = self.npc_movers.keys() - npcs
        for bot in fight.match.bots:
            if bot.entity in released_npcs:
                mover = self.npc_movers[bot.entity]
                if bot.position == tuple(mover.position) and bot.velocity == mover.velocity:
                    bot.velocity = (0.0, 0.0, 0.0)
        for entity in released_npcs:
            del self.npc_movers[entity]
