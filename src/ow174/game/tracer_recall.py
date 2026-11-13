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
    later_positions: tuple = ()


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
        later_positions = tuple(item.feet for item in (history or ()) if item.now > sample.now)
        self.phases[entity] = Phase(sample, now + windup, now + windup + duration,
                                    later_positions=later_positions)
        self.fight.zenyatta.cleanse_discord(entity)

    def locked(self, actor):
        return getattr(actor, "body", None) in self.phases

    def active(self, actor):
        phase = self.phases.get(getattr(actor, "body", None))
        return phase is not None and self.fight.now < phase.ends

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
                self.finish(target, phase.sample, later_positions=phase.later_positions)

    def finish(self, target, sample, *, later_positions=()):
        player = target.owner
        vitals = self.fight.vitals_of(target)
        destination = sample.feet
        if not self.fight.body_clearance(player, destination):
            destination = next((point for point in later_positions
                                if self.fight.body_clearance(player, point)), target.feet)
        if destination != target.feet:
            self.fight.teleport_body(target, destination)
        player.mover.velocity = (0.0, 0.0, 0.0)
        player.mover.fall = 0.0
        health = min(vitals.most[HEALTH_POOL], sample.health)
        if health > vitals.now[HEALTH_POOL]:
            vitals.now[HEALTH_POOL] = health
            vitals.changed.add(HEALTH_POOL)
            self.fight._sync_body_health(player, vitals)
        from ow174.game import tracer

        # Finish runs before the recipient's next pose. Never select a retained
        # pre-restore snapshot as the first owner correction.
        tracer.correct_pose(player, player.body_script.component.frame + 1)


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


def end_phase(state, finished):
    """Retain the delivered cancellation/cooldown bridge for either Recall phase."""
    if finished or not (matches(state, 4) or matches(state, 1)):
        return
    instance = state.instance
    match = getattr(instance.component.world, "match", None)
    if match is None or instance.component.entity not in match.combat.recalls.phases:
        return
    match.combat.recalls.interrupt(instance.component.entity)
    instance.write((expr.INSTANCE, 61), instance.find_var(expr.INSTANCE, 472).value())
