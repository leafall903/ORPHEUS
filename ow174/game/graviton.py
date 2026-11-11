"""104319 Graviton projectile and reconstructed field consumers."""

import math
from dataclasses import dataclass

from ow174.game import bots, reaper, sigma_flux
from ow174.game.commands import Command
from ow174.game.script import expr, graph

CAST = (0x025F, 1)
FIELD_ENTITY = 0x04000000000003E2
# 00A3.053 invokes 0261. Node 2 supplies damage; node 15 supplies the
# scalar interpreted as pull speed by the reconstructed mover.
DAMAGE_RATE = 5.0
PULL_SPEED = 3.0
LOADOUT = 0x0B9000000000005B


def launch_gravity(state):
    impact = state.node.field("m_49CBA3BC.m_entityDefinition")
    if state.evaluate(impact, watch=False) != expr.Asset(FIELD_ENTITY):
        raise ValueError("Graviton field resource changed")
    motion, = state.node.field("m_projectileMotions")
    return expr.to_float(state.evaluate(motion["m_599AB8D7"], watch=False))


def duration_ticks(tick_seconds):
    return math.ceil(graph.graph(0x0260).node(4).field("m_value.m_value") / tick_seconds)


@dataclass
class Field:
    owner: object
    body: int
    position: tuple
    radius: float
    last_tick: int
    until: int


class Gravitons:
    def __init__(self, fight):
        self.fight = fight
        self.fields = []
        self.pulled = set()

    def land(self, owner, point):
        from ow174.game.combat import TICK

        definition = graph.graph(0x0260)
        radius = definition.node(5).field("m_value.m_value")
        tick = self.fight.tick
        self.fields.append(Field(owner, owner.body, point, radius, tick, tick + duration_ticks(TICK)))

    def contains(self, field, target):
        from ow174.game.combat import capsule_visible, segment_distance

        if self.fight.halts.cryo_immune(target.owner):
            return False
        a, b = target.segment()
        return (segment_distance(field.position, a, b) - target.radius <= field.radius
                and capsule_visible(self.fight._collision(), field.position, a, b, target.radius)
                and not self.fight.walls.blocked(field.position, (target.feet[0], a[1], target.feet[2])))

    def source_active(self, field):
        return (not field.owner.gone and field.owner.body == field.body
                and not self.fight.match.ended)

    def npc_move(self, bot, tick):
        if not self.fields or bot.dead or bot.entity in self.fight.pins:
            return None
        target = self.fight._bot_target(bot)
        for field in self.fields:
            if (field.last_tick <= tick < field.until and self.source_active(field)
                    and (self.fight.match.game_map.free_for_all or bot.team != field.owner.team)
                    and self.contains(field, target)):
                x, y, z = field.position
                return (x, y - target.eye, z), PULL_SPEED
        return None

    def controls(self, player):
        return (player.body in self.pulled and not reaper.intangible(player)
                and player.body not in self.fight.pins
                and not self.fight.hooks.controlled(player.body))

    def update(self, tick):
        from ow174.game.combat import TICK

        if not self.fields and not self.pulled:
            return
        moved = set()
        advanced = False
        for field in tuple(self.fields):
            if not self.source_active(field):
                self.fields.remove(field)
                advanced = True
                continue
            elapsed = max(0, min(tick, field.until) - field.last_tick)
            if not elapsed:
                continue
            advanced = True
            field.last_tick += elapsed
            for target in self.fight._enemies(field.owner):
                if not self.contains(field, target):
                    continue
                self.fight._damage(field.owner, target, DAMAGE_RATE * elapsed * TICK, False, LOADOUT,
                                   tick, world_tick=tick, award_charge=False, bypass_armour=True)
                if (isinstance(target.owner, bots.Bot) or tick >= field.until or target.entity in moved
                        or self.fight.vitals_of(target).dead
                        or self.fight.fortify_control_immune(target) or target.entity in self.fight.pins
                        or self.fight.hooks.controlled(target.entity)):
                    continue
                player = target.owner
                mover = player.mover
                mover.use(world=self.fight.match.collision_world)
                x, y, z = field.position
                before = tuple(mover.position)
                frame = player.body_script.frame + 1 if player.body_script is not None else tick
                destination = (x, y - target.eye, z)
                flux_motion = sigma_flux.needs_pose(player)
                mover.step_pose(Command(frame, yaw=mover.yaw, pitch=mover.pitch), TICK, tick,
                                move_to=None if flux_motion else (destination, PULL_SPEED),
                                ground_override=self.fight.walls.ground(player))
                if flux_motion:
                    mover.move_toward_horizontal(destination, PULL_SPEED, TICK)
                self.fight.walls.move_player(player, before)
                history = self.fight.history.get(target.entity)
                if history:
                    frame, _, height, eye = history[-1]
                    history[-1] = frame, tuple(mover.position), height, eye
                moved.add(target.entity)
            if tick >= field.until:
                self.fields.remove(field)
        if self.fields and not advanced:
            return
        for player in self.fight.match.players:
            if player.body in self.pulled - moved and not reaper.intangible(player):
                mover = player.mover
                mover.velocity = (0.0, mover.velocity[1], 0.0)
                mover.driven = False
        self.pulled = moved
