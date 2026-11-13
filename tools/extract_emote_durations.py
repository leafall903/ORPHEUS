"""Extract animation durations, primary clip GUIDs and scalar tracks from 104319 .021 XML."""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from functools import partial
from itertools import pairwise
from pathlib import Path

from extract_game_graphs import StuXml, guid_text

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]
from ow174.paths import EMOTE_ANY_CATEGORY, EMOTE_DURATION_DATA, GREETING_CLIP_DATA, GREETING_DURATION_DATA

CATEGORY_TYPE = 0x0280000000000000
GREETING_ALIASES = frozenset((0x0C800000000002DE, 0x0C800000000001EE))


def first_animation_node(xml, game_data):
    """Follow the native F4EA30 child before its IK output."""
    if game_data is None or game_data.tag != "STU_7D00A73D":
        return None
    nodes = xml.field(game_data, "m_nodes")
    if nodes is None or not len(nodes):
        return None
    index = 0
    visited = set()
    while index not in visited:
        visited.add(index)
        node = xml.resolve(nodes[index])
        if node.tag != "STU_30C2E3AE":
            return node
        index = int(node.get("m_38CF6CD3", "0"))
        if not 0 < index < len(nodes):
            break
    return None


def embedded_data(xml, node):
    if node is not None and node.tag == "STU_331B7B06" and node.get("m_53A1B672", "0") == "0":
        return xml.field(node, "m_EB45D767")
    return None


def primary_record(xml, game_data, leaf, clips):
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
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    if not clips:
        return seconds
    # The selected record holds the primary clip even when the graph lists auxiliary clips.
    clip = int(row.get("m_584DEF6E", "0"))
    return f"0x{clip:016X}" if clip >> 48 == 0x0A00 and clip & 0xFFFFFFFFFFFF else None


@dataclass(frozen=True)
class ScalarTrack:
    times: tuple[float, ...] = ()
    values: tuple[float, ...] = ()


def scalar_track(xml, _game_data, leaf):
    if leaf is None or leaf.tag != "STU_3C8FB392":
        return None
    # With a sample group, F517F0 writes this index plus one at result +14.
    index = int(leaf.get("m_76E8D211", "-1"))
    if index < 0 or leaf.get("m_3A9A1E85", "0") == "0":
        return ScalarTrack()
    model = xml.field(xml.root, "m_D1467FCA")
    if model is None or model.tag != "STU_B38E3226":
        return None
    rows = xml.field(model, "m_A867458D")
    if rows is None or index >= len(rows):
        return None
    row = xml.resolve(rows[index])
    if row.tag != "STU_CE2BEF36":
        return None
    track = xml.field(row, "m_3D2FFAAA")
    if track is None:
        return ScalarTrack()
    if track.tag != "STU_0AB74EE9":
        return None
    times = xml.field(track, "m_4DC48800")
    values = xml.field(track, "m_1EB5B02B")
    if times is None or values is None or not len(values):
        return ScalarTrack()
    if len(times) != len(values):
        return None
    times = tuple(float(xml.resolve(item).text) for item in times)
    values = tuple(float(xml.resolve(item).text) for item in values)
    if not all(map(math.isfinite, times + values)) or any(a > b for a, b in pairwise(times)):
        return None
    return ScalarTrack(times, values)


