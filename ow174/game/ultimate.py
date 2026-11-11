"""Authored0B8B charge expressions, bridged to Roadhog's actual combat pools.

Adapted from the Soldier lane's shared expression bridge. Host event/tag/mode
eligibility and the fallback input-frame clock remain explicit reconstruction.
The graph owns amounts, multipliers, interval and cap; no native server claim.
"""

from ow174.game.script import expr, runtime

CHARGE_GRAPH = 0x0B8B
ROADHOG = 0x02E0000000000040


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


def damage_received_multiplier(script) -> float:
    if getattr(script, "hero", None) == ROADHOG:
        for instance in script.component.instances.values():
            if instance.graph.index != 0x01C7:
                continue
            # 01C7/111 is a client-only stack. Evaluate its authored value
            # for the server's victim-side damage credit without starting it.
            node = instance.graph.node(111)
            if node is not None and (node.field("m_out_Var") or {}).get("m_identifier") == 15586:
                value = instance.evaluate(node.field("m_value"))
                if type(value) in (int, float):
                    return value
            break
    return 1.0


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
