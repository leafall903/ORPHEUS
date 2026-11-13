"""Original numeric Sentry animation mappings on its actual script component.

Registration preserves original self/model/Handle target policies. It does not
invent a model Handle, controller, pose, F4 BOOL answer or animation phase.
"""
from ow174.game.script import data_flow
from ow174.game.script.nodes import DataFlowMapping


class SentryDataFlowMapping(DataFlowMapping):
    def live_context(self):
        component = self.instance.component
        context = component.world.data_flow_context(component.entity)
        return context if type(context) is data_flow.Context and not context.retired else None

    def on_begin(self):
        if self.live_context() is not None:
            data_flow.begin(self)

    def dependency_changed(self):
        if self.live_context() is not None:
            data_flow.refresh(self)

    def on_cleanup(self):
        if self.live_context() is not None:
            data_flow.cleanup(self)
