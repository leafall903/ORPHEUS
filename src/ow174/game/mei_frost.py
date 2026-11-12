"""Scoped104319 0B77 source aggregation, not native effect-instance hosting.

Each(instigator body,channel) owns #499 progress; node5 takes a maximum,
node36 adds enabled stacking contributions. Frozen aggregate deadlines never
refresh. This grouping, mixed-policy ordering and post-thaw reset are explicit
reconstructions. Entity slots model a declared external#392 settings proxy;
missing slots retain native ValueOrDefault.2/1 instead of assuming menu.3.
"""

import math

from ow174.game import mei
from ow174.game.script import expr

RATE, MINIMUM, DURATION, STACKING = 0x2FE6, 0x2FEA, 0x300D, 0x301F


def settings(source, *, weapon):
    script = getattr(source, 'body_script', None)
    component = getattr(script, 'component', None)

    def value(key, default):
        slot = component.entity_var(key, create=False) if component is not None else None
        return default if slot is None or slot.value() is None else slot.value()

    def bounded(key, default, low, high):
        result = expr.to_float(value(key, default))
        return expr.f32(max(low, min(high, result))) if math.isfinite(result) else default

    stacking = expr.truthy(value(STACKING, False))
    if not weapon:
        return None, 1., mei.FREEZE_MS, stacking
    minimum = None if value(MINIMUM, None) is None else bounded(MINIMUM, mei.FROST_INITIAL, 0., 1.)
    return (minimum, bounded(RATE, 1., 0., 5.),
            round(mei.FREEZE_MS * bounded(DURATION, 1., .2, 5.)), stacking)


def prune(entries, at):
    for key, (sample, _stacking) in tuple(entries.items()):
        if mei.frost_view(sample, at) is None:
            del entries[key]


def combine(entries, at, yaw, pitch, *, freeze_ms=mei.FREEZE_MS):
    if not entries:
        return None
    maximum, additive, touched = 0., 0., 0
    for key in sorted(entries):
        sample, stacking = entries[key]
        progress, time, _end, _yaw, _pitch = sample
        touched = max(touched, time)
        if stacking:
            additive = expr.f32(additive + progress)
        else:
            maximum = max(maximum, progress)
    progress = min(1., expr.f32(maximum + additive))
    return progress, touched, at + freeze_ms if progress >= 1. else 0, yaw, pitch
