"""Host timing for the 104319 Mei spray frost applied to player bodies."""

from ow174.game.script import expr

SPRAY_LOADOUT = 0x0B900000000000D3
# 0B77 and 05E7 frost settings.
INITIAL = expr.f32(0.2)
STEP = expr.f32(0.0285)
GAP_MS = 1000
FREEZE_MS = 1300
PROGRESS_VAR = 2427
STACKING_VAR = 0x301F


def immune(component):
    return any(
        (var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
        for key in (6604, 8440, 9161)
    )


def live(sample, now):
    if sample is None:
        return None
    _, touched, deadline, _, _ = sample
    return sample if now < (deadline or touched + GAP_MS) else None


def source_hit(source, previous, now, *, initial, step):
    component = getattr(getattr(source, "body_script", None), "component", None)
    stacking = component.entity_var(STACKING_VAR, create=False) if component is not None else None
    progress = min(1.0, max(initial, expr.f32(previous + step)))
    return progress, now, stacking is not None and expr.truthy(stacking.value())


def prune(sources, now):
    for entity, (_, touched, _) in tuple(sources.items()):
        if now >= touched + GAP_MS:
            del sources[entity]


def aggregate(sources, now, yaw, pitch):
    maximum, additive, newest = 0.0, 0.0, 0
    for entity in sorted(sources):
        progress, touched, stacking = sources[entity]
        newest = max(newest, touched)
        if stacking:
            additive = expr.f32(additive + progress)
        else:
            maximum = max(maximum, progress)
    progress = min(1.0, expr.f32(maximum + additive))
    return progress, now if progress == 1.0 else newest, (
        now + FREEZE_MS if progress == 1.0 else 0
    ), yaw, pitch


def projectile_target_limit(state):
    if state.instance.graph.index != 0x05E6 or state.index != 13:
        return 1
    config = state.node.field("m_6396149F") or {}
    limit = config.get("m_B4454CD7")
    return max(1, min(99, expr.to_int(state.evaluate(limit, watch=False)))) if limit else 1
