"""Prepare an external Supercharger graph/status/checked-model supplement."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.extract_game_graphs import EnumTables, GraphXml


def prepare(xml, types, status, geometry):
    xml, status, geometry = Path(xml), Path(status), Path(geometry)
    root = ET.parse(status).getroot()
    references = [int(e.get("GUID")) for e in root.iter() if e.get("GUID", "").isdigit()]
    if root.tag.rsplit("}", 1)[-1] != "STU_76F2C784" or 0x0580000000000D01 not in references:
        raise ValueError("expected checked027E status XML referencing0D01")
    enums = EnumTables(Path(types))
    files = [xml / "01B" / f"{i:012X}.01B.xml" for i in (0xD00, 0xD01, 0x25B4)]
    mesh = json.loads(geometry.read_text(encoding="utf-8-sig"))
    if mesh.get("model") != "0x0D000000000042A2":
        raise ValueError("Supercharger requires model42A2 geometry")
    return {
        "schema": "orpheus-orisa-supercharger-v1", "build": "1.74.0.0.104319",
        "graphs": [GraphXml(p, enums).record(i) for p, i in zip(files, (0xD00, 0xD01, 0x25B4), strict=True)],
        "geometry": mesh,
        "status": {"guid": "0x04A000000000027E", "graph": 0xD01,
                   "unverified_m_7DAE8F10": root.get("m_7DAE8F10")},
        "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (*files, status, geometry)},
        "native_status_semantics_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xml", "types", "status", "geometry", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    result = prepare(args.xml, args.types, args.status, args.geometry)
    Path(args.output).write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"3 authored graphs + {len(result['geometry']['triangles'])} model triangles -> {args.output}")


if __name__ == "__main__":
    main()
