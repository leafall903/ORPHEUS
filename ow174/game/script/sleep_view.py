"""Bounded 224E view branch using the static server's unparented body pose."""

import math

from ow174.game.script import expr, graph, play, runtime
from ow174.game.script.nodes import LinkedTarget


class _ViewWorld(runtime.World):
    def __init__(self, body, yaw, pitch):
        super().__init__()
        self.body = body
        self.yaw = yaw
        self.pitch = pitch

    def native(self, cfg, instance):
        cls = cfg.get("$")
        if cls in ("STU_CC53F52B", "STU_7BE6D76D"):
            target = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.EVALUATION_FAILED)
            if expr.to_entity(target) == self.body:
                units = self.yaw if cls == "STU_CC53F52B" else self.pitch
                return expr.f32(units * (360.0 / 65536))
            return expr.EVALUATION_FAILED
        return super().native(cfg, instance)

    def linked_target_sample(self, entity):
        # The bounded branch uses the owning pose with no parent adjustment.
        return (None, 0.0, None, None) if entity == self.body else NotImplemented


def delay_ticks(tick):
    definition = graph.graph(0x224E)
    if definition is None:
        return None
    return math.ceil(definition.node(29).field("m_timeout.m_value") / tick)


def sample(body, yaw, pitch):
    definition = graph.graph(0x224E)
    if definition is None:
        return None
    component = runtime.Component(body, strict=True, world=_ViewWorld(body, yaw, pitch))
    instance = component.create(definition, init=False)
    instance.initialized = True
    try:
        # The combat clock schedules Wait 29, which leads to this output chain.
        instance.run_node(definition.node(30), None)
        component.run_now()
        state = instance.node_state(definition.node(34))
        return state.mover_angles if isinstance(state, LinkedTarget) else None
    except runtime.UnsupportedScriptError as error:
        expr.warn_once("sleep-view-resource", "%s", error)
        return None
    finally:
        component.destroy(instance)
