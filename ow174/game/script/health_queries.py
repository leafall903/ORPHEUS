"""104319 CEB6C0/CF12A0/CDFCC0 health queries through explicit world services.

Native D177C0/D17AE0 select component51 via BCC870 and call its +40/+50
numeric getters. D1AC30 selects the filtered current +70 getter. SceneWorld supplies an explicit body-pool snapshot.
Engine pool aggregation and observation callbacks are outside those bindings.
"""
import math
from dataclasses import dataclass

from ow174.game.script import expr, play

CURRENT = "STU_9B7648C3"
MAXIMUM = "STU_60146072"
FILTERED_CURRENT = "STU_783B4CF0"


@dataclass(frozen=True)
class Pools:
    """A point-in-time snapshot of current base amounts and body totals."""

    base: tuple[float, float, float]
    current: float
    maximum: float


def evaluate(evaluation, cfg):
    cls = cfg.get("$")
    if cls not in (CURRENT, MAXIMUM, FILTERED_CURRENT):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    filtered = cls == FILTERED_CURRENT
    if filtered:
        # CDFCC0 evaluates tag, kind, then entity. Failed conversions retain zero.
        tag = play._asset(play.evaluate_scalar(instance, cfg.get("m_7BE02F89"), watcher=watcher))
        kind = expr.to_int(play.evaluate_scalar(instance, cfg.get("m_7E8B666C"), watcher=watcher))
    value = play.evaluate_scalar(instance, cfg.get("m_entity"),
                                 initial=expr.Entity(0), watcher=watcher)
    entity = expr.to_entity(value) & 0xFFFFFFFF
    if not expr.valid_entity(entity):
        return expr.f32(0.0)  # native invalid identity writes zero successfully
    values = (component.world.filtered_body_health(entity, kind, tag) if filtered
              else component.world.body_health(entity))
    component.ensure_valid()
    if (instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance):
        component.unsupported(instance, "health query owner retired during lookup", cls)
    if values is NotImplemented:
        return NotImplemented
    if values is None:
        return expr.EVALUATION_FAILED  # valid identity, but native entity lookup failed
    if filtered:
        if type(values) not in (int, float) or not math.isfinite(values) or values < 0:
            component.unsupported(instance, "filtered health query pool result", cls)
        return expr.f32(values)
    if (type(values) is not tuple or len(values) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values)):
        component.unsupported(instance, "health query pool result", cls)
    return expr.f32(values[cls == MAXIMUM])
