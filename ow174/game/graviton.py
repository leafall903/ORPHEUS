"""World combat for Zarya's authored 03E2 deployable."""

import math
from dataclasses import dataclass

from ow174.game import bots, defenses, dva, npc_charge
from ow174.game.mover import Mover, mover_data
from ow174.game.script import graph

FIELD_ENTITY = 0x04000000000003E2
DAMAGE_PER_SECOND = 5.0  # 0261/node2 from the 00A3.053 aura
PULL_SPEED = 3.0  # host interpretation of 0261/node15
LOADOUT = 0x0B9000000000005B
INPUT_LOCK = "graviton"
BOOSTERS_BUTTON = 0x08


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
        self.pulled: set[int] = set()
        self.npc_movers: dict[int, Mover] = {}
        self.last_tick = -1

    def landed(self, rocket, point, tick_seconds) -> None:
        if getattr(rocket.shooter, "gone", False) or self.combat.match.ended:
            return
        definition = graph.graph(0x0260)
        duration = definition.node(4).field("m_value.m_value")
        radius = definition.node(5).field("m_value.m_value")
        tick = self.combat.tick
        self.fields.append(Field(rocket.shooter, rocket.source_entity, rocket.team, point, tick,
                                 tick + math.ceil(duration / tick_seconds), radius))

    def _includes(self, field, target) -> bool:
        from ow174.game.combat import segment_distance

        if (target.owner is field.shooter or target.entity == field.source_entity
                or (isinstance(target.owner, bots.Bot) and target.owner not in self.combat.match.bots)
                or self.combat.vitals_of(target).dead
                or (not self.combat.match.game_map.free_for_all and target.team == field.team)):
            return False
        protection = defenses.protection(target.owner)
        script = getattr(target.owner, "body_script", None)
        # Let the authored Call Mech immunity scope own the entry move.
        entry_immune = protection == "immune" and getattr(script, "hero", None) == dva.HERO
        if protection == "intangible" or entry_immune:
            return False
        a, b = target.segment()
        return (segment_distance(field.position, a, b) - target.radius <= field.radius
                and self.combat._blast_visible(field.position, target))

    def update(self, tick, tick_seconds) -> None:
        if tick <= self.last_tick or (not self.fields and not self.pulled):
            return
        self.last_tick = tick
        pulled = set()
        targets = self.combat._targets()
        for field in list(self.fields):
            if getattr(field.shooter, "gone", False) or self.combat.match.ended:
                self.fields.remove(field)
                continue
            until = min(tick, field.end_tick)
            seconds = max(0, until - field.last_tick) * tick_seconds
            if seconds <= 0:
                continue
            field.last_tick = until
            for target in targets:
                if not self._includes(field, target):
                    continue
                self.combat._damage(field.shooter, target, DAMAGE_PER_SECOND * seconds,
                                    False, LOADOUT, tick, world_tick=tick, armour_scalar=1.0,
                                    source_entity=field.source_entity)
                if self.combat.vitals_of(target).dead:
                    pulled.discard(target.entity)
                    continue
                if (target.entity not in pulled
                        and tick < field.end_tick
                        and defenses.active_fortify(target.owner) is None
                        and target.entity not in self.combat.pins.values()):
                    if target.entity not in self.pulled:
                        npc = isinstance(target.owner, bots.Bot)
                        if not npc or target.owner.control_mover is not None:
                            self.combat.record_input_lock(target.entity, INPUT_LOCK, None)
                            if npc:
                                self.combat.stop_boosters(target.owner)
                        elif target.owner.hero == npc_charge.HERO:
                            npc_charge.stop(target.owner)
                    self._pull(target, field.position, seconds, tick)
                    pulled.add(target.entity)
            if tick >= field.end_tick:
                self.fields.remove(field)
        released = self.pulled - pulled
        for target in targets:
            if target.entity in released:
                self.release(target.owner)
        self.pulled = pulled
        self.npc_movers = {entity: mover for entity, mover in self.npc_movers.items()
                           if entity in pulled}

    def release(self, actor) -> None:
        """End the current body's pull before its movement owner changes."""
        npc = isinstance(actor, bots.Bot)
        entity = actor.entity if npc else actor.body
        if entity not in self.pulled:
            return
        self.pulled.remove(entity)
        self.combat.close_input_lock(entity, INPUT_LOCK)
        mover = self._mover(actor)
        self.npc_movers.pop(entity, None)
        if mover is not None and mover.driven:
            mover.velocity = (0.0, 0.0, 0.0)
            mover.driven = False
            if npc:
                actor.velocity = mover.velocity

    def _mover(self, actor):
        if isinstance(actor, bots.Bot):
            return actor.control_mover or self.npc_movers.get(actor.entity)
        return actor.mover

    def _pull(self, target, position, seconds, tick) -> None:
        actor = target.owner
        mover = self._mover(actor)
        if mover is None:
            mover = self.npc_movers[actor.entity] = Mover(
                actor.position, int(actor.yaw), self.combat._collision(), mover_data(actor.body),
            )
        if isinstance(actor, bots.Bot) and actor.control_mover is None:
            mover.position[:] = actor.position
        x, y, z = position
        mover.driven = True
        mover._move_to(seconds, (x, y - target.eye, z), PULL_SPEED)
        if isinstance(actor, bots.Bot):
            actor.position, actor.velocity = tuple(mover.position), mover.velocity
            actor.moved_tick = tick
