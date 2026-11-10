"""Extract deterministic animation durations from 104319 .021 XML."""

import argparse
import json
import math
import sys
from pathlib import Path

from extract_game_graphs import StuXml, guid_text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.paths import EMOTE_ANY_CATEGORY, EMOTE_DURATION_DATA

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


def record_duration(xml, game_data, leaf):
    if leaf is None or leaf.tag != "STU_3C8FB392":
        return None
    # F4CCA0 emits the primary index; BB8970 reads this record's duration.
    index = int(leaf.get("m_9D16E038", "-1"))
    records = xml.field(game_data, "m_animDatas")
    if records is None or not 0 <= index < len(records):
        return None
    row = xml.resolve(records[index])
    if row.tag != "STUAnimGameData_AnimationData":
        return None
    seconds = float(row.get("m_D8514AD9", "0"))
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def duration_tree(xml, data, node, keys, depth=0):
    # C085B0 suppresses auxiliary nodes. F4CBC0 limits nested alias references.
    if depth >= 48 or node is None:
        return None
    if node.tag == "STU_3C8FB392":
        return record_duration(xml, data, node)
    if node.tag == "STU_331B7B06":
        nested = embedded_data(xml, node)
        return duration_tree(xml, nested, first_animation_node(xml, nested), keys, depth + 1)
    if node.tag != "STU_623B2B19" or node.get("m_E6058AAD") != "1":
        return None

    selectors = xml.field(node, "m_9F7CA0BB")
    indexes = xml.field(node, "m_540E2FC8")
    nodes = xml.field(data, "m_nodes")
    if (selectors is None or indexes is None or not len(selectors) or len(selectors) != len(indexes)
            or xml.field(node, "m_4D532CFC") is not None or xml.field(node, "m_53D9C839") is not None):
        return None
    selectors = [int(key.text) for key in selectors]
    if selectors[-1] != 0 or any(not 0 < key <= 0xFFFFFFFF or not key & 0x7FFFFFFF for key in selectors[:-1]):
        return None
    indexes = [int(child.text) for child in indexes]
    if any(not 0 < index < len(nodes) for index in indexes):
        return None
    keys.update(selectors[:-1])
    return tuple((key, duration_tree(xml, data, xml.resolve(nodes[index]), keys, depth + 1))
                 for key, index in zip(selectors, indexes))


def selected_duration(tree, category, parents):
    while isinstance(tree, tuple):
        choice = tree[-1][1]
        for key, branch in tree[:-1]:
            index = key & 0x7FFFFFFF
            if category == (CATEGORY_TYPE | index) or (not key & 0x80000000 and index in parents):
                choice = branch
                break
        tree = choice
    return tree


def load_category_parents(source):
    parents = {}
    if source is not None:
        for path in sorted(source.glob("*.015.xml")):
            xml = StuXml(path, None)
            if xml.root.tag != "STUAnimCategory":
                raise ValueError(f"{path.name}: expected an animation category")
            rows = xml.field(xml.root, "m_0E5A577A")
            category = int(guid_text(path.stem), 16)
            parents[category] = frozenset(int(row.text) for row in rows) if rows is not None else frozenset()
        if not parents:
            raise ValueError(f"No animation categories in {source}")
    return parents


def animation_durations(xml, item, category_parents):
    keys = set()
    data = xml.field(item, "m_gameData")
    tree = duration_tree(xml, data, first_animation_node(xml, data), keys)
    if tree is None:
        return {}
    if not isinstance(tree, tuple):
        return {f"0x{EMOTE_ANY_CATEGORY:016X}": tree}

    # Strict selectors can use their zero-category result for every unlisted key.
    # Inheriting selectors need the actual category's compiled parent list.
    candidates = {0: frozenset()}
    strict = all(key & 0x80000000 for key in keys)
    if strict:
        candidates.update((CATEGORY_TYPE | (key & 0x7FFFFFFF), frozenset()) for key in sorted(keys))
    else:
        candidates.update(category_parents)
    result = {}
    for category, parents in candidates.items():
        seconds = selected_duration(tree, category, parents)
        if seconds is not None:
            result[f"0x{category:016X}"] = seconds
    if strict and len(result) == len(candidates):
        result[f"0x{EMOTE_ANY_CATEGORY:016X}"] = result["0x0000000000000000"]
    return result


def extract(source, category_source=None):
    category_parents = load_category_parents(category_source)
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
            categories = animation_durations(xml, xml.resolve(item), category_parents)
            if categories:
                durations[f"0x{key:016X}"] = categories
        if durations:
            tables[guid_text(path.stem)] = durations
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing *.021.xml")
    parser.add_argument("output", type=Path, help=f"runtime data/{EMOTE_DURATION_DATA}")
    parser.add_argument("--categories", type=Path, help="directory containing *.015.xml")
    args = parser.parse_args()
    rows = extract(args.source, args.categories)
    if not rows:
        raise SystemExit("No supported animation data found")
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote model animation durations from {len(rows)} blend tree sets")


if __name__ == "__main__":
    main()
