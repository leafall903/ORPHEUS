"""Collect on-apply StateScript bindings from local 104319 status definitions."""

import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET


from runtime_graph_closure import dependency_issue


STATUS_SCRIPTS = "status_scripts_174.json"
NS = "{https://yretenai.com/dragonml/v1}"


def status_scripts(xml: Path):
    result = {}
    for path in sorted(xml.glob("*.053.xml")):
        if not re.fullmatch(r"[0-9A-Fa-f]{12}\.053\.xml", path.name):
            raise ValueError(f"Invalid status resource name: {path.name}")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as error:
            raise ValueError(f"Malformed status definition {path.name}: {error}") from error
        if root.tag != "STU_76F2C784":
            continue
        entries = [child for child in root if child.get(NS + "name") == "m_5A4051BC"]
        if not entries:
            continue
        if (len(entries) != 1 or root.get("m_5A4051BC") is not None or
                entries[0].tag != NS + "ref" or entries[0].get("Target") != "STU_6BE90C5C"):
            raise ValueError(f"Ambiguous on-apply script field in {path.name}")
        guid = entries[0].get("GUID", "")
        if not re.fullmatch(r"[0-9A-Fa-f]{12}\.01B", guid):
            raise ValueError(f"Expected .01B on-apply script in {path.name}")
        index = int(guid[:12], 16)
        if not 0 < index <= 0xffffffff:
            raise ValueError(f"Invalid native script index in {path.name}: {guid}")
        result["0x04A0" + path.name[:12].upper()] = index
    return {"version": 1, "on_apply": result}

def read_status_roots(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = data.get("on_apply") if isinstance(data, dict) and data.get("version") == 1 else None
    if not isinstance(mapping, dict) or not mapping or any(
            not re.fullmatch(r"0x04A0[0-9A-Fa-f]{12}", key) or type(value) is not int or not 0 < value <= 0xffffffff
            for key, value in mapping.items()):
        raise ValueError("Invalid status script catalog or on-apply binding")
    return set(mapping.values())


def status_graph_issue(bundle, path):
    if not path.is_file():
        return f"Missing status script catalog: {path.name}"
    roots = read_status_roots(path)
    loaded = {row["index"] for row in bundle["graphs"]}
    edges = bundle.get("status_graph_dependencies", {})
    if not isinstance(edges, dict):
        return "Invalid status graph dependency table."
    return dependency_issue(roots, edges, loaded, "status")