def primary_tree(xml, data, node, keys, read_record, depth=0):
    # C085B0 suppresses auxiliary nodes. F4CBC0 limits nested alias references.
    if depth >= 48 or node is None:
        return None
    if node.tag == "STU_3C8FB392":
        return read_record(xml, data, node)
    if node.tag == "STU_331B7B06":
        nested = embedded_data(xml, node)
        return primary_tree(xml, nested, first_animation_node(xml, nested), keys, read_record, depth + 1)
    if node.tag == "STU_30C2E3AE":
        # F4EA30 delegates to its child through F524B0 before adding IK output.
        # Category branches can contain this wrapper, not only the root node.
        nodes = xml.field(data, "m_nodes")
        index = int(node.get("m_38CF6CD3", "0"))
        if nodes is None or not 0 < index < len(nodes):
            return None
        return primary_tree(xml, data, xml.resolve(nodes[index]), keys, read_record, depth + 1)
    if node.tag == "STU_4178508A" and node.get("m_5E009A60") == "1":
        # F4D310 picks adjacent branches. The result is deterministic only when all branches agree.
        indexes = xml.field(node, "m_540E2FC8")
        thresholds = xml.field(node, "m_0EC67F50")
        nodes = xml.field(data, "m_nodes")
        if (
            nodes is None
            or indexes is None
            or thresholds is None
            or not len(indexes)
            or len(indexes) != len(thresholds)
        ):
            return None
        stops = [float(item.text) for item in thresholds]
        if not all(math.isfinite(stop) for stop in stops) or any(a >= b for a, b in pairwise(stops)):
            return None
        children = [int(item.text) for item in indexes]
        if any(not 0 < index < len(nodes) for index in children):
            return None
        records = {read_record(xml, data, xml.resolve(nodes[index])) for index in children}
        return records.pop() if len(records) == 1 else None
    if node.tag != "STU_623B2B19" or node.get("m_E6058AAD") != "1":
        return None

    selectors = xml.field(node, "m_9F7CA0BB")
    indexes = xml.field(node, "m_540E2FC8")
    nodes = xml.field(data, "m_nodes")
    if (
        selectors is None
        or indexes is None
        or not len(selectors)
        or len(selectors) != len(indexes)
        or xml.field(node, "m_4D532CFC") is not None
        or xml.field(node, "m_53D9C839") is not None
    ):
        return None
    selectors = [int(key.text) for key in selectors]
    if selectors[-1] != 0 or any(not 0 < key <= 0xFFFFFFFF or not key & 0x7FFFFFFF for key in selectors[:-1]):
        return None
    indexes = [int(child.text) for child in indexes]
    if any(not 0 < index < len(nodes) for index in indexes):
        return None
    keys.update(selectors[:-1])
    return tuple(
        (key, primary_tree(xml, data, xml.resolve(nodes[index]), keys, read_record, depth + 1))
        for key, index in zip(selectors, indexes, strict=False)
    )


def select_primary(tree, category, parents):
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


def animation_records(xml, item, category_parents, read_record):
    keys = set()
    data = xml.field(item, "m_gameData")
    tree = primary_tree(xml, data, first_animation_node(xml, data), keys, read_record)
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
        record = select_primary(tree, category, parents)
        if record is not None:
            result[f"0x{category:016X}"] = record
    if strict and len(result) == len(candidates):
        result[f"0x{EMOTE_ANY_CATEGORY:016X}"] = result["0x0000000000000000"]
    return result


def extract(source, category_source=None, *, clips=False):
    return extract_records(source, category_source, partial(primary_record, clips=clips))


def extract_records(source, category_source, read_record):
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
        for alias, item in zip(aliases, items, strict=True):
            key = int(alias.text)
            if key >> 48 != 0x0C80 or key & 0xFFFFFFFF == 0:
                continue
            categories = animation_records(xml, xml.resolve(item), category_parents, read_record)
            if categories:
                durations[f"0x{key:016X}"] = categories
        if durations:
            tables[guid_text(path.stem)] = durations
    return tables


def body_animation_records(tables, body_definitions):
    """Bind supported alias/category records to the body's actual model tree."""
    bodies = {}
    for path in sorted(body_definitions):
        xml = StuXml(path, None)
        refs = [ref.get("GUID") for node in xml.root.iter("STUModelComponent")
                if (ref := xml.field(node, "m_animBlendTreeSet")) is not None]
        if len(refs) != 1 or not refs[0] or not refs[0].endswith(".021"):
            raise ValueError(f"{path.name}: expected one model blend tree set")
        records = tables.get(guid_text(refs[0]), {})
        if records:
            bodies[guid_text(path.stem)] = records
    return bodies


