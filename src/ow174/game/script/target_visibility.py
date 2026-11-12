"""104319 CA4450 sample combination with explicit vector and ray services.

This is the configured-sample helper, not CA3570's outer filtering/fallbacks.
The query backend owns transforms, collision policy and the resulting cache.
"""

from dataclasses import dataclass

from ow174.game.script import expr

ZERO = expr.Vec3(0.0, 0.0, 0.0)  # original1B721B0 fallback at3241710


class VisibilityUnavailable(RuntimeError):
    """A backend did not supply a required native vector or ray result."""


@dataclass(frozen=True)
class SampleResult:
    rejected: bool
    segment: tuple[expr.Vec3, expr.Vec3] | None = None


def _rounded(value):
    return expr.Vec3(*(expr.f32(axis) for axis in value))


def _vector(config, evaluate):
    if config is None:
        return ZERO
    value = evaluate(config)
    if value is NotImplemented:
        raise VisibilityUnavailable("visibility vector service is unavailable")
    # Known evaluation/conversion failure uses1B721B0's explicit zero default.
    return _rounded(value) if isinstance(value, expr.Vec3) else ZERO


def _blocked(ray, start, end):
    value = ray(start, end)
    if type(value) is not bool:
        raise VisibilityUnavailable("visibility ray service must supply a known bool")
    return value


def sample_segments(samples, *, origin, evaluate, ray, test_offset_leg):
    """Return native rejection and the first clear segment to cache, if any.

    evaluate(config) returns Vec3 or a known conversion failure; NotImplemented
    means unavailable. ray(start,end) supplies the explicit obstruction bool.
    A missing/empty sample array is not rejected by this helper; the outer
    caller must still implement its own fallback policy.
    """
    if not isinstance(origin, expr.Vec3) or type(test_offset_leg) is not bool:
        raise VisibilityUnavailable("visibility origin and offset-leg policy must be explicit")
    origin = _rounded(origin)
    had_sample = False
    for sample in samples or ():
        had_sample = True
        offset = sample.get("m_83BE3F26")
        if offset is not None:
            displacement = _vector(offset, evaluate)
            start = expr.Vec3(*(expr.f32(a + b) for a, b in zip(origin, displacement, strict=True)))
            if test_offset_leg and _blocked(ray, origin, start):
                continue
        else:
            start = _vector(sample.get("m_start"), evaluate)
        end = _vector(sample.get("m_end"), evaluate)
        if not _blocked(ray, start, end):
            return SampleResult(False, (start, end))
    return SampleResult(had_sample)
