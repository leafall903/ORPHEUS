"""Build 104319 frost progression and the reconstructed 0B77 host."""

import math

from ow174.game import body_protection, tracer
from ow174.game.script import expr

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
        return None, 1., FREEZE_MS, stacking
    minimum = None if value(MINIMUM, None) is None else bounded(MINIMUM, FROST_INITIAL, 0., 1.)
    return (minimum, bounded(RATE, 1., 0., 5.),
            round(FREEZE_MS * bounded(DURATION, 1., .2, 5.)), stacking)


def prune(entries, at):
    for key, (sample, _stacking) in tuple(entries.items()):
        if frost_view(sample, at) is None:
            del entries[key]


def combine(entries, at, yaw, pitch, *, freeze_ms=FREEZE_MS):
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


def protected(actor):
    return tracer.recalling(actor) or body_protection.active(actor) or frost_immune(actor)


def write_progress(actor, progress):
    script = getattr(actor, "body_script", None)
    if script is not None and not script.component.strict:
        script.component.set(2427, progress)


def clear(combat, entity, actor=None):
    combat.frost_sources.pop(entity, None)
    if combat.frost_hits.pop(entity, None) is not None and actor is not None:
        write_progress(actor, 0.0)


def update(combat):
    if not combat.frost_hits:
        return
    targets = {target.entity: target for target in combat._targets()}
    at = combat.tick * 16
    for entity, sample in tuple(combat.frost_hits.items()):
        target = targets.get(entity)
        if target is None:
            clear(combat, entity)
        elif (combat.vitals_of(target).dead or protected(target.owner)
              or frost_view(sample, at) is None):
            clear(combat, entity, target.owner)
        elif not sample[2]:
            entries = combat.frost_sources[entity]
            prune(entries, at)
            updated = combine(entries, at, *sample[3:])
            if updated is None:
                clear(combat, entity, target.owner)
            elif updated != sample:
                combat.frost_hits[entity] = updated
                write_progress(target.owner, updated[0])


def contact(combat, shooter, target, tick, *, channel=0, now_ms=None, initial=FROST_INITIAL, step=FROST_STEP):
    from ow174.game.combat import _source_entity

    actor = target.owner
    if protected(actor):
        return
    at = tick * 16 if now_ms is None else now_ms
    current = combat.frost_hits.get(target.entity)
    if current is not None:
        at = max(at, current[1])
        if current[2]:
            if at < current[2]:
                return
            clear(combat, target.entity, actor)
    mover = getattr(actor, "mover", None)
    yaw, pitch = (mover.yaw, mover.pitch) if mover is not None else (round(actor.yaw), 0)
    entries = combat.frost_sources.setdefault(target.entity, {})
    prune(entries, at)
    minimum, scale, duration, stacking = settings(shooter, weapon=channel == 0)
    source = (_source_entity(shooter), channel)
    previous = entries.get(source, (None, False))[0]
    entries[source] = (frost_hit(previous, at, yaw, pitch,
                                initial=initial if minimum is None else minimum,
                                step=expr.f32(step * scale), freeze_ms=duration), stacking)
    sample = combine(entries, at, yaw, pitch, freeze_ms=duration)
    combat.frost_hits[target.entity] = sample
    write_progress(actor, sample[0])
    if sample[2]:
        combat._interrupt(target)


def movement_mods(combat, actor, active):
    sample = frost_view(combat.frost_hits.get(actor.body), combat.tick * 16)
    if sample is None:
        return active
    active = dict(active)
    active[(-1, 0, 0)] = (0, 1, max(0.3, 1.0 - sample[0]), 99.0)
    return active


def projectile_target_limit(state):
    if state.instance.graph.index != 0x05E6 or state.index != 13:
        return 1
    config = state.node.field("m_6396149F.m_B4454CD7")
    return max(1, min(99, expr.to_int(state.evaluate(config, watch=False)))) if config else 1
