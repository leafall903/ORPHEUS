"""Prepare original Halt orb/target graphs as external104319 inputs."""

import argparse
import hashlib
import json
from pathlib import Path

from tools.extract_game_graphs import EnumTables, GraphXml


def prepare(xml, types):
    xml = Path(xml)
    enums = EnumTables(Path(types))
    files = [xml / "01B" / f"{i:012X}.01B.xml" for i in (0xCCC, 0xCCE)]
    return {"schema": "orpheus-orisa-halt-v1", "build": "1.74.0.0.104319",
            "graphs": [GraphXml(p, enums).record(i) for p, i in zip(files, (0xCCC, 0xCCE), strict=True)],
            "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
            "native_force_and_target_queries_accepted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xml", "types", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(prepare(args.xml, args.types), separators=(",", ":")) + "\n",
                                 encoding="utf-8")


if __name__ == "__main__":
    main()
