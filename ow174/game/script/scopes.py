"""Explicit registration and calls for native auxiliary subgraph interfaces.

104319 BD02E168 interface callbacks 9FB880/9FB8D0 enter/exit its +E8 plug.
The owner of the native component decides when to call them. Registering a
state is separate from beginning it; this module invents no trigger policy.
"""


class Registry:
    def __init__(self):
        self.states = []

    def register(self, state):
        if state in self.states:
            raise ValueError("subgraph interface is already registered")
        self.states.append(state)

    def unregister(self, state):
        if state in self.states:
            self.states.remove(state)

    def clear(self):
        # Failure/destruction must not run more authored graph code.
        self.states.clear()

    def _check(self, state):
        instance = state.instance
        if (state not in self.states or instance.stopped or instance.teardown
                or instance.component.instances.get(instance.id) is not instance
                or instance.states.get(state.index) is not state):
            raise ValueError("subgraph interface does not belong to a live registered state")
        instance.component.ensure_valid()

    def enter(self, state):
        self._check(state)
        with state.instance.component.operation():
            state.scope_enter()

    def exit(self, state):
        self._check(state)
        with state.instance.component.operation():
            state.scope_exit()
