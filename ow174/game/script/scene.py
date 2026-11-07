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

from ow174.game.script import expr, graph, runtime

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
    World's strict diagnostics, including dynamic script routing and relations.
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
        record = self.entities.pop(entity, None)
        if record is not None and record.reference.int:
            del self.references[record.reference]
        return record

    def position(self, entity: int):
        record = self.entities.get(entity)
        return record.position if record is not None else None

    def entity_var(self, entity: int, var: int):
        if entity == runtime.GAME_MODE_ENTITY and runtime.mode_variables(self.mode) is not None:
            return super().entity_var(entity, var)
        # Catalog bindings do not activate the entity's script component.
        return NotImplemented

    def native(self, cfg: dict, instance: runtime.Instance):
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
