"""0B8B charge expressions, with explicitly reconstructed host inputs.

The graph owns amounts, live multipliers and cap. Controller lookup, combat
message eligibility and the passive clock's engine scheduling remain open.
"""

from ow174.game.script import expr, graph, runtime
from ow174.game.content import PRACTICE_RANGE_MODE, PRACTICE_RANGE_VARIANT_MODE, SOLDIER


HEAL_CREDIT = 0x0240000000000044
PRACTICE_GRAPH = 0x13C0
MODE_RECEIVER = 0x01C7
PASSIVE_SUPPRESS = 11942


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


def field_healing_points(script, amount: float, recipient: int) -> float:
    """Run the authored healing entry with actual eligible HP and recipient."""
    instance = instance_of(script)
    if instance is None:
        return 0.0
    component = script.component
    charge = component.entity_var(342)
    before = expr.to_float(charge.value())
    # The Field host has no recovered native healing classification (parameter41).
    params = {0x3F: expr.Entity(recipient), 0x40: expr.f32(amount)}
    runtime.send_instance_message(instance, HEAL_CREDIT, params, component.entity)
    component.run_now()
    return max(0.0, expr.to_float(charge.value()) - before)


def practice_timer(world) -> bool:
    if world.match is None or world.hero != SOLDIER or world.mode not in (
        PRACTICE_RANGE_MODE, PRACTICE_RANGE_VARIANT_MODE,
    ):
        return False
    if world.mode == PRACTICE_RANGE_VARIANT_MODE:
        binding = graph.mode_scripts(world.mode)
        if binding is None or expr.guid_of(binding.get("graph")) != 0x05800000000013C0:
            return False
    return graph.graph(PRACTICE_GRAPH) is not None


def practice_passive_clock(script) -> bool:
    return script.ult_seconds is None and practice_timer(script.world)


def start_practice_timer(script, sender: int) -> None:
    """Deliver Practice Range 13C0/node3 to the authored body receiver."""
    if not practice_timer(script.world):
        return
    start = graph.graph(PRACTICE_GRAPH).node(3)
    message = expr.guid_of((start.field("m_gameMessage") or {}).get("m_gameMessage"))
    for instance in script.component.instances.values():
        if instance.graph.index == MODE_RECEIVER:
            runtime.send_instance_message(instance, message, {}, sender)
            script.component.run_now()
            return


def sync_practice_timer(script) -> None:
    """Use the authored suppressor for the explicit sandbox override and death."""
    if not practice_timer(script.world):
        return
    component = script.component
    value = True if script.ult_seconds is not None or component.owner_dead else None
    slot = component.entity_var(PASSIVE_SUPPRESS, create=False)
    if value is not (slot.base if slot is not None else None):
        component.set(PASSIVE_SUPPRESS, value)
