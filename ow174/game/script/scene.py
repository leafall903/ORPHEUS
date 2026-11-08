"""Map identity and static entity queries for authored mission scripts.

This consumes the external map-world input format. Client-created placements
use the native client_id namespace (104319 RVA 1B483B6..3C8); server placements
must be explicitly supplied by the owner of the network creates. Definition
components and instance overrides stay separate. No component activation,
movement, network creation or mission scheduling is implied by these bindings.
"""

import copy
import math
import struct
import uuid
from dataclasses import dataclass

from ow174.game.script import (
    animation_alias,
    controller_heroes,
    data_flow,
    effect_routing,
    entity_control,
    entity_queries,
    entity_team,
    expr,
    game_message,
    graph,
    message_receive,
    message_state,
    model_look,
    modifiers,
    play,
    play_queue,
    runtime,
    scopes,
    voice_stimulus,
)
from ow174.game.script.effect_allocation import EffectList

CLIENT_PLACEABLE = 0xC0000000
SERVER_PLACEABLE = 0x80000000
ENTITY_INDEX_MASK = 0x1FFFFFFF
ZERO_UUID = uuid.UUID(int=0)


def _reference(value) -> uuid.UUID:
    if value is None:
        return ZERO_UUID
    if isinstance(value, uuid.UUID):
        return value
    if type(value) is not str:
        raise ValueError("entity reference must be a UUID")
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("invalid entity reference UUID") from None


def _position(value) -> expr.Vec3:
    if isinstance(value, expr.Vec3):
        value = tuple(value)
    if (type(value) not in (list, tuple) or len(value) != 3
            or any(type(part) not in (int, float) or not math.isfinite(part) for part in value)):
        raise ValueError("entity position must contain three finite numbers")
    result = expr.Vec3(*(expr.f32(part) for part in value))
    if not all(math.isfinite(part) for part in result):
        raise ValueError("entity position is outside float32 range")
    return result


@dataclass(frozen=True)
class Placement:
    index: int
    flat: int
    client_id: int
    reference: uuid.UUID
    definition: int
    identifier: int
    position: expr.Vec3
    source: dict


@dataclass(frozen=True)
class SceneEntity:
    id: int
    reference: uuid.UUID
    position: expr.Vec3 | None
    placement: Placement | None = None


