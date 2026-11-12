"""Prepare a separate graph bundle with the extracted Enforcer controller."""

import argparse
import gzip
import json
from pathlib import Path

CONTROLLER = "0x040000000000227A"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_bundle", type=Path)
    parser.add_argument("controller_profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.resolve() in {args.base_bundle.resolve(), args.controller_profile.resolve()}:
        parser.error("output must be separate from the source files")

    bundle = json.loads(gzip.decompress(args.base_bundle.read_bytes()))
    profile = json.loads(args.controller_profile.read_text(encoding="utf-8"))
    record = profile["controllers"][CONTROLLER]["record"]
    entity = bundle["entities"].setdefault(CONTROLLER, record)
    if entity != record:
        parser.error("the base bundle already has a different Enforcer controller definition")
    existing = {record["index"] for record in bundle["graphs"]}
    for row in profile["graphs"]:
        graph = row["record"]
        if graph["index"] not in existing:
            bundle["graphs"].append(graph)
            existing.add(graph["index"])
    if any(entry["graph"] not in existing for entry in record["graphs"]):
        parser.error("prepared bundle is missing Enforcer controller roots")
    data = json.dumps(bundle, separators=(",", ":")).encode("utf-8")
    args.output.write_bytes(gzip.compress(data, mtime=0))


if __name__ == "__main__":
    main()
