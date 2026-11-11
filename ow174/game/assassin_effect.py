"""Strict 1177 sandbox on a separate 000E controller namespace."""

from ow174.game.script import (
    expr,
    graph,
    message_receiver,
    message_state,
    modifiers,
    play,
    play_queue,
    runtime,
    scopes,
)

_EFFECT = 0x0580000000001177
_DEFINITION = 0x040000000000000E
_EMPTY_COMPONENT = {"$": "STUStatescriptComponent", "m_B634821A": None,
                    "m_2D9815BA": None, "m_clientOnly": 0}


class ControllerWorld(runtime.World):
    def __init__(self, entity, frame, *, health_absent, mover_sample):
        super().__init__()
        self.entity = entity
        self.health_absent = health_absent
        self.mover_sample = mover_sample
        self.numeric = modifiers.NumericModifiers(frame) if isinstance(mover_sample, tuple) else mover_sample
        self.scopes = scopes.Registry()
        self.component = runtime.Component(entity, world=self, strict=True)
        self.messages = message_state.Context(
            self.component, message_state.Host(notify=message_receiver.notify),
            policy=self.message_policy, frame=self.message_frame, priority=self.message_priority,
        )
        self.component.run_frame(frame)

    @staticmethod
    def message_policy(state):
        # Reconstructed local authority for the isolated controller.
        return message_state.Policy(False, False, False)

    @staticmethod
    def message_frame(state):
        return expr.i32(state.instance.component.frame)

    @staticmethod
    def message_priority(state, resource):
        if resource:
            state.instance.component.unsupported(state.instance, "pin effect message priority",
                                                 state.node.cls, state.node)
        return None

    def native(self, cfg, instance):
        if (self.health_absent is True and instance is not None and instance.component is self.component
                and instance.graph.index == 0x1177 and cfg.get("$") == "STU_9B7648C3"
                and cfg.get("m_entity") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0}):
            return 0.0
        return super().native(cfg, instance)

    def linked_target_sample(self, entity):
        return self.mover_sample if entity == self.entity else NotImplemented

    def property_modifiers(self, entity):
        return self.numeric if entity == self.entity else NotImplemented

    def subgraph_scopes(self, entity):
        return self.scopes if entity == self.entity else NotImplemented

    def voice_context(self, entity):
        return None if entity == self.entity else NotImplemented

    def message_state_context(self, entity, *, component=None):
        if entity == self.entity and component is self.component and not self.messages.retired:
            return self.messages
        return NotImplemented

    def script_failed(self, component):
        self.messages.host.retire()
        self.scopes.clear()
        if isinstance(self.numeric, modifiers.NumericModifiers):
            self.numeric.records.clear()


class VictimEffect:
    """Authoritative effect storage, with declared component presence and no body alias."""

    def __init__(self, entity, frame, *, health_absent, mover_sample):
        record = graph.entity_body(_DEFINITION)
        if (record is None or record.get("graphs") != [] or record.get("manager") is not None
                or record.get("weapons") != [] or record.get("script_component") != _EMPTY_COMPONENT):
            raise ValueError("Assassin victim effect requires the empty 000E controller definition")
        if not expr.valid_entity(entity):
            raise ValueError("Assassin victim effect requires a live controller identity")
        self.component = ControllerWorld(
            entity, frame, health_absent=health_absent, mover_sample=mover_sample,
        ).component

    def submit(self, attack, node):
        if attack.graph.index != 0x1157 or node is not attack.graph.node(45):
            return False
        source = attack.component.entity
        resource = play.resolve_resource(attack, node)
        if resource is None or resource.guid != _EFFECT:
            return False
        request = play.resolve_request(
            attack, node, resource, context_provider=lambda: play.InvocationContext(source, source, source),
        )
        if (request is None or request.targets != (self.component.entity,)
                or (request.policy, request.wire_flag) != (0, 0)):
            return False
        definition = graph.graph(_EFFECT & 0xFFFFFFFFFFFF)
        if definition is None:
            return False
        queue = play_queue.Queue(self.component, lambda guid: play_queue.Load("ready", definition))
        queue.submit(request)
        return True

    def owner_effect_wires(self):
        """Wire state for the supported controller pin effect."""
        from ow174.game.script.nodes import LinkedTarget
        from ow174.game.statescript import Binding, EventWire, InstanceWire, StateWire, sendable

        if self.component.fault is not None:
            return (), {}
        effects = tuple(item for item in self.component.instances.values()
                        if item.graph.index == 0x1177 and item.auto_retire and item.initialized
                        and not item.stopped)
        if not effects:
            return (), {}
        effect_ids = {effect.id for effect in effects}

        def wire_var(var):
            return var.value(), tuple(
                Binding(link.state.instance.id, link.state.index, link.slot, link.priority,
                        link.above, link.dynamic, link.frame_cached)
                for link in var.links if link.stack and link.state.active and link.state.networked
            )

        dynamic = []
        for effect in effects:
            nodes = effect.graph.owner_states()
            states = {
                node.state: StateWire(
                    node.cls, True,
                    state.payload(linked_instances=effect_ids)
                    if isinstance(state, LinkedTarget) else state.payload(),
                )
                for node in nodes if (state := effect.states.get(node.state)) is not None and state.active
            }
            events = [
                EventWire(event.time, event.state.index, event.kind == runtime.STATE_OP, event.param)
                for event in effect.timers() if sendable(event.state.node.cls, True)
            ]
            vars_ = {entry.var: wire_var(effect.vars[entry.var]) for entry in effect.graph.sync_vars
                     if entry.scope == 0 and entry.var in effect.vars}
            dynamic.append(InstanceWire(effect.id, effect.graph, vars=vars_, states=states, events=events))
        flag = self.component.entity_var(14695, create=False)
        return tuple(dynamic), {14695: wire_var(flag)} if flag is not None else {}

    def advance(self, frame):
        numeric = self.component.world.numeric
        if isinstance(numeric, modifiers.NumericModifiers):
            numeric.source_frame = frame
        self.component.run_frame(frame)

    def retire(self, source):
        for instance in tuple(self.component.instances.values()):
            if (instance.graph.index == 0x1177 and instance.auto_retire
                    and instance.cached_source_entity == source):
                self.component.destroy(instance)
