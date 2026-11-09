"""Extract voice-line playback effects from 104319 DataTool 0A5 STU XML."""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from extract_game_graphs import guid_text

NAME = "{https://yretenai.com/dragonml/v1}name"


def extract(source: Path) -> dict[str, str]:
    effects = {}
    for path in sorted(source.glob("*.0A5.xml")):
        root = ET.parse(path).getroot()
        if root.tag != "STUUnlock_VoiceLine":
            continue
        effect = next((field.get("GUID") for field in root if field.get(NAME) == "m_F57B051E"), None)
        if effect is not None:
            if not effect.endswith(".00D") or int(effect.partition(".")[0], 16) == 0:
                raise ValueError(f"{path.name}: m_F57B051E must be an effect GUID")
            effects[guid_text(path.stem)] = guid_text(effect)
    if not effects:
        raise ValueError(f"No voice effects in {source}")
    return effects


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing *.0A5.xml")
    parser.add_argument("output", type=Path, help="runtime data/voice_effects_174.json")
    args = parser.parse_args()
    effects = extract(args.source)
    args.output.write_text(json.dumps(effects, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(effects)} voice effects.")


if __name__ == "__main__":
    main()
