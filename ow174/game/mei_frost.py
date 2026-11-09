"""104319 0B77 spray progress and 05E7 freeze, hosted on the server clock."""

import math
from dataclasses import dataclass, field

from ow174.game.script import expr

EFFECT = 0x04A0000000000169
MOD = (-1, 0, 0)
GAP_MS, FREEZE_MS = 1000, 1300
STEP, INITIAL = expr.f32(0.0285), expr.f32(0.2)


@dataclass
class Frost:
    sources: dict[tuple[int, int], tuple[float, int, bool]] = field(default_factory=dict)
    frozen_from: int = 0
    frozen_until: int = 0
    yaw: int = 0
    pitch: int = 0

    def frozen(self, tick):
        return self.frozen_until > 0 and self.frozen_from <= tick * 16 < self.frozen_until

    def progress(self, now):
        maximum, added = 0.0, 0.0
        for key in sorted(self.sources):
            value, last, stacks = self.sources[key]
            if now >= last + GAP_MS:
                continue
            if stacks:
                added = expr.f32(added + value)
            else:
                maximum = max(maximum, value)
        return min(1.0, expr.f32(maximum + added))

    def hit(self, source, now, yaw, pitch, *, channel=0, initial=INITIAL, step=STEP):
        # Frost stores independent source progress. Active freeze never refreshes.
        self.prune(now)
        component = source.body_script.component

        def setting(key, default):
            slot = component.entity_var(key, create=False)
            return default if slot is None or slot.value() is None else slot.value()

        def bounded(key, default, low, high):
            value = expr.to_float(setting(key, default))
            return expr.f32(min(high, max(low, value))) if math.isfinite(value) else default

        minimum = initial if channel else bounded(0x2FEA, initial, 0.0, 1.0)
        rate = 1.0 if channel else bounded(0x2FE6, 1.0, 0.0, 5.0)
        duration = 1.0 if channel else bounded(0x300D, 1.0, 0.2, 5.0)
        stacks = expr.truthy(setting(0x301F, False))
        key = source.body, channel
        previous = self.sources.get(key, (0.0, 0, False))[0]
        value = min(1.0, max(minimum, expr.f32(previous + expr.f32(step * rate))))
        self.sources[key] = value, now, stacks
        if self.progress(now) >= 1.0:
            self.frozen_from, self.frozen_until = now, now + round(FREEZE_MS * duration)
            self.yaw, self.pitch = yaw, pitch

    def movement_mod(self, active, tick):
        active[MOD] = (0, 1, max(0.3, 1.0 - self.progress(tick * 16)), 99.0)

    def prune(self, now):
        for source, (_, last, _) in tuple(self.sources.items()):
            if now >= last + GAP_MS:
                del self.sources[source]


def immune(actor):
    script = actor.body_script
    return script is not None and any(
        var is not None and expr.truthy(var.value())
        for var in (script.component.entity_var(key, create=False) for key in (6604, 8440, 9161))
    )


HERO = 0x02E00000000000DD
SPRAY_PROJECTILE = 0x04000000000008BE  # verified 0145 exclusion tag
SPRAY_LOADOUT = 0x0B900000000000D3


def projectile_target_limit(state) -> int:
    """Reconstruct spray's99-target field; do not infer other weapons' policy."""
    if state.instance.graph.index != 0x05E6 or state.index != 13:
        return 1
    config = state.node.field("m_6396149F") or {}
    value = config.get("m_B4454CD7")
    return max(1, min(99, expr.to_int(state.evaluate(value, watch=False)))) if value else 1
