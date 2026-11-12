"""Extract an external104319 Sonic Arrow profile; never write game data into source."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.extract_game_graphs import EnumTables, GraphXml, StuXml, full_guid  # noqa: E402
from tools.extract_hanzo_dragon import constant  # noqa: E402


def extract(folder, enums, status):
    sources = {}

    def fingerprint(path):
        sources[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()

    for definition in (0x149, 0x20D):
        path = folder / "003" / f"{definition:012X}.003.xml"
        fingerprint(path)
        if "0000000000C3.01B" not in path.read_text(encoding="utf-8-sig"):
            raise ValueError("Sonic entity no longer names00C3")
    records = []
    for index in (0xC3, 0x10E):
        path = folder / "01B" / f"{index:012X}.01B.xml"
        fingerprint(path)
        record = GraphXml(path, enums).record(index)
        if record["unknown_nodes"]:
            raise ValueError("selected Sonic graph has unread nodes")
        records.append(record)
    nodes = {n["pos"]: n for n in records[0]["nodes"]}
    literals = []
    for pos, identifier in ((12, 215), (13, 422)):
        fields = nodes[pos]["fields"]
        target = fields["m_out_Var"]
        if (nodes[pos]["class"] != "STUStatescriptActionSetVar"
                or target.get("m_identifier") != identifier or target.get("m_60DB8F99") != 0):
            raise ValueError("Sonic literal binding changed")
        literals.append(constant(fields["m_value"]))
    if nodes[20]["fields"]["m_A76A8CF6"]["m_A76A8CF6"] != "0x04A000000000003E":
        raise ValueError("Sonic status changed")
    wait = nodes[9]["fields"]["m_timeout"]
    reach = nodes[11]["fields"]["m_8862A75D"]
    if (wait.get("m_identifier") != 215 or wait.get("m_60DB8F99") != 0
            or reach.get("m_identifier") != 422 or reach.get("m_60DB8F99") != 0):
        raise ValueError("Sonic lifetime/radius wiring changed")
    fingerprint(status)
    status_xml = StuXml(status, enums)
    link = status_xml.field(status_xml.root, "m_14AA219A")
    if full_guid(link.get("GUID")) != 0x058000000000010E:
        raise ValueError("Sonic effect no longer names010E")
    return {
        "format_version": 1, "hero": "0x02E0000000000005", "source_sha256": sources,
        "duration": literals[0], "radius": literals[1], "status_graph": records[1],
        "unverified": ["attachment geometry", "target filter flags", "pulse/refresh cadence",
                       "native status lifecycle", "viewer projection/replication"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--owlib", type=Path, required=True)
    parser.add_argument("--status-effect", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("extracted game data must stay outside the source repository")
    data = extract(args.xml, EnumTables(args.owlib / "TankLib/STU/Types"), args.status_effect)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted Sonic profile to {args.output}")


if __name__ == "__main__":
    main()
