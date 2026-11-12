"""Synthetic statescript graph fixtures shared by both test lanes."""

from types import SimpleNamespace
from unittest.mock import patch

from ow174.game.script.graph import Graph


def node(position, cls, *, state=None, **fields):
    return {
        "pos": position,
        "class": cls,
        "uid": position,
        "rid": position,
        "remote": -1,
        "state": state,
        "client_only": False,
        "server_only": False,
        "fields": {"m_2BBEEAB8": state is not None, "m_ADEB6E05": state is None, **fields},
    }


def plug(*links):
    return {"$": "STUStatescriptOutputPlug", "links": [list(item) for item in links]}


def setter(position, value, *links):
    return node(
        position,
        "STUStatescriptActionSetVar",
        m_out_Var={"$": "STUConfigVarDynamic", "m_identifier": 7, "m_60DB8F99": 0},
        m_value={"$": "STUConfigVarInt", "m_value": value},
        m_outPlug=plug(*links),
    )


def graph(nodes=(), entries=()):
    return Graph(
        {
            "index": 1,
            "header": {},
            "fields": {},
            "items": len(nodes),
            "containers": {},
            "nodes": list(nodes),
            "unknown_nodes": [],
            "states": [item["pos"] for item in nodes if item["state"] is not None],
            "entries": list(entries),
            "remote": [],
            "sync_vars": [],
        }
    )


def export_synthetic_component_vm(component, *, extra_roots=()):
    """Run the real exporter with a local source stamp for synthetic graphs."""
    from ow174.game import body_vm_snapshot

    source = SimpleNamespace(read_bytes=lambda: b"ORPHEUS synthetic statescript test graphs v1")
    with patch.object(body_vm_snapshot.graph, "GRAPHS_PATH", source):
        return body_vm_snapshot.export_component_vm(component, extra_roots=extra_roots)
