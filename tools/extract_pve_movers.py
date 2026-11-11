"""Export only catalog-selected PvE mover definitions to an external supplement.

No game archive/runtime pack is copied or changed. Use this table explicitly in
static fixtures; deployment into a reviewed runtime is separate acceptance.
"""

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract(xml_entities: Path, catalog: Path) -> dict:
    from tools.extract_game_movers import mover_of

    data = json.loads(catalog.read_text(encoding="utf-8"))
    if data.get("format_version") != 1 or not data.get("definitions"):
        raise ValueError("explicit version1 NPC catalog required")
    result = {}
    for row in data["definitions"]:
        body = int(row["body"], 16)
        if body >> 48 != 0x0400 or not body & 0xFFFFFFFFFFFF:
            raise ValueError("NPC mover needs a full nonzero entity GUID")
        path = xml_entities / f"{body & 0xFFFFFFFFFFFF:012X}.003.xml"
        values = mover_of(path.read_text(encoding="utf-8"))
        if values is None:
            raise ValueError(f"no authored character mover in {path}")
        if (any(not math.isfinite(value) for value in values.values())
                or values["stand_radius"] <= 0 or values["stand_height"] < 2 * values["stand_radius"]):
            raise ValueError(f"invalid authored character mover in {path}")
        key = f"0x{body:016X}"
        if key in result:
            raise ValueError(f"duplicate body in NPC catalog: {key}")
        result[key] = {"name": row["name"], **values}
    return dict(sorted(result.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml-entities", type=Path, required=True)
    parser.add_argument("--npc-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("extracted game data must remain outside the source repository")
    table = extract(args.xml_entities, args.npc_catalog)
    output.write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(table)} selected authored NPC movers -> {output}")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
