"""104319 1B8D970 candidate scope and 90BFC0/F64940 condition evaluation.

This is the authored-condition stage only. It does not grant entity lookup,
category/status bits, tags or native filter-registry services to AC829876.
"""

import math

from ow174.game.script import expr, play


def boolean(value, default):
    """F64940 conversion, including empty arrays and unsupported Handle tags."""
    if type(default) is not bool:
        raise ValueError("native boolean conversion default must be bool")
    if value is None:
        return False
    if type(value) is bool:
        return value
    if type(value) is int:
        return expr.i32(value) != 0
    if type(value) is float:
        value = expr.f32(value)
        return value != 0.0 and not math.isnan(value)
    if type(value) is expr.Asset:
        return value.guid & 0xFFFFFFFFFFFFFFFF != 0
    if type(value) is expr.Entity:
        return expr.valid_entity(value.id & 0xFFFFFFFF)
    if type(value) is expr.Vec3:
        return any(boolean(float(axis), False) for axis in value)
    if type(value) is tuple:
        return boolean(value[0], default) if value else default
    if type(value) is expr.Map:
        return bool(value.items)
    if type(value) is expr.Handle:
        return default
    raise ValueError("unsupported native filter condition value")


def evaluate(instance, config, target, *, watcher=None):
    """Evaluate an authored condition with temporary candidate parameter0008.

    Native invalid targets do not shadow an outer candidate scope. Missing
    conditions succeed before checking instance availability. Known evaluator
    failure retains the native true default; unknown strict queries still fault.
    """
    if type(target) is not int or not 0 <= target <= 0xFFFFFFFF:
        raise ValueError("filter candidate must be a native uint32 identity")
    if config is None:
        return True
    if instance is None:
        return False
    component = instance.component

    def run():
        value, success = play.evaluate_scalar_result(instance, config, initial=0, watcher=watcher)
        component.ensure_valid()
        return boolean(value, True) if success else True

    with component.operation():
        if expr.valid_entity(target):
            with component.parameter_scope({8: expr.Entity(target)}):
                return run()
        return run()
