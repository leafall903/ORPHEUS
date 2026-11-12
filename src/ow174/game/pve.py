"""The authored PvE entrance state, separate from mission AI/objective execution.

Build 104319's loader (RVA 706BFF..707174) checks the mode's m_0FC17230,
then accepts an active state derived from STU_9FC136D7 on its mode entity.
The class registry at RVA 25FB69 names that type; its verified codec is empty.
This state confirms that the server has prepared the world for entering it.
"""

from dataclasses import dataclass

from ow174.game.script import graph

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
