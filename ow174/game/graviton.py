"""Graviton Surge field on the reconstructed Match host."""

import math
from dataclasses import dataclass

from ow174.game import body_protection, bots, tracer
from ow174.game.script import expr, graph

FIELD_ENTITY = 0x04000000000003E2
LOADOUT = 0x0B9000000000005B
# The 104319 0261 XML is outside the runtime hero closure. Node 2
# supplies continuous damage and node 15 supplies the pull scalar.
DAMAGE_PER_SECOND = 5.0
PULL_SPEED = 3.0


def launched(state):
    if (state.instance.graph.index, state.node.pos) != (0x025F, 1):
        return False
    cfg = state.node.field("m_49CBA3BC")
    return (state.evaluate(cfg["m_entityDefinition"], watch=False) == expr.Asset(FIELD_ENTITY)
            and expr.guid_of(cfg["m_410BF8BD"]["m_graph"]) == 0x0580000000000260)


@dataclass
class Field:
    caster: object
    source: int
    team: int
    position: tuple
    last_tick: int
    end_tick: int
    radius: float
    damage: float
    speed: float


class Gravitons:
    def __init__(self, combat):
        self.combat = combat
        self.fields: list[Field] = []
        self.player_pulls: set[int] = set()

    def landed(self, rocket, point):
        from ow174.game.combat import TICK

        if rocket.shooter.body != rocket.graviton_body or self.combat.match.ended:
            return
        placement = graph.graph(0x0260)
        if placement is None:
            return
        seconds = float(placement.node(4).field("m_value.m_value"))
        radius = float(placement.node(5).field("m_value.m_value"))
        tick = self.combat.tick
        self.fields.append(Field(rocket.shooter, rocket.graviton_body, rocket.team, tuple(point), tick,
                                 tick + math.ceil(seconds / TICK), radius, DAMAGE_PER_SECOND, PULL_SPEED))

    def includes(self, field, target):
        from ow174.game.combat import capsule_visible, segment_distance

        if (target.owner is field.caster or self.combat.vitals_of(target).dead
                or tracer.recalling(target.owner) or body_protection.active(target.owner)
                or (not self.combat.match.game_map.free_for_all and target.team == field.team)):
            return False
        a, b = target.segment()
        return (segment_distance(field.position, a, b) - target.radius <= field.radius
                and capsule_visible(self.combat._collision(), field.position, a, b, target.radius))

    def charging_blocked(self, body):
        return (any(field.source == body for field in self.fields)
                or any(rocket.graviton_body == body for rocket in self.combat.rockets))

    def update(self, tick):
        from ow174.game.combat import TICK

        if not self.fields and not self.player_pulls:
            return
        if self.fields and all(tick <= field.last_tick for field in self.fields):
            return
        pulled = set()
        for field in list(self.fields):
            if field.caster.body != field.source or field.caster.gone or self.combat.match.ended:
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            duration = max(0, until - field.last_tick) * TICK
            field.last_tick = max(until, field.last_tick)
            for target in self.combat._targets() if duration > 0 else ():
                if not self.includes(field, target):
                    continue
                self.combat._damage(field.caster, target, field.damage * duration, False, LOADOUT, tick,
                                    world_tick=tick, damage_over_time=True)
                if (isinstance(target.owner, bots.Bot) or self.combat.vitals_of(target).dead
                        or tick >= field.end_tick or target.entity in pulled):
                    continue
                if target.entity not in self.player_pulls:
                    tracer.discard_mobility_input(target.owner)
                pulled.add(target.entity)
                x, y, z = field.position
                mover = target.owner.mover
                mover.driven = True
                mover._move_to(duration, (x, y - target.eye, z), field.speed)
                history = self.combat.history.get(target.entity)
                if history:
                    frame, _, *pose = history[-1]
                    history[-1] = frame, tuple(mover.position), *pose
            if tick >= field.end_tick:
                self.fields.remove(field)
        released = self.player_pulls - pulled
        for player in self.combat.match.players:
            if player.body in released and not tracer.recalling(player):
                player.mover.velocity = (0.0, 0.0, 0.0)
                player.mover.driven = False
        self.player_pulls = pulled
