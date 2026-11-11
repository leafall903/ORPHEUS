"""Authored Enforcer movement modifier on a manually admitted controller branch."""

from ow174.game.script import graph, runtime
from ow174.game.script.driver import create_initial_graphs

BODY = 0x0400000000002279
CONTROLLER = 0x040000000000227A
CONTROL_GRAPH = 0x13EA
CONTROL_NODE = 62
MOVEMENT_GRAPH = 0x13EF
SPEED_GATE = 12299


def create(entity, frame):
    record = graph.entity_body(CONTROLLER)
    component = runtime.Component(entity)
    component.frame = frame
    component.now = frame * component.frame_ms
    for instance in create_initial_graphs(component, record):
        if instance.graph.index == CONTROL_GRAPH:
            overrides = tuple((key, variable.base) for key, variable in instance.vars.items())
            instance.play_schema(overrides, reorder_entries=False)
        instance.init()
    component.run_frame(frame)
    return component


def set_movement(bot, enabled):
    if bot.body != BODY or bot.dead or bot.body_script is None:
        return False
    controller = bot.body_script.component.world.enforcer_controller
    if controller is None or controller.owner_dead:
        return False
    instance = next((i for i in controller.instances.values() if i.graph.index == CONTROL_GRAPH), None)
    if instance is None:
        return False
    state = instance.node_state(instance.graph.node(CONTROL_NODE))
    if enabled and not state.active:
        state.request(runtime.BEGIN)
    elif not enabled and state.active:
        state.end(False)
    controller.run_now()
    return state.active == enabled


def retire(script):
    controller = getattr(script.component.world, "enforcer_controller", None)
    if controller is not None and not controller.owner_dead:
        controller.owner_dead = True
        for instance in list(controller.instances.values()):
            controller.destroy(instance)


def speed_scale(bot):
    if bot.body != BODY or bot.body_script is None:
        return 1.0
    for instance in bot.body_script.component.instances.values():
        if instance.graph.index == MOVEMENT_GRAPH:
            node = instance.graph.node(0)
            state = instance.states.get(node.state)
            if state is not None and state.active:
                row = node.field("m_BC5E91CF")[0]
                return state.evaluate(row["m_9B7A63EA"], watch=False)
    return 1.0
