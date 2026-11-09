"""Extract untagged 104319 emote aliases from DataTool's 0A5 STU XML."""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from extract_game_graphs import guid_text

NAME = "{https://yretenai.com/dragonml/v1}name"


def extract(source: Path) -> tuple[dict[str, str], int]:
    aliases = {}
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
        aliases[guid_text(path.stem)] = guid_text(alias)
    if not aliases:
        raise ValueError(f"No untagged emote unlocks in {source}")
    return aliases, tagged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing *.0A5.xml")
    parser.add_argument("output", type=Path, help="runtime data/emote_aliases_174.json")
    args = parser.parse_args()
    aliases, tagged = extract(args.source)
    args.output.write_text(json.dumps(aliases, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(aliases)} aliases. Skipped {tagged} emotes with target tags.")


if __name__ == "__main__":
    main()
