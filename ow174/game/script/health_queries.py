"""104319 component51 health queries."""

import math

from ow174.game.script import expr, play

CURRENT = "STU_9B7648C3"  # CEB6C0, component51 +40
MAXIMUM = "STU_60146072"  # CF12A0, component51 +50
FILTERED_CURRENT = "STU_783B4CF0"  # CDFCC0, component51 +70


def evaluate(evaluation, cfg):
    cls = cfg.get("$")
    if cls not in (CURRENT, MAXIMUM, FILTERED_CURRENT):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    if cls == CURRENT:
        local = component.world.local_current_health(cfg, instance)
        if local is not NotImplemented:
            return local
    filtered = cls == FILTERED_CURRENT
    if filtered:
        local = component.world.local_filtered_health(cfg, instance)
        if local is not NotImplemented:
            return local
        # The native evaluator resolves tag, kind, then entity.
        tag = play._asset(play.evaluate_scalar(instance, cfg.get("m_7BE02F89"), watcher=watcher))
        kind = expr.to_int(play.evaluate_scalar(instance, cfg.get("m_7E8B666C"), watcher=watcher))
    value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0), watcher=watcher)
    entity = expr.to_entity(value) & 0xFFFFFFFF
    if not expr.valid_entity(entity):
        return 0.0
    amounts = (component.world.filtered_body_health(entity, kind, tag, instance) if filtered
               else component.world.body_health(entity, instance))
    component.ensure_valid()
    if (instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance):
        component.unsupported(instance, "health query owner retired during lookup", cls)
    if amounts is NotImplemented:
        return NotImplemented
    if amounts is None:
        return expr.EVALUATION_FAILED
    if filtered:
        values = (amounts,)
    elif type(amounts) is tuple and len(amounts) == 2:
        values = amounts
    else:
        component.unsupported(instance, "health query pool result", cls)
    if any(type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0 for amount in values):
        component.unsupported(instance, "health query pool result", cls)
    return expr.f32(values[cls == MAXIMUM])
