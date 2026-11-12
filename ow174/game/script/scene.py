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
from collections.abc import Callable
from dataclasses import dataclass
from types import NotImplementedType

from ow174.game.content import (
    JUNKENSTEIN_MODES,
    TUTORIAL_MODE,
    junkenstein_card_difficulty,
    junkenstein_card_mode_values,
)
from ow174.game.script import (
    animation_alias,
    b61,
    component45_spatial,
    component86,
    controller_heroes,
    controller_queries,
    data_flow,
    distance_constraints,
    effect_routing,
    entity_control,
    entity_filter,
    entity_queries,
    entity_team,
    expr,
    game_message,
    graph,
    health_queries,
    message_receive,
    message_state,
    model_look,
    modifiers,
    npc_queries,
    play,
    play_queue,
    possession_requests,
    runtime,
    scopes,
    voice_stimulus,
)
from ow174.game.script.effect_allocation import EffectList

CLIENT_PLACEABLE = 0xC0000000
SERVER_PLACEABLE = 0x80000000
ENTITY_INDEX_MASK = 0x1FFFFFFF
ZERO_UUID = uuid.UUID(int=0)
JUNKENSTEIN_WEAPON_DAMAGE = {
    # Controller 1958 uses profiles 0008, 0007, 0066 and 0009.
    (0x0019, 0x0400000000000022, 0x0400000000001958): {
        0x0D80000000001961: (1.75, 3.5, 5.25, 7.0),
    },
    # Controller 172E uses profiles 0039, 003B, 0064 and 003A.
    (0x006D, 0x04000000000000E4, 0x040000000000172E): {
        0x0D80000000001961: (5.0, 10.0, 15.0, 20.0),
    },
    # Controller 1959 uses profiles 000F, 000E, 0067 and 0010.
    (0x01C6, 0x040000000000028F, 0x0400000000001959): {
        0x0D80000000001961: (expr.f32(1.8000001), 4.5, 6.75, 9.0),
        0x0D80000000001962: (expr.f32(1.8000001), 4.5, 6.75, 9.0),
        0x0D80000000001C98: (10.0, 25.0, 37.5, 50.0),
    },
    (0x01C8, 0x040000000000028F, 0x0400000000001959): {
        0x0D80000000001C96: (6.0, 15.0, 22.5, 30.0),
    },
    # Controller 16FB uses 003C on Easy and 003E on the other three tiers.
    (0x022B, 0x0400000000000380, 0x04000000000016FB): {
        0x0D80000000001961: (15.0, 30.0, 30.0, 30.0),
        0x0D80000000001962: (10.0, 20.0, 20.0, 20.0),
    },
    # Controller 171D uses profiles 0028, 0026, 0026 and 0027.
    (0x0CA1, 0x040000000000171C, 0x040000000000171D): {
        0x0D80000000001961: (30.0, 60.0, 60.0, 90.0),
    },
    # Controller 1DD3 uses profiles 005C, 005A, 0065 and 005B.
    (0x157C, 0x04000000000026F0, 0x0400000000001DD3): {
        0x0D80000000001961: (expr.f32(3.2), 8.0, 12.0, 16.0),
        0x0D80000000001962: (16.0, 40.0, 60.0, 80.0),
    },
}
JUNKENSTEIN_DIFFICULTY_INDEX = {
    0x0D8000000000195E: 0, 0x0D8000000000195F: 1, 0x0D800000000026DF: 2, 0x0D80000000001960: 3,
}


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
    definition: int | None = None
    target_tags: frozenset[int] | None = None
    health: Callable[[], float] | None = None
    health_pool: Callable[[int], float] | None = None
    hero: int | None = None
    pool_capacity: Callable[[int], float] | None = None
    velocity: Callable[[], tuple[float, float, float]] | None = None
    position_reader: Callable[[], tuple[float, float, float]] | None = None
    self_damage: Callable[[float], None] | None = None
    pet_component_absent: bool = False
    normal_health: Callable[[bool], float] | None = None
    component92_present: bool | None = None
    movement_reader: Callable[[int], object] | None = None
    scale_reader: Callable[[], float | NotImplementedType] | None = None
    on_fire_reader: Callable[[], bool | None] | None = None  # A reader returns None if GMP stats are absent.
    same_team_contact: Callable[[bool], None] | None = None
    maximum_health: Callable[[], float] | None = None
    rotation_reader: Callable[[], tuple[float, float, float, float]] | None = None
    facing_writer: Callable[[expr.Vec3, int], None] | None = None
    aim_writer: Callable[[int, int, int], None] | None = None


