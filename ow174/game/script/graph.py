"""The statescript graphs the game server runs, from data/game_graphs_174.json.gz (made by
tools/extract_game_graphs.py from DataTool's XML dump of 1.74).

The file is gzip JSON:
- "graphs": one record per 01B graph: "index" (the graph's 16-bit index); "header" (its scalar fields:
  m_nodesBitCount, m_statesBitCount, m_remoteSyncNodesBitCount, m_syncVarsBitCount, m_predictionBehavior,
  ...); "fields" (its other fields: m_publicSchema, m_9CEC6985, m_680A2CB2, ...); "items" (the length of
  m_graph.m_items); "containers" (item position -> class of the editor's subgraph boxes); "nodes";
  "unknown_nodes" (m_nodes indexes DataTool could not read); "states", "entries", "remote" (m_states,
  m_entries and m_remoteSyncNodes as node positions, None for an entry DataTool could not read);
  "sync_vars" ([variable id or None, scope, m_AC9480C7] per m_syncVars entry).
- A node: "pos" (its position in m_items, the key links and lists use), "class" (the STU class, STU_<hash>
  when unnamed), "uid" (m_uniqueID), "rid" (m_871A8203, its index in m_nodes), "remote" (m_602E1F8F, -1 =
  none), "state" (m_26B6454C, None for a node that is not a state), "client_only", "server_only" and
  "fields": every other field. The editor's layout (positions, comments, names, colours) is left out.
- Values: None, numbers (enum fields hold their numbers), lists, an asset GUID as "0x" and 16 hex digits
  (the full 64-bit key, type bits included), {"node": position}, {"item": position} (a subgraph box) and
  objects {"$": class, field: value, ...}. A plug is an object with "links": [node position, the input
  plug's field path in that node] per link, in the order the client follows them; the position is None
  when the target node is one DataTool could not read.
- "bodies": per hero body (the 003 GUID): its statescript component's initial graphs in definition order
  (graph index and schema overrides m_1EB5A024) and its weapon component's manager and weapon scripts.
- "entities" (optional): additional body records with the same graph fields. An "ai_weapon_buttons" map
  can link decimal fire-control slots to logical buttons from the associated AI definition.
- "junkenstein" (optional): encounter-specific entity records and their hero/body/controller pairings.
- "mode_variables" (optional): per 0C5 mode GUID, its explicit mode entity values by 01C identifier index.
- "mode_scripts" (optional): per 0C5 mode GUID, its primary/shared graphs, loading flag and team scripts.
- "mission_scripts" (optional): per map and mode GUID, a configured mission graph with its mode graph,
  source asset and schema overrides. These bindings come from external map/mission inputs.
- "map_scripts" (optional): per map GUID, placed statescript component instances in entity order,
  including graph order and schema overrides. This is not entity-definition defaults or transforms.
- "missing": graph indexes referenced but not in the dump.

Which states a frame carries (read in the client at 0x7FF78991BE30):
- an owner (H=1) frame has one entry per m_states entry that is neither client-only nor server-only;
- an H=0 frame has one per m_remoteSyncNodes entry that is a state (the client casts it to the state base;
  actions fail) and neither client-only nor server-only.
The per-instance variable list has a presence bit per m_syncVars entry that has an identifier and the
instance scope (0x7FF78991C270).
"""

import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

from ow174.game.script.expr import Asset
from ow174.paths import DATA_DIR

_graph_bundle = os.environ.get("ORPHEUS_GRAPH_BUNDLE")
GRAPHS_PATH = Path(_graph_bundle).resolve() if _graph_bundle else DATA_DIR / "game_graphs_174.json.gz"
_supplement_path = os.environ.get("ORPHEUS_GRAPH_SUPPLEMENT")
GRAPH_SUPPLEMENT_PATH = Path(_supplement_path).resolve() if _supplement_path else None
INSTANCE, ENTITY = 0, 1  # variable scopes: sync var m_56341592, STUConfigVarDynamic.m_60DB8F99
GUID_TEXT = re.compile(r"0x[0-9A-F]{16}$")
GRAPH_TYPE = 0x0580  # a 01B GUID's type bits, bits 48..63
PATH_PART = re.compile(r"(\w+)((?:\[\d+\])*)")


