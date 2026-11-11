"""Reconstructed body frost from the 104319 Mei spray effect."""

import math
from dataclasses import dataclass, field

from ow174.game.script import expr

SPRAY_EFFECT = 0x04A0000000000169
INITIAL = expr.f32(.2)  # 0B77/node31 fallback
STEP = expr.f32(.0285)
FREEZE_SECONDS = 1.3  # 05E7/node28


@dataclass
class Frost:
    sources: dict[tuple[int, int], tuple[float, int]] = field(default_factory=dict)
    until: int = 0
    yaw: int = 0
    pitch: int = 0

    def progress(self, tick):
        if self.until:
            return 1.0 if tick < self.until else 0.0
        return max((value for value, end in self.sources.values() if tick < end), default=0.0)


def sample(combat, body, tick):
    frost = combat.frost.get(body)
    return frost if frost is not None and frost.progress(tick) > 0 else None


def hit(combat, target, source, *, channel=0, initial=INITIAL, increment=STEP):
    from ow174.game import bots
    from ow174.game.combat import TICK, _source_entity

    owner = target.owner
    component = getattr(getattr(owner, "body_script", None), "component", None)
    if component is not None and any(
            (var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
            for key in (6604, 8440, 9161)):
        return
    tick = combat.tick
    current = sample(combat, target.entity, tick)
    if current is not None and current.until:
        return  # Hits during a freeze do not extend the deadline.
    if current is None:
        current = combat.frost[target.entity] = Frost()
    current.sources = {body: value for body, value in current.sources.items() if value[1] > tick}
    source_id = _source_entity(source), channel
    previous = current.sources.get(source_id, (0.0, 0))[0]
    value = min(1.0, max(initial, expr.f32(previous + increment)))
    current.sources[source_id] = value, tick + math.ceil(1.0 / TICK)
    if value >= 1:
        current.until = tick + math.ceil(FREEZE_SECONDS / TICK)
        if isinstance(owner, bots.Bot):
            from ow174.game import assassin

            assassin.cancel(combat.match, owner)
        else:
            current.yaw, current.pitch = owner.mover.yaw, owner.mover.pitch
        combat._interrupt(target)


def update(combat):
    for body, frost in tuple(combat.frost.items()):
        if frost.progress(combat.tick) <= 0:
            del combat.frost[body]
