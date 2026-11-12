#!/usr/bin/env python3
"""Export external 053 combat-effect definitions and their referenced graph closure.

The body-only graph extractor cannot follow an indirect 053 -> 01B edge. Keep
these raw research records outside the source tree; this is not a combat emulator.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from extract_game_graphs import EnumTables, GraphXml, StuXml, tag

ROOT = Path(__file__).resolve().parents[1]


def references(value):
    if isinstance(value, str) and value.startswith("0x0580") and len(value) == 18:
        yield int(value, 16) & 0xFFFFFFFFFFFF
    elif isinstance(value, dict):
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def collect(effect_xml, graph_xml, enums, indexes):
    effects, graphs, missing, sources = [], {}, [], []

    def record_source(path):
        sources.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    queue = []
    for index in sorted(set(indexes)):
        path = effect_xml / f"{index:012X}.053.xml"
        xml = StuXml(path, enums)
        record_source(path)
        fields = xml.fields(xml.root)
        effects.append({"index": index, "class": tag(xml.root), "fields": fields})
        queue.extend(references(fields))
    while queue:
        index = queue.pop(0)
        if index in graphs or index in missing:
            continue
        path = graph_xml / f"{index:012X}.01B.xml"
        if not path.is_file():
            missing.append(index)
            continue
        xml = GraphXml(path, enums)
        graphs[index] = xml.record(index)
        record_source(path)
        queue.extend(sorted(xml.graph_refs()))
    return {"scope": "external-combat-effect-research-not-execution-acceptance", "effects": effects,
            "graphs": [graphs[i] for i in sorted(graphs)], "missing": sorted(missing), "sources": sources}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effects-xml", type=Path, required=True, help="directory containing .053.xml")
    parser.add_argument("--graph-xml", type=Path, required=True, help="directory containing .01B.xml")
    parser.add_argument("--owlib", type=Path, required=True)
    parser.add_argument("--effect", action="append", required=True, type=lambda value: int(value, 16))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error("raw combat research output must stay outside the source repository")
    enums = EnumTables(args.owlib / "TankLib" / "STU" / "Types")
    report = collect(args.effects_xml, args.graph_xml, enums, args.effect)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(report['effects'])} effects, {len(report['graphs'])} graphs; "
          f"{len(report['missing'])} graph references missing. Not execution acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
