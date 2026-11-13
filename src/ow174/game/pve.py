"""The authored PvE entrance state, separate from mission AI/objective execution.

Build 104319's loader (RVA 706BFF..707174) checks the mode's m_0FC17230,
then accepts an active state derived from STU_9FC136D7 on its mode entity.
The class registry at RVA 25FB69 names that type; its verified codec is empty.
This state confirms that the server has prepared the world for entering it.
"""

import json
import math
from collections import deque
from dataclasses import dataclass
from functools import cache

from ow174 import paths
from ow174.game import npc
from ow174.game.script import expr, graph

READY_CLASS = "STU_9FC136D7"
SUBSCRIPT_CLASS = "STU_37D754C8"
SCRIPT_PATH = "m_36F7B64C.m_36F7B64C.m_C974E369.m_graph"
MODE_UI = 0x0CB6

# Uprising's phases as the client shows them: a director graph per phase draws that phase's objective
# box when the mode entity carries it (docs/research/uprising-mission.md). The server plays no mission
# behind them yet: the directors are sent by hand and their own server states never run.
UPRISING = 0x0D28
DEFEND, ESCORT = 0x0D4A, 0x0D4B
DEFEND_SECONDS = 240.0
CLOCK = 1303  # the mode entity's countdown variable; the defend box shows it as its time remaining


@dataclass(frozen=True)
class Entrance:
    graph: graph.Graph
    ui_state: int
    ready_state: int | None
    mode_graph: graph.Graph | None = None


def entrance(mode_guid: int, map_guid: int | None = None) -> Entrance | None:
    """Require the real mode root and its unique common-UI SubScript.

    Dynamically configured missions must supply their graph binding before they
    can use this path; an unrelated mode's readiness graph is never substituted.
    """
    mode = graph.mode_scripts(mode_guid)
    if mode is None or not mode.get("pve"):
        return None
    reference = graph.guid(mode.get("graph"))
    primary = graph.graph(reference & 0xFFFF) if reference is not None else None
    if primary is None:
        return None
    mode_graph = None
    mission = graph.mission_script(mode_guid, map_guid) if map_guid is not None else None
    if mission is not None:
        # A dynamic mission belongs to this map and this exact mode root. Keep
        # that root present alongside the configured script on the mode entity.
        if graph.guid(mission.get("mode_graph")) != reference:
            return None
        configured = graph.guid(mission.get("graph"))
        if (configured is None or configured >> 48 != 0x0580
                or not 0 < (configured & 0xFFFFFFFFFFFF) <= 0xFFFF):
            return None
        mode_graph = primary
        primary = graph.graph(configured & 0xFFFF)
        if primary is None:
            return None
    remote = {node.state for node in primary.remote_states() if node.is_networked}
    ui = [
        node.state for node in primary.states
        if node is not None and node.is_networked and node.cls == SUBSCRIPT_CLASS
        and graph.guid(node.field(SCRIPT_PATH)) == (0x0580000000000000 | MODE_UI)
    ]
    ready = [
        node.state for node in primary.states
        if node is not None and node.state in remote and node.cls == READY_CLASS
    ]
    if len(ui) != 1 or len(ready) > 1 or (mode.get("scripted_loading") and len(ready) != 1):
        return None
    return Entrance(primary, ui[0], ready[0] if ready else None, mode_graph)


def defend_seconds(mode_guid: int, map_guid: int | None = None) -> float | None:
    """How long the mode's defend phase is shown, or None for a mode without Uprising's directors."""
    found = entrance(mode_guid, map_guid)
    if found is None or found.graph.index != UPRISING or None in (graph.graph(DEFEND), graph.graph(ESCORT)):
        return None
    return DEFEND_SECONDS


def director(found: Entrance, left: float | None) -> graph.Graph | None:
    """The director of the phase an Uprising mission is in with `left` seconds of its defend phase to
    go; None before the mission runs and in every other mode."""
    if left is None or found.graph.index != UPRISING:
        return None
    return graph.graph(DEFEND if left > 0 else ESCORT)


# The defend phase's first wave. 0D4A enters state 46 after Wait [33] and starts SubScript [50] (the shared
# spawner 0D1C) after Wait [42] in that state's subgraph. [50]'s centre #1629 is [148] the position of the
# entity in #20283, which [219] sets to tag 7CA; its facing #3192 is [149] norm(pos(#8931) - #1629), #8931
# set by [145]. In 0D1C, [7] is the spawn search, [8] the candidate range and [13] the creation.
WAVE_WAITS, WAVE_SPAWNER, CENTRE_SET, FACING_SET = (33, 42), 50, 219, 145
SPAWNER, SPAWNER_SEARCH, SPAWNER_RANGE, SPAWNER_CREATE = 0x0D1C, 7, 8, 13
HERO, ALIVE, TOTAL, ARC, NEAR, FAR = 8398, 8397, 8744, 8451, 6734, 6737


@dataclass(frozen=True)
class Wave:
    delay: float  # seconds after the defend phase starts
    hero: int
    total: int
    alive: int
    team: int
    centre: tuple
    facing: tuple
    arc: float
    near: float
    far: float
    min_candidates: int
    max_candidates: int


@cache
def map_tags(map_guid: int) -> dict[int, list[tuple]]:
    """Tag GUID -> positions of the map's placeables that carry it (tools/extract_map_tags.py); {} without."""
    path = paths.DATA_DIR / "map_tags_174.json"
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))["maps"].get(f"0x{map_guid:016X}", {})
    return {int(tag, 16): [tuple(p) for p in where] for tag, where in rows.items()}


