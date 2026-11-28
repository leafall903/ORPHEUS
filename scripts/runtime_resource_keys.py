"""Extract data/resource_keys_174.json from local DataTool 090 (STUResourceKey) XML exports."""

import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

NS = "{https://yretenai.com/dragonml/v1}"
KEY_BYTES = 16


def _reverse_key_id(hex_id: str) -> str | None:
    text = hex_id.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{16}", text):
        return None
    raw = bytes.fromhex(text)
    if not any(raw):
        return None
    return raw[::-1].hex().upper()


def _parse_key_xml(path: Path) -> tuple[str | None, str] | None:
    raw = path.read_text(encoding="utf-8-sig")
    if "<dragon:null" in raw:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError(f"Malformed resource key XML {path.name}: {error}") from error
    if root.tag != "STUResourceKey":
        return None
    key_id_el = next((c for c in root if c.get(NS + "name") == "m_keyID"), None)
    key_el = next((c for c in root if c.get(NS + "name") == "m_key"), None)
    if key_el is None:
        return None
    try:
        key_bytes = bytes(int((child.text or "").strip()) for child in key_el)
    except ValueError as error:
        raise ValueError(f"Invalid key byte in {path.name}: {error}") from error
    if len(key_bytes) != KEY_BYTES:
        raise ValueError(f"Expected {KEY_BYTES} key bytes in {path.name}, found {len(key_bytes)}")
    name = _reverse_key_id(key_id_el.get("Value", "")) if key_id_el is not None else None
    return name, key_bytes.hex().upper()


def collect_resource_keys(xml_dir: Path) -> dict[str, dict[str, str | None]]:
    """Parse every STUResourceKey (.090.xml) record into the runtime key catalog format."""
    if not xml_dir.is_dir():
        raise ValueError(f"Missing 090 resource key XML directory: {xml_dir}")
    records: list[tuple[int, str | None, str]] = []
    for path in sorted(xml_dir.glob("*.090.xml")):
        stem = path.name.split(".", 1)[0]
        if not re.fullmatch(r"[0-9a-fA-F]+", stem):
            continue
        parsed = _parse_key_xml(path)
        if parsed is None:
            continue
        name, key_hex = parsed
        records.append((int(stem, 16), name, key_hex))
    if not records:
        raise ValueError(f"No valid STUResourceKey records found in {xml_dir}")
    records.sort(key=lambda item: item[0])
    return {
        f"{index:X}": {"name": name, "key": key_hex, "note": ""}
        for index, name, key_hex in records
    }


def write_resource_keys(xml_dir: Path, output: Path) -> int:
    catalog = collect_resource_keys(xml_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return len(catalog)
