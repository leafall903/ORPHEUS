"""0B8B charge expressions, with explicitly reconstructed host inputs.

The graph owns amounts, live multipliers and cap. Controller lookup, combat
message eligibility and the passive clock's engine scheduling remain open.
"""

from ow174.game.script import expr, runtime


class ChargeInput(runtime.InstanceContext):
    def __init__(self, instance, identifier, amount):
        super().__init__(instance, None)
        self.identifier, self.amount = identifier, amount

    def var(self, scope, var):
        return self.amount if (scope, var) == (expr.INSTANCE, self.identifier) else super().var(scope, var)


def instance_of(script):
    return next((item for item in script.component.instances.values() if item.graph.index == 0x0B8B), None)


def combat_value(script, amount: float, kind: str) -> float:
    instance = instance_of(script)
    if instance is None:
        return expr.to_float(script.component.entity_var(342).value())
    position, identifier = {"damage": (23, 336), "healing": (32, 499)}[kind]
    return expr.to_float(expr.evaluate(instance.graph.node(position).field("m_value"),
                                     ChargeInput(instance, identifier, amount)))


def passive_points(script) -> None:
    """Host elapsed accepted-command time when the actual Wait is not running."""
    instance = instance_of(script)
    if instance is None:
        script.passive_charge_ms = 0
        return
    timer = instance.graph.node(3)
    state = instance.states.get(timer.state)
    if state is not None and state.active:
        script.passive_charge_ms = 0
        return  # the real graph already owns this loop; never double-credit
    interval = round(expr.to_float(instance.evaluate(timer.field("m_timeout"))) * 1000)
    if interval <= 0:
        raise ValueError("invalid authored passive charge interval")
    script.passive_charge_ms += script.frame_ms
    while script.passive_charge_ms >= interval:
        script.passive_charge_ms -= interval
        value = expr.to_float(instance.evaluate(instance.graph.node(4).field("m_value")))
        script.set_ult_charge(value)
