"""Selected Mei 05E6 spray and 0B77 frost reconstruction.

The normal values come from 104319; per-source aggregation, movement scaling
and world-clock lifecycle are host policy. Blizzard has a separate cast path.
"""

import math

from ow174.game.mover import HISTORY
from ow174.game.script import expr

HERO = 0x02E00000000000DD
SPRAY_PROJECTILE = 0x04000000000008BE  # verified 0145 exclusion tag
SPRAY_LOADOUT = 0x0B900000000000D3
FROST_STEP, FROST_INITIAL = expr.f32(0.0285), expr.f32(0.2)
FROST_GAP_MS, FREEZE_MS = 1000, 1300
RATE, MINIMUM, DURATION, STACKING = 0x2FE6, 0x2FEA, 0x300D, 0x301F


def frost_immune(actor, *, stale_ultimate=False):
    """0B77/node15 retires frost for these actual entity predicates.

    This only consumes their frost policy; it does not infer generic damage
    immunity or native tag/foreign-setting semantics from their identifiers.
    """
    component = getattr(getattr(actor, 'body_script', None), 'component', None)
    if component is None:
        return False
    return any((var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
               for key in (6604, 8440, 9161) if key != 8440 or not stale_ultimate)


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


class Frost:
    def __init__(self, combat):
        self.combat = combat
        self.samples = {}
        self.sources = {}
        self.freeze_history = {}  # body -> retired freezes still needed for recovered poses

    @staticmethod
    def entity(actor):
        return actor.body if getattr(actor, "mover", None) is not None else actor.entity

    def sample(self, actor, tick):
        return self.sample_for_body(self.entity(actor), tick)

    def sample_for_body(self, entity, tick):
        now = tick * 16
        current = frost_view(self.samples.get(entity), now)
        if current is not None and current[1] <= now:
            return current
        return next((sample for sample in reversed(self.freeze_history.get(entity, ()))
                     if sample[1] <= now < sample[2]), None)

    def allowed(self, actor):
        # The selected authored marker also covers live Fortify. Wraith, Cryo
        # and Transcendence use their declared world-clock projection.
        zen = self.combat.zenyatta
        return not (zen.ultimate(actor) or frost_immune(actor, stale_ultimate=zen.stale_ultimate_flag(actor)))

    def _remember_freeze(self, entity, sample, cutoff):
        progress, start, end, yaw, pitch = sample
        end = min(end, cutoff)
        if start < end:
            self.freeze_history.setdefault(entity, []).append((progress, start, end, yaw, pitch))

    def retire(self, entity, actor=None, *, cutoff=None):
        self.sources.pop(entity, None)
        sample = self.samples.pop(entity, None)
        if cutoff is None:
            self.freeze_history.pop(entity, None)
        elif sample is not None and sample[2]:
            self._remember_freeze(entity, sample, cutoff)
        if sample is not None and actor is not None:
            script = getattr(actor, "body_script", None)
            if script is not None:
                script.component.set(2427, 0.0)

    def write(self, actor, entity, sample):
        self.samples[entity] = sample
        script = getattr(actor, "body_script", None)
        if script is not None:
            script.component.set(2427, sample[0])

    def update(self, tick):
        oldest = (tick - HISTORY) * 16
        for entity, samples in tuple(self.freeze_history.items()):
            retained = [sample for sample in samples if sample[2] > oldest]
            if retained:
                self.freeze_history[entity] = retained
            else:
                del self.freeze_history[entity]
        if not self.samples:
            return
        live = {target.entity: target for target in self.combat._targets()}
        for entity, sample in tuple(self.samples.items()):
            target = live.get(entity)
            actor = target.owner if target is not None else None
            if target is None or self.combat.vitals_of(target).dead:
                self.retire(entity, actor)
            elif not self.allowed(actor) or frost_view(sample, tick * 16) is None:
                self.retire(entity, actor, cutoff=tick * 16)
            elif not sample[2]:
                entries = self.sources[entity]
                prune(entries, tick * 16)
                aggregate = combine(entries, tick * 16, *sample[3:])
                if aggregate is None:
                    self.retire(entity, actor, cutoff=tick * 16)
                else:
                    self.write(actor, entity, aggregate)

    def apply(self, source, target, *, tick, channel=0, initial=FROST_INITIAL, step=FROST_STEP,
              at_ms=None):
        actor = target.owner
        if not self.allowed(actor):
            return
        entity = target.entity
        previous = self.samples.get(entity)
        now = tick * 16 if at_ms is None else at_ms
        if previous is not None:
            now = max(now, previous[1])
        if previous is not None and previous[2]:
            if now < previous[2]:
                return  # repeated contact does not extend an established freeze
            self._remember_freeze(entity, previous, now)
            self.sources.pop(entity, None)
        mover = getattr(actor, "mover", None)
        yaw = mover.yaw if mover is not None else round(getattr(actor, "yaw", 0))
        pitch = mover.pitch if mover is not None else 0
        entries = self.sources.setdefault(entity, {})
        prune(entries, now)
        minimum, scalar, duration, stacking = settings(source, weapon=channel == 0)
        key = (self.entity(source), channel)
        old = entries.get(key, (None, False))[0]
        entries[key] = (frost_hit(old, now, yaw, pitch,
                                  initial=initial if minimum is None else minimum,
                                  step=expr.f32(step * scalar), freeze_ms=duration), stacking)
        aggregate = combine(entries, now, yaw, pitch, freeze_ms=duration)
        self.write(actor, entity, aggregate)
        if aggregate[2]:
            self.combat._interrupt(target)