class SceneWorld(runtime.World):
    """A concrete UUID lookup service, with explicit entity binding lifetimes.

    Missing references return the native no-output failure (CCDFF0), rather than
    a successful null or a fabricated identifier. Other native services retain
    World's strict diagnostics, including dynamic routing and undeclared relations.
    Static positions are exposed only for bound entities.
    """

    def __init__(self, hero: int = 0, mode: int = 0) -> None:
        super().__init__(hero, mode)
        self.map_guid: int | None = None
        self.entity_start = 0
        self.entities: dict[int, SceneEntity] = {}
        self.references: dict[uuid.UUID, int] = {}
        self.placements: dict[int, Placement] = {}
        self.definitions: dict[int, dict] = {}
        self.possessors: set[int] | None = None  # None means component availability is unknown.
        self.primary_possessors: dict[int, int] = {}
        self.modifier_components: dict[int, modifiers.NumericModifiers | None] = {}
        self.scope_components: dict[int, scopes.Registry | None] = {}
        self.effect_contexts: dict[int, effect_routing.Context | None] = {}
        self.effect_targets: dict[int, effect_routing.Target | None] = {}
        self.control_contexts: dict[int, entity_control.Context | None] = {}
        self.alias_components: dict[int, animation_alias.Aliases | None] = {}
        self.look_contexts: dict[int, model_look.Context] = {}
        self.data_flow_contexts: dict[int, data_flow.Context] = {}
        self.script_components: dict[int, runtime.Component | None] = {}
        self.play_queues: dict[int, play_queue.Queue] = {}
        self.voice_contexts: dict[int, voice_stimulus.Context | None] = {}
        self.query_contexts: dict[int, entity_queries.Context] = {}
        self.hero_contexts: dict[int, controller_heroes.Context] = {}
        self.team_contexts: dict[int, entity_team.Context] = {}
        self.message_contexts: dict[int, message_state.Context] = {}

    def bind(self, entity: int, reference=None, position=None,
             *, placement: Placement | None = None) -> SceneEntity:
        """Bind an entity that the world owner has created; never allocate its ID."""
        if (type(entity) is not int or not 0 <= entity <= 0xFFFFFFFF
                or not expr.valid_entity(entity)):
            raise ValueError("invalid scene entity identifier")
        reference = _reference(reference)
        if entity in self.entities:
            raise ValueError("scene entity identifier is already bound")
        if reference.int and reference in self.references:
            raise ValueError("scene entity UUID is already bound")
        resolved_position = _position(position) if position is not None else None
        record = SceneEntity(entity, reference, resolved_position, placement)
        self.entities[entity] = record
        if reference.int:
            self.references[reference] = entity
        return record

    def unbind(self, entity: int) -> SceneEntity | None:
        """Forget a query binding after its owner has retired the entity."""
        self.unbind_script_component(entity)
        self._retire_effect_bindings(entity)
        record = self.entities.pop(entity, None)
        self.modifier_components.pop(entity, None)
        self._retire_control_contexts(entity=entity)
        aliases = self.alias_components.pop(entity, None)
        if aliases is not None:
            aliases.retire()
        look = self.look_contexts.pop(entity, None)
        if look is not None:
            look.retire()
        mapping = self.data_flow_contexts.pop(entity, None)
        if mapping is not None:
            mapping.retire()
        scope = self.scope_components.pop(entity, None)
        if scope is not None:
            scope.clear()
        if record is not None and record.reference.int:
            del self.references[record.reference]
        if record is not None and self.possessors is not None:
            self.possessors.discard(entity)
            self.primary_possessors.pop(entity, None)
            self.primary_possessors = {
                target: owner for target, owner in self.primary_possessors.items() if owner != entity
            }
        return record

    def _bound_entity(self, entity: int) -> None:
        if type(entity) is not int or entity not in self.entities:
            raise ValueError("entity must already be bound")

    def bind_script_component(self, entity: int, component: runtime.Component | None) -> None:
        """Publish the actual component, or explicitly declare component absence."""
        self._bound_entity(entity)
        if component is not None:
            if (not isinstance(component, runtime.Component) or component.entity != entity
                    or component.world is not self):
                raise ValueError('script component belongs to another entity or world')
            component.ensure_valid()
        if entity in self.script_components and self.script_components[entity] is not component:
            raise ValueError('detach the existing script component before replacement')
        self.script_components[entity] = component

    def _notify_script_retired(self, component):
        for instance in component.instances.values():
            for state in instance.states.values():
                state.unwatch()
        for variable in component.vars.values():
            for state in list(variable.watchers):
                if state.instance.component is component:
                    continue
                variable.watchers.pop(state, None)
                state.watched.pop(variable, None)
                owner = state.instance.component
                if state.active and owner.fault is None and not state.flags_changed:
                    state.flags_changed = True
                    state.instance.post(runtime.Event(owner.now, 0, runtime.CHANGED, state=state))

    def unbind_script_component(self, entity: int, component: runtime.Component | None = None) -> None:
        """Forget this lifecycle's routing; never destroy a replacement component."""
        if component is not None and self.script_components.get(entity) is not component:
            raise ValueError('script component binding belongs to another lifecycle')
        previous = self.script_components.pop(entity, None)
        messages = self.message_contexts.pop(entity, None)
        if messages is not None:
            messages.retire()
        query = self.query_contexts.pop(entity, None)
        if query is not None:
            query.retire()
        heroes = self.hero_contexts.pop(entity, None)
        if heroes is not None:
            heroes.retire()
        teams = self.team_contexts.pop(entity, None)
        if teams is not None:
            teams.retire()
        queue = self.play_queues.pop(entity, None)
        if queue is not None:
            queue.retire()
        voice = self.voice_contexts.pop(entity, None)
        if voice is not None:
            voice.retire()
        if previous is not None:
            self._notify_script_retired(previous)

    def entity_var_slot(self, entity: int, var: int, *, create: bool = False):
        if entity not in self.entities:
            # Keep the existing immutable mode-asset fallback until a live mode
            # component has actually been installed by the scene owner.
            return NotImplemented if entity == runtime.GAME_MODE_ENTITY else None
        component = self.script_components.get(entity, NotImplemented)
        if component is None:
            return None
        if component is NotImplemented or component.fault is not None:
            return NotImplemented
        return component.entity_var(var, create=create)

    def script_component_present(self, entity: int):
        if entity not in self.entities:
            return False
        component = self.script_components.get(entity, NotImplemented)
        return NotImplemented if component is NotImplemented else component is not None

    def _game_message_component(self, instance, node):
        component = instance.component
        component.ensure_valid()
        if (self.script_components.get(component.entity) is not component
                or component.instances.get(instance.id) is not instance
                or instance.stopped or instance.teardown):
            component.unsupported(instance, 'scene message source lifecycle', node.cls, node)
        return component

    def game_message_owner(self, instance, node):
        return self._game_message_component(instance, node).entity

    def set_message_state_context(self, entity, context):
        component = self.script_components.get(entity)
        if (type(context) is not message_state.Context or context.component is not component
                or component is None or context.retired or context.host.manager.retired):
            raise ValueError('message-State context needs its live bound component')
        component.ensure_valid()
        previous = self.message_contexts.get(entity)
        if previous is not None and previous is not context and not previous.retired:
            raise ValueError('retire the existing message-State context before replacement')
        self.message_contexts[entity] = context

    def message_state_context(self, entity, *, component=None):
        if component is not None and self.script_components.get(entity) is not component:
            return NotImplemented
        return self.message_contexts.get(entity, NotImplemented)

    def validate_game_message_source(self, instance, node):
        self._game_message_component(instance, node)

    def game_message_source(self, instance, node):
        self._game_message_component(instance, node)
        source = instance.source_entity()
        self._game_message_component(instance, node)
        return source

    def submit_game_message(self, instance, node, request):
        source = self._game_message_component(instance, node)
        if type(request) is not game_message.Request or request.owner != source.entity:
            source.unsupported(instance, 'scene message request owner', node.cls, node)
        packet = message_receive.Packet(request.message, request.owner, request.instigator, request.params)
        for target in request.targets:
            self.send_game_message_packet(instance, node, target, packet)
        if request.self_target:
            self.send_game_message_packet(instance, node, source.entity, packet)

    def send_game_message_packet(self, instance, node, target, packet):
        source = self._game_message_component(instance, node)
        if target not in self.entities:
            return
        receiver = self.script_components.get(target, NotImplemented)
        if receiver is None:
            return
        if receiver is NotImplemented or receiver.fault is not None:
            source.unsupported(instance, 'scene message target component', node.cls, node)
        message_receive.deliver(receiver, packet)
        self._game_message_component(instance, node)
        if self.script_components.get(target) is not receiver or receiver.fault is not None:
            source.unsupported(instance, 'scene message target lifecycle', node.cls, node)

    def game_message_subject(self, instance, node, instigator):
        component = self._game_message_component(instance, node)
        context = self.query_contexts.get(component.entity, NotImplemented)
        if (type(context) is not entity_queries.Context or context.retired
                or context.owner != component.entity or context.namespace_present is not True
                or not callable(context.lookup)):
            component.unsupported(instance, 'scene message subject lookup', node.cls, node)
        component.consume_work('game message subject lookup')
        target = context.lookup(instigator)
        self._game_message_component(instance, node)
        if (self.query_contexts.get(component.entity) is not context or context.retired
                or context.owner != component.entity or context.namespace_present is not True):
            component.unsupported(instance, 'scene message subject lifecycle', node.cls, node)
        if target is None:
            return instigator
        if type(target) is not entity_queries.Target or target.entity != instigator:
            component.unsupported(instance, 'scene message subject lookup result', node.cls, node)
        if target.possession is None:
            return instigator
        if type(target.possession) is not entity_queries.Possession:
            component.unsupported(instance, 'scene message subject possession', node.cls, node)
        current = target.possession.current
        if type(current) is not int or not 0 <= current <= 0xFFFFFFFF:
            component.unsupported(instance, 'scene message subject possession', node.cls, node)
        return current if expr.valid_entity(current) else instigator

    def send_script_message(self, instance, node, target, message, params, sender):
        source = instance.component
        if self.script_components.get(source.entity) is not source:
            source.unsupported(instance, 'scene message source lifecycle', node.cls, node)
        source.ensure_valid()
        if target not in self.entities:
            return  # An actual missing entity is distinct from unknown component data.
        receiver = self.script_components.get(target, NotImplemented)
        if receiver is None:
            return
        if receiver is NotImplemented or receiver.fault is not None:
            source.unsupported(instance, 'scene message target component', node.cls, node)
        with receiver.operation():
            runtime.send_message(receiver, message, params, sender)
        if self.script_components.get(target) is not receiver or receiver.fault is not None:
            source.unsupported(instance, 'scene message target lifecycle', node.cls, node)

    def play_script_context(self, instance, node):
        """Entity-instance262E740's +60/+70 share its actual component VM."""
        component = instance.component
        if (self.script_components.get(component.entity) is not component
                or component.instances.get(instance.id) is not instance or instance.stopped):
            component.unsupported(instance, 'scene PlayScript source lifecycle', node.cls, node)
        component.ensure_valid()
        inherited = instance.source_entity()
        if (self.script_components.get(component.entity) is not component
                or component.instances.get(instance.id) is not instance or instance.stopped):
            component.unsupported(instance, 'scene PlayScript source lifecycle', node.cls, node)
        return play.InvocationContext(component.entity, component.entity, inherited)

    def set_play_queue(self, entity: int, queue: play_queue.Queue) -> None:
        self._bound_entity(entity)
        if (type(queue) is not play_queue.Queue or queue.component.world is not self
                or self.script_components.get(entity) is not queue.component or queue.closed):
            raise ValueError('PlayScript queue must belong to the actual live script component')
        if entity in self.play_queues and self.play_queues[entity] is not queue:
            raise ValueError('retire the existing PlayScript queue before replacement')
        self.play_queues[entity] = queue

    def set_voice_context(self, entity: int, context: voice_stimulus.Context | None) -> None:
        self._bound_entity(entity)
        if context is not None and (
                type(context) is not voice_stimulus.Context or context.component.world is not self
                or context.component.entity != entity
                or self.script_components.get(entity) is not context.component or context.retired):
            raise ValueError('voice context must belong to the actual live script component')
        if entity in self.voice_contexts and self.voice_contexts[entity] is not context:
            raise ValueError('retire the existing voice context before replacement')
        self.voice_contexts[entity] = context

    def voice_context(self, entity: int):
        if entity not in self.entities:
            return None
        return self.voice_contexts.get(entity, NotImplemented)

    def submit_script_request(self, instance, node, request):
        source = instance.component
        if (self.script_components.get(source.entity) is not source
                or source.instances.get(instance.id) is not instance or instance.stopped):
            source.unsupported(instance, 'scene PlayScript source lifecycle', node.cls, node)
        source.ensure_valid()
        for entity in request.targets:
            if entity not in self.entities:
                continue
            receiver = self.script_components.get(entity, NotImplemented)
            if receiver is None:
                continue
            if receiver is NotImplemented or receiver.fault is not None:
                source.unsupported(instance, 'scene PlayScript target component', node.cls, node)
            if (request.policy == 3 and receiver is source
                    and request.resource.guid == (graph.GRAPH_TYPE << 48) | instance.graph.index):
                source.unsupported(instance, 'scene PlayScript active self replacement', node.cls, node)
            queue = self.play_queues.get(entity)
            if queue is None:
                source.unsupported(instance, 'scene PlayScript queue', node.cls, node)
            try:
                queue.submit(request)
            except ValueError as error:
                source.unsupported(instance, 'scene PlayScript queue', str(error), node)
            if (self.script_components.get(entity) is not receiver
                    or self.play_queues.get(entity) is not queue or receiver.fault is not None):
                source.unsupported(instance, 'scene PlayScript target lifecycle', node.cls, node)
            if (self.script_components.get(source.entity) is not source
                    or source.instances.get(instance.id) is not instance or instance.stopped):
                source.unsupported(instance, 'scene PlayScript source lifecycle', node.cls, node)

    def set_property_modifiers(self, entity: int, component: modifiers.NumericModifiers | None) -> None:
        """Declare component 31 and its clock; UUID bindings do not imply it."""
        self._bound_entity(entity)
        if component is not None and type(component) is not modifiers.NumericModifiers:
            raise ValueError("expected numeric modifier component or known absence")
        self.modifier_components[entity] = component

    def property_modifiers(self, entity: int):
        if entity not in self.entities:
            return None
        return self.modifier_components.get(entity, NotImplemented)

    def set_subgraph_scopes(self, entity: int, registry: scopes.Registry | None) -> None:
        """Declare interface registration without guessing the host's dispatch policy."""
        self._bound_entity(entity)
        if registry is not None and type(registry) not in (scopes.Registry, scopes.ForwardHost):
            raise ValueError("expected subgraph scope registry or known no service")
        if (type(registry) is scopes.ForwardHost
                and (registry.component.entity != entity or registry.component.world is not self)):
            raise ValueError("scope host belongs to another world or entity")
        if entity in self.scope_components and self.scope_components[entity] is not registry:
            raise ValueError("retire the entity before replacing its subgraph scope service")
        self.scope_components[entity] = registry

    def subgraph_scopes(self, entity: int):
        if entity not in self.entities:
            return None
        return self.scope_components.get(entity, NotImplemented)

    def set_effect_context(self, entity: int, context: effect_routing.Context | None) -> None:
        self._bound_entity(entity)
        if context is not None:
            if type(context) is not effect_routing.Context:
                raise ValueError("expected native effect context or known engine absence")
            self._bound_entity(context.owner)
        if entity in self.effect_contexts and self.effect_contexts[entity] is not context:
            raise ValueError("retire the entity before replacing its effect context")
        self.effect_contexts[entity] = context

    def auxiliary_effect_context(self, entity: int):
        if entity not in self.entities:
            return None
        return self.effect_contexts.get(entity, NotImplemented)

    def set_effect_target(self, entity: int, target: effect_routing.Target | None) -> None:
        self._bound_entity(entity)
        if target is not None:
            if type(target) is not effect_routing.Target:
                raise ValueError("expected component43 descriptor or known absence")
            self._bound_entity(target.owner)
        if entity in self.effect_targets and self.effect_targets[entity] is not target:
            raise ValueError("retire the entity before replacing its effect target")
        self.effect_targets[entity] = target

    def auxiliary_effect_target(self, entity: int):
        # Missing entity evidence is not a known component-43 absence.
        return self.effect_targets.get(entity, NotImplemented)

    def set_entity_control_context(self, entity: int, context: entity_control.Context | None) -> None:
        """Declare real native services; UUID/component existence does not imply them."""
        self._bound_entity(entity)
        if context is not None:
            if type(context) is not entity_control.Context:
                raise ValueError("expected entity control context or known engine absence")
            target = context.component
            if target is not None and (not isinstance(target, runtime.Component) or target.world is not self):
                raise ValueError("entity control target belongs to another world")
            if target is not None:
                self._bound_entity(target.entity)
        if entity in self.control_contexts and self.control_contexts[entity] is not context:
            raise ValueError("retire the entity before replacing its entity control context")
        self.control_contexts[entity] = context

    def entity_control_context(self, entity: int):
        return self.control_contexts.get(entity, NotImplemented)

    def set_animation_aliases(self, entity: int, aliases: animation_alias.Aliases | None) -> None:
        self._bound_entity(entity)
        if aliases is not None and (
            type(aliases) is not animation_alias.Aliases or aliases.owner != entity or aliases.retired
        ):
            raise ValueError("expected live animation alias component for this entity")
        if entity in self.alias_components and self.alias_components[entity] is not aliases:
            raise ValueError("retire the entity before replacing its animation alias component")
        self.alias_components[entity] = aliases

    def animation_aliases(self, entity: int):
        return self.alias_components.get(entity, NotImplemented)

    def set_model_look_context(self, entity: int, context: model_look.Context) -> None:
        self._bound_entity(entity)
        if type(context) is not model_look.Context or context.owner != entity or context.retired:
            raise ValueError("expected live model-look context for this entity")
        if entity in self.look_contexts and self.look_contexts[entity] is not context:
            raise ValueError("retire the entity before replacing its model-look context")
        self.look_contexts[entity] = context

    def set_data_flow_context(self, entity: int, context: data_flow.Context) -> None:
        self._bound_entity(entity)
        if type(context) is not data_flow.Context or context.owner != entity or context.retired:
            raise ValueError('expected live data-flow context for this entity')
        if entity in self.data_flow_contexts and self.data_flow_contexts[entity] is not context:
            raise ValueError('retire the entity before replacing its data-flow context')
        self.data_flow_contexts[entity] = context

    def data_flow_context(self, entity: int):
        return self.data_flow_contexts.get(entity, NotImplemented)

    def model_look_context(self, entity: int):
        return self.look_contexts.get(entity, NotImplemented)

    def set_entity_query_context(self, entity: int, context: entity_queries.Context) -> None:
        self._bound_entity(entity)
        if type(context) is not entity_queries.Context or context.owner != entity or context.retired:
            raise ValueError('expected live entity-query context for this entity')
        if entity in self.query_contexts and self.query_contexts[entity] is not context:
            raise ValueError('detach the existing query context before replacement')
        self.query_contexts[entity] = context

    def entity_query_context(self, entity: int):
        return self.query_contexts.get(entity, NotImplemented)

    def set_controller_hero_context(self, entity: int, context: controller_heroes.Context) -> None:
        self._bound_entity(entity)
        if type(context) is not controller_heroes.Context or context.owner != entity or context.retired:
            raise ValueError('expected live controller-hero context for this entity')
        if entity in self.hero_contexts and self.hero_contexts[entity] is not context:
            raise ValueError('detach the existing controller-hero context before replacement')
        self.hero_contexts[entity] = context

    def controller_hero_context(self, entity: int, *, component: runtime.Component | None = None):
        if component is not None and self.script_components.get(entity) is not component:
            return NotImplemented
        return self.hero_contexts.get(entity, NotImplemented)

    def set_entity_team_context(self, entity: int, context: entity_team.Context) -> None:
        self._bound_entity(entity)
        if type(context) is not entity_team.Context or context.owner != entity or context.retired:
            raise ValueError('expected live entity-team context for this entity')
        if entity in self.team_contexts and self.team_contexts[entity] is not context:
            raise ValueError('detach the existing entity-team context before replacement')
        self.team_contexts[entity] = context

    def entity_team_context(self, entity: int, *, component: runtime.Component | None = None):
        if component is not None and self.script_components.get(entity) is not component:
            return NotImplemented
        return self.team_contexts.get(entity, NotImplemented)

    def _retire_control_contexts(self, *, entity=None, component=None):
        for source, context in list(self.control_contexts.items()):
            target = context.component if type(context) is entity_control.Context else None
            if (source == entity or (component is not None and source == component.entity)
                    or (isinstance(target, runtime.Component)
                        and (target.entity == entity or target is component))):
                self.control_contexts.pop(source, None)

    def _retire_effect_bindings(self, entity: int) -> None:
        sources = {entity} | {source for source, context in self.effect_contexts.items()
                              if context is not None and context.owner == entity}
        retiring = {target.entries for key, target in self.effect_targets.items()
                    if target is not None and (key == entity or target.owner == entity)}
        owners = retiring | {target.entries for target in self.effect_targets.values() if target is not None}
        owners.update(context.fallback for context in self.effect_contexts.values()
                      if context is not None and type(context.fallback) is EffectList)
        for owner in owners:
            for entry in owner.entries:
                effect, observer = entry.effect, entry.effect.observer
                source = observer.instance.component.entity if isinstance(observer, runtime.State) else None
                if owner in retiring or source in sources or effect.target == entity:
                    effect.observer = None
                    owner.retire(effect)
                    if isinstance(observer, runtime.State):
                        observer.on_effect_abandoned(effect)
        self.effect_contexts = {key: context for key, context in self.effect_contexts.items()
                                if key not in sources}
        self.effect_targets = {key: target for key, target in self.effect_targets.items()
                               if key != entity and (target is None or target.owner != entity)}

    def script_failed(self, component: runtime.Component) -> None:
        actual = self.script_components.get(component.entity, NotImplemented)
        if actual is not NotImplemented and actual is not component:
            return  # A stale source cannot retire its replacement's engine services.
        messages = self.message_contexts.pop(component.entity, None)
        if messages is not None:
            messages.retire()
        query = self.query_contexts.pop(component.entity, None)
        if query is not None:
            query.retire()
        heroes = self.hero_contexts.pop(component.entity, None)
        if heroes is not None:
            heroes.retire()
        teams = self.team_contexts.pop(component.entity, None)
        if teams is not None:
            teams.retire()
        if self.script_components.get(component.entity) is component:
            voice = self.voice_contexts.pop(component.entity, None)
            if voice is not None:
                voice.retire()
            queue = self.play_queues.pop(component.entity, None)
            if queue is not None:
                queue.retire()
            self._notify_script_retired(component)
        mapping = self.data_flow_contexts.pop(component.entity, None)
        if mapping is not None:
            mapping.retire()
        self._retire_control_contexts(component=component)
        look = self.look_contexts.pop(component.entity, None)
        if look is not None:
            look.retire()
        aliases = self.alias_components.pop(component.entity, None)
        if aliases is not None:
            aliases.retire()
        backend = self.modifier_components.get(component.entity)
        if backend is not None:
            backend.records.clear()
        scope = self.scope_components.get(component.entity)
        if scope is not None:
            scope.clear()

    def set_possessions(self, *, possessors=(), primary=()) -> None:
        """Install a complete known possession catalog, separately from UUIDs.

        possessors declares entities with native component 44. primary contains
        the reverse registry's (possessable, primary possessor) pairs. Neither
        identity nor mere script ownership can imply these relationships.
        """
        known = set()
        for entity in possessors:
            self._bound_entity(entity)
            known.add(entity)
        rows = primary.items() if isinstance(primary, dict) else primary
        relations = {}
        for entity, owner in rows:
            self._bound_entity(entity)
            self._bound_entity(owner)
            if owner not in known:
                raise ValueError("primary possessor must have component 44")
            if entity in relations:
                raise ValueError("duplicate primary possession relation")
            relations[entity] = owner
        self.possessors, self.primary_possessors = known, relations

    def set_possessor(self, entity: int, present: bool) -> None:
        """Reflect actual component 44 creation/removal, without allocating an entity."""
        self._bound_entity(entity)
        if self.possessors is None:
            raise ValueError("possession catalog has not been initialized")
        if type(present) is not bool:
            raise ValueError("possessor presence must be a bool")
        if present:
            self.possessors.add(entity)
        else:
            self.possessors.discard(entity)
            self.primary_possessors = {
                target: owner for target, owner in self.primary_possessors.items() if owner != entity
            }

    def set_primary_possessor(self, entity: int, owner: int | None) -> None:
        """Reflect a reverse-registry update; None removes the existing relation."""
        self._bound_entity(entity)
        if self.possessors is None:
            raise ValueError("possession catalog has not been initialized")
        if owner is None:
            self.primary_possessors.pop(entity, None)
            return
        self._bound_entity(owner)
        if owner not in self.possessors:
            raise ValueError("primary possessor must have component 44")
        self.primary_possessors[entity] = owner

    def primary_possessor(self, entity: int):
        """DA7F10: direct component 44 first, otherwise the reverse registry."""
        if self.possessors is None:
            return NotImplemented
        if entity not in self.entities:
            return 0
        if entity in self.possessors:
            return entity
        owner = self.primary_possessors.get(entity, 0)
        return owner if owner in self.entities and owner in self.possessors else 0

    def position(self, entity: int):
        record = self.entities.get(entity)
        return record.position if record is not None else None

    def entity_var(self, entity: int, var: int):
        slot = self.entity_var_slot(entity, var)
        if slot is not NotImplemented:
            return slot.value() if slot is not None else None
        if (entity == runtime.GAME_MODE_ENTITY and entity not in self.script_components
                and runtime.mode_variables(self.mode) is not None):
            return super().entity_var(entity, var)
        # Catalog bindings do not activate the entity's script component.
        return NotImplemented

    def native(self, cfg: dict, instance: runtime.Instance):
        if cfg.get("$") == "STU_DB742151" and self.possessors is not None:
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            return expr.Entity(self.primary_possessor(expr.to_entity(value)))
        if cfg.get("$") == "STUConfigVarEntityReference":
            try:
                reference = _reference(cfg.get("m_4A2AD74A"))
            except ValueError:
                instance.component.unsupported(instance, "entity reference UUID", cfg["$"])
            entity = self.references.get(reference)
            return expr.Entity(entity) if entity is not None else expr.EVALUATION_FAILED
        return super().native(cfg, instance)

    @classmethod
    def from_map(cls, data: dict, *, entity_start: int, kept_identifiers=(),
                 server_placeables=(), hero: int = 0, mode: int = 0) -> "SceneWorld":
        """Bind mode-selected client placements and already-created server ones.

        entity_start is the independently recovered count of preceding flat
        placeable types (lights/areas), not the ENTITY record index. A zero
        client_id never becomes C0000000 and is not automatically instantiated.
        The caller must supply its actual server-create flat indexes explicitly.
        """
        if type(data) is not dict or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("unknown map-world input format")
        map_guid = graph.guid(data.get("map"))
        if map_guid is None or map_guid >> 48 != 0x0800:
            raise ValueError("map-world input has no map GUID")
        if type(entity_start) is not int or not 0 <= entity_start < ENTITY_INDEX_MASK:
            raise ValueError("invalid first entity placeable index")
        kept = frozenset(kept_identifiers)
        if any(type(item) is not int or not 0 < item <= 0xFFFFFFFFFFFFFFFF for item in kept):
            raise ValueError("invalid kept placeable identifier")
        server_placeables = frozenset(server_placeables)
        if any(type(item) is not int or not 0 < item < ENTITY_INDEX_MASK for item in server_placeables):
            raise ValueError("invalid server placeable index")
        source = copy.deepcopy(data)
        entities = source.get("entities")
        if type(entities) is not list or entity_start + len(entities) > ENTITY_INDEX_MASK:
            raise ValueError("invalid map-world entity list")
        world = cls(hero, mode)
        world.map_guid, world.entity_start = map_guid, entity_start
        world.definitions = {
            graph.guid(key): value for key, value in (source.get("definitions") or {}).items()
        }
        seen_references = set()
        matched_server = set()
        for index, item in enumerate(entities):
            if type(item) is not dict or type(item.get("index")) is not int or item["index"] != index:
                raise ValueError("map-world entity indexes must be contiguous and ordered")
            try:
                header = bytes.fromhex(item["placeable_header"])
                metadata = bytes.fromhex(item["record_metadata"])
            except (ValueError, TypeError, KeyError):
                raise ValueError("invalid map-world entity identity fields") from None
            if len(header) != 20 or header[19] != 9 or len(metadata) != 44:
                raise ValueError("invalid ENTITY header or metadata")
            reference = uuid.UUID(bytes_le=header[:16])
            if reference.int and reference in seen_references:
                raise ValueError("duplicate map-world entity UUID")
            seen_references.add(reference)
            client_id = struct.unpack_from("<H", metadata, 36)[0]  # original body +104
            definition = graph.guid(item.get("definition"))
            if (definition is None or definition >> 48 != 0x0400
                    or not isinstance(world.definitions.get(definition), dict)):
                raise ValueError("map-world entity definition is missing")
            identifiers = item.get("identifiers")
            if (type(identifiers) is not list or len(identifiers) != 2
                    or any(graph.guid(value) is None for value in identifiers)):
                raise ValueError("invalid map-world placeable identifiers")
            identifier = graph.guid(identifiers[0])
            flat = entity_start + index
            placement = Placement(index, flat, client_id, reference, definition, identifier,
                                  _position(item.get("position")), item)
            world.placements[index] = placement
            selected = not identifier or identifier in kept
            if flat in server_placeables:
                if client_id or not selected:
                    raise ValueError("server create names a client-created or filtered placeable")
                matched_server.add(flat)
                world.bind(SERVER_PLACEABLE | flat, reference, placement.position, placement=placement)
            elif client_id and selected:
                world.bind(CLIENT_PLACEABLE | client_id, reference, placement.position, placement=placement)
        if matched_server != server_placeables:
            raise ValueError("server create names a placeable absent from the map")
        return world
