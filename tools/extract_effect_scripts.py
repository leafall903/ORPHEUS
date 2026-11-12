"""Extend an external graph bundle with selected .053 effect-script closures.

Effect definitions are engine resources, not direct graph references. The hero
graph closure alone therefore omits Mercy's healing0079 and boost006C graphs.
Read already-extracted DragonML and preserve these bindings for a real effect
owner; extraction does not implement effect application or prove combat.
"""

import argparse
import copy
import gzip
import json
from pathlib import Path

from tools.extract_game_graphs import EnumTables, GraphXml, StuXml, full_guid, graph_ref

ROOT = Path(__file__).resolve().parents[1]


def extend_bundle(base, effects: Path, graphs: Path, enums: EnumTables, selected: list[int]):
    """Resolve actual effect->graph and nested graph/effect references strictly."""
    result = copy.deepcopy(base)
    records = {item["index"]: item for item in result["graphs"]}
    bindings = result.setdefault("effect_scripts", {})
    effect_queue, graph_queue = list(selected), []
    seen_effects, seen_graphs = set(), set()
    while effect_queue or graph_queue:
        if effect_queue:
            index = effect_queue.pop()
            if index in seen_effects:
                continue
            seen_effects.add(index)
            path = effects / f"{index:012X}.053.xml"
            xml = StuXml(path, enums)
            graph_index = graph_ref(xml, xml.root, "m_5A4051BC")
            if graph_index is None:
                raise ValueError(f"effect {index:012X}.053 has no script binding")
            definition = full_guid(f"{index:012X}.053")
            record = {"graph": graph_index, "definition": xml.fields(xml.root)}
            key = f"0x{definition:016X}"
            if key in bindings and bindings[key] != record:
                raise ValueError(f"conflicting effect binding {key}")
            bindings[key] = record
            graph_queue.append(graph_index)
            continue
        index = graph_queue.pop()
        if index in seen_graphs:
            continue
        seen_graphs.add(index)
        xml = GraphXml(graphs / f"{index:012X}.01B.xml", enums)
        record = xml.record(index)
        if xml.problems or record["unknown_nodes"]:
            raise ValueError(f"effect graph {index:04X} has unread nodes or links")
        if index in records and records[index] != record:
            raise ValueError(f"conflicting graph {index:04X}")
        records[index] = record
        graph_queue.extend(xml.graph_refs())
        effect_queue.extend(
            int(value.split(".")[0], 16)
            for element in xml.root.iter()
            if (value := element.get("GUID", "")).endswith(".053")
        )
    result["graphs"] = [records[index] for index in sorted(records)]
    result["missing"] = sorted(set(result.get("missing", [])) - seen_graphs)
    result.setdefault("roots", {})["effects"] = sorted(
        set(result["roots"].get("effects", [])) | {f"0x{full_guid(f'{i:012X}.053'):016X}" for i in selected}
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="existing external .json.gz graph bundle")
    parser.add_argument("--effects", type=Path, required=True, help="directory of extracted .053 XML")
    parser.add_argument("--graphs", type=Path, required=True, help="directory of extracted .01B XML")
    parser.add_argument("--owlib", type=Path, required=True, help="OWLib source used for enum definitions")
    parser.add_argument("--effect", type=lambda text: int(text, 16), action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination == args.base.resolve() or destination.is_relative_to(ROOT):
        parser.error("output must be external to the repository and must not replace the base bundle")
    enums = EnumTables(args.owlib / "TankLib/STU/Types")
    try:
        base = json.loads(gzip.decompress(args.base.read_bytes()))
        result = extend_bundle(base, args.effects, args.graphs, enums, args.effect)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    destination.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    print(f"{len(result['effect_scripts'])} effect bindings; {len(result['graphs'])} graphs -> {destination}")


if __name__ == "__main__":
    main()
