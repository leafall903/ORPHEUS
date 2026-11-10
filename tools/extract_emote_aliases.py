"""Extract 104319 emote aliases, blend trees and target tags from DataTool STU XML."""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from extract_game_graphs import guid_text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.paths import EMOTE_BLEND_TREE_DATA, EMOTE_TAGGED_DATA

NAME = "{https://yretenai.com/dragonml/v1}name"


def target_tags(array: ET.Element, source: Path) -> list[str]:
    tags = []
    for item in array:
        guid = item.get("GUID", "")
        if not guid.endswith(".06C") or int(guid.partition(".")[0], 16) == 0:
            raise ValueError(f"{source.name}: expected a target tag GUID")
        tags.append(guid_text(guid))
    return tags


def extract_body_tags(paths: list[Path]) -> dict[str, list[str] | None]:
    bodies = {}
    for path in paths:
        root = ET.parse(path).getroot()
        if root.tag != "STUEntityDefinition" or not path.stem.endswith(".003"):
            raise ValueError(f"{path.name}: expected a .003 entity definition")
        components = list(root.iter("STUTargetTagComponent"))
        arrays = [field for component in components for field in component if field.get(NAME) == "m_targetTags"]
        if len(arrays) > 1:
            raise ValueError(f"{path.name}: ambiguous target tag component")
        tags = None
        if components:
            tags = target_tags(arrays[0], path) if arrays else []
        bodies[guid_text(path.stem)] = tags
    return bodies


def extract(source: Path) -> tuple[dict[str, str], dict[str, str], dict]:
    aliases = {}
    blend_trees = {}
    tagged = {}
    for path in sorted(source.glob("*.0A5.xml")):
        root = ET.parse(path).getroot()
        if root.tag != "STUUnlock_Emote":
            continue
        fields = {child.get(NAME): child for child in root}
        tags = fields.get("m_7E3ED979")
        alias = fields["m_emote"].get("GUID", "")
        if not alias.endswith(".014") or int(alias.partition(".")[0], 16) == 0:
            raise ValueError(f"{path.name}: m_emote must be an animation alias GUID")
        unlock = guid_text(path.stem)
        blend = fields.get("m_emoteBlendTreeSet")
        guid = blend.get("GUID", "") if blend is not None else ""
        if guid and not guid.endswith(".021"):
            raise ValueError(f"{path.name}: m_emoteBlendTreeSet must be a blend tree set GUID")
        blend_guid = guid_text(guid) if guid and int(guid.partition(".")[0], 16) else None
        if tags is not None and len(tags):
            tagged[unlock] = {
                "alias": guid_text(alias), "blend": blend_guid or "0x0", "tags": target_tags(tags, path),
            }
            continue
        aliases[unlock] = guid_text(alias)
        if blend_guid is not None:
            blend_trees[unlock] = blend_guid
    if not aliases:
        raise ValueError(f"No untagged emote unlocks in {source}")
    return aliases, blend_trees, tagged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing *.0A5.xml")
    parser.add_argument("output", type=Path, help="runtime data/emote_aliases_174.json")
    parser.add_argument("--body-definitions", nargs="+", type=Path, default=[],
                        help="entity definition .003 XML files for tagged emote targets")
    args = parser.parse_args()
    aliases, blend_trees, tagged = extract(args.source)
    bodies = extract_body_tags(args.body_definitions)
    args.output.write_text(json.dumps(aliases, indent=2) + "\n", encoding="utf-8")
    blend_output = args.output.with_name(EMOTE_BLEND_TREE_DATA)
    blend_output.write_text(json.dumps(blend_trees, indent=2) + "\n", encoding="utf-8")
    tagged_output = args.output.with_name(EMOTE_TAGGED_DATA)
    tagged_output.write_text(json.dumps({"unlocks": tagged, "bodies": bodies}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(aliases)} untagged aliases, {len(blend_trees)} blend trees and {len(tagged)} tagged unlocks.")


if __name__ == "__main__":
    main()
