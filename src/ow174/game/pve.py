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
