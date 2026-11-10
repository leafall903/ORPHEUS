"""Extract deterministic category-switch animation durations from 104319 .021 XML."""

import argparse
import json
import math
import sys
from pathlib import Path

from extract_game_graphs import StuXml, guid_text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.paths import EMOTE_DURATION_DATA

CATEGORY_TYPE = 0x0280000000000000


def first_animation_node(xml, game_data):
    """Follow the native F4EA30 child before its IK output."""
    if game_data is None or game_data.tag != "STU_7D00A73D":
        return None
    nodes = xml.field(game_data, "m_nodes")
    if nodes is None or not len(nodes):
        return None
    node = xml.resolve(nodes[0])
    if node.tag == "STU_30C2E3AE":
        child = int(node.get("m_38CF6CD3", "0"))
        if not 0 < child < len(nodes):
            return None
        node = xml.resolve(nodes[child])
    return node


def embedded_data(xml, node):
    if node is not None and node.tag == "STU_331B7B06" and node.get("m_53A1B672", "0") == "0":
        return xml.field(node, "m_EB45D767")
    return None


def category_durations(xml, item):
    root = xml.field(item, "m_gameData")
    data = embedded_data(xml, first_animation_node(xml, root))
    node = first_animation_node(xml, data)
    if node is None or node.tag != "STU_623B2B19" or node.get("m_E6058AAD") != "1":
        return {}
    keys = xml.field(node, "m_9F7CA0BB")
    indexes = xml.field(node, "m_540E2FC8")
    nodes = xml.field(data, "m_nodes")
    if (keys is None or indexes is None or len(keys) != len(indexes) or not len(keys)
            or xml.field(node, "m_4D532CFC") is not None or xml.field(node, "m_53D9C839") is not None):
        return {}
    keys = [int(key.text) for key in keys]
    if keys[-1] != 0 or any(not 0x80000000 < key <= 0xFFFFFFFF for key in keys[:-1]):
        return {}

    result = {}
    for key, child in zip(keys, indexes):
        index = int(child.text)
        if not 0 < index < len(nodes):
            continue
        game_data = embedded_data(xml, xml.resolve(nodes[index]))
        leaf = first_animation_node(xml, game_data)
        if leaf is None or leaf.tag != "STU_3C8FB392":
            continue
        # BB8970 consumes the leaf's primary, zero-based record index.
        index = int(leaf.get("m_9D16E038", "-1"))
        records = xml.field(game_data, "m_animDatas")
        if records is None or not 0 <= index < len(records):
            continue
        row = xml.resolve(records[index])
        seconds = float(row.get("m_D8514AD9", "0"))
        category = CATEGORY_TYPE | (key & 0x7FFFFFFF) if key else 0
        if row.tag == "STUAnimGameData_AnimationData" and math.isfinite(seconds) and seconds > 0:
            result.setdefault(f"0x{category:016X}", seconds)
    return result


def extract(source):
    tables = {}
    for path in sorted(source.glob("*.021.xml")):
        xml = StuXml(path, None)
        if xml.root.tag != "STUAnimBlendTreeSet":
            continue
        aliases = xml.field(xml.root, "m_253EE7C8")
        items = xml.field(xml.root, "m_blendTreeItems")
        if aliases is None or items is None or len(aliases) != len(items):
            continue
        durations = {}
        for alias, item in zip(aliases, items):
            key = int(alias.text)
            if key >> 48 != 0x0C80 or key & 0xFFFFFFFF == 0:
                continue
            categories = category_durations(xml, xml.resolve(item))
            if categories:
                durations[f"0x{key:016X}"] = categories
        if durations:
            tables[guid_text(path.stem)] = durations
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing *.021.xml")
    parser.add_argument("output", type=Path, help=f"runtime data/{EMOTE_DURATION_DATA}")
    args = parser.parse_args()
    rows = extract(args.source)
    if not rows:
        raise SystemExit("No supported category-switch animation data found")
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote model animation durations from {len(rows)} blend tree sets")


if __name__ == "__main__":
    main()
