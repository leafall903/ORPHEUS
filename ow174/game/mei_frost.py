"""Build 104319 frost accumulation and reconstructed settings proxy."""

import math

from ow174.game.script import expr

SPRAY_LOADOUT = 0x0B900000000000D3
# 0B77 node 31 advances the source from the 05E7 initial value.
INITIAL, STEP = expr.f32(0.2), expr.f32(0.0285)
GAP_MS, FREEZE_MS = 1000, 1300
TICK_MS = 16
RATE, MINIMUM, DURATION, STACKING = 0x2FE6, 0x2FEA, 0x300D, 0x301F


def settings(source, *, initial, step, channel):
    # Body slots proxy explicit external #392 overrides. Absent slots preserve
    # the defaults in the actual 0B77 effect graph.
    component = getattr(getattr(source, "body_script", None), "component", None)

    def value(key):
        slot = component.entity_var(key, create=False) if component is not None else None
        return slot.value() if slot is not None else None

    def scalar(key, default, low, high):
        raw = value(key)
        if type(raw) not in (int, float) or not math.isfinite(raw):
            return default
        return expr.f32(max(low, min(high, raw)))

    stacking = expr.truthy(value(STACKING))
    if channel:
        return initial, step, FREEZE_MS, stacking
    initial = scalar(MINIMUM, initial, 0.0, 1.0)
    step = expr.f32(step * scalar(RATE, 1.0, 0.0, 5.0))
    freeze_ms = round(FREEZE_MS * scalar(DURATION, 1.0, 0.2, 5.0))
    return initial, step, freeze_ms, stacking


def immune(actor):
    """Read the three scoped frost-removal predicates from 0B77 node 15."""
    component = getattr(getattr(actor, "body_script", None), "component", None)
    if component is None:
        return False
    return any((var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
               for key in (6604, 8440, 9161))


def expired(touched, tick):
    return (tick - touched) * TICK_MS >= GAP_MS


def hit(previous, tick, *, initial=INITIAL, step=STEP):
    progress, touched = previous
    if expired(touched, tick):
        progress = 0.0
    return min(1.0, max(initial, expr.f32(progress + step))), tick


def combined_progress(hits, tick):
    maximum = additive = 0.0
    # 0B77 node 5 takes the maximum and node 36 adds stacking sources.
    for key in sorted(hits):
        progress, touched, stacking = hits[key]
        if expired(touched, tick):
            continue
        if stacking:
            additive = expr.f32(additive + progress)
        else:
            maximum = max(maximum, progress)
    return min(1.0, expr.f32(maximum + additive))


def thaw_tick(tick, freeze_ms):
    return tick + (freeze_ms + TICK_MS - 1) // TICK_MS


def spray_target_limit(state):
    if state.instance.graph.index != 0x05E6 or state.index != 13:
        return 1
    limit = state.node.field("m_6396149F").get("m_B4454CD7")
    return max(1, min(99, expr.to_int(state.evaluate(limit, watch=False)))) if limit else 1