def _constant(cfg):
    """A config value with no variables (expr.CONSTANTS, or a hero); None otherwise."""
    kind = cfg.get("$") if isinstance(cfg, dict) else None
    if kind == "STUConfigVarHero":
        return graph.guid(cfg.get("m_hero"))
    return expr.CONSTANTS[kind](cfg) if kind in expr.CONSTANTS else None


def _tag_position(node, path: str, tags: dict):
    found = tags.get(graph.guid(node.field(path)) if node is not None else None) or []
    return found[0] if len(found) == 1 else None


def first_wave(map_guid: int = 0x08000000000006FE) -> Wave | None:
    """Uprising's first wave as its graphs and the map give it; None when a piece of it is missing."""
    defend, spawner = graph.graph(DEFEND), graph.graph(SPAWNER)
    if defend is None or spawner is None:
        return None
    sub = defend.node(WAVE_SPAWNER)
    if sub is None or graph.guid(sub.field(SCRIPT_PATH)) != 0x0580000000000000 | SPAWNER:
        return None
    init = {
        item["m_out_Var"]["m_identifier"]: _constant(item.get("m_value"))
        for item in sub.field("m_89402D74") or () if isinstance(item, dict) and item.get("m_out_Var")
    }
    tags = map_tags(map_guid)
    centre = _tag_position(defend.node(CENTRE_SET), "m_value.m_9C7BD8B8.m_7D7C86A1", tags)
    toward = _tag_position(defend.node(FACING_SET), "m_value.m_9C7BD8B8.m_7D7C86A1", tags)
    waits = [_constant(defend.node(pos).field("m_timeout")) for pos in WAVE_WAITS]
    search, create = spawner.node(SPAWNER_SEARCH), spawner.node(SPAWNER_CREATE)
    team = _constant(create.field("m_3EBF988B")) if create is not None else None
    counts = [_constant(search.field(key)) if search is not None else None
              for key in ("m_EC1B83C7", "m_7084DA43")]
    if (None in (centre, toward, team, *waits, *counts)
            or any(init.get(v) is None for v in (HERO, ALIVE, TOTAL, ARC, NEAR, FAR))):
        return None
    facing = tuple(b - a for a, b in zip(centre, toward, strict=True))
    length = math.hypot(*facing)
    return Wave(
        float(sum(waits)), init[HERO], int(init[TOTAL]), int(init[ALIVE]), int(team), centre,
        tuple(axis / length for axis in facing), float(init[ARC]), float(init[NEAR]), float(init[FAR]),
        *counts,
    )


def wave_definition(wave: Wave) -> npc.Definition | None:
    """The wave hero's body and health from the pack's NPC catalog (tools/extract_pve_npcs.py)."""
    path = paths.DATA_DIR / "uprising-npcs.json"
    if not path.is_file():
        return None
    return next((d for d in npc.load_catalog(path).values() if d.hero == wave.hero), None)


def _walkable(collision_world, start, radius: float, data) -> tuple[float, dict]:
    """(cell size, {cell: floor point}) of the floor walkable from `start` within `radius`, on a grid of
    the body's width, each step checked by npc_navigation.edge."""
    from ow174.game import npc_navigation

    # shortcut: a flood over a capsule-width grid stands in for the native spawn search's floor test
    # (C82B40, not read); upgrade when that is decompiled or a navmesh is in the pack.
    size = 2 * data.stand_radius
    x, _, z = start
    cells, queue = {(0, 0): start}, deque([(0, 0)])
    while queue:
        i, k = queue.popleft()
        for cell in ((i + 1, k), (i - 1, k), (i, k + 1), (i, k - 1)):
            if cell in cells or math.hypot(*cell) * size > radius:
                continue
            nx, nz = x + cell[0] * size, z + cell[1] * size
            point = npc_navigation.edge(collision_world, cells[(i, k)], nx, nz, data)
            if point is not None:
                cells[cell] = point
                queue.append(cell)
    return size, cells


def wave_positions(collision_world, wave: Wave) -> list[tuple]:
    """Where the wave's units are created: 0D1C [7]'s spawn search about the centre, in its candidate
    pattern (npc_navigation.spawn_candidate_probes), kept to [8]'s range. The arc doubles up to 360
    degrees while too few candidates pass, as 0D1C [25] does on a retry."""
    from ow174.game import bots
    from ow174.game.mover import mover_data

    definition = wave_definition(wave)
    data = mover_data(definition.body if definition is not None else None)
    x, y, z = wave.centre
    floor = collision_world.ground(x, z, y + bots.STEP_UP, y - data.step_down)
    if floor is None:
        return []
    size, cells = _walkable(collision_world, (x, floor.y, z), wave.far, data)
    heading = math.atan2(wave.facing[2], wave.facing[0])
    golden_turn = (math.sqrt(5) - 1) / 2
    arc, points = wave.arc, []
    while True:
        half = arc * math.pi / 360
        # shortcut: native 0D1C runs a fresh search per unit and takes candidate [0] ([63]); the native
        # candidate pattern and order are unknown, so units take distinct passing candidates of the
        # Junkenstein reconstruction's golden-angle pattern. Upgrade with C82B40.
        points = []
        for index in range(wave.max_candidates):
            radius = wave.far * math.sqrt((index + 0.5) / wave.max_candidates)
            angle = heading + half * (2 * (index * golden_turn % 1) - 1)
            cell = round(radius * math.cos(angle) / size), round(radius * math.sin(angle) / size)
            point = cells.get(cell)
            if (point is not None and point not in points
                    and wave.near <= math.dist(point, wave.centre) <= wave.far):
                points.append(point)
        if len(points) >= wave.total or arc >= 360:
            return points[:wave.total]
        arc = min(360.0, arc * 2)
