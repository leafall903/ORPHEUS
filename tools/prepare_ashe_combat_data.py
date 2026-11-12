"""Prepare missing Ashe actor/controller metadata, without copying a runtime.

Input XML and OWLib types are authorized external research inputs. Output stays
outside the repository. Existing base graphs are not copied or overridden.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from tools.extract_game_graphs import EnumTables, GraphXml, StuXml, body_record, guid_index
from tools.extract_game_movers import FIELDS, mover_of


def prepare(xml_dir, types_dir, base_path, base_movers=None, npc_catalog=None, *, npc_controllers=False,
            player_controller=False):
    raw = base_path.read_bytes()
    base = json.loads(gzip.decompress(raw))
    enums = EnumTables(types_dir)
    existing = {record["index"] for record in base["graphs"]}
    entities, queue, graphs, controllers = {}, [], {}, {}
    definitions = [(0x25C3, 0x021D, "B.O.B."), (0x2533, None, "Dynamite")]
    if player_controller:
        definitions.append((0x000E, None, "Player controller"))
    for index, hero, name in definitions:
        guid = f"0x{0x0400000000000000 | index:016X}"
        if guid in base.get("entities", {}):
            continue
        path = xml_dir / "003" / f"{index:012X}.003.xml"
        hero_guid = f"0x{0x02E0000000000000 | hero:016X}" if hero is not None else None
        record, references = body_record(path, enums, hero_guid, name)
        entities[guid] = record
        queue.extend(references)
    if npc_controllers:
        if npc_catalog is None:
            raise ValueError("NPC controllers require an explicit catalog")
        for row in json.loads(npc_catalog.read_text(encoding="utf-8"))["definitions"]:
            body = int(row["body"], 16)
            path = xml_dir / "003" / f"{body & 0xFFFFFFFFFFFF:012X}.003.xml"
            body_xml = StuXml(path, enums)
            ref = body_xml.field(body_xml.root, "m_8B9A461F")
            guid = ref.get("GUID", "") if ref is not None else ""
            if not guid.endswith(".003") or not (index := guid_index(guid)):
                raise ValueError(f"missing associated NPC AI entity for {body:016X}")
            key = f"0x{0x0400000000000000 | index:016X}"
            ai_path = path.with_name(f"{index:012X}.003.xml")
            controllers[row["body"]] = {
                "definition": key, "body_source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256(ai_path.read_bytes()).hexdigest(),
            }
            if key not in base.get("entities", {}) and key not in entities:
                record, references = body_record(ai_path, enums, None, row["name"] + " AI")
                entities[key] = record
                queue.extend(references)
    while queue:
        index = queue.pop()
        if index in existing or index in graphs:
            continue
        path = xml_dir / "01B" / f"{index:012X}.01B.xml"
        if not path.is_file():
            raise ValueError(f"missing graph {index:04X}")
        xml = GraphXml(path, enums)
        graphs[index] = xml.record(index)
        queue.extend(xml.graph_refs())
    result = {"format_version": 1, "base_sha256": hashlib.sha256(raw).hexdigest(),
              "entities": entities, "graphs": [graphs[key] for key in sorted(graphs)]}
    if controllers:
        result["npc_controllers"] = controllers
    if base_movers is not None:
        mover_raw = base_movers.read_bytes()
        existing_movers = {int(key, 16) for key in json.loads(mover_raw)}
        bodies = {0x04000000000025C3}
        if npc_catalog:
            bodies.update(int(row["body"], 16) for row in json.loads(
                npc_catalog.read_text(encoding="utf-8"))["definitions"])
        movers, evidence = {}, {}
        for body in sorted(bodies - existing_movers):
            path = xml_dir / "003" / f"{body & 0xFFFFFFFFFFFF:012X}.003.xml"
            source = path.read_bytes()
            values = mover_of(source.decode("utf-8"))
            if values is None:
                raise ValueError(f"missing mover component for {body:016X}")
            guid = f"0x{body:016X}"
            movers[guid] = values
            evidence[guid] = hashlib.sha256(source).hexdigest()
        result.update(base_mover_sha256=hashlib.sha256(mover_raw).hexdigest(),
                      movers=movers, mover_source_sha256=evidence)
        path = xml_dir / "01B" / "000000001537.01B.xml"
        record = GraphXml(path, enums).record(0x1537)
        profiles = {}
        for pos, name in ((61, "bob_charge"), (97, "bob_idle")):
            node = next(row for row in record["nodes"] if row["pos"] == pos)
            override = next(item["m_9B7A63EA"] for item in node["fields"]["m_BC5E91CF"]
                            if item["$"] == "STU_ED2B22BD")
            profiles[name] = {field: override["m_" + hashed] for hashed, field in FIELDS.items()}
        result.update(mover_profiles=profiles,
                      mover_profile_source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--types", type=Path, required=True)
    parser.add_argument("--base-graphs", type=Path, required=True)
    parser.add_argument("--base-movers", type=Path)
    parser.add_argument("--npc-catalog", type=Path)
    parser.add_argument("--npc-controllers", action="store_true")
    parser.add_argument("--player-controller", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.is_relative_to(source):
        parser.error("research graph output must stay outside the source repository")
    inputs = [args.base_graphs, args.base_movers, args.npc_catalog]
    if any(path and output == path.resolve() for path in inputs) or output.is_relative_to(args.xml.resolve()):
        parser.error("output must not replace research input")
    if args.npc_catalog and not args.base_movers:
        parser.error("--npc-catalog requires --base-movers")
    if args.npc_controllers and not args.npc_catalog:
        parser.error("--npc-controllers requires --npc-catalog")
    data = prepare(args.xml, args.types, args.base_graphs, args.base_movers, args.npc_catalog,
                   npc_controllers=args.npc_controllers, player_controller=args.player_controller)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0))
    print(json.dumps({"graphs_added": len(data["graphs"]), "entities_added": len(data["entities"]),
                      "movers_added": len(data.get("movers", {})),
                      "base_sha256": data["base_sha256"], "output_bytes": output.stat().st_size}))


if __name__ == "__main__":
    main()
