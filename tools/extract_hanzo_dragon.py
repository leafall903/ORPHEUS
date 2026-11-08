"""Extract a small external Hanzo dragon profile from authored104319 XML.

This records source literals and wiring. It does not prove execution, damage
rate units, native effect deduplication, geometry or client presentation.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.extract_game_graphs import EnumTables, GraphXml, StuXml, full_guid  # noqa: E402


def constant(config):
    if not isinstance(config, dict):
        raise ValueError("missing numeric configuration")
    kind = config.get("$")
    if kind in ("STUConfigVarInt", "STUConfigVarFloat"):
        result = config.get("m_value")
    elif kind == "STU_919FD47C":
        data = config.get("m_expression") or {}
        values = data.get("m_D99EF254") or []
        if data.get("m_opcodes") != [6, 0, 0] or len(values) != 1 or data.get("m_dynamicVars"):
            raise ValueError("numeric expression is not a single constant")
        result = values[0]
    else:
        raise ValueError("unsupported numeric configuration")
    if type(result) not in (int, float) or not math.isfinite(result):
        raise ValueError("profile literal must be finite")
    return result


def extract(folder, enums, status_effect):
    sources = {}

    def fingerprint(path):
        sources[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()

    def record(index):
        path = folder / "01B" / f"{index:012X}.01B.xml"
        fingerprint(path)
        data = GraphXml(path, enums).record(index)
        if data["unknown_nodes"]:
            raise ValueError("selected dragon graph has unread nodes")
        return {item["pos"]: item for item in data["nodes"]}

    def fields(table, position, kind):
        item = table[position]
        if item["class"] != kind:
            raise ValueError("authored dragon node class changed")
        return item["fields"]

    cast, bridge, travel, effect = (record(index) for index in (0x101, 0xBA6, 0x132, 0x118))
    phase = fields(cast, 29, "STU_54FEB21C")
    recovery = fields(cast, 37, "STU_54FEB21C")
    initial = fields(cast, 52, "STUStatescriptStateWeaponVolley")
    definition = initial["m_projectileEntity"]["m_entityDef"]["m_entityDef"]
    if definition != "0x040000000000034F":
        raise ValueError("initial dragon projectile changed")
    create = fields(bridge, 1, "STU_B6E3C77D")
    play = fields(bridge, 0, "STUStatescriptActionPlayScript")
    if (create["m_entityDefinition"]["m_entityDef"] != "0x04000000000002BA"
            or play["m_script"]["m_graph"] != "0x0580000000000132"):
        raise ValueError("dragon continuation wiring changed")
    literals = {}
    for position, variable in ((3, 215), (4, 681), (29, 422), (33, 479)):
        setter = fields(travel, position, "STUStatescriptActionSetVar")
        target = setter["m_out_Var"]
        if target.get("m_identifier") != variable or target.get("m_60DB8F99") != 0:
            raise ValueError("dragon literal binding changed")
        literals[str(variable)] = constant(setter["m_value"])
    tracks = [item["pos"] for item in travel.values()
              if item["class"] == "STUStatescriptStateTrackTargets"
              and item["fields"].get("m_radius") == {
                  "$": "STUConfigVarDynamic", "m_identifier": 422, "m_60DB8F99": 0, "m_EE729DCB": 0,
              }]
    effects = [item["fields"]["m_A76A8CF6"]["m_A76A8CF6"] for item in travel.values()
               if item["class"] == "STU_431F3FCC"]
    if len(effects) != len(tracks) or set(effects) != {"0x04A0000000000040"}:
        raise ValueError("dragon target/effect wiring changed")
    fingerprint(status_effect)
    status = StuXml(status_effect, enums)
    reference = status.field(status.root, "m_14AA219A")
    if reference is None or full_guid(reference.get("GUID")) != 0x0580000000000118:
        raise ValueError("dragon status no longer links to graph0118")
    central = fields(effect, 1, "STUStatescriptStateModifyHealth")["m_modifyHealth"]
    outer = fields(effect, 2, "STUStatescriptStateModifyHealth")["m_modifyHealth"]
    ratio = fields(effect, 4, "STUStatescriptActionSetVar")["m_value"]["m_expression"]
    if (ratio.get("m_opcodes") != [8, 0, 6, 0, 25, 0]
            or ratio.get("m_dynamicVars") != [{"$": "STUConfigVarDynamic", "m_identifier": 422,
                                             "m_60DB8F99": 0, "m_EE729DCB": 0}]
            or len(ratio.get("m_D99EF254") or []) != 1):
        raise ValueError("dragon core-radius expression changed")
    return {
        "format_version": 1, "hero": "0x02E0000000000005", "source_sha256": sources,
        "cast_seconds_literal": constant(phase["m_ED97A97C"]),
        "recovery_seconds_literal": constant(recovery["m_ED97A97C"]),
        "initial": {
            "definition": definition, "speed": constant(initial["m_projectileSpeed"]),
            "range": constant(initial["m_FEC435C6"]),
            "lifetime": constant(initial["m_projectileLifetime"]),
            "damage": constant(initial["m_6396149F"]["m_modifyHealth"]["m_amount"]),
        },
        "dragon": {
            "definition": create["m_entityDefinition"]["m_entityDef"],
            "graph": play["m_script"]["m_graph"], "source_literals": literals,
            "tracking_nodes": sorted(tracks), "status_definition": effects[0],
            "status_graph": "0x0580000000000118",
            "core_radius_factor": ratio["m_D99EF254"][0],
            "central_amount_literal": constant(central["m_amount"]),
            "outer_amount_literal": constant(outer["m_amount"]),
            "central_FB3F6CA9": central["m_FB3F6CA9"],
            "outer_FB3F6CA9": outer["m_FB3F6CA9"],
        },
        "unverified": ["damage-rate units", "effect refresh/deduplication", "target-volume semantics",
                       "native time/scaling/cancellation", "live replication and presentation"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--owlib", required=True, type=Path)
    parser.add_argument("--status-effect", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT):
        parser.error("extracted game data must stay outside the source repository")
    data = extract(args.xml, EnumTables(args.owlib / "TankLib/STU/Types"), args.status_effect)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted dragon source profile to {args.output}")


if __name__ == "__main__":
    main()
