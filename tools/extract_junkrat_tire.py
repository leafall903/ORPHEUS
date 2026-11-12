"""Prepare a small read-only-runtime tire mover profile from target-build external XML."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.extract_game_movers import mover_of


def extract(path):
    raw = path.read_bytes()
    mover = mover_of(raw.decode("utf-8-sig"))
    if mover is None:
        raise ValueError("039C has no character mover component")
    health = None
    for entry in ET.fromstring(raw).iter():
        if entry.tag.split("}")[-1] != "STUStatescriptSchemaEntry":
            continue
        children = list(entry)
        if any(child.get("GUID") == "0000000000C7.01C" for child in children):
            health = next((float(child.get("m_value")) for child in children
                           if child.tag.split("}")[-1] == "STUConfigVarInt"), None)
    if health is None or health <= 0:
        raise ValueError("039C has no positive authored health override199")
    return {"format_version": 1, "build": 104319, "body": "0x040000000000039C",
            "health": health, "mover": mover, "source_sha256": hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(extract(args.xml), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
