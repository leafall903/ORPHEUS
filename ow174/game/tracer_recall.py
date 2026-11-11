"""Server combat consumer for Tracer's authored Recall phases."""

import math
from collections import deque
from dataclasses import dataclass

from ow174.game.script import expr
from ow174.game.world import HEALTH_POOL

HERO = 0x02E0000000000003
GRAPH = 0x001A
HISTORY_FRAMES = 256


@dataclass(frozen=True)
class Sample:
    now: float
    feet: tuple
    health: float


@dataclass
class Phase:
    sample: Sample
    starts: float
    ends: float
    cleansed: bool = False


def matches(state, position):
    instance = state.instance
    component = instance.component
    return (not component.strict and component.world.hero == HERO
            and instance.graph.index == GRAPH and state.node.pos == position)


def timing(state):
    instance = state.instance
    rewind = instance.graph.node(1)
    windup = instance.graph.node(16)
    return tuple(expr.to_float(instance.evaluate(field)) for field in (
        windup.field("m_5BEFF040"), rewind.field("m_FAF3B2D3"), rewind.field("m_6B5DA460"),
    ))


def exit_frame(player):
    """Recover the queued Recall exit before a bounded input gap skips it."""
    if player.hero.guid != HERO or player.body_script is None:
        return None
    component = player.body_script.component
    for instance in component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        rewind = instance.states.get(0)
        if rewind is not None and rewind.active and matches(rewind, 1):
            _, duration, _ = timing(rewind)
        else:
            rewind = instance.states.get(3)
            if rewind is None or not rewind.active or not matches(rewind, 4):
                continue
            windup, main, _ = timing(rewind)
            duration = windup + main
        if math.isfinite(duration) and duration > 0:
            return (rewind.start + round(duration * 1000)) // component.frame_ms
    return None


class Recalls:
    def __init__(self, fight):
        self.fight = fight
        self.history = {}
        self.phases = {}

    def observe(self, target):
        player = target.owner
        if getattr(getattr(player, "hero", None), "guid", None) != HERO:
            return
        vitals = self.fight.vitals_of(target)
        if not vitals.dead:
            history = self.history.setdefault(target.entity, deque(maxlen=HISTORY_FRAMES))
            if not history or history[-1].now < self.fight.now:
                history.append(Sample(self.fight.now, target.feet, vitals.now[HEALTH_POOL]))

    def start(self, state, windup, duration, lookback):
        entity = state.instance.component.entity
        target = next((target for target in self.fight._targets() if target.entity == entity), None)
        if target is None:
            return
        vitals = self.fight.vitals_of(target)
        if vitals.dead or entity in self.phases:
            return
        now = self.fight.now
        history = self.history.get(entity)
        if history:
            sample = next((item for item in reversed(history) if item.now <= now - lookback), history[0])
        else:
            sample = Sample(now, target.feet, vitals.now[HEALTH_POOL])
        self.phases[entity] = Phase(sample, now + windup, now + windup + duration)

    def locked(self, actor):
        return getattr(actor, "body", None) in self.phases

    def active(self, actor):
        phase = self.phases.get(getattr(actor, "body", None))
        return phase is not None and phase.starts <= self.fight.now < phase.ends

    def retire(self, entity):
        self.interrupt(entity)
        self.history.pop(entity, None)

    def interrupt(self, entity):
        self.phases.pop(entity, None)

    def update(self):
        if not self.phases:
            return
        targets = {target.entity: target for target in self.fight._targets()}
        for entity, phase in tuple(self.phases.items()):
            target = targets.get(entity)
            if target is None or self.fight.vitals_of(target).dead:
                self.retire(entity)
                continue
            if self.fight.now < phase.starts:
                continue
            if not phase.cleansed:
                self.fight.cleanse_ana_effects(entity)
                phase.cleansed = True
            if self.fight.now >= phase.ends:
                del self.phases[entity]
                self.finish(target, phase.sample)

    def finish(self, target, sample):
        player = target.owner
        vitals = self.fight.vitals_of(target)
        if self.fight.body_clearance(player, sample.feet):
            self.fight.teleport_body(target, sample.feet)
        health = min(vitals.most[HEALTH_POOL], sample.health)
        if health > vitals.now[HEALTH_POOL]:
            vitals.now[HEALTH_POOL] = health
            vitals.changed.add(HEALTH_POOL)
            self.fight._sync_body_health(player, vitals)


def prelude(state):
    if not matches(state, 4):
        return False
    windup, duration, lookback = timing(state)
    if not all(math.isfinite(value) and value > 0 for value in (windup, duration, lookback)):
        return False
    match = getattr(state.instance.component.world, "match", None)
    if match is not None:
        match.combat.recalls.start(state, windup, duration, lookback)
    state.request_finish(round(windup * 1000))
    return True
