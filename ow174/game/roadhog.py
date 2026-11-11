"""Roadhog's authored Breather schedule and Scrap Gun pellet patterns."""

import math
from dataclasses import dataclass

from ow174.game.hook import HERO
from ow174.game.script import expr

BREATHER = 0x0240
FRAME_MS = 16
SCRAP_GUN = 0x01C6
PRIMARY = 26
SECONDARY = 29
PELLET = 0x040000000000029A
SLUG = 0x0400000000000295
SCRAP_PROJECTILES = frozenset((PELLET, SLUG))


def reorient_pellets(directions, before, after):
    def basis(forward):
        x, y, z = forward
        flat = math.hypot(x, z)
        right = (z / flat, 0.0, -x / flat) if flat > 1e-9 else (1.0, 0.0, 0.0)
        up = (y * right[2], z * right[0] - x * right[2], -y * right[0])
        return right, up, forward

    old, new = basis(before), basis(after)
    rotated = []
    for direction in directions:
        local = [sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in old]
        rotated.append(tuple(sum(value * axis[i] for value, axis in zip(local, new, strict=True))
                             for i in range(3)))
    return tuple(rotated)


@dataclass
class Breather:
    owner: object
    activation: int
    healing_start_ms: int
    end_ms: int
    last_ms: int
    total_healing: float
    reduction: float
    cancelled: bool = False

    def cumulative_healing(self, ms):
        duration = self.end_ms - self.healing_start_ms
        elapsed = min(duration, max(0, ms - self.healing_start_ms))
        if elapsed == duration:
            return self.total_healing
        return round(self.total_healing * elapsed * 1024 / duration) / 1024


def observe(fight, player):
    """Attach one world schedule to the actual outer Wait activation."""
    if player.hero.guid != HERO:
        return
    component = player.body_script.component
    instance = next((i for i in component.instances.values() if i.graph.index == BREATHER), None)
    if instance is None:
        return
    outer = instance.states.get(1)  # node 4
    if outer is None or not outer.active:
        fight.breathers.pop(player.body, None)
        return
    previous = fight.breathers.get(player.body)
    if previous is not None and previous.activation == outer.counter:
        return
    windup = instance.states.get(8)  # node 19
    if windup is None or not 0 <= windup.ms < outer.ms:
        return
    cfg = instance.graph.node(18).field("m_modifyHealth.m_amount")
    total = expr.to_float(outer.evaluate(cfg, watch=False))
    reduction = expr.to_float(component.entity_var(8243).value())
    if not math.isfinite(total) or total <= 0 or not 0 < reduction <= 1:
        return
    now_ms = (fight.tick + 1) * FRAME_MS
    start_ms = now_ms - (component.now + 1 - outer.start)
    fight.breathers[player.body] = Breather(
        player, outer.counter, start_ms + windup.ms, start_ms + outer.ms, now_ms, total, reduction,
    )


def cancel(fight, entity):
    state = fight.breathers.get(entity)
    if state is not None and not state.cancelled:
        state.cancelled = True
        _end_wait(state, (fight.tick + 1) * FRAME_MS - 1, finished=False)


def incoming_scale(fight, target):
    state = fight.breathers.get(target.entity)
    if (state is not None and not state.cancelled and fight.tick * FRAME_MS < state.end_ms
            and state.owner.body_script is not None and target.entity not in fight.hacks
            and not fight.is_stunned(target.owner)):
        return state.reduction
    return 1.0


def replay_until(fight, previous):
    return max((state.end_ms // FRAME_MS for state in fight.breathers.values()
                if not state.cancelled), default=previous)


def _end_wait(state, at_ms, *, finished):
    script = state.owner.body_script
    if script is None or script.retired:
        return
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == BREATHER), None)
    outer = instance.states.get(1) if instance is not None else None
    if outer is None or not outer.active or outer.counter != state.activation:
        return
    saved_now = component.now
    local_end = outer.start + outer.ms + min(0, at_ms - state.end_ms)
    # The authored end plug starts cooldown without consuming input frames.
    with component.operation():
        try:
            component.now = max(saved_now, local_end)
            outer.end(finished)
        finally:
            component.now = saved_now


def advance(fight):
    if not fight.breathers:
        return
    now_ms = (fight.tick + 1) * FRAME_MS
    targets = None
    for entity, state in tuple(fight.breathers.items()):
        if state.cancelled:
            continue
        if (state.owner.body != entity or state.owner.gone or state.owner.body_script is None
                or entity in fight.hacks or fight.is_stunned(state.owner) or fight.is_dead(state.owner)):
            cancel(fight, entity)
            continue
        if fight.tick * FRAME_MS >= state.end_ms:
            _end_wait(state, state.end_ms, finished=True)
            fight.breathers.pop(entity, None)
            continue
        end = min(now_ms, state.end_ms)
        if end <= state.last_ms:
            continue
        amount = state.cumulative_healing(end) - state.cumulative_healing(state.last_ms)
        state.last_ms = end
        if amount <= 0:
            continue
        if targets is None:
            targets = {target.entity: target for target in fight._targets()}
        target = targets.get(entity)
        if target is not None:
            fight.heal_target(target, amount, healer=state.owner)
