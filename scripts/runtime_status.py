"""Collect on-apply StateScript bindings from local 104319 status definitions."""

from pathlib import Path
import re
from xml.etree import ElementTree as ET


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