def guid(value) -> int | None:
    """An asset GUID of the data as an int, None for any other value."""
    if isinstance(value, str) and GUID_TEXT.match(value):
        return int(value, 16)
    return None


def is_plug(value) -> bool:
    return isinstance(value, dict) and "$" in value and "links" in value


@lru_cache(maxsize=1024)
def _field_path(path: str) -> tuple[tuple[str, tuple[int, ...]], ...] | None:
    steps = []
    for part in path.split("."):
        match = PATH_PART.fullmatch(part)
        if match is None:
            return None
        indexes = tuple(int(index) for index in re.findall(r"\[(\d+)\]", match[2]))
        steps.append((match[1], indexes))
    return tuple(steps)


def field_at(fields: dict, path: str):
    """The value at a field path such as "m_onBeginPlug", "m_transitionPlug[0]" or "m_45216F79.m_D44EB181";
    None when it is not there."""
    steps = _field_path(path)
    if steps is None:
        return None
    value = fields
    for name, indexes in steps:
        if not isinstance(value, dict):
            return None
        value = value.get(name)
        for index in indexes:
            if not isinstance(value, list) or index >= len(value):
                return None
            value = value[index]
    return value


def _plugs(value, path: str, out: dict) -> None:
    if is_plug(value):
        out[path] = value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "$":
                _plugs(item, f"{path}.{key}" if path else key, out)
    elif isinstance(value, list):
        for number, item in enumerate(value):
            _plugs(item, f"{path}[{number}]", out)


@dataclass(frozen=True)
class SyncVar:
    index: int  # the entry's position in m_syncVars
    var: int | None  # the variable id (the .01C identifier's index), None for padding
    scope: int  # INSTANCE or ENTITY
    flag: int  # m_AC9480C7

    @property
    def has_presence_bit(self) -> bool:
        return self.var is not None and self.scope == INSTANCE


class Node:
    """One node of a graph. `fields` holds every field the file keeps, in the generic form above."""

    def __init__(self, record: dict) -> None:
        self.pos: int = record["pos"]
        self.cls: str = record["class"]
        self.uid: int = record["uid"]
        self.rid: int = record["rid"]
        self.remote: int = record["remote"]
        self.state: int | None = record["state"]
        self.client_only = bool(record["client_only"])
        self.server_only = bool(record["server_only"])
        self.fields: dict = record["fields"]

    def __repr__(self) -> str:
        return f"<Node pos {self.pos} rid {self.rid} {self.cls}>"

    @property
    def is_state(self) -> bool:
        return bool(self.fields.get("m_2BBEEAB8"))

    @property
    def is_action(self) -> bool:
        return bool(self.fields.get("m_ADEB6E05"))

    @property
    def is_networked(self) -> bool:
        """Neither client-only nor server-only: the kind of state a frame carries."""
        return not self.client_only and not self.server_only

    def field(self, path: str):
        return field_at(self.fields, path)

    def plugs(self) -> dict[str, dict]:
        """Every plug of the node by field path."""
        out = {}
        _plugs(self.fields, "", out)
        return out

    def links(self, path: str) -> list[tuple[int | None, str]]:
        """(target node position, input plug path) of a plug's links, in order; [] for no such plug."""
        plug = self.field(path)
        return [tuple(link) for link in plug["links"]] if is_plug(plug) else []


