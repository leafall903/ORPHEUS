"""Inspect placement server/client roles and confirmation routing from actual graphs.

Writes only a compact external evidence report; no runtime or client is changed.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ow174.game.script import expr, graph  # noqa: E402


def inspect():
    scopes = []
    for index in (0x156B, 0x1571):
        definition = graph.graph(index)
        for node in definition.nodes:
            if node.cls != "STU_BD02E168":
                continue
            for position, path in node.links("m_5B10D01F"):
                child = definition.node(position)
                scopes.append(
                    {
                        "graph": index,
                        "scope": node.pos,
                        "child": position,
                        "input": path,
                        "class": child.cls,
                        "client_only": child.client_only,
                        "server_only": child.server_only,
                    }
                )
    definition = graph.graph(0x156B)
    route = ((57, "m_F6955E81", 89), (89, "m_outPlug", 0), (0, "m_onBeginPlug", 36), (36, "m_outPlug", 82))
    for origin, plug, target in route:
        if target not in {pos for pos, _ in definition.node(origin).links(plug)}:
            raise ValueError("authored placement confirmation route changed")
    nodes = []
    for position in (57, 89, 0, 36, 82):
        node = definition.node(position)
        nodes.append(
            {
                "position": position,
                "class": node.cls,
                "client_only": node.client_only,
                "server_only": node.server_only,
                "out_var": expr.source(node.field("m_out_Var")),
                "value": expr.source(node.field("m_value")),
            }
        )
    return {
        "source": str(graph.GRAPHS_PATH),
        "source_sha256": hashlib.sha256(graph.GRAPHS_PATH.read_bytes()).hexdigest(),
        "scope_children": scopes,
        "confirmation_edges": route,
        "confirmation_nodes": nodes,
        "scope_conclusion": "BD02E168 children are client-only; server execution rejects them",
        "charge_and_orientation_conclusion": "57 success starts Wait0, spending charge and assigning axis82",
        "native_placement_admission_accepted": False,
        "positive_actor_creation_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT) or args.output.resolve() == graph.GRAPHS_PATH.resolve():
        parser.error("write the report outside source and the immutable input")
    result = inspect()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "scope_children": len(result["scope_children"]),
                "confirmation_nodes": len(result["confirmation_nodes"]),
            }
        )
    )


if __name__ == "__main__":
    main()
