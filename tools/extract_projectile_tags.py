"""Extract target tags from local build104319 entity XML; generated data stays external."""

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.extract_game_graphs import NS, full_guid


def entity_tags(path):
    root = ET.parse(path).getroot()
    if root.tag != "STUEntityDefinition":
        raise ValueError(f"expected entity definition: {path.name}")
    objects = {element.get(NS + "id"): element for element in root.iter()
               if element.get(NS + "id") is not None and element.tag != NS + "ref"}

    def resolve(element):
        visited = set()
        while element.tag == NS + "ref" and element.get("GUID") is None:
            key = element.get(NS + "ref") or element.get(NS + "id")
            if key in visited:
                raise ValueError(f"cyclic XML reference in {path.name}")
            visited.add(key)
            element = objects[key]
        return element

    tags = set()
    mapping = next((resolve(child) for child in root if child.get(NS + "name") == "m_componentMap"), ())
    for entry in mapping:
        if entry.tag != "STUTargetTagComponent":
            continue
        component = next((resolve(child) for child in entry
                          if child.get(NS + "name") == "dragon:value"), entry)
        for child in component:
            if child.get(NS + "name") != "m_targetTags":
                continue
            for item in resolve(child):
                item = resolve(item)
                guid = item.get("GUID")
                if guid == "000000000000.001":
                    continue  # explicit null resource GUID
                if guid is None or not re.fullmatch(r"[0-9A-Fa-f]{1,12}\.06C", guid):
                    raise ValueError(f"invalid projectile tag in {path.name}")
                tags.add(f"0x{full_guid(guid):016X}")
    return sorted(tags)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entities = {}
    for path in sorted((args.xml / "003").glob("*.003.xml")):
        key = path.name.removesuffix(".xml")
        if not re.fullmatch(r"[0-9A-Fa-f]{1,12}\.003", key):
            raise ValueError(f"invalid entity filename {path.name}")
        entities[f"0x{full_guid(key):016X}"] = entity_tags(path)
    if not entities:
        raise ValueError("no003 definitions found")
    args.output.write_text(json.dumps({"version": 1, "build": "1.74.0.0.104319", "entities": entities},
                                     indent=2) + "\n", encoding="utf-8")
    print(f"Exported target tags for {len(entities)} entity definitions")


if __name__ == "__main__":
    main()
