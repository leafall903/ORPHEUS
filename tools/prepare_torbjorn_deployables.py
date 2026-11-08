"""Add authored Torbjorn deployable dependencies to an external runtime bundle.

No game data is written into source. Numeric catalog values follow104319 XML;
runtime build healing, target acquisition and muzzle placement are reconstructed.
"""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_game_graphs as extract  # noqa: E402


def find_field(value, key):
    if isinstance(value, dict):
        if key in value:
            yield value[key]
        for child in value.values():
            yield from find_field(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from find_field(child, key)


def prepare(base: Path, xml_folder: Path, owlib: Path, output: Path):
    if output.resolve().is_relative_to(ROOT):
        raise ValueError("prepared game data must be outside the source checkout")
    enums = extract.EnumTables(owlib / "TankLib/STU/Types")
    data = json.loads(gzip.decompress(base.read_bytes()))
    records = {row["index"]: row for row in data["graphs"]}
    old_count = len(records)
    pending = []
    sources = {str(base): hashlib.sha256(base.read_bytes()).hexdigest()}
    geometry = None
    for body, name in ((0x28B7, "Torbjorn Turret"), (0x2903, "Molten Core Pool")):
        path = xml_folder / "003" / f"{body:012X}.003.xml"
        record, used = extract.body_record(path, enums, "0x02E0000000000006", name)
        data.setdefault("entities", {})[f"0x040000000000{body:04X}"] = record
        pending.extend(sorted(used))
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        if body == 0x28B7:
            xml = extract.StuXml(path, enums)
            geometry = next((float(node.get("m_height")), float(node.get("m_radius")))
                            for node in xml.root.iter() if extract.tag(node) == "STU_50BA775C"
                            and node.get("m_radius") is not None)
    seen = set(records)
    while pending:
        index = pending.pop(0)
        if index in seen:
            continue
        seen.add(index)
        path = xml_folder / "01B" / f"{index:012X}.01B.xml"
        xml = extract.GraphXml(path, enums)
        record = xml.record(index)
        if xml.problems:
            raise ValueError(f"graph{index:04X} has unresolved XML links:{xml.problems[:2]}")
        records[index] = record
        pending.extend(sorted(xml.graph_refs()))
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    turret = data["entities"]["0x04000000000028B7"]
    overrides = next(row for row in turret["graphs"] if row["graph"] == 0x33)["m_1EB5A024"]
    values = {int(row["m_0D09D2D9"], 16) & 0xFFFF: row.get("m_value") for row in overrides}
    build = next(node for node in records[0x1688]["nodes"] if node["pos"] == 29)
    tracking_range = next(value["m_value"] for value in find_field(records[0x1688], "m_6A79BD98")
                          if isinstance(value, dict) and "m_value" in value)
    repair = next(node for node in records[0x1688]["nodes"] if node["state"] == 5)
    catalog = {"format_version": 1, "turret": {
        "body": "0x04000000000028B7", "maximum_health": values[199]["m_value"],
        "initial_health": values[1932]["m_value"], "build_seconds": build["fields"]["m_value"]["m_value"],
        "range": tracking_range, "repair": repair["fields"]["m_modifyHealth"]["m_amount"]["m_value"],
        "height": geometry[0], "radius": geometry[1], "gun_graph": 0x1689, "gun_state": 1,
        "geometry_source": "28B7/STU_50BA775C m_height/m_radius",
        "reconstruction": "linear build healing;nearest enemy;centre muzzle;native services incomplete"}}
    data["deployables"] = catalog
    data["graphs"] = [records[index] for index in sorted(records)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0))
    (output.parent / "torbjorn_deployables_174.json").write_text(
        json.dumps(catalog, indent=2), encoding="utf-8")
    return {"base": str(base), "output": str(output),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "added_graphs": len(records) - old_count, "new_missing": [], "source_sha256": sources,
            "ordinary_startup_accepted": False, "original_client_accepted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--owlib", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.base, args.xml, args.owlib, args.output)
    args.output.with_suffix(".provenance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "source_sha256"}, indent=2))


if __name__ == "__main__":
    main()
