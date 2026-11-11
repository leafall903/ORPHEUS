"""Local consumer for authored 224E control requests."""

import math

from ow174.game import defenses
from ow174.game.script import expr, graph, runtime


class _ControlContext(expr.Context):
    """Read the target's actual gate variables without creating an effect instance."""

    def __init__(self, component, overrides):
        self.component = component
        self.overrides = {identifier & 0xFFFF: value for identifier, value in overrides}

    def var(self, scope, identifier):
        if scope == graph.INSTANCE:
            return self.overrides.get(identifier)
        variable = self.component.entity_var(identifier, create=False)
        return variable.value() if variable is not None else None

    def would_stack_to_top(self, var, priority):
        if var is None or var[0] != graph.ENTITY:
            return False
        variable = self.component.entity_var(var[1], create=False)
        return variable is None or runtime.would_stack_to_top(variable, priority)


def apply_stun(fight, target, resource):
    """Evaluate the recipient gates and apply the authored duration."""
    if resource is None or resource.guid != ((graph.GRAPH_TYPE << 48) | 0x224E):
        return False
    duration = dict(resource.overrides).get(0x0D800000000000D7)
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return False
    if (fight.vitals_of(target).dead or defenses.phase(target.owner)[0]
            or defenses.fortified(target.owner)):
        return False
    script = getattr(target.owner, "body_script", None)
    definition = graph.graph(0x224E)
    if (definition is None or script is None or script.component.entity != target.entity
            or script.component.strict or script.component.owner_dead or script.component.fault is not None):
        return False
    context = _ControlContext(script.component, resource.overrides)
    for pos in (4, 122):
        if not expr.truthy(expr.evaluate(definition.node(pos).field("m_condition"), context)):
            return False
    seconds = expr.to_int(expr.f32(duration * 1000)) / 1000
    fight._apply_stun(target, seconds, extend_only=True)
    return True
