"""Independent000E player-controller VM; clock/lifetime hosting is reconstructed.

The actual definition has an empty, non-client-only script component. This host
does not alias its variables to a body or send frames. Manual Assassin control can
install a scoped static profile when the caller has supplied no filter provider.
"""

import math

from ow174.game.script import expr, graph, message_receiver, message_state, modifiers, runtime, scopes
from ow174.game.script.controller_filter import ControllerFilterInputs

DEFINITION = 0x040000000000000E
EMPTY_COMPONENT = {"$": "STUStatescriptComponent", "m_B634821A": None,
                   "m_2D9815BA": None, "m_clientOnly": 0}
# Only the presence inputs reached by the scoped manual filter and pin host.
MANUAL_PIN_MASKS = ((1 << 25) | (1 << 26) | (1 << 47), 1 << (97 - 64))
MANUAL_PIN_PROFILE = "static manual Assassin controller based on retained 104319 presence"


class PinEffectServices:
    """Explicit manual-sandbox policy for the controller's 1177 effect."""

    def __init__(self, component):
        self.registry = scopes.Registry()
        self.messages = message_state.Context(
            component, message_state.Host(notify=message_receiver.notify),
            policy=self.policy, frame=self.frame, priority=self.priority,
        )

    @staticmethod
    def policy(state):
        return message_state.Policy(False, False, False)

    @staticmethod
    def frame(state):
        return expr.i32(state.instance.component.frame)

    @staticmethod
    def priority(state, resource):
        if resource:
            state.instance.component.unsupported(state.instance, "pin effect message priority",
                                                 state.node.cls, state.node)
        return None

    def retire(self):
        self.messages.host.retire()
        self.registry.clear()


class ControllerWorld(runtime.World):
    def __init__(self, hero, mode):
        super().__init__(hero, mode)
        self.readers = []  # Explicit NPC member bindings, never implicit body aliases.
        self.filter_inputs = None
        self.pin_services = None
        self.linked_target_inputs = None
        self.numeric_modifiers = None

    def _pin_services(self, entity):
        services = self.pin_services
        if (services is not None and services.messages.component.entity == entity
                and not services.messages.retired):
            return services
        return None

    def subgraph_scopes(self, entity):
        services = self._pin_services(entity)
        return services.registry if services is not None else NotImplemented

    def voice_context(self, entity):
        return None if self._pin_services(entity) is not None else NotImplemented

    def message_state_context(self, entity, *, component=None):
        services = self._pin_services(entity)
        if services is not None and services.messages.component is component:
            return services.messages
        return NotImplemented

    def script_failed(self, component):
        services = self.pin_services
        if services is not None and services.messages.component is component:
            services.retire()

    def linked_target_sample(self, entity):
        inputs = self.filter_inputs
        if inputs is None or inputs.owner != entity or inputs.retired:
            return NotImplemented
        if not inputs.has_component(31):
            return None
        if self.linked_target_inputs is None:
            return NotImplemented
        return self.linked_target_inputs

    def property_modifiers(self, entity):
        inputs = self.filter_inputs
        if inputs is None or inputs.owner != entity or inputs.retired:
            return NotImplemented
        if not inputs.has_component(31):
            return None
        return self.numeric_modifiers if self.numeric_modifiers is not None else NotImplemented

    def native(self, cfg, instance):
        inputs = self.filter_inputs
        if (instance is not None and instance.graph.index == 0x1177
                and cfg.get("$") == "STU_9B7648C3"
                and cfg.get("m_entity") == {"$": "STU_B5A0CAF0", "m_EE729DCB": 0}
                and inputs is not None and inputs.owner == instance.component.entity
                and not inputs.retired and not inputs.has_component(51)):
            return 0.0
        return super().native(cfg, instance)


