"""BD1BC0/BD1CB0 sphere history selection from explicit newest-first samples.

Native ring storage/lifetime and actor bindings are deliberately not inferred.
"""

from dataclasses import dataclass

from .expr import f32
from .target_spatial import _mask, _rounded, _sphere

NATIVE_TICK_SCALE = 16000  # captured global2D16B30, original BD1CB0 multiplier


@dataclass(frozen=True)
class SphereSample:
    frame: int
    generation: int
    sphere: tuple


@dataclass(frozen=True)
class SphereHistory:
    samples: object = NotImplemented  # explicit tuple, newest first; () is known empty
    minimum_tick: object = NotImplemented  # component+2B8, always clamps
    flagged_minimum_tick: object = NotImplemented  # component+2B0, clamps when bool arg is true

    def at(self, tick, use_flagged_minimum):
        tick = _mask(tick)
        if type(use_flagged_minimum) is not bool:
            raise ValueError("history floor flag must be explicit")
        if not isinstance(self.samples, tuple):
            raise ValueError("sphere history is unavailable")
        if not self.samples:
            return None
        tick = max(tick, _mask(self.minimum_tick))
        if use_flagged_minimum:
            tick = max(tick, _mask(self.flagged_minimum_tick))
        samples = []
        for sample in self.samples:
            if not isinstance(sample, SphereSample):
                raise ValueError("sphere history sample is unavailable")
            if type(sample.frame) is not int or not 0 <= sample.frame < 1 << 32:
                raise ValueError("sphere history frame must be uint32")
            if type(sample.generation) is not int or not 0 <= sample.generation < 1 << 32:
                raise ValueError("sphere history generation must be uint32")
            samples.append((sample.frame * NATIVE_TICK_SCALE, sample.generation, _sphere(sample.sphere)))
        older, newer = None, None
        for index, (time, _generation, _sphere_value) in enumerate(samples):
            if time >= tick:
                newer = index
            if time <= tick:
                older = index
                break
        if newer is None:
            newer = older
        elif older is None or samples[older][1] != samples[newer][1]:
            older = newer
        if older is None or newer is None:
            raise ValueError("sphere history has no selectable sample")
        old_time, _generation, old = samples[older]
        new_time, _generation, new = samples[newer]
        if older == newer or old_time == new_time:
            return old
        # Both durations are nonnegative in native uint64 before CVTSI2SS/DIVSS.
        alpha = f32(f32(tick - old_time) / f32(new_time - old_time))
        center = tuple(_rounded(_rounded(_rounded(new[i] - old[i]) * alpha) + old[i]) for i in range(3))
        return (*center, max(old[3], new[3]))
