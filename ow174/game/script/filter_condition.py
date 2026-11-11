"""104319 1B8D970 condition stage, distinct from the game filter D0DA20."""

from ow174.game.script import expr, play


def evaluate(instance, filter_cfg, candidate, *, context_present, watcher=None):
    if not isinstance(filter_cfg, dict) or type(context_present) is not bool:
        raise ValueError('expected a declared filter and context availability')
    if type(candidate) is not int or not 0 <= candidate <= 0xFFFFFFFF:
        raise ValueError('filter candidate must be a native uint32 identity')
    condition = filter_cfg.get('m_condition')
    if condition is None:
        return True
    if not context_present:
        return False

    def result():
        # 90BFC0 retains its true default after a known no-output failure.
        return expr.truthy(play.evaluate_scalar(instance, condition, initial=True, watcher=watcher))

    component = instance.component
    with component.operation():
        if expr.valid_entity(candidate):
            with component.parameter_scope({8: expr.Entity(candidate)}):
                return result()
        return result()
