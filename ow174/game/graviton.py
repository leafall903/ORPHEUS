"""Static Graviton field using the 0260 profile and recovered 0261 effect.

Capsule/visibility admission and interpreting the 0261 scalar as a pull speed
are reconstructed. The ballistic projectile remains in the combat sweep.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, fortify, phase_defenses, sigma_flux
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph

FIELD_ENTITY = 0x04000000000003E2
DAMAGE_PER_SECOND = 5.0  # 0261 node 2
PULL_SPEED = 3.0  # 0261 node 15
LOADOUT = 0x0B9000000000005B


@dataclass
class Field:
    owner: object
    source_body: int
    team: int
    point: tuple
    visible_from: tuple
    radius: float
    last_tick: int
    end_tick: int


class Gravitons:
    def __init__(self, fight):
        self.fight = fight
        self.fields: list[Field] = []
        self.pulled: set[int] = set()
        self.npc_movers: dict[int, Mover] = {}

    def land(self, rocket, point, visible_from):
        from ow174.game.combat import TICK

        owner = rocket.shooter
        if rocket.spawn != FIELD_ENTITY or owner not in self.fight.match.players or owner.gone:
            return
        profile = graph.graph(0x0260)
        if profile is None:
            return
        duration = profile.node(4).field("m_value.m_value")
        radius = profile.node(5).field("m_value.m_value")
        if not duration or not radius:
            return
        tick = self.fight.tick
        self.fields.append(Field(owner, owner.body, rocket.team, point, visible_from,
                                 radius, tick, tick + math.ceil(duration / TICK)))

    def includes(self, field, target):
        from ow174.game.combat import capsule_visible, segment_distance

        fight = self.fight
        if (target.owner is field.owner or getattr(target.owner, "combat_owner", None) is not None
                or (not fight.match.game_map.free_for_all and target.team == field.team)
                or fight.vitals_of(target).dead or phase_defenses.phase(target.owner)[0]):
            return False
        a, b = target.segment()
        return (segment_distance(field.point, a, b) - target.radius <= field.radius
                and capsule_visible(fight._collision(), field.visible_from, a, b, target.radius))

    def retire(self, body):
        self.fields[:] = [field for field in self.fields if field.source_body != body]
        self.pulled.discard(body)
        self.npc_movers.pop(body, None)

    def release_player(self, player):
        if player.body in self.pulled:
            self.pulled.remove(player.body)
            effect, _ = sigma_flux.active_motion(player)
            if effect is None:
                player.mover.velocity = (0.0, 0.0, 0.0)
                player.mover.driven = False

    def release_npc(self, bot):
        if bot.entity in self.pulled:
            self.pulled.remove(bot.entity)
            bot.velocity = (0.0, 0.0, 0.0)
        self.npc_movers.pop(bot.entity, None)

    def update(self, tick):
        from ow174.game.combat import TICK

        if not self.fields and not self.pulled:
            return
        fight = self.fight
        moved = set()
        targets = fight._targets() if self.fields else ()
        for field in tuple(self.fields):
            if field.owner.gone or field.owner.body != field.source_body or fight.match.ended:
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            elapsed = (until - field.last_tick) * TICK
            field.last_tick = until
            if elapsed > 0:
                for target in targets:
                    if not self.includes(field, target):
                        continue
                    amount = DAMAGE_PER_SECOND * elapsed
                    bubble = fight.bubbles.for_target(target.owner)
                    if bubble is not None:
                        fight.bubbles.damage(bubble, amount, field.owner)
                    else:
                        fight._damage(field.owner, target, amount, False, LOADOUT, tick,
                                      world_tick=tick, armour_reduction=False)
                    if fight.vitals_of(target).dead:
                        moved.discard(target.entity)
                    elif (tick < field.end_tick and target.entity not in moved
                          and target.entity not in fight.pins.values()
                          and fortify.state_for(target.owner) is None):
                        self.pull(field, target, elapsed, tick)
                        moved.add(target.entity)
                        # A later hit in this tick must be able to release the pull.
                        self.pulled.add(target.entity)
            if tick >= field.end_tick:
                self.fields.remove(field)
        for player in fight.match.players:
            if player.body not in moved:
                self.release_player(player)
        for bot in fight.match.bots:
            if bot.entity in self.pulled and bot.entity not in moved:
                self.release_npc(bot)
        for entity in self.npc_movers.keys() - moved:
            self.npc_movers.pop(entity)
        self.pulled = moved

    def pull(self, field, target, elapsed, tick):
        fight = self.fight
        owner = target.owner
        x, y, z = field.point
        destination = (x, y - target.eye, z)
        if isinstance(owner, bots.Bot):
            mover = self.npc_movers.get(target.entity)
            if mover is None:
                mover = self.npc_movers[target.entity] = Mover(
                    owner.position, round(owner.yaw), fight._collision(), mover_data(owner.body),
                )
            elif tuple(mover.position) != owner.position:
                mover.position[:] = owner.position
                mover.body = None
            mover.use(world=fight._collision())
            mover._move_to(elapsed, destination, PULL_SPEED)
            owner.position, owner.velocity = tuple(mover.position), mover.velocity
            owner.moved_tick = tick
        else:
            mover = owner.mover
            mover.use(world=fight._collision())
            effect, _ = sigma_flux.active_motion(owner)
            if effect is None:
                mover._move_to(elapsed, destination, PULL_SPEED)
            else:
                # Flux owns lift and fall. Sweep horizontally without resetting its motion.
                velocity, fall, driven = mover.velocity, mover.fall, mover.driven
                mover._move_to(elapsed, (x, mover.position[1], z), PULL_SPEED)
                mover.velocity, mover.fall, mover.driven = velocity, fall, driven
        history = fight.history.get(target.entity)
        if history:
            frame, _, height, eye = history[-1]
            history[-1] = frame, tuple(mover.position), height, eye
