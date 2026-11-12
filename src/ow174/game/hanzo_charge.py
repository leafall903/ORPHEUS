"""Feed Hanzo's shared 0B8B charge expressions from the reconstructed match host."""

from ow174.game.script import expr, runtime


class DamageInput(runtime.InstanceContext):
    def __init__(self, instance, amount):
        super().__init__(instance, None)
        self.amount = amount

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 336):
            return self.amount
        return super().var(scope, var)


def instance_of(script):
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == 0x0B8B), None)


def damage(script, amount):
    instance = instance_of(script)
    if instance is not None:
        value = expr.evaluate(instance.graph.node(23).field("m_value"), DamageInput(instance, amount))
        script.set_ult_charge(expr.to_float(value))


def passive(script):
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
        raise ValueError("invalid 0B8B passive charge interval")
    script.passive_charge_ms += script.frame_ms
    while script.passive_charge_ms >= interval:
        script.passive_charge_ms -= interval
        value = instance.evaluate(instance.graph.node(4).field("m_value"))
        script.set_ult_charge(expr.to_float(value))
