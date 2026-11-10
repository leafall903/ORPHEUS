"""Extract untagged 104319 emote aliases and blend trees from DataTool 0A5 STU XML."""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from extract_game_graphs import guid_text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.paths import EMOTE_BLEND_TREE_DATA

NAME = "{https://yretenai.com/dragonml/v1}name"


def extract(source: Path) -> tuple[dict[str, str], dict[str, str], int]:
    aliases = {}
    blend_trees = {}
    tagged = 0
    for path in sorted(source.glob("*.0A5.xml")):
        root = ET.parse(path).getroot()
        if root.tag != "STUUnlock_Emote":
            continue
        fields = {child.get(NAME): child for child in root}
        tags = fields.get("m_7E3ED979")
        if tags is not None and len(tags):
            tagged += 1
            continue
        alias = fields["m_emote"].get("GUID", "")
        if not alias.endswith(".014") or int(alias.partition(".")[0], 16) == 0:
            raise ValueError(f"{path.name}: m_emote must be an animation alias GUID")
        unlock = guid_text(path.stem)
        aliases[unlock] = guid_text(alias)
        blend = fields.get("m_emoteBlendTreeSet")
        guid = blend.get("GUID", "") if blend is not None else ""
        if guid:
            if not guid.endswith(".021"):
                raise ValueError(f"{path.name}: m_emoteBlendTreeSet must be a blend tree set GUID")
            if int(guid.partition(".")[0], 16):
                blend_trees[unlock] = guid_text(guid)
    if not aliases:
        raise ValueError(f"No untagged emote unlocks in {source}")
    return aliases, blend_trees, tagged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing *.0A5.xml")
    parser.add_argument("output", type=Path, help="runtime data/emote_aliases_174.json")
    args = parser.parse_args()
    aliases, blend_trees, tagged = extract(args.source)
    args.output.write_text(json.dumps(aliases, indent=2) + "\n", encoding="utf-8")
    blend_output = args.output.with_name(EMOTE_BLEND_TREE_DATA)
    blend_output.write_text(json.dumps(blend_trees, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(aliases)} aliases and {len(blend_trees)} blend trees. Skipped {tagged} tagged emotes.")


if __name__ == "__main__":
    main()
