"""Extract GetGameRulesetValue profiles from local DataTool 0C5, 075 and 0C6 XML."""

from functools import cache
import json
import math
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from build_runtime_state_metadata import guid_key, unique_entries


NAME = "{https://yretenai.com/dragonml/v1}name"


def _load(path, root_type):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise ValueError(f"Malformed {path}: {error}") from error
    if root.tag != root_type:
        raise ValueError(f"Expected {root_type} in {path}, found {root.tag}")
    return root


def _field(node, name):
    values = [child for child in node if child.get(NAME) == name]
    if len(values) != 1:
        raise ValueError(f"Expected one {node.tag}.{name}, found {len(values)}")
    return values[0]


def _array(node, name):
    if node.get(name) == "{null}":
        if any(child.get(NAME) == name for child in node):
            raise ValueError(f"Conflicting null and array for {node.tag}.{name}")
        return ()
    return _field(node, name)


def _reference(node, family):
    ref = node.get("GUID", "")
    if not re.fullmatch(r"[0-9A-F]{12}\." + family, ref):
        raise ValueError(f"Expected a {family} resource reference, found {ref!r}")
    return ref


def _value(node):
    raw = node.get("m_default")
    if node.tag == "STU_776E5ADD":
        # The schema enum's default is an STUIdentifier (resource type 01C), VM type 4.
        ref = _reference(_field(node, "m_default"), "01C")
        return guid_key("0x0D80" + ref[:12], 0x0D80)
    if raw is not None:
        if node.tag == "STU_8A8AA0A4" and raw in ("0", "1"):
            return raw == "1"
        if node.tag == "STU_118786E9":
            value = int(raw)
            if -(1 << 31) <= value < (1 << 31):
                return value
        if node.tag == "STU_A499C365":
            value = float(raw.replace(",", "."))
            if math.isfinite(value):
                return value
    raise ValueError(f"Unsupported or invalid 0C6 default {node.tag} {raw!r}")


def _schema(path):
    entries = {}
    for entry in _array(_load(path, "STUGameRulesetSchema"), "m_entries"):
        if entry.tag != "STUGameRulesetSchemaEntry":
            raise ValueError(f"Unexpected {entry.tag} in {path}")
        key = int(_reference(_field(entry, "m_3E783677"), "01C")[:12], 16)
        if not 0 < key <= 0xFFFFFFFF or key in entries:
            raise ValueError(f"Invalid or duplicate rule {key} in {path}")
        entries[key] = _value(_field(entry, "m_value"))
    return entries


def collect_rulesets(xml: Path, hero_table: Path, modes: list[str]) -> list[dict]:
    """Build mode and playable-hero rules. Schema list order defines first-match precedence."""
    heroes = json.loads(hero_table.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_entries)
    if not isinstance(heroes, dict) or not heroes:
        raise ValueError("Expected a non-empty playable hero catalog")
    schema = cache(_schema)

    def defaults(guid, family, root_type):
        model = _load(xml / family / f"{guid[-12:]}.{family}.xml", root_type)
        entries = {}
        for ref in _array(model, "m_gameRulesetSchemas"):
            filename = _reference(ref, "0C6") + ".xml"
            for key, value in schema(xml / "0C6" / filename).items():
                entries.setdefault(key, value)
        return entries

    hero_defaults = {}
    for hero in sorted(heroes):
        canonical = guid_key(hero, 0x02E0)
        if canonical in hero_defaults:
            raise ValueError(f"Duplicate playable hero {canonical}")
        hero_defaults[canonical] = defaults(canonical, "075", "STUHero")
    profiles = {}
    for mode in modes:
        canonical = guid_key(mode, 0x0230)
        if canonical in profiles:
            raise ValueError(f"Duplicate selected mode {canonical}")
        mode_values = defaults(canonical, "0C5", "STUGameMode")
        rows = [{"id": key, "value": mode_values[key]} for key in sorted(mode_values)]
        # GetGameRulesetValue searches the mode schemas before the hero schemas.
        # Rust consults body overrides first, so only emit unshadowed hero keys.
        for hero, values in hero_defaults.items():
            rows.extend({"id": key, "body": hero, "value": values[key]}
                        for key in sorted(values.keys() - mode_values.keys()))
        profiles[canonical] = {"mode": canonical, "rules": rows}
    return list(profiles.values())