def greeting_records(tables, body_definitions, *, clips=False):
    bodies = {}
    for path in sorted(body_definitions):
        xml = StuXml(path, None)
        refs = [
            ref.get("GUID")
            for node in xml.root.iter("STUModelComponent")
            if (ref := xml.field(node, "m_animBlendTreeSet")) is not None
        ]
        if len(refs) != 1 or not refs[0] or not refs[0].endswith(".021"):
            raise ValueError(f"{path.name}: expected one model blend tree set")
        aliases = tables.get(guid_text(refs[0]), {})
        records = {alias: rows for alias, rows in aliases.items() if int(alias, 16) in GREETING_ALIASES}
        if not clips and len(records) != len(GREETING_ALIASES):
            raise ValueError(f"{path.name}: missing greeting durations for {refs[0]}")
        if records:
            bodies[guid_text(path.stem)] = records
    return bodies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing *.021.xml")
    parser.add_argument("output", type=Path, help=f"runtime data/{EMOTE_DURATION_DATA}")
    parser.add_argument("--clips-output", type=Path, help="optional primary .006 selections")
    parser.add_argument("--scalar-output", type=Path, help="optional movement scalar tracks")
    parser.add_argument("--categories", type=Path, help="directory containing *.015.xml")
    parser.add_argument("--body-duration-output", type=Path,
                        help="optional supported alias/category durations keyed by body definition")
    parser.add_argument(
        "--body-definitions",
        nargs="+",
        type=Path,
        default=[],
        help="entity definition .003 XML files for greeting gestures",
    )
    args = parser.parse_args()
    outputs = [args.output.resolve()]
    if args.body_duration_output is not None:
        outputs.append(args.body_duration_output.resolve())
        if not args.body_definitions:
            parser.error("--body-duration-output requires --body-definitions")
    if args.body_definitions:
        outputs.append(args.output.with_name(GREETING_DURATION_DATA).resolve())
    if args.clips_output is not None:
        outputs.append(args.clips_output.resolve())
        if args.body_definitions:
            outputs.append(args.clips_output.with_name(GREETING_CLIP_DATA).resolve())
    if args.scalar_output is not None:
        outputs.append(args.scalar_output.resolve())
    if len(set(outputs)) != len(outputs):
        parser.error("animation outputs require separate paths")
    rows = extract(args.source, args.categories)
    if not rows:
        raise SystemExit("No supported animation data found")
    greetings = greeting_records(rows, args.body_definitions)
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    if args.body_duration_output is not None:
        args.body_duration_output.write_text(
            json.dumps(body_animation_records(rows, args.body_definitions), indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Wrote model animation durations from {len(rows)} blend tree sets")
    if args.clips_output is not None:
        clips = extract(args.source, args.categories, clips=True)
        args.clips_output.write_text(json.dumps(clips, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote primary clip selections from {len(clips)} blend tree sets")
        greeting_clips = greeting_records(clips, args.body_definitions, clips=True)
        if greeting_clips:
            output = args.clips_output.with_name(GREETING_CLIP_DATA)
            output.write_text(json.dumps(greeting_clips, indent=2) + "\n", encoding="utf-8")
            print(f"Wrote greeting clip selections for {len(greeting_clips)} body definitions")
    if args.scalar_output is not None:
        tracks = extract_records(args.source, args.categories, scalar_track)
        args.scalar_output.write_text(
            json.dumps(tracks, indent=2, default=vars) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote movement scalar tracks from {len(tracks)} blend tree sets")
    if greetings:
        output = args.output.with_name(GREETING_DURATION_DATA)
        output.write_text(json.dumps(greetings, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote greeting durations for {len(greetings)} body definitions")


if __name__ == "__main__":
    main()
