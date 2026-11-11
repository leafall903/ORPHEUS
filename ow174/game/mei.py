"""104319 spray and frost resource adapters."""

from ow174.game.script import expr

HERO = 0x02E00000000000DD
SPRAY_LOADOUT = 0x0B900000000000D3
# Actual104319 0B77/node31 and05E7/node28, inspected outside the source tree.
FROST_STEP, FROST_INITIAL = expr.f32(0.0285), expr.f32(0.2)
FROST_GAP_MS, FREEZE_MS = 1000, 1300


def frost_immune(actor):
    """0B77/node15 retires frost for these actual entity predicates.

    This only consumes their frost policy; it does not infer generic damage
    immunity or native tag/foreign-setting semantics from their identifiers.
    """
    component = getattr(getattr(actor, 'body_script', None), 'component', None)
    if component is None:
        return False
    return any((var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
               for key in (6604, 8440, 9161))


def frost_view(sample, now_ms):
    """Reconstructed aggregate frost lifetime; no hidden wall-clock timers."""
    if sample is None:
        return None
    _progress, touched, frozen_until, _yaw, _pitch = sample
    if frozen_until:
        return sample if now_ms < frozen_until else None
    return sample if now_ms < touched + FROST_GAP_MS else None


def frost_hit(sample, now_ms, yaw, pitch, *, initial=FROST_INITIAL, step=FROST_STEP, freeze_ms=FREEZE_MS):
    sample = frost_view(sample, now_ms)
    if sample is not None and sample[2] > now_ms:
        return sample  # Explicit reconstruction: an active freeze is not extended by hits.
    progress = sample[0] if sample is not None else 0.0
    progress = min(1.0, max(initial, expr.f32(progress + step)))
    return progress, now_ms, now_ms + freeze_ms if progress >= 1.0 else 0, yaw, pitch


def projectile_target_limit(state) -> int:
    """Reconstruct spray's99-target field; do not infer other weapons' policy."""
    if state.instance.graph.index != 0x05E6 or state.index != 13:
        return 1
    config = state.node.field("m_6396149F") or {}
    value = config.get("m_B4454CD7")
    return max(1, min(99, expr.to_int(state.evaluate(value, watch=False)))) if value else 1