class Graph:
    """One 01B graph."""

    def __init__(self, record: dict) -> None:
        self.index: int = record["index"]
        self.header: dict = record["header"]
        self.fields: dict = record["fields"]
        self.items: int = record["items"]
        self.containers = {int(position): cls for position, cls in record["containers"].items()}
        self.nodes = [Node(node) for node in record["nodes"]]
        self.unknown_rids: list[int] = record["unknown_nodes"]
        self._by_pos = {node.pos: node for node in self.nodes}
        self._by_rid = {node.rid: node for node in self.nodes}
        self.states = self._list(record["states"])
        self.entries = self._list(record["entries"])
        self.remote_nodes = self._list(record["remote"])
        self.sync_vars = [SyncVar(number, *entry) for number, entry in enumerate(record["sync_vars"])]
        self._owner = [node for node in self.states if node is not None and node.is_networked]
        self._remote = [
            node for node in self.remote_nodes if node is not None and node.is_state and node.is_networked
        ]
        self._presence = [entry for entry in self.sync_vars if entry.has_presence_bit]

    def __repr__(self) -> str:
        return f"<Graph {self.index:04X}: {len(self.nodes)} nodes>"

    def _list(self, positions: list) -> list:
        return [self._by_pos.get(position) if position is not None else None for position in positions]

    @property
    def nodes_bits(self) -> int:
        return self.header["m_nodesBitCount"]

    @property
    def states_bits(self) -> int:
        return self.header["m_statesBitCount"]

    @property
    def remote_bits(self) -> int:
        return self.header["m_remoteSyncNodesBitCount"]

    @property
    def sync_vars_bits(self) -> int:
        return self.header["m_syncVarsBitCount"]

    def node(self, position: int) -> Node | None:
        """The node at a position in m_items (what links, lists and {"node": ...} values name)."""
        return self._by_pos.get(position)

    def by_rid(self, rid: int) -> Node | None:
        """The node with an m_nodes index (what event lists name in an owner frame)."""
        return self._by_rid.get(rid)

    def state(self, index: int) -> Node | None:
        """The node of an m_states index (a descriptor's PSIn, an owner event's state)."""
        return self.states[index] if 0 <= index < len(self.states) else None

    def remote_node(self, index: int) -> Node | None:
        """The node of an m_remoteSyncNodes index (what H=0 event lists name)."""
        return self.remote_nodes[index] if 0 <= index < len(self.remote_nodes) else None

    def ref(self, value) -> Node | None:
        """The node a {"node": position} value names."""
        return self.node(value["node"]) if isinstance(value, dict) and "node" in value else None

    def owner_states(self) -> list[Node]:
        """The states of an owner (H=1) frame, in order: m_states minus client-only and server-only. An
        entry DataTool could not read counts as client-only (the only one in the data, 0D24 st75, sits
        under a client-only UI presenter)."""
        return self._owner

    def remote_states(self) -> list[Node]:
        """The states of an H=0 frame, in order: m_remoteSyncNodes entries that are states and neither
        client-only nor server-only."""
        return self._remote

    def presence_vars(self) -> list[SyncVar]:
        """The m_syncVars entries that have a presence bit in a full frame's per-instance list, in order."""
        return self._presence

    def owner_slot(self, state: int) -> int | None:
        """The bit position of an m_states index in an owner frame, None when it has none."""
        node = self.state(state)
        slots = self.owner_states()
        return slots.index(node) if node in slots else None

    def remote_slot(self, remote: int) -> int | None:
        """The bit position of an m_remoteSyncNodes index in an H=0 frame, None when it has none."""
        node = self.remote_node(remote)
        slots = self.remote_states()
        return slots.index(node) if node in slots else None

    def presence_bit(self, var: int) -> int | None:
        """The presence bit of an instance variable, None when the graph does not sync it."""
        for bit, entry in enumerate(self.presence_vars()):
            if entry.var == var:
                return bit
        return None

    def graph_refs(self) -> set[int]:
        """The indexes of the graphs this graph names (sub-scripts, play-script targets, schemas)."""
        found = set()
        _collect_graphs([self.fields, *[node.fields for node in self.nodes]], found)
        return found


