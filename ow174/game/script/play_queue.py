"""104319 component47 PlayScript queue, with an explicit resource provider.

64D2C0 submits and immediately calls64CD10. Ready Graph objects declare loaded
resource/namespace data; this does not read CASC or publish network descriptors.
"""

from dataclasses import dataclass

from ow174.game.script import graph, play, runtime

INSTANCE_LIMIT = 1500


@dataclass
class Load:
    phase: str  # pending, ready, failed; provider-owned readiness, never guessed.
    definition: graph.Graph | None = None


@dataclass
class Pending:
    request: play.Request
    load: Load
    instance_id: int = 0


class Queue:
    def __init__(self, component: runtime.Component, acquire):
        if not isinstance(component, runtime.Component) or not callable(acquire):
            raise ValueError('PlayScript queue needs its component and resource provider')
        component.ensure_valid()
        self.component, self.acquire = component, acquire
        self.pending: list[Pending] = []
        self.closed = False
        self._busy = False

    def _ensure(self):
        self.component.ensure_valid()
        if self.closed:
            raise ValueError('PlayScript queue lifecycle retired')

    def _matches(self, guid):
        if guid >> 48 != graph.GRAPH_TYPE:
            return []
        matches = []
        for instance in self.component.instances.values():
            self.component.consume_work('requested graph identity lookup')
            if instance.graph.index == guid & 0xFFFFFFFFFFFF:
                matches.append(instance)
        return matches

    def submit(self, request: play.Request):
        self._ensure()
        if (type(request) is not play.Request or type(request.policy) is not int
                or request.policy not in range(4) or type(request.wire_flag) is not int
                or not 0 <= request.wire_flag <= 255):
            raise ValueError('invalid PlayScript request policy or byte')
        if self._busy:
            raise ValueError('reentrant PlayScript queue mutation is not implemented')
        self._busy = True
        try:
            with self.component.operation():
                matches = self._matches(request.resource.guid)
                if matches and request.policy == 0:
                    return
                if request.policy == 3:
                    for instance in matches:
                        self.component.consume_work('replace requested graph')
                        self.component.destroy(instance)
                        self._ensure()
                load = self.acquire(request.resource.guid)
                self._ensure()
                if load is None:  # Explicit failed resource acquisition.
                    return
                if type(load) is not Load:
                    raise ValueError('PlayScript resource availability is unknown')
                self._check_load(load)
                if len(self.component.instances) <= INSTANCE_LIMIT:
                    self.pending.append(Pending(request, load))
                self._flush()
        finally:
            self._busy = False

    @staticmethod
    def _check_load(load):
        if load.phase not in ('pending', 'ready', 'failed'):
            raise ValueError('invalid PlayScript resource phase')
        if load.phase == 'ready' and not isinstance(load.definition, graph.Graph):
            raise ValueError('ready PlayScript resource has no loaded graph namespace')

    def flush(self):
        self._ensure()
        if self._busy:
            raise ValueError('reentrant PlayScript queue mutation is not implemented')
        self._busy = True
        try:
            with self.component.operation():
                self._flush()
        finally:
            self._busy = False

    def _flush(self):
        component = self.component
        if len(component.instances) > INSTANCE_LIMIT:
            self.pending.clear()
            return
        for index in range(len(self.pending)-1, -1, -1):
            self._ensure()
            component.consume_work('pending PlayScript request')
            pending = self.pending[index]
            request, load = pending.request, pending.load
            self._check_load(load)
            if request.policy == 2 and (matches := self._matches(request.resource.guid)):
                pending.instance_id = matches[0].id
            created = not pending.instance_id
            if created and load.phase != 'ready':
                if load.phase == 'failed':
                    self.pending.pop(index)
                continue
            active = None if created else component.instances.get(pending.instance_id)
            if not created and active is None:
                self.pending.pop(index)
                continue
            if load.phase != 'ready':
                raise ValueError('allocated PlayScript lost its declared ready resource')
            definition = load.definition
            if (request.resource.guid >> 48 != graph.GRAPH_TYPE
                    or definition.index != request.resource.guid & 0xFFFFFFFFFFFF):
                raise ValueError('PlayScript loaded resource identity mismatch')
            if created:
                active = component.create(definition, init=False)
                active.auto_retire = request.wire_flag == 0
                active.set_source_entity(request.context.instigator or component.entity)
                active.initialization_pending = True
                pending.instance_id = active.id
            elif active.graph is not definition:
                raise ValueError('PlayScript namespace replacement is not implemented')
            active.play_schema(request.resource.overrides, reorder_entries=created)
            self._ensure()
            if created:
                # The native new-allocation path prepares ordinary initialization
                # after schema writes and before the separately queued starts.
                component._initialize_pending()
                self._ensure()
            if component.instances.get(active.id) is not active or active.stopped:
                self.pending.pop(index)
                continue
            if not active.initialized:
                continue  # Owner's initial startup/namespace is still pending.
            active.start_entries(request.params, request.context.caller)
            self._ensure()
            self.pending.pop(index)

    def retire(self):
        self.closed = True
        self.pending.clear()