class ControllerVM:
    def __init__(self, entity, hero, mode, frame):
        record = graph.entity_body(DEFINITION)
        if (record is None or record.get("graphs") != [] or record.get("manager") is not None
                or record.get("weapons") != [] or record.get("script_component") != EMPTY_COMPONENT):
            raise ValueError("player controller requires the actual empty000E script definition")
        if type(entity) is not int or not 0 <= entity <= 0xFFFFFFFF or not expr.valid_entity(entity):
            raise ValueError("player controller requires a native entity identity")
        if type(frame) is not int or not 0 <= frame <= 0xFFFFFFFF:
            raise ValueError("player controller requires a native frame")
        from ow174.game import heroselect

        self.definition = DEFINITION
        self.retired = False
        self.reserved_wire_ids = heroselect.controller_wire_ids(heroselect.controller_of(mode))
        self.component = runtime.Component(entity, world=ControllerWorld(hero, mode), strict=True)
        # This VM and the existing owner encoder share one entity. Monotonic
        # allocation must start after the actual controller tree's namespace.
        self.component.next_id = max(self.reserved_wire_ids, default=0) + 1
        self.component.run_frame(frame)

    @property
    def filter_inputs(self):
        return self.component.world.filter_inputs

    def validate_namespace(self):
        if (type(self.component.instances) is not dict
                or any(type(item) is not int or item <= 0 for item in self.component.instances)
                or type(self.component.next_id) is not int):
            raise ValueError("invalid controller VM wire namespace storage")
        if any(item in self.reserved_wire_ids for item in self.component.instances):
            raise ValueError("controller VM instance overlaps the reserved controller wire namespace")
        if self.component.next_id <= max(self.reserved_wire_ids, default=0):
            raise ValueError("controller VM allocation enters the reserved controller wire namespace")

    def bind_filter_inputs(self, inputs):
        """Explicit provider binding, never an automatic historical profile."""
        if self.retired or self.component.fault is not None:
            raise ValueError("controller filter binding requires a live controller host")
        if inputs is not None:
            if (type(inputs) is not ControllerFilterInputs or inputs.owner != self.component.entity
                    or inputs.retired or inputs.bound_host not in (None, self)):
                raise ValueError("controller filter inputs must belong to this live owner")
            inputs.validate()
            if not inputs.has_component(47):
                raise ValueError("controller filter inputs require the live component47 VM")
        if inputs is self.filter_inputs:
            return
        if self.filter_inputs is not None:
            for instance in tuple(self.component.instances.values()):
                if instance.graph.index == 0x1177 and instance.auto_retire:
                    self.component.destroy(instance)
            self.filter_inputs.retire()
        self.component.world.linked_target_inputs = None
        self.component.world.numeric_modifiers = None
        self.component.world.filter_inputs = inputs
        if inputs is not None:
            inputs.bound_host = self

    def configure_manual_pin(self, replicated_bits):
        """Use the retained controller presence shape with the live record bits."""
        if self.retired or self.component.fault is not None:
            return
        inputs = self.filter_inputs
        if inputs is not None:
            if inputs.provenance == MANUAL_PIN_PROFILE and inputs.replicated_bits != replicated_bits:
                inputs.update(replicated_bits=replicated_bits)
            return
        self.bind_filter_inputs(ControllerFilterInputs(
            self.component.entity, MANUAL_PIN_MASKS, 0, replicated_bits, provenance=MANUAL_PIN_PROFILE))
        self.enable_pin_effects()

    def set_linked_target_sample(self, target, angle, heading_degrees, packed_degrees):
        """Supply reconstructed D1EEF0 and owner query results for this live controller."""
        inputs = self.filter_inputs
        if (self.retired or self.component.fault is not None or inputs is None
                or inputs.retired or not inputs.has_component(31)):
            raise ValueError("linked-target sample requires declared mover presence")
        if target is not None and (type(target) is not int or not 0x80000000 <= target <= 0xFFFFFFFF
                                   or not 0 < (target & 0x1FFFFFFF) < 0x1FFFFFFF):
            raise ValueError("invalid linked-target entity")
        if type(angle) not in (int, float) or not abs(angle) <= math.pi + 1e-6:
            raise ValueError("invalid linked-target sample angle")
        for value in (heading_degrees, packed_degrees):
            if value is not None and (type(value) not in (int, float) or not abs(value) <= 180.0001):
                raise ValueError("invalid linked-target owner angle")
        world = self.component.world
        if world.numeric_modifiers is None:
            # Explicit controller-frame clock for the reconstructed component 31 store.
            world.numeric_modifiers = modifiers.NumericModifiers(self.component.frame)
        world.linked_target_inputs = (target, expr.f32(angle), heading_degrees, packed_degrees)

    def enable_pin_effects(self):
        """Opt in to reconstructed scope, silent voice and local message services."""
        inputs = self.filter_inputs
        if (self.retired or self.component.fault is not None or inputs is None or inputs.retired
                or self.component.world.linked_target_sample(self.component.entity) is NotImplemented
                or inputs.has_component(51)):
            raise ValueError("pin effect services require a declared mover path and Health component absence")
        if self.component.world.pin_services is None:
            self.component.world.pin_services = PinEffectServices(self.component)

    def advance(self, frame, hero):
        if type(frame) is not int or not 0 <= frame <= 0xFFFFFFFF:
            raise ValueError("player controller requires a native frame")
        if self.retired:
            return False
        self.validate_namespace()
        self.component.world.hero = hero
        if frame <= self.component.frame:
            return False
        backend = self.component.world.numeric_modifiers
        if backend is not None:
            backend.source_frame = frame
        self.component.run_frame(frame)
        self.validate_namespace()
        return True

    def linked_target_angles(self):
        """Return the active pin limits for the reconstructed controller mover path."""
        from ow174.game.script.nodes import LinkedTarget

        if self.retired or self.component.fault is not None:
            return None
        sample = self.component.world.linked_target_sample(self.component.entity)
        if sample is None or sample is NotImplemented:
            return None
        states = [
            state for state in self.component.state_registries.get("STU_9935FBC8", ())
            if state.active and not state.ending and state.mover_angles is not None
            and state.instance.auto_retire and not state.instance.stopped
        ]
        if not states:
            return None
        yaw, pitch = states[-1].mover_angles[:2]
        return LinkedTarget.compose_angles(
            yaw, pitch,
            max(state.linked_cache[2] for state in states),
            max(state.linked_cache[3] for state in states),
            min(state.linked_cache[4] for state in states),
            min(state.linked_cache[5] for state in states),
        )

    def owner_effect_wires(self):
        """Wire state for the supported controller pin effect."""
        from ow174.game.script.nodes import LinkedTarget
        from ow174.game.statescript import Binding, EventWire, InstanceWire, StateWire, sendable

        if self.retired or self.component.fault is not None:
            return (), {}
        effects = tuple(item for item in self.component.instances.values()
                        if item.graph.index == 0x1177 and item.auto_retire and item.initialized
                        and not item.stopped)
        if not effects:
            return (), {}
        self.validate_namespace()
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
                    state.payload(linked_instances=effect_ids) if isinstance(state, LinkedTarget) else state.payload(),
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

    def retire(self):
        if self.retired:
            return
        self.retired = True
        for context in list(self.component.world.readers):
            context.set_target()
        self.component.owner_dead = True
        self.component.world.linked_target_inputs = None
        self.component.held.clear()
        self.component.shots.clear()
        for instance in list(self.component.instances.values()):
            self.component.destroy(instance)
        self.component.world.numeric_modifiers = None
        if self.filter_inputs is not None:
            self.filter_inputs.retire()
        services = self.component.world.pin_services
        if services is not None:
            services.retire()
