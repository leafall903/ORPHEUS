"""Roadhog 0240 Take a Breather through the hosted combat clock."""

import math
from dataclasses import dataclass

from ow174.game.script import expr

HERO = 0x02E0000000000040
GRAPH = 0x0240
INCOMING_SCALE = 8243


@dataclass
class Channel:
    instance: int
    activation: int
    until: float
    heal_started: float | None = None
    heal_duration: float = 0.0
    heal_amount: float = 0.0
    heal_accounted: float = 0.0
    deadline: int | None = None


class Breathers:
    def __init__(self, fight):
        self.fight = fight
        self.channels = {}

    def observe(self, player, *, tick=None, clock=True):
        if player.hero.guid != HERO:
            return
        component = player.body_script.component
        if component.strict:
            return
        for instance in component.instances.values():
            if instance.graph.index != GRAPH:
                continue
            wait = instance.states.get(instance.graph._by_pos[4].state)
            if wait is None or not wait.active or wait.ending:
                continue
            channel = self.channels.get(player.body)
            if channel is None or (channel.instance, channel.activation) != (instance.id, wait.counter):
                remaining = max(0, wait.start + wait.ms - component.now) / 1000
                channel = self.channels[player.body] = Channel(
                    instance.id, wait.counter, self.fight.now + remaining,
                )
            if clock or channel.deadline is None:
                # Original0040 note_breather_clock: world impacts precede body
                # input on the inclusive final tick. Use the actual endpoint
                # association during historical body callbacks.
                remaining = max(0, wait.start + wait.ms - component.now)
                frames = (remaining + component.frame_ms - 1) // component.frame_ms
                channel.deadline = (self.fight.tick if tick is None else tick) + frames
            heal = instance.states.get(instance.graph._by_pos[18].state)
            if channel.heal_started is None and heal is not None and heal.active and not heal.ending:
                self._start_healing(channel, heal)
            return
        self.retire(player.body)

    def _start_healing(self, channel, heal):
        modify = heal.node.field("m_modifyHealth")
        if ((heal.node.field("m_FA3ECC43") or {}).get("$") != "STU_B5A0CAF0"
                or modify.get("m_93CDFD2F") != 0):
            return
        amount = expr.to_float(heal.evaluate(modify["m_amount"], watch=False))
        duration = expr.to_float(heal.evaluate(heal.node.field("m_95F1FB0B"), watch=False))
        if not all(math.isfinite(value) and value > 0 for value in (duration, amount)):
            return
        channel.heal_started = self.fight.now
        channel.heal_duration = duration
        channel.heal_amount = amount

    def retire(self, body):
        self.channels.pop(body, None)

    def scale(self, owner, entity):
        channel = self.channels.get(entity)
        if channel is None or (self.fight.tick > channel.deadline if channel.deadline is not None
                               else self.fight.now > channel.until):
            return 1.0
        script = owner.body_script
        if (script is None or script.retired or script.component.owner_dead
                or script.component.fault is not None):
            return 1.0
        value = script.component.entity_var(INCOMING_SCALE, create=False)
        if value is None:
            return 1.0
        return max(0.0, min(1.0, expr.to_float(value.value())))

    def active_healing(self, now):
        return any(channel.heal_started is not None and channel.heal_accounted < channel.heal_amount
                   and now <= channel.until for channel in self.channels.values())

    def update(self):
        fight = self.fight
        if not self.channels:
            return
        for target in fight._targets():
            channel = self.channels.get(target.entity)
            if channel is None or channel.heal_started is None or channel.heal_accounted >= channel.heal_amount:
                continue
            script = target.owner.body_script
            instance = script.component.instances.get(channel.instance) if script is not None else None
            heal = instance.states.get(instance.graph._by_pos[18].state) if instance is not None else None
            if heal is None or not heal.active or heal.ending:
                continue
            # Original0240/node18 owns its body-frame heal budget. The manager
            # retains only its channel/reduction lifetime; never apply a second
            # world-clock dose to that same active node.
            if getattr(heal, "heal_duration", 0):
                continue
            elapsed = max(0.0, min(fight.now, channel.until) - channel.heal_started)
            share = min(1.0, elapsed / channel.heal_duration)
            if math.isclose(share, 1.0, abs_tol=1e-9):
                share = 1.0
            total = channel.heal_amount * share
            dose = total - channel.heal_accounted
            if dose > 0.0:
                channel.heal_accounted = total
                fight._heal(target.owner, target, dose)
