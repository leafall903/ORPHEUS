"""Extract known combat-body target tags for the scoped authored charge bridge."""

import argparse
import gzip
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.extract_game_graphs import full_guid


def extract(xml, graphs, npc_catalog):
    xml, graphs, npc_catalog = Path(xml), Path(graphs), Path(npc_catalog)
    data = json.loads(gzip.decompress(graphs.read_bytes()))
    bodies = {int(k, 16): {"hero": v["hero"], "name": v["name"]}
              for k, v in data["bodies"].items()}
    # D.Va's controlled mech is created by the pilot graph, outside the hero body table.
    bodies.setdefault(0x0400000000000407, {"hero": "0x02E000000000007A", "name": "D.Va Mech"})
    for row in json.loads(npc_catalog.read_text(encoding="utf-8"))["definitions"]:
        bodies[int(row["body"], 16)] = {"hero": row["hero"], "name": row["name"]}
    result, sources = {}, {}
    for body, row in sorted(bodies.items()):
        path = xml / "003" / f"{body & 0xFFFFFFFFFFFF:012X}.003.xml"
        root = ET.parse(path).getroot()
        tags = {full_guid(e.get("GUID")) for c in root.iter("STUTargetTagComponent")
                for e in c.iter() if e.get("GUID")}
        result[f"0x{body:016X}"] = {**row, "tags": [f"0x{t:016X}" for t in sorted(tags)]}
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (graphs, npc_catalog):
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"schema": "orpheus-orisa-charge-v1", "build": "1.74.0.0.104319",
            "bodies": result, "sources": sources, "runtime_tag_mutations_restored": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xml", "graphs", "npc-catalog", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    result = extract(args.xml, args.graphs, args.npc_catalog)
    Path(args.output).write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(result['bodies'])} known combat-body tag records -> {args.output}")


if __name__ == "__main__":
    main()