def _collect_graphs(value, found: set) -> None:
    number = guid(value)
    if number is not None and number >> 48 == GRAPH_TYPE:
        found.add(number & 0xFFFFFFFFFFFF)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_graphs(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_graphs(item, found)


@cache
def _data() -> dict:
    data = json.loads(gzip.decompress(GRAPHS_PATH.read_bytes()))
    if GRAPH_SUPPLEMENT_PATH is not None:
        extra = json.loads(gzip.decompress(GRAPH_SUPPLEMENT_PATH.read_bytes()))
        data = add_supplement(data, extra)
    return data


def load_bundle(path: Path, extension=None) -> dict:
    """Read one explicit base plus an optional non-replacing fingerprint-bound extension."""
    raw = path.read_bytes()
    data = json.loads(gzip.decompress(raw))
    if extension:
        extra = json.loads(gzip.decompress(Path(extension).read_bytes()))
        if (extra.get("format_version") != 1
                or extra.get("base_sha256") != hashlib.sha256(raw).hexdigest()):
            raise ValueError("graph extension does not match the selected base bundle")
        present = {record["index"] for record in data["graphs"]}
        additions = extra.get("graphs")
        entities = extra.get("entities")
        if not isinstance(additions, list) or not isinstance(entities, dict):
            raise ValueError("invalid graph extension tables")
        for record in additions:
            index = record["index"]
            if type(index) is not int or not 0 <= index <= 0xFFFF or index in present:
                raise ValueError("graph extension repeats or replaces a graph")
            present.add(index)
        current = data.setdefault("entities", {})
        if any(key in current for key in entities):
            raise ValueError("graph extension replaces an entity definition")
        references = set()
        _collect_graphs([additions, entities], references)
        for entity in entities.values():
            references.update(row["graph"] for row in entity.get("graphs", ()))
        if not references <= present:
            raise ValueError("graph extension has unresolved graph references")
        data["graphs"].extend(additions)
        current.update(entities)
        controllers = data.setdefault("npc_controllers", {})
        additions = extra.get("npc_controllers", {})
        if (not isinstance(controllers, dict) or not isinstance(additions, dict)
                or any(key in controllers for key in additions)):
            raise ValueError("graph extension replaces NPC controller associations")
        for body, association in additions.items():
            definition = association.get("definition") if isinstance(association, dict) else None
            if (not isinstance(body, str) or not GUID_TEXT.fullmatch(body)
                    or int(body, 16) >> 48 != 0x0400 or not int(body, 16) & 0xFFFFFFFFFFFF
                    or body not in (data.get("bodies", {}) | current)
                    or not isinstance(definition, str) or not GUID_TEXT.fullmatch(definition)
                    or int(definition, 16) >> 48 != 0x0400 or definition == body
                    or definition not in current):
                raise ValueError("unresolved NPC controller definition")
        controllers.update(additions)
        data["missing"] = [index for index in data.get("missing", ()) if index not in present]
    return data


def add_supplement(data: dict, extra: dict) -> dict:
    """Add prepared entity/graph/effect inputs without overriding base data.

    Supplements live outside the read-only runtime pack. Conflicting definitions
    are errors rather than silent changes to already accepted mode/hero inputs.
    """
    if (not isinstance(extra, dict) or type(extra.get("version")) is not int or extra["version"] != 1
            or set(extra) - {"version", "graphs", "entities", "effect_scripts", "deployables", "npc_controllers"}):
        raise ValueError("graph supplement requires version1 and only graphs/entities/effect_scripts/deployables/npc_controllers")
    graphs = {record["index"]: record for record in data["graphs"]}
    supplied = set()
    for record in extra.get("graphs", []):
        index = record["index"]
        if type(index) is not int or not 0 <= index <= 0xFFFF or index in supplied:
            raise ValueError("invalid or duplicate supplemental graph index")
        supplied.add(index)
        if index in graphs and graphs[index] != record:
            raise ValueError(f"supplement conflicts with graph {index:04X}")
        graphs[index] = record
    entities = dict(data.get("entities") or {})
    for key, record in extra.get("entities", {}).items():
        identifier = guid(key)
        if identifier is None or identifier >> 48 != 0x0400 or not isinstance(record, dict):
            raise ValueError("supplement requires full003 entity definitions")
        if key in entities and entities[key] != record:
            raise ValueError(f"supplement conflicts with entity {key}")
        entities[key] = record
    result = {**data, "graphs": list(graphs.values()), "entities": entities}
    if "effect_scripts" in extra:
        supplied_effects = extra["effect_scripts"]
        if not isinstance(supplied_effects, dict):
            raise ValueError("supplement effect_scripts must be a mapping")
        effects = dict(data.get("effect_scripts") or {})
        for key, record in supplied_effects.items():
            identifier = guid(key)
            if (identifier is None or identifier >> 48 != 0x04A0 or not isinstance(record, dict)
                    or type(record.get("graph")) is not int or record["graph"] not in graphs):
                raise ValueError("supplement requires full04A effect bindings to supplied graphs")
            if key in effects and effects[key] != record:
                raise ValueError(f"supplement conflicts with effect {key}")
            effects[key] = record
        result["effect_scripts"] = effects
    if "deployables" in extra:
        supplied_deployables = extra["deployables"]
        if not isinstance(supplied_deployables, dict):
            raise ValueError("supplement deployables must be a mapping")
        deployables = dict(data.get("deployables") or {})
        for key, record in supplied_deployables.items():
            if not isinstance(key, str) or (key == "format_version" and (type(record) is not int or record != 1)):
                raise ValueError("invalid supplemental deployables metadata")
            if key != "format_version" and not isinstance(record, dict):
                raise ValueError("supplement deployable entries must be mappings")
            if key in deployables and deployables[key] != record:
                raise ValueError(f"supplement conflicts with deployable {key}")
            deployables[key] = record
        result["deployables"] = deployables
    if "npc_controllers" in extra:
        controllers = dict(data.get("npc_controllers") or {})
        additions = extra["npc_controllers"]
        if not isinstance(additions, dict):
            raise ValueError("supplement NPC controller associations must be a mapping")
        bodies = data.get("bodies", {}) | entities
        for body, association in additions.items():
            definition = association.get("definition") if isinstance(association, dict) else None
            if (not isinstance(body, str) or not re.fullmatch(r"0x0400[0-9A-F]{12}", body)
                    or not int(body, 16) & 0xFFFFFFFFFFFF or body not in bodies
                    or not isinstance(definition, str)
                    or not re.fullmatch(r"0x0400[0-9A-F]{12}", definition)
                    or definition == body or definition not in entities):
                raise ValueError("unresolved NPC controller definition")
            if body in controllers and controllers[body] != association:
                raise ValueError(f"supplement conflicts with NPC controller {body}")
            controllers[body] = association
        result["npc_controllers"] = controllers
    return result


def merge_extra_bundle(base: dict, extra: dict) -> dict:
    """Read-only graph/entity shards may add missing records, never replace known ones."""
    if not isinstance(extra, dict) or extra.get("version", 1) != 1:
        raise ValueError("unsupported supplementary graph bundle")
    if any(extra.get(key) for key in ("bodies", "mode_scripts", "mode_variables", "map_scripts")):
        raise ValueError("supplementary bundles may add only graphs and entity definitions")
    if not isinstance(extra.get("graphs", []), list) or not isinstance(extra.get("entities", {}), dict):
        raise ValueError("supplementary graph/entity sections have invalid types")
    records = {row["index"]: row for row in base["graphs"]}
    seen = set()
    for row in extra.get("graphs", []):
        index = row.get("index") if isinstance(row, dict) else None
        if type(index) is not int or not 0 <= index <= 0xFFFF or index in seen:
            raise ValueError("invalid or duplicate supplementary graph identity")
        seen.add(index)
        if index in records and records[index] != row:
            raise ValueError(f"supplementary graph conflicts with canonical graph {index:04X}")
        records[index] = row
    entities = dict(base.get("entities", {}))
    for key, row in extra.get("entities", {}).items():
        if not re.fullmatch(r"0x0400[0-9A-F]{12}", key) or not isinstance(row, dict):
            raise ValueError("invalid supplementary entity definition")
        known = entities.get(key, base.get("bodies", {}).get(key))
        if known is not None and known != row:
            raise ValueError(f"supplementary entity conflicts with canonical entity {key}")
        entities[key] = row
    return {**base, "graphs": list(records.values()), "entities": entities}


@cache
def _records() -> dict[int, dict]:
    return {record["index"]: record for record in _data()["graphs"]}


@cache
def graph(index: int) -> Graph | None:
    """The graph with a 16-bit index (0x13C1), or None when the server does not have it."""
    record = _records().get(index)
    if record is None:
        from ow174.game.script import status_data

        supplement = status_data.configured()
        if supplement is not None:
            record = next((item for item in supplement["graphs"] if item["index"] == index), None)
    return Graph(record) if record is not None else None


def graph_indexes() -> list[int]:
    return sorted(_records())


def bodies() -> dict[int, dict]:
    """Hero body (003 GUID) -> {"hero", "name", "graphs": [{"graph", "m_1EB5A024", ...}], "manager",
    "weapons"}; new bundles also retain "script_component" (definition or known absence)."""
    return {int(key, 16): record for key, record in _data()["bodies"].items()}


def effect_script(definition: int) -> dict | None:
    """An extracted .053 gameplay-effect binding, when present in the runtime pack."""
    return (_data().get("effect_scripts") or {}).get(f"0x{definition:016X}")


def entity_body(body: int) -> dict | None:
    """An optional non-player body definition from the same graph bundle."""
    return (_data().get("entities") or {}).get(f"0x{body:016X}")


def junkenstein_enemy(hero: int) -> dict | None:
    """Return the encounter's authored body and controller pair for a hero resource."""
    return _data().get('junkenstein', {}).get('pairs', {}).get(f'0x{hero:016X}')


def junkenstein_entity(definition: int) -> dict | None:
    """Read one encounter-specific body or controller record."""
    return _data().get('junkenstein', {}).get('entities', {}).get(f'0x{definition:016X}')


def junkenstein_player_tags(body: int) -> list[int] | None:
    """Return authored tags for the player body, or unknown for older bundles."""
    return _data().get('junkenstein', {}).get('player_target_tags', {}).get(f'0x{body:016X}')


def npc_controller(body: int) -> dict | None:
    """Optional authored AI-entity association; no implicit controller allocation."""
    return (_data().get("npc_controllers") or {}).get(f"0x{body:016X}")


def entity_script_component(definition: int, *, junkenstein: bool = False):
    """Actual entity component dict, known absence None, or unknown old/missing data.

    The encounter uses its own catalog so a boss can use different startup
    data from the playable hero. A legacy flattened list cannot declare absence.
    """
    if (type(definition) is not int or not 0 < definition < 1 << 64
            or definition >> 48 != 0x0400 or not definition & 0xFFFFFFFFFFFF):
        raise ValueError('script component definition must be a nonzero .003 entity resource')
    key = f'0x{definition:016X}'
    data = _data()
    sections = ((data.get('junkenstein', {}).get('entities', {}),) if junkenstein
                else (data.get('bodies', {}), data.get('entities', {})))
    if any(not isinstance(section, dict) for section in sections):
        raise ValueError('invalid entity script component catalog')
    records = [section[key] for section in sections if key in section]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError('invalid entity script component record')
    if not records or any('script_component' not in record for record in records):
        return NotImplemented
    component = records[0]['script_component']
    if any(record['script_component'] != component for record in records[1:]):
        raise ValueError('conflicting entity script component definitions')
    if component is not None and (not isinstance(component, dict)
                                  or component.get('$') != 'STUStatescriptComponent'):
        raise ValueError('unsupported entity script component definition')
    return component


@cache
def mode_variables(mode: int) -> dict[int, bool | int | Asset] | None:
    """Explicit initial mode entity values, or None when the bundle has no record for the mode."""
    record = _data().get("mode_variables", {}).get(f"0x{mode:016X}")
    if record is None:
        return None
    return {
        int(key): Asset(int(value, 16)) if isinstance(value, str) else value
        for key, value in record.items()
    }


def missing() -> list[int]:
    """Graphs a kept graph names that are not in the dump."""
    return list(_data()["missing"])


def mode_scripts(mode: int) -> dict | None:
    """The authored mode/team script associations, absent in older runtime bundles."""
    return _data().get("mode_scripts", {}).get(f"0x{mode:016X}")


def mission_script(mode: int, map_guid: int) -> dict | None:
    """A map's configured mission script, keyed by both map and supported mode.

    Local extractors retain the source mission asset and its mode graph alongside
    the configured graph. Older bundles have no dynamic mission bindings.
    """
    return _data().get("mission_scripts", {}).get(f"0x{map_guid:016X}", {}).get(f"0x{mode:016X}")


def status_definition(definition: int):
    """A known .053 definition, or NotImplemented when its data is unavailable."""
    if type(definition) is not int or definition >> 48 != 0x04A0 or not definition & 0xFFFFFFFFFFFF:
        raise ValueError("expected a nonzero .053 resource GUID")
    key = f"0x{definition:016X}"
    found = (_data().get("status_definitions") or {}).get(key)
    if found is not None:
        return found
    from ow174.game.script import status_data

    supplement = status_data.configured()
    return supplement["status_definitions"].get(key, NotImplemented) if supplement else NotImplemented