class SceneWorld(runtime.World):
    """A concrete UUID lookup service, with explicit entity binding lifetimes.

    Missing references return the native no-output failure (CCDFF0), rather than
    a successful null or a fabricated identifier. Other native services retain
    World's strict diagnostics, including dynamic routing and undeclared relations.
    Static positions are exposed only for bound entities.
    """

    def __init__(self, hero: int = 0, mode: int = 0, *, entity_creator=None, hero_creator=None, native_state_misses=()) -> None:
        super().__init__(hero, mode)
        if entity_creator is not None and not callable(entity_creator):
            raise ValueError("scene entity creator must be callable")
        self.entity_creator = entity_creator
        if hero_creator is not None and not callable(hero_creator):
            raise ValueError("scene hero creator must be callable")
        self.hero_creator = hero_creator
        misses = tuple(native_state_misses)
        if any(type(cls) is not str or not cls.startswith(("STU_", "STUStatescriptState"))
               for cls in misses):
            raise ValueError("native state factory misses must be STU class names")
        self._native_state_misses = frozenset(misses)
        self.map_guid: int | None = None
        self.entity_start = 0
        self.entities: dict[int, SceneEntity] = {}
        self.references: dict[uuid.UUID, int] = {}
        self.placements: dict[int, Placement] = {}
        self.definitions: dict[int, dict] = {}
        self.possessors: set[int] | None = None  # None means component availability is unknown.
        self.primary_possessors: dict[int, int] = {}
        self.current_possessions: dict[int, int] = {}
        self.linked_controllers: dict[int, int | None] = {}
        self._owned_deployables: dict[int, frozenset[int]] = {}
        self.component44_body_ids: dict[int, int] = {}
        self.ai_difficulties: dict[int, expr.Asset] = {}
        self.b61_registries: dict[int, b61.Registry] = {}
        self.component86_registries: dict[int, component86.Registry | None] = {}
        self.health_state_counters: dict[int, health_queries.StateCounter | None] = {}
        self.health_pool_amounts: dict[int, tuple[tuple[float, float, float], float | None]] = {}
        self.modifier_components: dict[int, modifiers.NumericModifiers | None] = {}
        self.scope_components: dict[int, scopes.Registry | None] = {}
        self.effect_contexts: dict[int, effect_routing.Context | None] = {}
        self.effect_targets: dict[int, effect_routing.Target | None] = {}
        self.control_contexts: dict[int, entity_control.Context | None] = {}
        self.alias_components: dict[int, animation_alias.Aliases | None] = {}
        self.look_contexts: dict[int, model_look.Context] = {}
        self.data_flow_contexts: dict[int, data_flow.Context] = {}
        self.script_components: dict[int, runtime.Component | None] = {}
        self.target_tag_grants: dict[int, dict[runtime.State, int]] = {}
        self.combat_damage_filters: dict[int, dict[runtime.State, tuple[float, frozenset[int]]]] = {}
        self.display_name_overrides: dict[int, dict[runtime.State, int]] = {}
        self.play_queues: dict[int, play_queue.Queue] = {}
        self.voice_contexts: dict[int, voice_stimulus.Context | None] = {}
        self.voice_sets: dict[int, int] = {}
        self.voice_category_callback = NotImplemented
        self.voice_set_overrides: dict[int, tuple[runtime.State, int]] = {}
        self.participant_roster = None  # Owner-supplied (entity, filter bits, participant flags) rows.
        self.player_participant_roster = None
        self.enemy_participant_roster = None
        self.junkenstein_voice_recipients = None
        self.session_entity_roster = None  # Ordered entity IDs from the host's resolved session roster.
        self.array_filter_inputs = None  # Entity ID -> (component 97 present, component 25 bits or None).
        self.spawn_search = None
        self.wave_health = None
        self.contact_effect = None
        self.shield_recharge = None
        self.witch_staff_heal = None
        self.witch_staff_targets = None
        self.junkenstein_body_name = None
        self.explosion_damage = None
        self.roadhog_hook_cast = None
        self.symmetra_beam_cast = None
        self.symmetra_beam_hit = None
        self.ghost_targets = None
        self.ghost_contact_damage = None
        self.update_vengeful_ghost = None
        self.roadhog_hook_targets = None
        self.roadhog_hook_hit = None
        self.roadhog_hook_target = None
        self.objective_route = None
        self.effect_entity_action = None
        self.corpse_retirements: dict[int, tuple[SceneEntity, SceneEntity, runtime.Component, bool]] = {}
        self.ai_control_records: dict[int, dict[runtime.State, object]] = {}
        self.ai_facing_records: dict[int, dict[runtime.State, object]] = {}
        self.ai_control_settings: dict[int, tuple[float, bool]] = {}
        self.ai_weapon_records: dict[int, dict[runtime.State, object]] = {}
        self.ai_weapon_slots: dict[int, dict[int, frozenset[int]]] = {}
        self.ai_behavior_requests: dict[int, dict[runtime.State, object]] = {}
        self._ai_linked_targets: dict[tuple[int, int], SceneEntity] = {}
        self._ai_link_revision = runtime.Var(('scene', 'AI linked targets'))
        self._possession_revisions: dict[int, runtime.Var] = {}
        self._array_filter_revisions: dict[int, runtime.Var] = {}
        self.script_team_setter = None
        self.mission_message_route = None
        self.team_member_count = None
        self.mode_score_thresholds = None  # Extracted mode GUID -> (float targets, difficulty limits).
        self.engine_settings: dict[int, bool | int | float] | None = None
        self.global_data_flow: dict[int, tuple[runtime.State, bool]] = {}
        self.possession_requests: dict[int, possession_requests.Registry] = {}
        self.distance_constraints: dict[int, distance_constraints.Registry] = {}
        self.npc_query_contexts: dict[int, npc_queries.Context] = {}
        self.query_contexts: dict[int, entity_queries.Context] = {}
        self.controller_records: dict[int, controller_queries.Record] = {}
        self.absent_spatial_binding_owners: set[int] = set()
        self.component45_spatial_contexts: dict[int, component45_spatial.Context] = {}
        self.component76_records: dict[int, entity_queries.Component76Keys | None] = {}
        self.indexed_targets: dict[int, dict[tuple[int, int], int] | None] = {}
        self.target_record_scalars: dict[int, dict[int, float] | None] = {}
        self.entity_filter_records: dict[int, entity_filter.Record] = {}
        self._target_tag_candidates: dict[int, frozenset[int]] | None = None
        self.target_tag_members: dict[int, tuple[int, ...]] = {}
        self.entity_directions: dict[int, tuple[expr.Vec3 | None, expr.Vec3 | None]] | None = None
        self.model_gate_consumers = {}
        self.hero_contexts: dict[int, controller_heroes.Context] = {}
        self.team_contexts: dict[int, entity_team.Context] = {}
        self.message_contexts: dict[int, message_state.Context] = {}

    def bind(self, entity: int, reference=None, position=None,
             *, placement: Placement | None = None, definition: int | None = None,
             target_tags=None, health: Callable[[], float] | None = None,
             health_pool: Callable[[int], float] | None = None, hero: int | None = None,
             pool_capacity: Callable[[int], float] | None = None,
             velocity: Callable[[], tuple[float, float, float]] | None = None,
             position_reader: Callable[[], tuple[float, float, float]] | None = None,
             self_damage: Callable[[float], None] | None = None,
             pet_component_absent: bool = False,
             normal_health: Callable[[bool], float] | None = None,
             component92_present: bool | None = None,
             movement_reader: Callable[[int], object] | None = None,
             scale_reader: Callable[[], float | NotImplementedType] | None = None,
             on_fire_reader: Callable[[], bool | None] | None = None,
             same_team_contact: Callable[[bool], None] | None = None,
             maximum_health: Callable[[], float] | None = None,
             rotation_reader: Callable[[], tuple[float, float, float, float]] | None = None,
             facing_writer: Callable[[expr.Vec3, int], None] | None = None,
             aim_writer: Callable[[int, int, int], None] | None = None) -> SceneEntity:
        """Bind an entity that the world owner has created; never allocate its ID."""
        if (type(entity) is not int or not 0 <= entity <= 0xFFFFFFFF
                or not expr.valid_entity(entity)):
            raise ValueError("invalid scene entity identifier")
        reference = _reference(reference)
        if entity in self.entities:
            raise ValueError("scene entity identifier is already bound")
        if reference.int and reference in self.references:
            raise ValueError("scene entity UUID is already bound")
        if placement is not None:
            if definition is not None and definition != placement.definition:
                raise ValueError("scene entity definition disagrees with its placement")
            definition = placement.definition
        if definition is not None and (type(definition) is not int or not (
                definition == 0 or definition >> 48 == 0x0400 and definition & 0xFFFFFFFFFFFF)):
            raise ValueError("expected an entity-definition GUID or explicit absence")
        resolved_position = _position(position) if position is not None else None
        tags = None
        if target_tags is not None:
            values = tuple(target_tags)
            if any(type(tag) is not int or tag >> 48 != 0x0D60 or not tag & 0xFFFFFFFFFFFF
                   for tag in values):
                raise ValueError("scene target tags must be nonzero .06C assets")
            tags = frozenset(values)
        readers = (health, health_pool, pool_capacity, normal_health, maximum_health)
        if any(reader is not None and not callable(reader) for reader in readers):
            raise ValueError("scene health must read the current body pools")
        if self_damage is not None and (health is None or not callable(self_damage)):
            raise ValueError("scene damage requires a live body health binding")
        if velocity is not None and not callable(velocity):
            raise ValueError("scene velocity must read the current body velocity")
        if position_reader is not None and (position is not None or not callable(position_reader)):
            raise ValueError("scene position requires either coordinates or a live reader")
        if rotation_reader is not None and not callable(rotation_reader):
            raise ValueError("scene rotation must read the current entity transform")
        if facing_writer is not None and not callable(facing_writer):
            raise ValueError("scene facing must use a live body writer")
        if aim_writer is not None and not callable(aim_writer):
            raise ValueError("scene aim must use a live body writer")
        if hero is not None and (type(hero) is not int or (hero != 0 and hero >> 48 != 0x02E0)):
            raise ValueError("scene hero must be a .075 asset or a declared null resource")
        if type(pet_component_absent) is not bool:
            raise ValueError("scene pet component absence must be a bool")
        if component92_present is not None and type(component92_present) is not bool:
            raise ValueError("scene component 92 presence must be a bool")
        if movement_reader is not None and not callable(movement_reader):
            raise ValueError("scene movement must read the current body snapshot")
        if scale_reader is not None and not callable(scale_reader):
            raise ValueError("scene scale must read the current entity scale")
        if on_fire_reader is not None and not callable(on_fire_reader):
            raise ValueError("scene On Fire state must use a live stats reader")
        if same_team_contact is not None and not callable(same_team_contact):
            raise ValueError("scene team contact requires a mover callback")
        record = SceneEntity(
            entity, reference, resolved_position, placement, definition, tags, health, health_pool,
            hero, pool_capacity, velocity, position_reader, self_damage, pet_component_absent, normal_health,
            component92_present, movement_reader, scale_reader, on_fire_reader, same_team_contact,
            maximum_health, rotation_reader, facing_writer, aim_writer)
        self.entities[entity] = record
        if reference.int:
            self.references[reference] = entity
        return record

    def entity_component92_present(self, entity: int) -> bool | None:
        target = self.entities.get(entity)
        return target.component92_present if target is not None else None

    def display_name_override(self, entity: int) -> int | None:
        names = self.display_name_overrides.get(entity)
        return names[next(reversed(names))] if names else None

    def register_display_name_override(self, state, entity: int, name: int):
        component = state.instance.component
        if (self.script_components.get(component.entity) is not component
                or self.entity_component92_present(entity) is not True):
            return NotImplemented
        names = self.display_name_overrides.setdefault(entity, {})
        names[state] = name

        def remove():
            names.pop(state, None)
            if not names and self.display_name_overrides.get(entity) is names:
                del self.display_name_overrides[entity]

        return remove

    def unbind(self, entity: int) -> SceneEntity | None:
        """Forget a query binding after its owner has retired the entity."""
        self.unbind_script_component(entity)
        self._retire_effect_bindings(entity)
        record = self.entities.pop(entity, None)
        if self._target_tag_candidates is not None:
            self._target_tag_candidates.pop(entity, None)
        self.notify_entity_filter_change(entity, retired=True)
        self._owned_deployables.pop(entity, None)
        for owner, deployables in self._owned_deployables.items():
            if entity in deployables:
                self._owned_deployables[owner] = deployables - {entity}
        self.display_name_overrides.pop(entity, None)
        self.corpse_retirements.pop(entity, None)
        removed_links = [key for key, target in self._ai_linked_targets.items()
                         if key[0] == entity or target is record]
        for key in removed_links:
            del self._ai_linked_targets[key]
        if removed_links:
            self._notify_ai_link_change()
        self.ai_control_records.pop(entity, None)
        self.ai_facing_records.pop(entity, None)
        self.ai_control_settings.pop(entity, None)
        self.ai_weapon_records.pop(entity, None)
        self.ai_weapon_slots.pop(entity, None)
        self.ai_behavior_requests.pop(entity, None)
        requests = self.possession_requests.pop(entity, None)
        if requests is not None:
            requests.clear()
        constraints = self.distance_constraints.pop(entity, None)
        if constraints is not None:
            constraints.clear()
        if record is not None:
            self.linked_controllers.pop(entity, None)
            for body, controller in self.linked_controllers.items():
                if controller == entity:
                    self.linked_controllers[body] = None
        self.modifier_components.pop(entity, None)
        self.voice_sets.pop(entity, None)
        self.ai_difficulties.pop(entity, None)
        self.health_state_counters.pop(entity, None)
        self.health_pool_amounts.pop(entity, None)
        self.controller_records.pop(entity, None)
        self.absent_spatial_binding_owners.discard(entity)
        self.component45_spatial_contexts.pop(entity, None)
        self.component76_records.pop(entity, None)
        self.indexed_targets.pop(entity, None)
        self.target_record_scalars.pop(entity, None)
        self.entity_filter_records.pop(entity, None)
        if self.entity_directions is not None:
            self.entity_directions.pop(entity, None)
        for tag, members in self.target_tag_members.items():
            if entity in members:
                self.target_tag_members[tag] = tuple(member for member in members if member != entity)
        self.model_gate_consumers.pop(entity, None)
        registry = self.b61_registries.pop(entity, None)
        if registry is not None:
            registry.clear()
        target_registry = self.component86_registries.pop(entity, None)
        if target_registry is not None:
            target_registry.clear()
        self._retire_control_contexts(entity=entity)
        aliases = self.alias_components.pop(entity, None)
        if aliases is not None:
            aliases.retire()
        look = self.look_contexts.pop(entity, None)
        if look is not None:
            look.retire()
        scope = self.scope_components.pop(entity, None)
        if scope is not None:
            scope.clear()
        if record is not None and record.reference.int:
            del self.references[record.reference]
        if record is not None and self.possessors is not None:
            released = tuple(owner for owner, target in self.current_possessions.items()
                             if target == entity and owner != entity)
            self.current_possessions = {
                owner: target for owner, target in self.current_possessions.items()
                if owner != entity and target != entity
            }
            self.possessors.discard(entity)
            self.component44_body_ids.pop(entity, None)
            self.primary_possessors.pop(entity, None)
            self.primary_possessors = {
                target: owner for target, owner in self.primary_possessors.items() if owner != entity
            }
            self._notify_possession_change(entity, retired=True)
            for owner in released:
                self._notify_possession_change(owner)
        return record

    def _bound_entity(self, entity: int) -> None:
        if type(entity) is not int or entity not in self.entities:
            raise ValueError("entity must already be bound")

    def set_possession_requests(self, entity: int, registry: possession_requests.Registry) -> None:
        self._bound_entity(entity)
        if (type(registry) is not possession_requests.Registry or entity in self.possession_requests
                or registry in self.possession_requests.values()):
            raise ValueError("possession requests need an unbound registry")
        self.possession_requests[entity] = registry

    def possession_request_registry(self, entity: int):
        return self.possession_requests.get(entity, NotImplemented)

    def set_distance_constraints(self, entity: int, registry: distance_constraints.Registry) -> None:
        self._bound_entity(entity)
        if entity in self.distance_constraints:
            raise ValueError("distance constraint registry is already bound")
        self.distance_constraints[entity] = registry

    def distance_constraint_registry(self, entity: int):
        return self.distance_constraints.get(entity, NotImplemented)

    def set_npc_query_context(self, entity: int, context: npc_queries.Context) -> None:
        self._bound_entity(entity)
        if type(context) is not npc_queries.Context:
            raise ValueError('invalid NPC query context')
        self.npc_query_contexts[entity] = context

    def npc_query_context(self, entity: int):
        return self.npc_query_contexts.get(entity, NotImplemented)

    def set_target_tag_candidates(self, candidates) -> None:
        """Declare the active query order and effective uint32 tags explicitly."""
        ordered = {}
        for entity, tags in candidates:
            self._bound_entity(entity)
            keys = frozenset(tags)
            if entity in ordered or any(type(tag) is not int or not 0 < tag <= 0xFFFFFFFF for tag in keys):
                raise ValueError("invalid target-tag candidate catalog")
            ordered[entity] = keys
        self._target_tag_candidates = ordered

    def target_tag_candidates(self):
        if self._target_tag_candidates is None:
            return NotImplemented
        return tuple(self._target_tag_candidates.items())

    def set_target_tag_entities(self, tag: int, entities) -> None:
        """Supply the ordered active matches for an authored TargetTag."""
        if type(tag) is not int or tag >> 48 != 0x0D60:
            raise ValueError("expected a TargetTag GUID")
        members = tuple(entities)
        for entity in members:
            self._bound_entity(entity)
        self.target_tag_members[tag] = tuple(dict.fromkeys(members))

    def target_tag_entities(self, tag: int):
        return self.target_tag_members.get(tag, NotImplemented)

    def set_entity_directions(self, entity: int, *, aim=None, forward=None) -> None:
        """Bind the two transform-derived outputs independently for a scene entity."""
        self._bound_entity(entity)
        directions = (
            _position(aim) if aim is not None else None,
            _position(forward) if forward is not None else None,
        )
        if self.entity_directions is None:
            self.entity_directions = {}
        self.entity_directions[entity] = directions

    def set_ai_difficulty(self, entity: int, definition: int, difficulty: int) -> None:
        """Select an authored difficulty key on an explicitly bound AI entity."""
        self._bound_entity(entity)
        if type(definition) is not int or type(difficulty) is not int:
            raise ValueError("AI definition and difficulty must be resource identifiers")
        placement = self.entities[entity].placement
        if placement is not None and placement.definition != definition:
            raise ValueError("AI difficulty definition differs from the bound placement")
        record = graph.entity_body(definition)
        profiles = record.get("ai_difficulty_profiles", {}) if record is not None else {}
        if not isinstance(profiles, dict) or f"0x{difficulty:016X}" not in profiles:
            raise ValueError("difficulty is not authored by the AI definition")
        self.ai_difficulties[entity] = expr.Asset(difficulty)

    def set_b61_registry(self, entity: int, registry: b61.Registry) -> None:
        self._bound_entity(entity)
        if (type(registry) is not b61.Registry or registry.entries or entity in self.b61_registries
                or registry in self.b61_registries.values()):
            raise ValueError("B61 registry must be an empty service with a single scene owner")
        self.b61_registries[entity] = registry

    def b61_registry(self, entity: int):
        return self.b61_registries.get(entity, NotImplemented)

    def set_component86(self, entity: int, registry: component86.Registry | None) -> None:
        self._bound_entity(entity)
        if (entity in self.component86_registries or (registry is not None and (
                type(registry) is not component86.Registry or registry.entries or registry.selection_entries
                or registry in self.component86_registries.values()))):
            raise ValueError('component86 requires an unbound empty registry or known absence')
        self.component86_registries[entity] = registry

    def component86(self, entity: int):
        return self.component86_registries.get(entity, NotImplemented)

    def set_health_pool_amounts(self, entity: int, health: float, armour: float, shields: float,
                                *, maximum: float | None = None) -> None:
        self._bound_entity(entity)
        amounts = tuple(expr.f32(amount) for amount in (health, armour, shields))
        if any(not math.isfinite(amount) or amount < 0 for amount in amounts):
            raise ValueError("expected finite nonnegative current health pools")
        if maximum is not None:
            maximum = expr.f32(maximum)
            if not math.isfinite(maximum) or maximum < 0:
                raise ValueError("expected a finite nonnegative maximum health total")
        self.health_pool_amounts[entity] = amounts, maximum

    def set_component51_state_counter(self, entity: int, counter: health_queries.StateCounter | None):
        self._bound_entity(entity)
        if counter is not None and type(counter) is not health_queries.StateCounter:
            raise ValueError("expected an explicit component51 state counter or known absence")
        self.health_state_counters[entity] = counter

    def component51_state_counter(self, entity: int):
        return self.health_state_counters.get(entity, NotImplemented)

    def filtered_body_health(self, entity: int, kind: int, tag: int):
        pools = self.health_pool_amounts.get(entity)
        if pools is None or tag != 0 or not 0 <= kind < len(pools[0]):
            return NotImplemented
        return pools[0][kind]

    def body_health(self, entity: int):
        if entity not in self.entities:
            return None
        pools = self.health_pool_amounts.get(entity)
        if pools is None or pools[1] is None:
            return NotImplemented
        amounts, maximum = pools
        return expr.f32(sum(amounts)), maximum

    def native_state_factory_miss(self, cls: str) -> bool:
        return cls in self._native_state_misses

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
        self.target_tag_grants.pop(entity, None)
        self.combat_damage_filters.pop(entity, None)
        if entity in self.ai_control_settings:
            self.ai_control_settings[entity] = (5.0, False)
        if previous is not None:
            for target, names in tuple(self.display_name_overrides.items()):
                for state in tuple(names):
                    if state.instance.component is previous:
                        del names[state]
                if not names:
                    del self.display_name_overrides[target]
            for catalog in (self.ai_control_records, self.ai_facing_records,
                            self.ai_weapon_records, self.ai_behavior_requests):
                for records in catalog.values():
                    for state in tuple(records):
                        if state.instance.component is previous:
                            del records[state]
        self.voice_set_overrides.pop(entity, None)
        for resource, (state, _) in tuple(self.global_data_flow.items()):
            if state.instance.component is previous:
                del self.global_data_flow[resource]
        messages = self.message_contexts.pop(entity, None)
        if messages is not None:
            messages.retire()
        self.npc_query_contexts.pop(entity, None)
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
        mapping = self.data_flow_contexts.pop(entity, None)
        if mapping is not None:
            mapping.retire()
        if previous is not None:
            registry = self.b61_registries.get(entity)
            if registry is not None:
                registry.clear()
            for target in self.component86_registries.values():
                if target is not None:
                    target.remove_component(previous)
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

    def create_script_entity(self, instance, node, definition: int, position: expr.Vec3):
        if (self.mode in JUNKENSTEIN_MODES and instance.component.entity == runtime.GAME_MODE_ENTITY
                and (instance.graph.index, node.pos, definition) == (0x0C8C, 283, 0x0400000000003C53)):
            create = getattr(self, 'create_vengeful_ghost', None)
            if create is not None:
                return self._create_script_binding(instance, node, create, (definition, position), definition)
        if self.entity_creator is None:
            return super().create_script_entity(instance, node, definition, position)
        args = (definition, position)
        return self._create_script_binding(instance, node, self.entity_creator, args, definition)

    def create_script_hero(
        self, instance, node, hero: int, position: expr.Vec3, team: int, yaw: float, flags: tuple, **resources
    ):
        if self.hero_creator is None:
            return super().create_script_hero(instance, node, hero, position, team, yaw, flags, **resources)
        args = (hero, position, team, yaw, flags)
        return self._create_script_binding(instance, node, self.hero_creator, args, **resources)

    def _create_script_binding(
        self, instance, node, create, args: tuple, definition: int | None = None, **options
    ):
        source = instance.component
        source.ensure_valid()
        if (self.script_components.get(source.entity) is not source or instance.stopped or instance.teardown
                or source.instances.get(instance.id) is not instance):
            source.unsupported(instance, "entity creation source lifecycle", node.cls, node)
        previous = set(self.entities)
        target = create(*args, **options)
        source.ensure_valid()
        if (self.script_components.get(source.entity) is not source or instance.stopped or instance.teardown
                or source.instances.get(instance.id) is not instance):
            source.unsupported(instance, "entity creation source lifecycle", node.cls, node)
        if target is None:
            return None
        if (not isinstance(target, SceneEntity) or target.id in previous
                or self.entities.get(target.id) is not target or target.definition is None
                or (definition is not None and target.definition != definition)):
            source.unsupported(instance, "entity creation result binding", node.cls, node)
        return expr.Entity(target.id)

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

    def ruleset_for(self, entity: int, key: int):
        binding = self.entities.get(entity)
        if binding is None or binding.hero is None:
            return super().ruleset_for(entity, key)
        from ow174.game.script import rulesets

        return rulesets.default(key, binding.hero, self.mode)

    def mode_score_threshold_row(self):
        if self.mode_score_thresholds is None:
            return NotImplemented
        return self.mode_score_thresholds.get(self.mode, ((), {}))

    def configure_junkenstein_card(self, card):
        """Bind Arcade inputs before the encounter's first frame."""
        difficulty = junkenstein_card_difficulty(card)
        if difficulty is None:
            return
        self.junkenstein_difficulty = expr.Asset(difficulty)
        owner = self.script_components.get(runtime.GAME_MODE_ENTITY)
        if owner is not None:
            for key, value in junkenstein_card_mode_values(card).items():
                owner.set(key, value)

    def routed_game_message_targets(self, instance, node, message):
        index = instance.graph.index
        if self.mission_message_route is not None:
            targets = self.mission_message_route(index, node.pos, message)
            if targets is not NotImplemented:
                if (self.mode in JUNKENSTEIN_MODES and type(targets) is tuple
                        and (index, node.pos, message) == (0x0CE0, 2, 0x02400000000006AC)):
                    # CBA starts 0C95 through the body's linked controller. Its 0CDC child receives 06AC.
                    body = instance.component.entity
                    controller = self.linked_controllers.get(body)
                    component = self.script_components.get(controller)
                    if (component is not None and component.fault is None
                            and self.current_possessions.get(controller) == body
                            and any(item.graph.index == 0x0CDC and not item.stopped and not item.teardown
                                    for item in component.instances.values())):
                        target = expr.Entity(controller)
                        if target not in targets:
                            return (*targets, target)
                return targets
        owner = instance.component
        if self.mode in JUNKENSTEIN_MODES and owner.entity == runtime.GAME_MODE_ENTITY:
            receivers = {
                (0x0C8C, 84, 0x02400000000006BE): {0x0CA5, 0x0CA7, 0x0CBC, 0x0CC7},
                (0x0CBB, 21, 0x0240000000000669): {0x0636, 0x0C90, 0x13A4, 0x13C1},
                (0x0CBB, 68, 0x0240000000000045): {0x01CA, 0x01CB, 0x14B1},
                (0x181D, 0, 0x0240000000000669): {0x0636, 0x0C90, 0x13A4, 0x13C1},
                (0x1F93, 1, 0x0240000000000F58): {0x0CBB},
            }.get((index, node.pos, message))
            if index == 0x0CB3 and node.pos in (98, 101, 102, 104) and message == 0x02400000000006D3:
                receivers = {0x0CBC}
            if index == 0x0CEC and node.pos in (2, 3) and message == 0x02400000000006BB:
                receivers = {0x0CA5, 0x0CED}
            if receivers is not None:
                return tuple(expr.Entity(entity) for entity, component in self.script_components.items()
                             if component is not None and component.fault is None
                             and any(item.graph.index in receivers and not item.stopped and not item.teardown
                                     for item in component.instances.values()))
        # These authored wave messages are consumed by 0CBD on the same mode owner.
        if (self.mode in JUNKENSTEIN_MODES and owner.entity == runtime.GAME_MODE_ENTITY
                and (index, node.pos, message & 0xFFFFFFFFFFFF) in (
                    (0x0C8C, 322, 0x06CC), (0x0C8C, 323, 0x06CC),
                    (0x0CB3, 70, 0x06CD), (0x0CB3, 71, 0x06CD),
                    (0x0CB3, 73, 0x06DB), (0x0CB3, 75, 0x06CE), (0x0CB3, 125, 0x06DC),
                )
                and message >> 48 == 0x0240
                and any(item.graph.index == 0x0CBD and not item.stopped and not item.teardown
                        for item in owner.instances.values())):
            return (expr.Entity(owner.entity),)
        return NotImplemented

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

    def managed_script_resource(self, instance, node, resource):
        component = instance.component
        if (self.script_components.get(component.entity) is not component or instance.stopped
                or instance.teardown or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "scene managed script source lifecycle", node.cls, node)
        queue = self.play_queues.get(component.entity)
        if queue is None or queue.component is not component or queue.closed:
            component.unsupported(instance, "scene managed script loader", node.cls, node)
        load = queue.acquire(resource.guid)
        component.ensure_valid()
        if (self.play_queues.get(component.entity) is not queue or queue.closed
                or self.script_components.get(component.entity) is not component
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "scene managed script loader retired", node.cls, node)
        if load is None or (type(load) is play_queue.Load and load.phase == "failed"):
            return None
        if (type(load) is not play_queue.Load or load.phase != "ready"
                or not isinstance(load.definition, graph.Graph)
                or resource.guid != (graph.GRAPH_TYPE << 48) | load.definition.index):
            component.unsupported(instance, "scene managed script resource readiness", node.cls, node)
        return load.definition

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

    def voice_category_service(self, instance):
        component = instance.component
        if self.script_components.get(component.entity) is not component:
            return NotImplemented
        return self.voice_category_callback

    def voice_context(self, entity: int):
        if entity not in self.entities:
            return None
        return self.voice_contexts.get(entity, NotImplemented)

    def bind_voice_set(self, entity: int, definition: int) -> None:
        self._bound_entity(entity)
        if entity in self.voice_sets or definition >> 48 != 0x07A0:
            raise ValueError('invalid or duplicate voice set component')
        self.voice_sets[entity] = definition

    def voice_set(self, entity: int) -> int | None:
        override = self.voice_set_overrides.get(entity)
        return override[1] if override is not None else self.voice_sets.get(entity)

    def apply_voice_set(self, state, definition: int):
        component = state.instance.component
        entity = component.entity
        if (self.script_components.get(entity) is not component
                or entity not in self.voice_sets or entity in self.voice_set_overrides):
            return NotImplemented
        self.voice_set_overrides[entity] = (state, definition)
        return True

    def clear_voice_set(self, state) -> None:
        entity = state.instance.component.entity
        override = self.voice_set_overrides.get(entity)
        if override is not None and override[0] is state:
            del self.voice_set_overrides[entity]

    def set_global_data_flow(self, state, resource: int, value: bool):
        component = state.instance.component
        if self.script_components.get(component.entity) is not component or resource in self.global_data_flow:
            return NotImplemented
        self.global_data_flow[resource] = (state, value)
        return True

    def clear_global_data_flow(self, state) -> None:
        for resource, (owner, _) in tuple(self.global_data_flow.items()):
            if owner is state:
                del self.global_data_flow[resource]

    def set_script_entity_team(self, instance, target, team):
        component = instance.component
        if (self.script_components.get(component.entity) is not component
                or not callable(self.script_team_setter)):
            return NotImplemented
        binding = self.entities.get(target)
        if binding is None:
            return True
        result = self.script_team_setter(target, team)
        if (self.script_components.get(component.entity) is not component
                or self.entities.get(target) is not binding):
            return NotImplemented
        return result

    def filter_array_candidate(self, state, candidate, config):
        if ((state.instance.graph.index, state.node.pos) == (0x0CBA, 6)
                and self.mode in JUNKENSTEIN_MODES and candidate.id in self.entities):
            revision = self._array_filter_revisions.get(candidate.id)
            if revision is None:
                revision = self._array_filter_revisions[candidate.id] = runtime.Var(('array filter', candidate.id))
            state.watch(revision)
        return self._filter_entity(state.instance, candidate.id, config)

    def notify_entity_filter_change(self, entity: int, *, retired=False) -> None:
        revision = (self._array_filter_revisions.pop(entity, None) if retired
                    else self._array_filter_revisions.get(entity))
        if revision is not None:
            self._notify_query_change(revision)
            if retired:
                for watcher in revision.watchers:
                    watcher.watched.pop(revision, None)
                revision.watchers.clear()

    def _filter_entity(self, instance, entity, config):
        """D0DA20's entity masks and literal tag-list tests on scene bindings."""
        if config is None:
            return True
        component = instance.component
        fields = ('m_84065FD1', 'm_1E14CCA8', 'm_4EA686DB')
        tag_fields = ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3')
        killer_filter = (self.mode in JUNKENSTEIN_MODES and instance.graph.index in (0x05AC, 0x01A3, 0x157C)
                         and config.get('m_66747E7D') == 2)
        witch_ally_filter = (self.mode in JUNKENSTEIN_MODES and instance.graph.index in (0x006B, 0x0071)
                             and config.get('m_66747E7D') == 1 and config.get('m_89831602') == 4)
        predicate = config.get('m_condition')
        beam_filter = self.mode in JUNKENSTEIN_MODES and instance.graph.index == 0x157C and predicate is not None
        ghost_filter = (self.mode in JUNKENSTEIN_MODES and instance.graph.index == 0x2B72
                        and config == instance.graph.node(34).field('m_filter'))
        death_predicate = killer_filter and instance.graph.index == 0x01A3 and predicate is not None
        allowed = (*fields, *tag_fields, 'm_66747E7D') if killer_filter else (*fields, *tag_fields)
        if witch_ally_filter:
            allowed += ('m_66747E7D', 'm_89831602')
        if beam_filter or death_predicate or witch_ally_filter or ghost_filter:
            allowed += ('m_condition',)
        if (self.script_components.get(component.entity) is not component
                or not callable(self.array_filter_inputs) or config.get('$') != 'STUConfigVarFilter'
                or any(value is not None and value != 0 for key, value in config.items()
                       if key != '$' and key not in allowed)):
            return NotImplemented
        any_bits, all_bits, excluded = (0 if config.get(key) is None else config[key] for key in fields)
        if any(type(bits) is not int or not 0 <= bits <= 0xFFFFFFFFFFFFFFFF
               for bits in (any_bits, all_bits, excluded)):
            return NotImplemented
        binding = self.entities.get(entity)
        if binding is None:
            return False
        inputs = self.array_filter_inputs(entity)
        if (self.script_components.get(component.entity) is not component
                or self.entities.get(entity) is not binding
                or type(inputs) is not tuple or len(inputs) != 2):
            return NotImplemented
        component_present, bits = inputs
        if (type(component_present) is not bool or (bits is not None and
                (type(bits) is not int or not 0 <= bits <= 0xFFFFFFFFFFFFFFFF))):
            return NotImplemented
        if any_bits | all_bits | excluded:
            if not component_present and any_bits != 0x3FFFFFFF:
                return False
            bits = bits or 0
            if not ((any_bits == 0x3FFFFFFF or bits & any_bits)
                    and bits & all_bits == all_bits and not bits & excluded):
                return False
        if killer_filter or witch_ally_filter:
            # BCAAE0 uses the +0x40 relation mask as an additional required predicate.
            context = self.entity_team_context(component.entity, component=component)
            if context is NotImplemented:
                return NotImplemented
            source = context.lookup(component.entity)
            target = context.lookup(entity)
            if source is None or source.filters is None or target is None or target.filters is None:
                return False
            source_team = entity_team.index_from_filters(source.filters.bits)
            target_team = entity_team.index_from_filters(target.filters.bits)
            if min(source_team, target_team) < 0:
                return False
            friendly = source_team == target_team
            if (killer_filter and friendly) or (witch_ally_filter and (not friendly or entity == component.entity)):
                return False

        # DB2C10 omits null tokens. DB2FD0 tests any, all and excluded lists in that order.
        for key, required in zip(tag_fields, (True, True, False), strict=True):
            tag_cfg = config.get(key)
            if tag_cfg is None:
                continue
            if (beam_filter and not required and isinstance(tag_cfg, dict)
                    and tag_cfg.get('$') == 'STU_06610EBC'):
                tag_configs = tag_cfg.get('m_configVars') or ()
            else:
                tag_configs = (tag_cfg,)
            for item in tag_configs:
                if (self.mode in JUNKENSTEIN_MODES and instance.graph.index == 0x0CBC
                        and key == 'm_B486603E' and expr.dynamic(item) == (runtime.INSTANCE, 6299)):
                    # 0CBC/95 stores the target tag delivered by the mode's 06D3 broadcast.
                    value = instance.evaluate(item)
                    values = value if isinstance(value, tuple) else (value,)
                    for token in values:
                        if token is None or token == expr.Asset(0):
                            continue
                        if not isinstance(token, expr.Asset) or token.guid >> 48 != 0x0D60:
                            return NotImplemented
                        present = self._has_target_tag(binding, token.guid)
                        if present is not True:
                            return present
                    continue
                else:
                    if (not isinstance(item, dict) or item.get('$') != 'STU_357DABBB'
                            or item.get('m_resourceKey')):
                        return NotImplemented
                    resource = item.get('m_7D7C86A1')
                    tag = graph.guid(resource) if resource is not None else 0
                if tag == 0:
                    continue
                if tag is None or tag >> 48 != 0x0D60 or not tag & 0xFFFFFFFFFFFF:
                    return NotImplemented
                present = self._has_target_tag(binding, tag)
                if present is NotImplemented:
                    return NotImplemented
                if present != required:
                    return False
        if beam_filter:
            # The authored predicate reads the candidate as field 8117 of parameter 8.
            with component.parameter_scope({8: expr.Map(((8117, expr.Entity(entity)),))}):
                return expr.truthy(play.evaluate_scalar(instance, predicate))
        if death_predicate:
            return expr.truthy(play.evaluate_scalar(instance, predicate))
        if (witch_ally_filter or ghost_filter) and predicate is not None:
            with component.parameter_scope({8: expr.Entity(entity)}):
                return expr.truthy(play.evaluate_scalar(instance, predicate, initial=True))
        return True

    def _first_bound_combat_target(self, instance, filter_config):
        for entity in self.team_contexts:
            instance.component.consume_work("Junkenstein combat target query")
            matched = self._filter_entity(instance, entity, filter_config)
            if matched is NotImplemented:
                return NotImplemented
            if matched:
                return expr.Entity(entity)
        return expr.Entity(0)

    def submit_spawn_search(self, state, request, complete):
        """Submit on the scene thread and return a non-throwing cancellation callback."""
        component = state.instance.component
        if self.script_components.get(component.entity) is not component or not callable(self.spawn_search):
            return NotImplemented
        return self.spawn_search(state, request, complete)

    def bind_ai_control(self, entity):
        """Declare component 86 on a body with a live scene binding."""
        self._bound_entity(entity)
        self.ai_control_records.setdefault(entity, {})
        self.ai_facing_records.setdefault(entity, {})
        self.ai_control_settings.setdefault(entity, (5.0, False))
        self.ai_weapon_records.setdefault(entity, {})

    def register_ai_control_record(self, state, target, record):
        return self._register_ai_record(state, target, record, self.ai_control_records)

    def register_ai_facing_record(self, state, target, record):
        return self._register_ai_record(state, target, record, self.ai_facing_records)

    def set_ai_control_setting(self, state, value, flag):
        component = state.instance.component
        entity = component.entity
        if self.script_components.get(entity) is not component or entity not in self.ai_control_settings:
            return NotImplemented
        self.ai_control_settings[entity] = (value, flag)
        return True

    def bind_ai_behavior(self, entity):
        """Declare component 88 on a live controller."""
        self._bound_entity(entity)
        self.ai_behavior_requests.setdefault(entity, {})

    def register_ai_behavior_request(self, state, record):
        owner = state.instance.component.entity
        return self._register_ai_record(state, owner, record, self.ai_behavior_requests)

    def register_ai_weapon_record(self, state, target, record):
        if target not in self.entities:
            return None
        controller = self.linked_controllers.get(target, NotImplemented)
        if controller is None:
            return None
        slots = self.ai_weapon_slots.get(controller)
        if slots is None:
            return NotImplemented
        if record.identifier not in slots.get(record.slot, ()):
            return None
        return self._register_ai_record(state, target, record, self.ai_weapon_records)

    def _register_ai_record(self, state, target, record, catalog):
        component = state.instance.component
        if self.script_components.get(component.entity) is not component:
            return NotImplemented
        records = catalog.get(target)
        if records is None:
            return NotImplemented if target in self.entities else None
        following = [(owner, other) for owner, other in records.items()
                     if record.priority > other.priority
                     or (record.prefer_later and record.priority == other.priority)]
        for owner, _ in following:
            del records[owner]
        records[state] = record
        records.update(following)
        return lambda: records.pop(state, None)

    def session_script_entities(self, instance):
        component = instance.component
        if (self.script_components.get(component.entity) is not component
                or not callable(self.session_entity_roster)):
            return NotImplemented
        result = []
        for entity in self.session_entity_roster():
            component.consume_work('session roster traversal')
            if self.mode in JUNKENSTEIN_MODES and instance.graph.index == 0x2B72:
                entity = self.current_possessions.get(entity)
            if entity in self.entities:
                result.append(expr.Entity(entity))
        return tuple(result)

    def query_participants(self, instance, filter_config, *, required_tag=None):
        component = instance.component
        if (self.script_components.get(component.entity) is not component
                or (filter_config.get('m_B486603E') is not None) != (required_tag is not None)):
            return NotImplemented
        mask_fields = ('m_84065FD1', 'm_1E14CCA8', 'm_4EA686DB')
        required, team, excluded = (filter_config.get(key) for key in mask_fields)
        player_query = (required, team, excluded) == (0x40000, 0x800000, 0x10)
        if not player_query and (required, team, excluded) != (1, 0x1000000, 0x10):
            return NotImplemented
        if any(value is not None and value != 0 for key, value in filter_config.items()
               if key not in ('$', 'm_B486603E', *mask_fields)):
            return NotImplemented
        roster = self.participant_roster
        if roster is None:
            if player_query and required_tag is None:
                roster = self.player_participant_roster
            elif not player_query:
                roster = self.enemy_participant_roster
        if not callable(roster):
            return NotImplemented
        result = []
        for entity, bits, flags in roster():
            component.consume_work('participant query')
            binding = self.entities.get(entity)
            if (binding is not None and not flags & 4 and bits & required
                    and bits & team and not bits & excluded):
                if required_tag is not None:
                    present = self._has_target_tag(binding, required_tag.guid)
                    if present is NotImplemented:
                        return NotImplemented
                    if not present:
                        continue
                result.append(expr.Entity(entity))
        return tuple(result)

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

    def bind_data_flow_providers(self, entity: int) -> data_flow.Context:
        """Register providers for a declared live component without choosing a renderer consumer."""
        component = self.script_components.get(entity)
        if component is None:
            raise ValueError('data-flow providers require a bound script component')
        component.ensure_valid()
        context = data_flow.Context.managed(component, self.position)
        self.set_data_flow_context(entity, context)
        return context

    def data_flow_context(self, entity: int):
        return self.data_flow_contexts.get(entity, NotImplemented)

    def model_look_context(self, entity: int):
        return self.look_contexts.get(entity, NotImplemented)

    def set_entity_query_context(self, entity: int, context: entity_queries.Context | None = None,
                                 *, ai_links: bool = False) -> None:
        self._bound_entity(entity)
        if context is None:
            if self.possessors is None:
                raise ValueError('scene query context needs a declared possession catalog')
            context = entity_queries.Context(entity, True, True, True, lookup=self._scene_query_target,
                                             watch_possession=self._watch_possession, watch_environment=None)
            if ai_links:
                context.lookup_linked = self._lookup_ai_linked_target
                context.watch_environment = self._watch_ai_link_change
        elif ai_links:
            raise ValueError('choose a scene AI link service or a supplied query context')
        if type(context) is not entity_queries.Context or context.owner != entity or context.retired:
            raise ValueError('expected live entity-query context for this entity')
        if entity in self.query_contexts and self.query_contexts[entity] is not context:
            raise ValueError('detach the existing query context before replacement')
        self.query_contexts[entity] = context

    def entity_query_context(self, entity: int):
        return self.query_contexts.get(entity, NotImplemented)

    def set_ai_linked_target(self, controller: int, slot: int, target: int | None) -> None:
        """Publish one host-selected target, retaining its entity binding identity."""
        if (controller not in self.ai_behavior_requests
                or type(slot) is not int or not 0 <= slot <= 0xFFFFFFFF):
            raise ValueError('linked target requires a bound AI controller and a uint32 slot')
        binding = None
        if target is not None:
            self._bound_entity(target)
            binding = self.entities[target]
        key = (controller, slot)
        if self._ai_linked_targets.get(key) is binding:
            return
        if binding is None:
            del self._ai_linked_targets[key]
        else:
            self._ai_linked_targets[key] = binding
        self._notify_ai_link_change()

    def _lookup_ai_linked_target(self, controller: int, slot: int):
        target = self._ai_linked_targets.get((controller, slot))
        return target.id if target is not None else None

    def _watch_ai_link_change(self, state, kind):
        if kind == 0x20:
            state.watch(self._ai_link_revision)

    def _notify_ai_link_change(self):
        self._notify_query_change(self._ai_link_revision)

    def _watch_possession(self, state, target):
        revision = self._possession_revisions.get(target.entity)
        if revision is None:
            revision = self._possession_revisions[target.entity] = runtime.Var(('possession', target.entity))
        state.watch(revision)

    def _notify_possession_change(self, owner, *, retired=False):
        revision = (self._possession_revisions.pop(owner, None) if retired
                    else self._possession_revisions.get(owner))
        if revision is not None:
            self._notify_query_change(revision)
            if retired:
                for watcher in revision.watchers:
                    watcher.watched.pop(revision, None)
                revision.watchers.clear()

    @staticmethod
    def _notify_query_change(revision):
        before = revision.base
        revision.base = (before or 0) + 1
        if watcher := next(iter(revision.watchers), None):
            watcher.instance.component.changed(revision, before)

    def _scene_query_target(self, entity: int):
        if entity not in self.entities:
            return None
        possession = None
        if entity in self.possessors:
            possession = entity_queries.Possession(self.current_possessions.get(entity, 0))
        return entity_queries.Target(entity, possession)

    def entity_definition_resource(self, entity: int):
        record = self.entities.get(entity)
        if record is None:
            return 0
        return NotImplemented if record.definition is None else record.definition

    def declare_spatial_binding_owner_absent(self, entity: int) -> None:
        self._bound_entity(entity)
        self.absent_spatial_binding_owners.add(entity)

    def spatial_binding_owner(self, entity: int):
        return None if entity in self.absent_spatial_binding_owners else NotImplemented

    def set_component45_spatial_context(self, entity: int, context: component45_spatial.Context) -> None:
        self._bound_entity(entity)
        if type(context) is not component45_spatial.Context:
            raise ValueError("expected explicit component45 spatial context")
        self.component45_spatial_contexts[entity] = context

    def component45_spatial_context(self, entity: int):
        return self.component45_spatial_contexts.get(entity, NotImplemented)

    def set_component76_keys(self, entity: int, keys: entity_queries.Component76Keys | None) -> None:
        self._bound_entity(entity)
        if keys is not None and type(keys) is not entity_queries.Component76Keys:
            raise ValueError("expected explicit component76 keys or known absence")
        self.component76_records[entity] = keys

    def component76_keys(self, entity: int):
        if entity not in self.entities:
            return None
        return self.component76_records.get(entity, NotImplemented)

    def indexed_target_lookup(self, entity: int):
        records = self.indexed_targets.get(entity, NotImplemented)
        if records is NotImplemented or records is None:
            return records
        return lambda owner, index: records.get((owner, index), 0)

    def target_record_scalar(self, owner: int, target: int):
        if owner not in self.entities:
            return None
        records = self.target_record_scalars.get(owner, NotImplemented)
        if records is NotImplemented or records is None:
            return records
        return records.get(target)

    def set_controller_record(self, record: controller_queries.Record) -> None:
        if type(record) is not controller_queries.Record:
            raise ValueError("expected an explicit controller query record")
        self._bound_entity(record.entity)
        self.controller_records[record.entity] = record

    def controller_record(self, entity: int):
        if entity not in self.entities:
            return None
        return self.controller_records.get(entity, NotImplemented)

    def set_entity_filter_record(self, entity: int, record: entity_filter.Record) -> None:
        self._bound_entity(entity)
        if type(record) is not entity_filter.Record:
            raise ValueError("expected an explicit entity filter record")
        self.entity_filter_records[entity] = record

    def entity_filter_record(self, entity: int):
        if entity not in self.entities:
            return None
        return self.entity_filter_records.get(entity, NotImplemented)

    def set_model_gate_consumer(self, entity: int, consumer) -> None:
        self._bound_entity(entity)
        if consumer is not None and not callable(consumer):
            raise ValueError("expected a model gate setter or known absence")
        self.model_gate_consumers[entity] = consumer

    def model_gate_consumer(self, entity: int):
        if entity not in self.entities:
            return None
        return self.model_gate_consumers.get(entity, NotImplemented)

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
        self.npc_query_contexts.pop(component.entity, None)
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
            backend.vector_records.clear()
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
        released = set(self.current_possessions)
        self.possessors, self.primary_possessors = known, relations
        self.component44_body_ids.clear()
        self.current_possessions.clear()
        for owner in tuple(self._possession_revisions):
            if owner not in known or owner in released:
                self._notify_possession_change(owner, retired=owner not in known)

    def set_owned_deployables(self, owner: int, entities: tuple[int, ...]) -> None:
        """Publish the owner's active deployable IDs from the creation/retirement service."""
        self._bound_entity(owner)
        for entity in entities:
            self._bound_entity(entity)
        self._owned_deployables[owner] = frozenset(entities)

    def set_current_possession(self, owner: int, target: int | None) -> None:
        """Reflect component 44's current body, independently of the reverse registry."""
        self._bound_entity(owner)
        if self.possessors is None or owner not in self.possessors:
            raise ValueError("current possessor must have component 44")
        previous = self.current_possessions.get(owner)
        if target is None:
            self.current_possessions.pop(owner, None)
        else:
            self._bound_entity(target)
            self.current_possessions[owner] = target
        if previous != target:
            self._notify_possession_change(owner)

    def set_linked_controller(self, body: int, controller: int | None) -> None:
        """Declare component 43's link separately from the primary possessor registry."""
        self._bound_entity(body)
        if controller is not None:
            self._bound_entity(controller)
        self.linked_controllers[body] = controller

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
            self.current_possessions.pop(entity, None)
            self.component44_body_ids.pop(entity, None)
            self.primary_possessors = {
                target: owner for target, owner in self.primary_possessors.items() if owner != entity
            }
            self._notify_possession_change(entity, retired=True)

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

    def set_component44_body_id(self, entity: int, body_id: int) -> None:
        """Supply the raw Entity ID in a live component 44's +0xD0 member."""
        self._bound_entity(entity)
        if self.possessors is None or entity not in self.possessors:
            raise ValueError("body ID requires a declared component 44")
        if type(body_id) is not int or not 0 <= body_id <= 0xFFFFFFFF:
            raise ValueError("component 44 body ID must be an unsigned 32-bit integer")
        self.component44_body_ids[entity] = body_id

    def position(self, entity: int):
        record = self.entities.get(entity)
        if record is None or record.position_reader is None:
            return record.position if record is not None else None
        try:
            value = _position(record.position_reader())
        except (ValueError, OverflowError):
            return None
        return value if self.entities.get(entity) is record else None

    def _linked_on_fire(self, instance, body_id):
        body = self.entities.get(body_id)
        if body is None:
            return expr.EVALUATION_FAILED
        if body_id not in self.linked_controllers:
            return NotImplemented
        controller_id = self.linked_controllers[body_id]
        if controller_id is None:
            return expr.EVALUATION_FAILED
        controller = self.entities.get(controller_id)
        if controller is None or controller.on_fire_reader is None:
            return NotImplemented
        active = controller.on_fire_reader()
        instance.component.ensure_valid()
        if (self.entities.get(body_id) is body and self.entities.get(controller_id) is controller
                and self.linked_controllers.get(body_id) == controller_id
                and (active is None or type(active) is bool)):
            return active
        return NotImplemented

    def player_stats_available(self, instance):
        result = self._linked_on_fire(instance, instance.component.entity)
        if result is None:
            return False
        return True if type(result) is bool else NotImplemented

    def movement_snapshot(self, instance):
        component = instance.component
        record = self.entities.get(component.entity)
        if (record is None or record.movement_reader is None
                or self.script_components.get(component.entity) is not component):
            return NotImplemented
        sample = record.movement_reader(component.frame)
        if (self.entities.get(component.entity) is not record
                or self.script_components.get(component.entity) is not component):
            return NotImplemented
        return sample

    def grant_target_tag(self, state, tag):
        component = state.instance.component
        entity = component.entity
        binding = self.entities.get(entity)
        if (self.script_components.get(entity) is not component
                or binding is None or binding.target_tags is None):
            return NotImplemented
        self.target_tag_grants.setdefault(entity, {})[state] = tag
        return True

    def clear_target_tag(self, state) -> None:
        entity = state.instance.component.entity
        grants = self.target_tag_grants.get(entity)
        if grants is not None:
            grants.pop(state, None)
            if not grants:
                del self.target_tag_grants[entity]

    def _has_target_tag(self, target, tag):
        if tag in self.target_tag_grants.get(target.id, {}).values():
            return True
        return tag in target.target_tags if target.target_tags is not None else NotImplemented

    def targets_with_tag(self, instance, tag: int, *, excluded_tag: int | None = None):
        component = instance.component
        if self.script_components.get(component.entity) is not component:
            return NotImplemented
        placed = self.static_tag_targets(tag)
        excluded = self.static_tag_targets(excluded_tag) if excluded_tag is not None else ()
        if placed is NotImplemented or excluded is NotImplemented:
            return NotImplemented
        placed_ids = {entity.id for entity in placed}
        excluded_ids = {entity.id for entity in excluded}
        result = []
        for target in self.entities.values():
            component.consume_work('target tag traversal')
            if (target.id in placed_ids or self._has_target_tag(target, tag) is True):
                if target.id in excluded_ids:
                    continue
                if excluded_tag is None or self._has_target_tag(target, excluded_tag) is not True:
                    result.append(expr.Entity(target.id))
        return tuple(result)

    def static_target_tag(self, tag: int):
        """Resolve a unique bound placement whose tag comes from one source."""
        matches = self.static_tag_targets(tag)
        return matches[0] if isinstance(matches, tuple) and len(matches) == 1 else NotImplemented

    def static_tag_targets(self, tag: int | None):
        """Enumerate bound placements with the target-tag component."""
        matches = []
        for record in self.entities.values():
            placement = record.placement
            if placement is None:
                continue
            components = self.definitions[placement.definition].get("components") or {}
            defaults = [
                row["value"].get("m_targetTags") or ()
                for row in components.get("entries") or ()
                if row["value"].get("$") == "STUTargetTagComponent"
            ]
            placed = [
                row["fields"].get("m_targetTags") or ()
                for row in placement.source.get("components") or ()
                if row["class"] == "STUTargetTagInstanceData"
            ]
            if tag is None:
                if defaults:
                    matches.append(expr.Entity(record.id))
            elif any(tag == graph.guid(value) for tags in defaults + placed for value in tags):
                if len(defaults) != 1 or len(placed) > 1 or (any(defaults) and any(placed)):
                    return NotImplemented
                matches.append(expr.Entity(record.id))
        return tuple(matches)

    def entity_var(self, entity: int, var: int):
        slot = self.entity_var_slot(entity, var)
        if slot is not NotImplemented:
            return slot.value() if slot is not None else None
        if (entity == runtime.GAME_MODE_ENTITY and entity not in self.script_components
                and runtime.mode_variables(self.mode) is not None):
            return super().entity_var(entity, var)
        # Catalog bindings do not activate the entity's script component.
        return NotImplemented

    def health_pool_capacity(self, entity, kind):
        target = self.entities.get(entity)
        if target is None or target.pool_capacity is None:
            return NotImplemented
        amount = target.pool_capacity(kind)
        return amount if self.entities.get(entity) is target else NotImplemented

    def add_wave_health(self, state, amount):
        component = state.instance.component
        if self.script_components.get(component.entity) is component and callable(self.wave_health):
            return self.wave_health(component, amount)
        return NotImplemented

    def suppress_same_team_contact(self, state, entity):
        component = state.instance.component
        binding = self.entities.get(entity)
        if (self.script_components.get(component.entity) is not component
                or binding is None or binding.same_team_contact is None):
            return NotImplemented
        binding.same_team_contact(False)
        return lambda: binding.same_team_contact(True)

    def emit_contact_effect(self, state, entity, contact_set, targets):
        component = state.instance.component
        if (self.script_components.get(entity) is not component or entity not in self.entities
                or not callable(self.contact_effect)):
            return NotImplemented
        return self.contact_effect(state, entity, contact_set, targets)

    def run_effect_entity_action(self, instance, node):
        if not callable(self.effect_entity_action):
            return NotImplemented
        component = instance.component

        def require_bound_source():
            component.ensure_valid()
            if (self.script_components.get(component.entity) is not component
                    or component.instances.get(instance.id) is not instance
                    or instance.stopped or instance.teardown):
                component.unsupported(instance, 'effect entity action source lifecycle', node.cls, node)

        require_bound_source()
        accepted = self.effect_entity_action(instance, node)
        require_bound_source()
        return accepted

    def request_corpse_retirement(self, instance, entity, *, phase_exit=False):
        return self.queue_enemy_retirement(instance.component, entity, phase_exit=phase_exit)

    def queue_enemy_retirement(self, component, entity, *, phase_exit=False):
        body = self.entities.get(component.entity)
        controller = self.entities.get(self.linked_controllers.get(component.entity))
        if (not (component.owner_dead or phase_exit) or body is None or controller is None
                or controller is body or entity not in (body.id, controller.id)
                or controller.id not in self.ai_behavior_requests
                or self.script_components.get(body.id) is not component
                or self.current_possessions.get(controller.id) != body.id):
            return NotImplemented
        self.corpse_retirements[body.id] = (body, controller, component, phase_exit)
        return True

    def bind_combat_damage_filter(self, state):
        instance, node = state.instance, state.node
        component = instance.component
        entity = component.entity
        body = self.entities.get(entity)
        if (self.mode not in JUNKENSTEIN_MODES or body is None
                or self.script_components.get(entity) is not component):
            return NotImplemented
        if ((instance.graph.index, node.pos) == (0x156C, 9) and body.definition == 0x04000000000026B0
                and expr.guid_of(node.field('m_60703B1E')) == 0x0D80000000000318
                and node.field('m_41B10FF2') == 1):
            tags = frozenset(expr.guid_of(tag) for tag in node.field('m_F60AA143') or ())
            scalar = state.evaluate(node.field('m_50530EAB'), watch=False)
            if tags != {0x0D800000000006F9} or scalar != 0.5:
                return NotImplemented
            self.combat_damage_filters.setdefault(entity, {})[state] = (scalar, tags)
            return True
        if ((instance.graph.index, node.pos) != (0x0CBC, 141) or body.hero != 0x02E0000000000040
                or expr.guid_of(node.field('m_60703B1E')) != 0x0D80000000000317
                or node.field('m_41B10FF2') != 0):
            return NotImplemented
        # D275A0 rejects hits containing any tag in the second identifier array.
        excluded = frozenset(expr.guid_of(tag) for tag in node.field('m_5CFFCBAE') or ())
        adjustment = state.evaluate(node.field('m_50530EAB'), watch=False)
        if (not excluded or 0 in excluded
                or type(adjustment) not in (int, float) or not math.isfinite(adjustment)):
            return NotImplemented
        self.combat_damage_filters.setdefault(entity, {})[state] = (adjustment, excluded)
        return True

    def unbind_combat_damage_filter(self, state):
        entity = state.instance.component.entity
        filters = self.combat_damage_filters.get(entity)
        if filters is not None:
            filters.pop(state, None)
            if not filters:
                del self.combat_damage_filters[entity]

    def outgoing_combat_damage(self, entity, amount, hit_tags):
        filters = self.combat_damage_filters.get(entity)
        if not filters:
            return amount
        adjustment = sum(delta for delta, excluded in filters.values() if excluded.isdisjoint(hit_tags))
        return expr.f32(amount * max(0.0, expr.f32(1.0 + adjustment))) if adjustment else amount

    def incoming_barrier_damage(self, entity, amount, hit_tags):
        filters = self.combat_damage_filters.get(entity, {})
        return amount * math.prod(scale for scale, required in filters.values()
                                  if not required.isdisjoint(hit_tags))

    def objective_route_active(self, instance):
        component = instance.component
        if (self.script_components.get(component.entity) is not component
                or not callable(self.objective_route)):
            return NotImplemented
        result = self.objective_route(component.entity)
        component.ensure_valid()
        return result if type(result) is bool else NotImplemented

    def flush_corpse_retirements(self, retire):
        """Run outside the body's VM so it can release active states before unbinding."""
        pending, self.corpse_retirements = self.corpse_retirements, {}
        for body, controller, component, phase_exit in pending.values():
            if self.entities.get(body.id) is not body or self.script_components.get(body.id) is not component:
                continue
            owns_body = self.current_possessions.get(controller.id) == body.id
            if not component.owner_dead and not (phase_exit and owns_body
                                                 and self.entities.get(controller.id) is controller):
                continue
            retire(body.id)
            if (owns_body and self.entities.get(controller.id) is controller
                    and self.current_possessions.get(controller.id) is None):
                retire(controller.id)

    def target_effects(self, state):
        instance, node = state.instance, state.node
        component = instance.component
        if ((instance.graph.index, node.pos) == (0x006B, 62)
                and self.mode in JUNKENSTEIN_MODES and self.witch_staff_heal is not None):
            return self._witch_staff_effect(state)
        if ((instance.graph.index, node.pos) != (0x157C, 200) or not node.server_only
                or self.mode not in JUNKENSTEIN_MODES
                or self.script_components.get(component.entity) is not component):
            return NotImplemented
        body = self.entities.get(component.entity)
        resource = node.field('m_A76A8CF6') or {}
        params = node.field('m_params') or ()
        if (body is not None and body.definition == 0x04000000000026F0
                and resource.get('$') == 'STU_0E766B33' and resource.get('m_resourceKey') is None
                and expr.guid_of(resource.get('m_A76A8CF6')) == 0x04A000000000026E
                and len(params) == 1 and expr.guid_of(params[0].get('m_B5051BCE')) == 0x0A40000000000009
                and (params[0].get('m_value') or {}).get('$') == 'STU_B5A0CAF0'
                and expr.dynamic(node.field('m_targets')) == (expr.INSTANCE, 536)
                and self.player_stats_available(instance) is False):
            # 026E runs 0BD9's per-caster count, whose only output is 05FE to the caster.
            # 01A3/17 records a player criterion. Its 7059 gate can be set on this
            # NPC even though its controller has no player stats record.
            return True
        return NotImplemented

    def _witch_staff_effect(self, state):
        instance, node = state.instance, state.node
        component = instance.component
        source = self.entities.get(component.entity)
        if (source is None or source.definition != 0x04000000000000E4
                or self.script_components.get(source.id) is not component):
            return NotImplemented
        resource = node.field('m_A76A8CF6') or {}
        if expr.guid_of(resource.get('m_A76A8CF6')) != 0x04A0000000000019:
            return NotImplemented
        entity = expr.to_entity(state.evaluate(node.field('m_targets'), watch=False))
        recipient = self.script_components.get(entity)
        filter_config = instance.graph.node(121).field('m_filter')
        if component.owner_dead or recipient is None or recipient.fault is not None:
            state.clear_children()
            return True
        allowed = self._filter_entity(instance, entity, filter_config)
        if allowed is NotImplemented:
            return NotImplemented
        if not allowed:
            state.clear_children()
            return True
        for previous in tuple(state.children):
            if previous != entity:
                state.remove_child(previous)
        child = state.children.get(entity)
        if child is not None and recipient.instances.get(child.id) is child:
            return True
        definition = graph.graph(0x0079)
        if definition is None:
            return NotImplemented
        params = node.field('m_params') or ()
        if len(params) != 1 or expr.guid_of(params[0].get('m_B5051BCE')) != 0x0A4000000000002A:
            return NotImplemented
        amount = state.evaluate(params[0].get('m_value'), watch=False)
        child = recipient.create(definition, init=False)
        state.children[entity] = child
        child.set_source_entity(source.id)
        child.init()
        runtime.send_instance_message(child, 0x024000000000000F, {0x2A: amount}, source.id)
        return True

    def modify_health(self, state, elapsed_ms):
        """Apply encounter health changes through the live body's service."""
        instance, node = state.instance, state.node
        if ((instance.graph.index, node.pos) == (0x2B72, 7)
                and self.mode in JUNKENSTEIN_MODES and self.ghost_contact_damage is not None):
            return self.ghost_contact_damage(state, elapsed_ms)
        if ((instance.graph.index, node.pos) == (0x0033, 5)
                and self.mode in JUNKENSTEIN_MODES and self.shield_recharge is not None):
            return self.shield_recharge(state, elapsed_ms)
        if ((instance.graph.index, node.pos) == (0x0079, 0)
                and self.mode in JUNKENSTEIN_MODES and self.witch_staff_heal is not None):
            return self.witch_staff_heal(state, elapsed_ms)
        if (instance.graph.index, node.pos) == (0x0CBC, 69) and self.mode in JUNKENSTEIN_MODES:
            component = instance.component
            body = self.entities.get(component.entity)
            config = node.field('m_modifyHealth') or {}
            if (body is None or body.self_damage is None
                    or self.script_components.get(body.id) is not component
                    or config.get('m_93CDFD2F') != 1 or config.get('m_instigator') is not None
                    or node.field('m_8B707686') != 1):
                return NotImplemented
            if not elapsed_ms:
                entity = expr.Entity(body.id)
                if (state.evaluate(node.field('m_FA3ECC43'), watch=False) != entity
                        or state.evaluate(config.get('m_724CBA57'), watch=False) != entity
                        or state.evaluate(config.get('m_amount'), watch=False) != 10000):
                    return NotImplemented
                if not component.owner_dead:
                    body.self_damage(10000.0)
            return True
        if ((instance.graph.index, node.pos) == (0x157C, 170) and node.server_only
                and self.mode in JUNKENSTEIN_MODES and self.symmetra_beam_hit is not None):
            return self.symmetra_beam_hit(state, elapsed_ms)
        if (instance.graph.index, node.pos) == (0x01C9, 38) and self.roadhog_hook_target is not None:
            # Node 37 searches for filter bit 0x80, which no damageable scene body carries.
            targets = play.evaluate_scalar(instance, node.field('m_FA3ECC43'))
            return True if targets == () else NotImplemented
        if (instance.graph.index, node.pos) == (0x01C8, 19) and self.roadhog_hook_hit is not None:
            if elapsed_ms:
                return True
            applied = self.roadhog_hook_hit(state)
            if applied is True:
                state.request_finish()
            return applied
        if instance.graph.index != 0x0C89:
            return NotImplemented
        component = instance.component
        body = self.entities.get(component.entity)
        if self.script_components.get(component.entity) is not component or body is None:
            return NotImplemented
        if node.pos == 48:
            # A completed search can produce an explicitly empty victim list.
            targets = play.evaluate_scalar(instance, node.field('m_FA3ECC43'))
            return True if targets == () else NotImplemented
        if node.pos not in (38, 46):
            return NotImplemented
        if elapsed_ms:
            return True
        config = node.field('m_modifyHealth') or {}
        source = expr.Entity(body.id)
        if (config.get('m_93CDFD2F') != 1 or node.field('m_95F1FB0B') is not None
                or play.evaluate_scalar(instance, config.get('m_instigator')) != source
                or play.evaluate_scalar(instance, config.get('m_724CBA57')) != source):
            return NotImplemented
        target = play.evaluate_scalar(instance, node.field('m_FA3ECC43'))
        if not isinstance(target, expr.Entity):
            return NotImplemented
        victim = self.entities.get(target.id)
        amount = play.evaluate_scalar(instance, config.get('m_amount'))
        if (type(amount) not in (int, float) or not 0 <= amount <= 3.402823466e38
                or self.entities.get(body.id) is not body
                or victim is None or self.entities.get(target.id) is not victim):
            return NotImplemented
        if node.pos == 46:
            if victim is not body or body.self_damage is None or amount <= 0:
                return NotImplemented
            body.self_damage(expr.f32(amount))
        # Node 37 already scales damage by distance; finishing 38 advances the victim loop.
        elif (victim is body or not callable(self.explosion_damage)
                or self.explosion_damage(body.id, target.id, expr.f32(amount)) is not True):
            return NotImplemented
        else:
            state.request_finish()
        component.ensure_valid()
        return True

    def _health_value(self, instance, cfg, target, total):
        instance.component.ensure_valid()
        if (self.entities.get(target.id) is not target
                or type(total) not in (int, float) or not 0 <= total <= 3.402823466e38):
            instance.component.unsupported(instance, "live health value", cfg["$"])
        return expr.f32(total)

    def raycast_provider(self, instance, node):
        if (instance.graph.index == 0x157C and node.pos in (202, 203)
                and self.symmetra_beam_cast is not None):
            return lambda request: self.symmetra_beam_cast(instance, request)
        if (instance.graph.index == 0x01C8 and node.pos in (100, 137)
                and self.roadhog_hook_cast is not None):
            walls_only = node.pos == 137
            return lambda request: self.roadhog_hook_cast(instance.component, request, walls_only=walls_only)
        if (instance.graph.index == 0x01C9 and node.pos in (47, 55, 62, 63)
                and node.server_only and self.roadhog_hook_cast is not None):
            slot = instance.find_var(runtime.INSTANCE, 40, create=False)
            caster = expr.to_entity(slot.value()) if slot is not None else 0
            source = self.script_components.get(caster)
            if source is not None:
                return lambda request: self.roadhog_hook_cast(source, request, walls_only=True)
        return super().raycast_provider(instance, node)

    def physics_explosion(self, state):
        instance, node = state.instance, state.node
        component = instance.component
        body = self.entities.get(component.entity)
        if (self.mode not in JUNKENSTEIN_MODES or body is None
                or self.script_components.get(body.id) is not component or self.roadhog_hook_cast is None):
            return NotImplemented
        source = (instance.graph.index, node.pos)
        if ((source == (0x01C8, 146) and node.server_only and body.hero == 0x02E0000000000040)
                or (source == (0x01C9, 85) and component.state_registries.get('STU_4773DA56'))):
            # This host has static map collision and no dynamic map rigid bodies to receive the impulse.
            return True
        return NotImplemented

    def tracked_targets(self, state):
        if (state.instance.graph.index == 0x2B72 and state.node.pos in (54, 62)
                and self.mode in JUNKENSTEIN_MODES and self.ghost_targets is not None):
            return self.ghost_targets(state)
        if (state.instance.graph.index == 0x006B and state.node.pos in (6, 7, 121)
                and self.mode in JUNKENSTEIN_MODES and self.witch_staff_targets is not None):
            return self.witch_staff_targets(state)
        if ((state.instance.graph.index, state.node.pos) == (0x01C8, 17)
                and self.roadhog_hook_targets is not None):
            return self.roadhog_hook_targets(state)
        if ((state.instance.graph.index, state.node.pos) == (0x01C9, 37)
                and self.roadhog_hook_target is not None and state.node.server_only
                and state.node.field('m_filter.m_84065FD1') == 0x80):
            return ()
        return super().tracked_targets(state)

    def ghost_model_action(self, component, entity):
        body = self.entities.get(entity)
        if (self.mode not in JUNKENSTEIN_MODES or body is None or entity != component.entity
                or body.definition != 0x0400000000003C53
                or self.script_components.get(entity) is not component):
            return NotImplemented
        # Native 8EAEF0 calls 1B049E0(model, false) if a model is present.
        # The simulation has no model component, so take its absent-model exit.
        return True

    def set_ghost_transform(self, component, entity, position, yaw):
        callback = self.update_vengeful_ghost
        if (self.mode not in JUNKENSTEIN_MODES or entity != component.entity
                or self.script_components.get(entity) is not component or callback is None):
            return NotImplemented
        return callback(entity, position, yaw)

    def ghost_chase_target_is_live(self, instance):
        if self.mode not in JUNKENSTEIN_MODES or instance.graph.index != 0x2B72:
            return NotImplemented
        slot = instance.find_var(runtime.INSTANCE, 536, create=False)
        target = expr.to_entity(slot.value()) if slot is not None else 0
        if target not in self.entities:
            return False
        return self._filter_entity(instance, target, instance.graph.node(21).field('m_filter'))

    def velocity_action(self, instance, node):
        if self.mode not in JUNKENSTEIN_MODES or (instance.graph.index, node.pos) != (0x01C9, 60):
            return NotImplemented
        backend = self.property_modifiers(instance.component.entity)
        rows = node.field('m_BC5E91CF') or []
        if type(backend) is not modifiers.NumericModifiers or len(rows) != 1:
            return NotImplemented
        row = rows[0]
        if row.get('$') != 'STU_20E18047' or row.get('m_A9561CA0') != 0 or row.get('m_priority') is not None:
            return NotImplemented
        low = instance.evaluate(row.get('m_8AD739CE'))
        high = instance.evaluate(row.get('m_8213EB22'))
        if low != 0 or high != 1:
            return NotImplemented
        # D35310 recognizes this clamp row. C32860 maps velocity slot 0 to property 32 for one frame.
        frame = (backend.source_frame + 1) & 0xFFFFFFFF
        key = instance.id << 48 | node.pos << 32 | frame
        backend.upsert(modifiers.Record(3, key, frame, 32, 5, low, upper=high))
        backend.retire(3, key, (frame + 1) & 0xFFFFFFFF)
        return True

    def _roadhog_hook_anchor(self, cfg, instance):
        from ow174.game import props

        hardpoint = expr.guid_of((cfg.get('m_hardPoint') or {}).get('m_hardPoint'))
        source = cfg.get('m_entity')
        component = instance.component
        if hardpoint == 0x0DC000000000012D and cfg['$'] == 'STU_C1F83245':
            # The return stacks select the ordinary Roadhog body on the server.
            # The other branch uses the local view entity and has no server pose.
            branches = (source or {}).get('m_configVars') or []
            if [branch.get('$') for branch in branches] != ['STU_9D240539', 'STU_9521A27E', 'STU_B5A0CAF0']:
                return NotImplemented
            entity = component.entity
            offset = (1.1662592, 0.99586946, -0.016212437)  # 03A21 and 0928, bone 0781
        elif hardpoint == 0x0DC0000000000014:
            entity = expr.to_entity(play.evaluate_scalar(instance, source))
            body = self.entities.get(entity)
            if body is None:
                return expr.EVALUATION_FAILED
            # The four defenders' hardpoint 0014 is at the bind transform of bone 0053.
            offset = {
                0x0400000000001424: (0, 1.0662696, -0.0033355807),  # Ana, model 3686
                0x0400000000000141: (0, 1.0401409, -0.002852149),   # Hanzo, model 0A17
                0x04000000000003CF: (0, 1.1146115, -0.002852149),   # Soldier, model 1137
                0x04000000000002A3: (0, 1.1146115, -0.002852149),   # Cassidy, model 093F
            }.get(body.definition)
            if offset is None:
                return NotImplemented
        else:
            return NotImplemented
        body = self.entities.get(entity)
        position = self.position(entity)
        if body is None or position is None:
            return expr.EVALUATION_FAILED
        if body.rotation_reader is not None:
            rotation = body.rotation_reader()
        elif body.movement_reader is not None:
            yaw = body.movement_reader(component.frame).yaw * math.pi / 65536
            rotation = (0.0, math.sin(yaw), 0.0, math.cos(yaw))
        else:
            return NotImplemented
        component.ensure_valid()
        anchor = props.place(offset, tuple(position), (1, 1, 1), rotation)
        return expr.Vec3(*(expr.f32(axis) for axis in anchor))

    def _roadhog_pull_basis(self, cfg, instance):
        from ow174.game import props

        basis = cfg.get('m_64E06CA4') or {}
        if (basis.get('$') != 'STU_0762B33A' or basis.get('m_E26E5E09') is not None
                or cfg.get('m_AEE599AA') != 0 or cfg.get('m_1FDD7084') is not None
                or cfg.get('m_hardPoint') is not None):
            return NotImplemented
        entity = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_entity')))
        body = self.entities.get(entity)
        if body is None or body.rotation_reader is None:
            return expr.EVALUATION_FAILED
        forward = play.evaluate_scalar(instance, basis['m_32B00625'])
        aim = expr._index(play.evaluate_scalar(instance, basis['m_3661BC45']), 0)
        limit = play.evaluate_scalar(instance, basis['m_5023B9B0'])
        if (not isinstance(forward, expr.Vec3) or not isinstance(aim, expr.Vec3)
                or type(limit) not in (int, float)):
            return expr.EVALUATION_FAILED
        # CEB820 takes the captured heading and bounds pitch relative to the caster's forward basis.
        yaw = math.atan2(aim.x, aim.z)
        desired = math.atan2(aim.y, math.hypot(aim.x, aim.z))
        current = math.atan2(forward.y, math.hypot(forward.x, forward.z))
        limit = math.radians(limit)
        pitch = max(desired - limit, min(current, desired + limit))
        direction = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        x, y, z, w = body.rotation_reader()
        local = props.place(direction, (0, 0, 0), (1, 1, 1), (-x, -y, -z, w))
        return expr.Vec3(*(expr.f32(axis) for axis in local))

    def summoner_cast_button(self, state):
        instance, node = state.instance, state.node
        controller = instance.component
        entity = expr.to_entity(state.evaluate(node.field('m_14EDDDA7'), watch=False))
        button = state.evaluate(node.field('m_7E2A700B'), watch=False)
        pressed = state.evaluate(node.field('m_5E1D2E9C'), watch=False)
        if (self.mode not in JUNKENSTEIN_MODES or instance.graph.index != 0x185D
                or button != {59: 37, 61: 20}.get(node.pos) or pressed is not True
                or self.script_components.get(controller.entity) is not controller):
            return NotImplemented
        if entity != self.current_possessions.get(controller.entity) or not expr.valid_entity(entity):
            return True
        body = self.entities.get(entity)
        component = self.script_components.get(entity)
        if (body is None or body.definition != 0x04000000000026F0 or component is None
                or self.linked_controllers.get(entity) != controller.entity):
            return NotImplemented
        if not component.owner_dead and component.fault is None and button not in component.held:
            component.edges([(button, True), (button, False)])
        return True

    def summoner_placement(self, instance):
        component = instance.component
        body = self.entities.get(component.entity)
        if (instance.graph.index != 0x156B or self.mode not in JUNKENSTEIN_MODES
                or body is None or body.definition != 0x04000000000026F0
                or self.script_components.get(body.id) is not component):
            return NotImplemented
        controller = self.linked_controllers.get(body.id)
        if (component.owner_dead or controller not in self.ai_behavior_requests
                or self.current_possessions.get(controller) != body.id):
            return None
        enabled = instance.find_var(runtime.INSTANCE, 17098).value()
        # GameMessage parameters retain the single-item value arrays.
        point = instance.find_var(runtime.INSTANCE, 17096).value()
        axis = instance.find_var(runtime.INSTANCE, 17097).value()
        if isinstance(point, tuple) and len(point) == 1:
            point = point[0]
        if isinstance(axis, tuple) and len(axis) == 1:
            axis = axis[0]
        if (enabled is True and isinstance(point, expr.Vec3) and isinstance(axis, expr.Vec3)
                and all(math.isfinite(value) for value in (*point, *axis)) and math.hypot(axis.x, axis.z)):
            return point
        return None

    def native(self, cfg: dict, instance: runtime.Instance):
        kind = cfg.get("$")
        if kind == "STU_16753B11" and self.map_guid is not None:
            from ow174.game.content import authored_map_modes

            modes = authored_map_modes(self.map_guid)
            if modes is not None:
                value, success = play.evaluate_scalar_result(instance, cfg.get("m_gamemode"))
                mode = play._asset(value) if success else 0
                return mode != 0 and (mode in modes or mode == 0x0230000000000045)
        if (instance is not None and kind in ("STU_2200A66D", "STU_021D01CD")
                and self.entity_directions is not None):
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            entity = expr.to_entity(value)
            if entity not in self.entities:
                return expr.EVALUATION_FAILED
            pair = self.entity_directions.get(entity)
            direction = pair[kind == "STU_021D01CD"] if pair is not None else None
            if direction is not None:
                return direction
        if cfg.get("$") == "STU_A3DEF368":
            mode = self.script_components.get(runtime.GAME_MODE_ENTITY)
            if mode is not None and mode.fault is None:
                return expr.Entity(runtime.GAME_MODE_ENTITY)
        if cfg.get("$") == "STU_C3619A38" and instance is not None:
            value = play.evaluate_scalar(instance, cfg.get("m_F52607FF"), initial=expr.Entity(0))
            difficulty = self.ai_difficulties.get(expr.to_entity(value))
            if difficulty is not None:
                return difficulty
        if (kind == 'STU_D0E0DA18' and instance is not None and instance.graph.index == 0x2B72
                and self.mode in JUNKENSTEIN_MODES):
            direction = play.evaluate_scalar(instance, cfg.get('m_3ABD589C'))
            if isinstance(direction, expr.Vec3):
                # CD29B0 normalizes the direction and returns yaw and pitch in degrees.
                x, y, z = direction
                length = math.sqrt(x*x + y*y + z*z)
                if not length:
                    return expr.Vec3(0, 0, 0)
                pitch = math.asin(max(-1, min(1, -y / length)))
                return expr.Vec3(expr.f32(math.degrees(math.atan2(x, z))),
                                 expr.f32(math.degrees(pitch)), 0)
        if (kind == 'STU_E0A69706' and instance is not None and instance.graph.index == 0x2B72
                and self.mode in JUNKENSTEIN_MODES
                and expr.guid_of((cfg.get('m_displayText') or {}).get('m_displayText')) == 0x0DE0000000012294):
            # 12294.07C is the enUS name authored on the ghost's 2FCD code value.
            return 'Vengeful Ghost'
        if (kind == 'STU_A58C1E63' and instance is not None and instance.graph.index == 0x0B8B
                and self.mode in JUNKENSTEIN_MODES):
            component = instance.component
            body = self.entities.get(component.entity)
            if body is not None and body.definition == 0x04000000000026F0:
                context = self.entity_team_context(body.id, component=component)
                if context is not NotImplemented:
                    target = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_target')))
                    source = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_source')))
                    option = expr.truthy(play.evaluate_scalar(instance, cfg.get('m_AFBBE9C3'), initial=False))
                    source_actor = context.lookup(source)
                    target_actor = context.lookup(target)
                    if source == body.id and not option:
                        if source_actor is None or target_actor is None:
                            return source_actor is not target_actor
                        source_bits = source_actor.filters.bits
                        target_bits = target_actor.filters.bits
                        source_team = entity_team.index_from_filters(source_bits)
                        target_team = entity_team.index_from_filters(target_bits)
                        # C1CF60's ordinary two-team branch for scene-owned bodies.
                        if source_team in (0, 1) and target_team in (0, 1):
                            spectator = bool((source_bits | target_bits) & (1 << 28))
                            return source_team != target_team and not spectator
        if (kind == 'STU_295F1CDE' and instance is not None and instance.graph.index == 0x156C
                and self.mode in JUNKENSTEIN_MODES):
            # CC9A80/C1BBB0 reads the barrier health scale, with a default of one.
            scale = self.ruleset_for(instance.component.entity, 0x254B)
            return 1.0 if scale is None else scale
        if (kind == 'STU_C5148EFE' and instance is not None and instance.graph.index == 0x156B
                and self.mode in JUNKENSTEIN_MODES and cfg == instance.graph.node(89).field('m_value')):
            # The confirmed NPC placement has no attachment entity.
            if instance.evaluate(cfg.get('m_entity')) == expr.Entity(0):
                return instance.evaluate(cfg.get('m_BD631144'))
        if (kind == 'STU_CD7F3617' and instance is not None and instance.graph.index == 0x01CF
                and self.mode in JUNKENSTEIN_MODES and self.junkenstein_body_name is not None
                and instance.evaluate(cfg.get('m_B350E4FD')) is False):
            # CD27C0 resolves the supplied entity and emits a tag-5 display string.
            entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
            name = self.junkenstein_body_name(entity)
            return name if name is not None else expr.EVALUATION_FAILED
        if (kind in ('STU_F1C45C59', 'STU_005D99E6') and instance is not None
                and instance.graph.index == 0x01CF and self.mode in JUNKENSTEIN_MODES
                and cfg.get('m_7BE02F89') is None):
            # CF55D0 and CCBD10 pass the pool selector to component 51.
            selector = cfg.get('m_7E8B666C') or {}
            pool = selector.get('m_7E8B666C')
            if selector.get('$') == 'STU_297E1A30' and pool in (0, 1):
                entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
                if not expr.valid_entity(entity):
                    return 0.0
                target = self.entities.get(entity)
                if target is None:
                    return expr.EVALUATION_FAILED
                if target.definition == 0x04000000000000E4:
                    read = target.health_pool if kind == 'STU_F1C45C59' else target.pool_capacity
                    if read is not None:
                        return self._health_value(instance, cfg, target, read(pool))
        if (kind == 'STU_6EA8533F' and instance is not None and self.mode in JUNKENSTEIN_MODES
                and ((instance.graph.index == 0x0C8C and instance.component.entity == runtime.GAME_MODE_ENTITY)
                     or (instance.graph.index == 0x0CA2 and instance.component.entity in self.hero_contexts))):
            entities = self.session_script_entities(instance)
            if entities is not NotImplemented:
                return entities
        if (kind in ('STU_951CAB4C', 'STU_45EDF12D', 'STU_B96D3A98')
                and instance is not None and instance.graph.index == 0x0CA2
                and instance.component.entity in self.hero_contexts):
            if kind == 'STU_951CAB4C':
                values = play.evaluate_value(instance, cfg.get('m_814F8EC3'))
                if not isinstance(values, tuple):
                    return NotImplemented
                accepted = []
                for value in values:
                    instance.component.consume_work('Mystery Swap filter candidate')
                    with instance.component.parameter_scope({142: value}):
                        if expr.truthy(play.evaluate_scalar(instance, cfg.get('m_condition'))):
                            accepted.append(value)
                return tuple(accepted)
            if kind == 'STU_45EDF12D' and cfg.get('m_key') is None:
                values = play.evaluate_value(instance, cfg.get('m_814F8EC3'))
                value = play.evaluate_scalar(instance, cfg.get('m_value'))
                if isinstance(values, tuple):
                    return expr.contains_value(values, value)
            if kind == 'STU_B96D3A98':
                target = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_entity')))
                # Real defender controllers carry 75 and omit the NPC component 92.
                return target in self.hero_contexts and target in self.script_components
        if (kind == "STU_DAAE1875" and instance is not None and instance.graph.index == 0x180A
                and instance.component.entity == runtime.GAME_MODE_ENTITY and self.mode in JUNKENSTEIN_MODES):
            # 91A861: the server scene has no local presentation owner. The
            # native evaluator still succeeds with a tag-10 invalid entity.
            return expr.Entity(0)
        if kind == "STU_D3685C04" and self.mode in JUNKENSTEIN_MODES:
            # CE5450 reads bit 15 of the local command. The headless
            # encounter has no local command and takes the false path.
            return False
        if (kind == "STU_BEDA850F" and instance is not None and instance.graph.index == 0x180A
                and self.mode in JUNKENSTEIN_MODES):
            from ow174.game.script import rulesets

            # CDD1D0 succeeds when D9D2C0 finds 2548, regardless of its value.
            return rulesets.default(0x2548, 0, self.mode) is not None
        if (kind in ('STU_21A13145', 'STU_F7FD94F9') and self.roadhog_hook_target is not None
                and instance is not None and instance.graph.index == 0x01C9):
            entity = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_entity')))
            target = self.roadhog_hook_target(entity)
            if target is None:
                return expr.EVALUATION_FAILED
            if kind == 'STU_F7FD94F9':
                return expr.f32(target.radius)
            x, y, z = target.feet
            return expr.Vec3(x, expr.f32(y + target.eye), z)
        if (kind == 'STU_588FF314' and instance is not None and instance.graph.index == 0x01C9
                and self.roadhog_hook_target is not None):
            return self._roadhog_pull_basis(cfg, instance)
        if (kind in ('STU_FC99C77E', 'STU_C1F83245') and self.roadhog_hook_cast is not None
                and instance is not None and instance.graph.index == 0x01C8):
            return self._roadhog_hook_anchor(cfg, instance)
        if (kind == 'STU_FC99C77E' and instance is not None and instance.graph.index == 0x2B72
                and self.mode in JUNKENSTEIN_MODES
                and expr.guid_of((cfg.get('m_hardPoint') or {}).get('m_hardPoint')) == 0x0DC0000000000004):
            target = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_entity')))
            binding = self.entities.get(target)
            if binding is None:
                return expr.EVALUATION_FAILED
            if binding.definition in (0x0400000000003C53, 0x0400000000001424, 0x0400000000000141,
                                      0x04000000000003CF, 0x04000000000002A3):
                # Hardpoint 0004 is at the root bind transform in these 104319 models.
                return self.position(target)
        if (kind == 'STU_2200A66D' and instance is not None and instance.graph.index == 0x2B72
                and self.mode in JUNKENSTEIN_MODES):
            from ow174.game import props

            target = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_entity')))
            binding = self.entities.get(target)
            if (target == instance.component.entity and binding is not None
                    and binding.definition == 0x0400000000003C53 and binding.rotation_reader is not None):
                return expr.Vec3(*props.place((0, 0, 1), (0, 0, 0), (1, 1, 1), binding.rotation_reader()))
        source_hero = None
        if kind in ("STU_21A13145", "STU_2200A66D", "STU_66F3B89E") and instance is not None:
            if instance.graph.index == 0x01C8 and self.roadhog_hook_cast is not None:
                source_hero = 0x02E0000000000040
            elif instance.graph.index in (0x156B, 0x157C) and self.symmetra_beam_cast is not None:
                source_hero = 0x02E0000000000016
        if source_hero is not None:
            from ow174.game import combat

            component = instance.component
            entity = expr.to_entity(play.evaluate_scalar(instance, cfg.get('m_entity')))
            body = self.entities.get(entity)
            if (entity == component.entity and body is not None and body.hero == source_hero
                    and body.movement_reader is not None and self.script_components.get(entity) is component):
                if kind != "STU_21A13145":
                    pose = body.movement_reader(component.frame)
                    if kind == "STU_2200A66D":
                        return expr.Vec3(*combat.aim(pose.yaw, pose.pitch))
                    return expr.Vec3(*combat.aim(pose.yaw + 16384, 0))
                x, y, z = self.position(entity)
                return expr.Vec3(x, y + combat.EYE, z)
        if (kind in {"STU_7BE6D76D", "STU_CC53F52B", "STU_40AC5AE4"} and instance is not None
                and self.mode in JUNKENSTEIN_MODES):
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
            if target is None:
                return expr.EVALUATION_FAILED
            if target.movement_reader is not None:
                pose = target.movement_reader(instance.component.frame)
                instance.component.ensure_valid()
                axis = "yaw" if kind == "STU_CC53F52B" else "pitch"
                units = getattr(pose, axis, None)
                if (self.entities.get(target.id) is target
                        and type(units) is int and -32768 <= units <= 32767):
                    radians = expr.f32(units * expr.f32(math.tau / 65536))
                    return expr.f32(radians * expr.f32(180 / math.pi))
        if (cfg.get("$") == "STU_ED45C5E8" and instance is not None
                and instance.graph.index in (0x00CB, 0x14B1) and self.mode in JUNKENSTEIN_MODES
                and callable(self.junkenstein_voice_recipients)):
            source = play.evaluate_scalar(instance, cfg.get("m_B2ABF4FF"))
            flags = tuple(play.evaluate_scalar(instance, cfg.get(key))
                          for key in ("m_5912C8DF", "m_C9E6F46B", "m_5A72EEF5"))
            radius = play.evaluate_scalar(instance, cfg.get("m_2BA6230F"))
            component = instance.component
            if (source == expr.Entity(component.entity) and all(flag is False for flag in flags)
                    and radius == 0.0 and self.script_components.get(source.id) is component):
                return self.junkenstein_voice_recipients(instance)
        if (cfg.get("$") == "STU_4049CF77" and cfg.get("m_C5D0FA56") is None
                and instance is not None and self.mode in JUNKENSTEIN_MODES
                and self.junkenstein_difficulty is not None):
            component = instance.component
            body = self.entities.get(component.entity)
            controller = self.entities.get(self.linked_controllers.get(component.entity))
            selector = cfg.get("m_F00C2F3D")
            if (body is not None and controller is not None
                    and self.script_components.get(body.id) is component
                    and self.current_possessions.get(controller.id) == body.id
                    and isinstance(selector, dict) and selector.get("$") == "STUConfigVarIdentifier"
                    and selector.get("m_resourceKey") is None):
                weapon = (instance.graph.index, body.definition, controller.definition)
                profile = JUNKENSTEIN_WEAPON_DAMAGE.get(weapon)
                tier = JUNKENSTEIN_DIFFICULTY_INDEX.get(self.junkenstein_difficulty.guid)
                if profile is not None and tier is not None:
                    damage = profile.get(expr.guid_of(selector.get("m_identifier")))
                    if damage is not None:
                        return damage[tier]
        if (kind == "STU_4049CF77" and instance is not None and instance.graph.index == 0x0079
                and self.mode in JUNKENSTEIN_MODES and self.junkenstein_difficulty is not None):
            source = play.evaluate_scalar(instance, cfg.get('m_C5D0FA56'))
            selector = cfg.get('m_F00C2F3D') or {}
            if (source == expr.Entity(instance.source_entity())
                    and expr.guid_of(selector.get('m_identifier')) == 0x0D80000000002162):
                controller = self.entities.get(self.linked_controllers.get(source.id))
                tier = JUNKENSTEIN_DIFFICULTY_INDEX.get(self.junkenstein_difficulty.guid)
                if (controller is not None and controller.definition == 0x040000000000172E
                        and self.current_possessions.get(controller.id) == source.id and tier is not None):
                    return (1.0, 2.0, 3.0, 4.0)[tier]
        if (cfg.get("$") == "STU_4049CF77" and cfg.get("m_C5D0FA56") is None
                and instance is not None and instance.graph.index == 0x157C
                and self.mode in JUNKENSTEIN_MODES):
            # The PC registry uses base ConfigVar 155. Its +20 returns false without a write.
            return expr.EVALUATION_FAILED
        if cfg.get("$") == "STU_967463AB" and instance is not None:
            # CCF2D0/D24A20/C6CDA0 count active deployables of the requested definition.
            definition = play.evaluate_scalar(instance, cfg.get("m_C8655030"))
            value = play.evaluate_scalar(instance, cfg.get("m_92D12BE6"))
            owner = expr.to_entity(value) & 0xFFFFFFFF
            if not isinstance(definition, expr.Asset) or not definition.guid or not expr.valid_entity(owner):
                return expr.EVALUATION_FAILED
            if owner not in self.entities:
                return 0
            if owner in self._owned_deployables:
                return sum(self.entities[child].definition == definition.guid
                           for child in self._owned_deployables[owner])
        if cfg.get('$') == 'STU_F76C486C' and instance is not None:
            # CE2CF0/D1BEE0 returns a typed zero for a bound entity without component 72.
            context = self.entity_query_context(instance.component.entity)
            if context is not NotImplemented and context.namespace_present and not context.retired:
                value = play.evaluate_scalar(instance, cfg.get('m_D63CBEC2'), initial=expr.Entity(0))
                instance.component.ensure_valid()
                target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
                if target is not None and target.pet_component_absent:
                    return expr.Entity(0)
                if (target is not None and target.definition == 0x04000000000026B0
                        and self.mode in JUNKENSTEIN_MODES):
                    owner = next((entity for entity, pets in self._owned_deployables.items()
                                  if target.id in pets), 0)
                    return expr.Entity(owner)
        if cfg.get("$") == "STU_A77AC2AC" and self.mode:
            # CE8580 compares the scene's mode resource with Tutorial.
            return self.mode == TUTORIAL_MODE
        if cfg.get("$") == "STU_37F20D24" and self.mode:
            # CE1400 returns the active mode resource, as in BodyWorld.
            return expr.Asset(self.mode)
        if (cfg.get("$") in ("STU_4FC9D3E1", "STU_7DCA538E")
                and instance is not None and instance.graph.index == 0x0C83
                and self.mode in JUNKENSTEIN_MODES and cfg.get("m_resourceKey") is None):
            target = cfg.get("m_00903303")
            if isinstance(target, dict) and target.get("$") == "STU_B5A0CAF0":
                # The captured PC registry resolves these operands to base ConfigVar.
                # Its failed evaluation leaves the authored expression to handle the empty value.
                return expr.EVALUATION_FAILED
        if (cfg.get("$") == "STU_ABB6C23E" and cfg.get("m_resourceKey") is None
                and instance is not None and instance.graph.index == 0x0BA4
                and self.mode in JUNKENSTEIN_MODES and self.player_stats_available(instance) is False):
            # The 0BA4 objective-stat gate has no player record for this NPC.
            # Match the typed empty identifier from 104319's CEB0E0 evaluator in this branch.
            return expr.Asset(0)
        if cfg.get("$") == "STU_FDBB0B67" and instance is not None:
            # CEC030/D1E970 read the selected entity's effective scale from its mover or transform.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
            if target is None:
                return expr.EVALUATION_FAILED
            if target.scale_reader is not None:
                scale = target.scale_reader()
                instance.component.ensure_valid()
                if (self.entities.get(target.id) is target and type(scale) in (int, float)
                        and 0 < scale <= 3.402823466e38):
                    return expr.f32(scale)
        if cfg.get("$") == "STU_D3EB8D87" and instance is not None:
            # CDB700 tests for the selected entity's component 92, not its movement state.
            value = play.evaluate_scalar(instance, cfg.get("m_00903303"), initial=expr.Entity(0))
            instance.component.ensure_valid()
            target = expr.to_entity(value) & 0xFFFFFFFF
            if target not in self.entities:
                return False
            present = self.entity_component92_present(target)
            if present is not None:
                return present
        if cfg.get("$") == "STU_AC829876" and isinstance(cfg.get("m_filter"), dict):
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            result = self._filter_entity(instance, expr.to_entity(value) & 0xFFFFFFFF, cfg["m_filter"])
            if result is not NotImplemented:
                return result
        if cfg.get('$') == 'STU_A753DC79' and self.engine_settings is not None:
            # CF4A60 dispatches the authored hash as uint32 to a typed getter.
            key = cfg.get('m_74A2248F')
            if type(key) is int:
                return self.engine_settings.get(key & 0xFFFFFFFF, expr.EVALUATION_FAILED)
        if cfg.get('$') == 'STU_D289FA32' and self.team_member_count is not None:
            team = instance.evaluate(cfg.get('m_team'))
            if type(team) is int and 0 <= team < 5:
                return self.team_member_count(team)
        if cfg.get("$") in ("STU_783B4CF0", "STU_68883EAC") and cfg.get("m_7BE02F89") is None:
            # CDFCC0 and CE5640 pass the same selector to distinct health component getters.
            pool = play.evaluate_scalar(instance, cfg.get("m_7E8B666C"))
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            entity = expr.to_entity(value) & 0xFFFFFFFF
            target = self.entities.get(entity)
            if target is None:
                return expr.EVALUATION_FAILED if expr.valid_entity(entity) else 0.0
            reader = target.health_pool if cfg["$"] == "STU_783B4CF0" else target.pool_capacity
            if reader is not None and type(pool) is int:
                return self._health_value(instance, cfg, target, reader(pool))
            return super().native(cfg, instance)
        if cfg.get("$") == "STU_F7262B36":
            # CDBDE0/D15520 resolve the body's component 43 before returning the controller.
            value = play.evaluate_scalar(instance, cfg.get("m_801B298F"), initial=expr.Entity(0))
            body = expr.to_entity(value) & 0xFFFFFFFF
            if body not in self.entities:
                return expr.EVALUATION_FAILED
            if body not in self.linked_controllers:
                return super().native(cfg, instance)
            controller = self.linked_controllers[body]
            return expr.Entity(controller) if controller is not None else expr.EVALUATION_FAILED
        if cfg.get("$") == "STU_1ED4F039" and instance is not None:
            # CD2370 reads GMP stats on the body-linked controller. Missing stats leave the output alone.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            instance.component.ensure_valid()
            active = self._linked_on_fire(instance, expr.to_entity(value) & 0xFFFFFFFF)
            if active is not NotImplemented:
                return expr.EVALUATION_FAILED if active is None else active
        if cfg.get("$") in {"STU_021D01CD", "STU_C8C4C20D"}:
            # CCDD80 reads the transform's forward basis. CF3510 reports its heading.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
            if target is None:
                return expr.EVALUATION_FAILED
            if target.rotation_reader is not None:
                rotation = target.rotation_reader()
            elif target.placement is not None:
                rotation = target.placement.source.get("rotation")
            else:
                return super().native(cfg, instance)
            if (not isinstance(rotation, (list, tuple)) or len(rotation) != 4
                    or any(type(axis) not in (int, float) or not -1.0001 <= axis <= 1.0001
                           for axis in rotation)):
                return super().native(cfg, instance)
            x, y, z, w = rotation
            forward_x = expr.f32(2 * (x * z + w * y))
            forward_z = expr.f32(1 - 2 * (x * x + y * y))
            if cfg["$"] == "STU_021D01CD":
                return expr.Vec3(forward_x, expr.f32(2 * (y * z - w * x)), forward_z)
            if forward_x == 0 and forward_z <= 0:
                return 0.0
            return expr.f32(expr.f32(math.atan2(forward_x, forward_z)) * expr.f32(180 / math.pi))
        if cfg.get("$") == "STU_9B7648C3":
            # CEB6C0 returns the health total as float32, or a failed evaluation for a missing body.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
            if target is None:
                return expr.EVALUATION_FAILED
            if target.health is None:
                return super().native(cfg, instance)
            return self._health_value(instance, cfg, target, target.health())
        if cfg.get("$") in ("STU_60146072", "STU_32E6E4D8", "STU_7D7D2470"):
            # D17AE0 calls +50 for all active pool maxima. D18110/D17E00 use the normal-pool getters.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            entity = expr.to_entity(value) & 0xFFFFFFFF
            if not expr.valid_entity(entity):
                return 0.0
            target = self.entities.get(entity)
            if target is None:
                return expr.EVALUATION_FAILED
            if cfg["$"] == "STU_60146072":
                if target.maximum_health is not None:
                    return self._health_value(instance, cfg, target, target.maximum_health())
            elif target.normal_health is not None:
                amount = target.normal_health(cfg["$"] == "STU_7D7D2470")
                return self._health_value(instance, cfg, target, amount)
        if cfg.get("$") == "STU_25ECB55F":
            # CE5890 queries the selected entity's target-tag component.
            tag = graph.guid(cfg.get("m_88B0050A"))
            if tag is not None and tag >> 48 == 0x0D60 and tag & 0xFFFFFFFFFFFF:
                value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
                target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
                if target is None:
                    return False
                present = self._has_target_tag(target, tag)
                if present is not NotImplemented:
                    return present
            return super().native(cfg, instance)
        if cfg.get("$") == "STU_309B0570" and cfg.get("m_5B94EDB8") is None:
            # CF28B0/D1FD80 use the default mover-velocity path.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
            if target is None:
                return expr.EVALUATION_FAILED
            if target.velocity is not None:
                try:
                    velocity = _position(target.velocity())
                except (OverflowError, ValueError):
                    instance.component.unsupported(instance, "live body velocity", cfg["$"])
                instance.component.ensure_valid()
                return velocity if self.entities.get(target.id) is target else expr.EVALUATION_FAILED
        if cfg.get("$") == "STU_93DCDDFD":
            # CDF220 reads the hero resource of the selected body.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            entity = expr.to_entity(value) & 0xFFFFFFFF
            if instance.graph.index == 0x0CA2 and entity in self.hero_contexts:
                entity = self.current_possessions.get(entity, entity)
            target = self.entities.get(entity)
            if target is None:
                return expr.EVALUATION_FAILED
            if target.hero is not None:
                return expr.Asset(target.hero)
            return super().native(cfg, instance)
        if cfg.get("$") == "STU_1A3FA5B0":
            # CC99A0 writes the selected entity's definition as an asset value.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            target = self.entities.get(expr.to_entity(value) & 0xFFFFFFFF)
            if target is None:
                return expr.Asset(0)
            if target.definition is not None:
                return expr.Asset(target.definition)
            return super().native(cfg, instance)
        if cfg.get("$") == "STU_B3E67A36":
            if (instance.graph.index == 0x2B72 and self.mode in JUNKENSTEIN_MODES
                    and all(cfg.get(key) is None for key in ('m_filter', 'm_B486603E', 'm_8B2A4DF3'))):
                tag = play.evaluate_scalar(instance, cfg.get('m_9C7BD8B8'))
                if tag == expr.Asset(0x0D60000000000A79):
                    targets = self.targets_with_tag(instance, tag.guid)
                    if targets is not NotImplemented:
                        return targets[0] if targets else expr.Entity(0)
            if (instance.graph.index == 0x0CC0 and self.mode in JUNKENSTEIN_MODES
                    and all(cfg.get(key) is None for key in ('m_filter', 'm_9C7BD8B8', 'm_8B2A4DF3'))):
                tag = play.evaluate_scalar(instance, cfg.get('m_B486603E'))
                if isinstance(tag, expr.Asset) and tag.guid in (
                    0x0D600000000000FA, 0x0D600000000000FF, 0x0D6000000000011E, 0x0D60000000000120,
                ):
                    targets = self.targets_with_tag(instance, tag.guid)
                    if targets is not NotImplemented:
                        return targets[0] if targets else expr.Entity(0)
            if instance.graph.index == 0x0071 and self.mode in JUNKENSTEIN_MODES:
                filter_config = cfg.get("m_filter")
                if (isinstance(filter_config, dict) and filter_config.get('m_66747E7D') == 1
                        and filter_config.get('m_89831602') == 4
                        and all(cfg.get(key) is None for key in ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3'))):
                    target = self._first_bound_combat_target(instance, filter_config)
                    if target is not NotImplemented:
                        return target
            if instance.graph.index == 0x0CCD and self.mode in JUNKENSTEIN_MODES:
                filter_config = cfg.get("m_filter")
                tag = cfg.get("m_9C7BD8B8")
                if (isinstance(filter_config, dict) and isinstance(tag, dict)
                        and cfg.get("m_B486603E") is None and cfg.get("m_8B2A4DF3") is None
                        and filter_config.get("m_9C7BD8B8") is None):
                    # The authored branch only tests whether a live tagged combat body exists.
                    target = self._first_bound_combat_target(instance, {**filter_config, "m_9C7BD8B8": tag})
                    if target is not NotImplemented:
                        return target
            target = self.evaluate_static_target_tag(cfg.get, instance.evaluate)
            return super().native(cfg, instance) if target is NotImplemented else target
        if cfg.get("$") == "STU_71A87E92":
            # CCC750 distinguishes a bound entity at the origin from a zero vector fallback.
            value = play.evaluate_scalar(instance, cfg.get("m_entity"))
            entity = expr.to_entity(value) & 0xFFFFFFFF
            if entity in self.entities:
                position = self.position(entity)
                return super().native(cfg, instance) if position is None else position
            if isinstance(value, expr.Vec3) and value != expr.Vec3(0, 0, 0):
                return value
            return expr.EVALUATION_FAILED
        if cfg.get("$") == "STU_DB742151" and self.possessors is not None:
            value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0))
            return expr.Entity(self.primary_possessor(expr.to_entity(value)))
        if cfg.get("$") == "STU_00707E1E" and self.possessors is not None:
            value = play.evaluate_scalar(instance, cfg.get("m_00903303"), initial=expr.Entity(0))
            entity = expr.to_entity(value)
            if entity not in self.possessors:
                return expr.Entity(0)
            if entity in self.component44_body_ids:
                return expr.Entity(self.component44_body_ids[entity])
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
                 server_placeables=(), hero: int = 0, mode: int = 0, entity_creator=None,
                 hero_creator=None) -> "SceneWorld":
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
        creators = {}
        if entity_creator is not None:
            creators['entity_creator'] = entity_creator
        if hero_creator is not None:
            creators['hero_creator'] = hero_creator
        world = cls(hero, mode, **creators)
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
