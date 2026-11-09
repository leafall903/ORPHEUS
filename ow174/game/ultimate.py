"""Shared0B8B charge expressions with explicit combat/clock host inputs.

Adapted from the Soldier lane's bfde211 helper. Expressions own amount,
multipliers and cap; native event/tag eligibility and host timing remain open.
"""

from ow174.game.script import expr, runtime

CHARGE_GRAPH = 0x0B8B


class ChargeInput(runtime.InstanceContext):
    def __init__(self, instance, identifier, amount):
        super().__init__(instance, None)
        self.identifier, self.amount = identifier, amount

    def var(self, scope, var):
        return self.amount if (scope, var) == (0, self.identifier) else super().var(scope, var)


def instance_of(script):
    return next((item for item in script.component.instances.values()
                 if item.graph.index == CHARGE_GRAPH), None)


def combat_points(script, amount: float, kind: str) -> float:
    instance = instance_of(script)
    if instance is None:
        return 0.0
    position, identifier = {"damage": (23, 336), "healing": (32, 499)}[kind]
    before = expr.to_float(script.component.entity_var(342).value())
    value = expr.evaluate(instance.graph.node(position).field("m_value"),
                          ChargeInput(instance, identifier, amount))
    return max(0.0, expr.to_float(value) - before)


def passive_points(script) -> None:
    instance = instance_of(script)
    if instance is None:
        script.passive_charge_ms = 0
        return
    timer = instance.graph.node(3)
    state = instance.states.get(timer.state)
    if state is not None and state.active:
        script.passive_charge_ms = 0
        return
    interval = round(expr.to_float(instance.evaluate(timer.field("m_timeout"))) * 1000)
    if interval <= 0:
        raise ValueError("invalid authored passive charge interval")
    script.passive_charge_ms += script.frame_ms
    while script.passive_charge_ms >= interval:
        script.passive_charge_ms -= interval
        before = expr.to_float(script.component.entity_var(342).value())
        value = instance.evaluate(instance.graph.node(4).field("m_value"))
        script._gain_ult_points(max(0.0, expr.to_float(value) - before))
