"""Explicit registration and calls for native auxiliary subgraph interfaces.

104319 BD02E168 interface callbacks 9FB880/9FB8D0 enter/exit its +E8 plug.
The owner selects manual registration or the demonstrated normal-forward host
history policy, and supplies the native frame and phase boundary explicitly.
"""

from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class Edge:
    frame: int
    active: bool


@dataclass
class History:
    records: list[Edge] = field(default_factory=list)
    initial: bool = False
    dispatched: bool = False


class ForwardHost(Registry):
    """104319 A087E0/A0B460 normal-forward history for one explicit component.

    The owner must set the native frame before transitions, then call drain at
    its chosen phase boundary. Selecting this service does not install a Match
    host, infer frame mapping, run +88/+120 phases or implement rewind journals.
    Only the two implemented interfaces with a null +18 callback are supported.
    """

    def __init__(self, component):
        from ow174.game.script.runtime import Component

        if not isinstance(component, Component):
            raise ValueError("scope host needs its explicit script component")
        component.ensure_valid()
        super().__init__()
        self.component = component
        self.frame = None
        self.history: dict[object, History] = {}
        self.dirty = False
        self._processing = set()
        self._drain_depth = 0

    def set_frame(self, frame: int) -> None:
        if type(frame) is not int or not -0x80000000 <= frame < 0x80000000:
            raise ValueError("native scope frame must be a signed 32-bit integer")
        if self._drain_depth or self._processing:
            raise ValueError("cannot change the scope frame during callbacks")
        if self.frame is not None and frame < self.frame:
            raise ValueError("scope history rewind is not implemented")
        self.frame = frame

    def register(self, state):
        if state.instance.component is not self.component:
            raise ValueError("scope host belongs to another script component")
        if state.node.cls not in ("STU_BD02E168", "STU_582E0D5F"):
            raise ValueError("scope host needs a verified null +18 interface callback")
        super().register(state)
        self.history[state] = History()

    def record(self, state, active: bool) -> None:
        """A087E0: snapshot before the first edge, append frame/payload, mark dirty."""
        self._check(state)
        if type(active) is not bool:
            raise ValueError("scope edge must contain a boolean payload")
        if self.frame is None:
            self.component.unsupported(state.instance, "subgraph scope frame", state.node.cls, state.node)
        history = self.history[state]
        if not history.records:
            history.initial = state.active
        history.records.append(Edge(self.frame, active))
        self.dirty = True

    def discard(self, state) -> None:
        """Local terminal cleanup: no authored callbacks during VM/entity teardown."""
        if state in self.states:
            index = self.states.index(state)
            last = self.states.pop()
            if index < len(self.states):
                self.states[index] = last
        self.history.pop(state, None)

    def unregister(self, state):
        """A0A140: process live pending history, then swap the last registration."""
        if state not in self.states:
            return
        instance = state.instance
        if instance.stopped or instance.teardown or self.component.fault is not None:
            self.discard(state)
            return
        if state in self._processing:
            self.component.unsupported(instance, "subgraph scope retirement during callback",
                                       state.node.cls, state.node)
        with self.component.operation():
            self._process(state)
            self.discard(state)

    def clear(self):
        super().clear()
        self.history.clear()
        self.dirty = False

    def _process(self, state):
        self._check(state)
        history = self.history[state]
        if not history.records:
            return
        self.component.consume_work("auxiliary scope callback")
        self._processing.add(state)
        try:
            # The verified interfaces' +18 callback is a nullsub. A0B460 then
            # reads the current count's parity and the first-edge snapshot.
            if len(history.records) & 1:
                if history.initial:
                    self.exit(state)
                else:
                    self.enter(state)
                history.dispatched = not history.initial
            self.component.ensure_valid()
            if self.history.get(state) is not history:
                return  # A callback retired the instance/entity.
            if not state.active:
                # Clear the VM's modeled memberships. Native +88 membership and
                # other engine collections are outside this runtime's boundary.
                state.unwatch()
                state.polled = state.flags_changed = state.flags_check = False
                if state in state.instance.ticking:
                    state.instance.ticking.remove(state)
            # Native cleanup consumes even edges appended by this callback.
            history.records.clear()
        finally:
            self._processing.discard(state)

    def drain(self) -> None:
        """A0B460: scan live registration order, repeating when callbacks mark dirty."""
        with self.component.operation():
            self._drain_depth += 1
            try:
                while self.dirty:
                    self.component.consume_work("auxiliary scope pass")
                    self.dirty = False
                    index = 0
                    while index < len(self.states):
                        self.component.consume_work("auxiliary scope scan")
                        state = self.states[index]
                        if state.instance.id:
                            self._process(state)
                        index += 1
            finally:
                self._drain_depth -= 1
