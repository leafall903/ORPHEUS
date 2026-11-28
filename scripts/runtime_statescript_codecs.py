"""Generate data/statescript_codecs_174.json from the 104319 wire codec specification and local graphs."""

import gzip
import json
from pathlib import Path
import re


def _bits(f: str, n: int = 1, block: str | None = None) -> dict:
    op = {"op": "bits", "n": n, "f": f}
    if block is not None:
        op["block"] = block
    return op


def _f32(f: str) -> dict:
    return {"op": "f32", "f": f}


def _w_u16(f: str) -> dict:
    return {"op": "w_u16", "f": f}


def _var_b(f: str) -> dict:
    return {"op": "var_b", "f": f}


def _signed_var(f: str) -> dict:
    return {"op": "signed_var", "f": f}


def _entity(f: str) -> dict:
    return {"op": "entity_id", "f": f}


def _value(f: str) -> dict:
    return {"op": "value", "f": f}


def _link(f: str = "link") -> dict:
    return {"op": "link", "f": f}


def _when(cond: str, then: list[dict], otherwise: list[dict] | None = None) -> dict:
    return {"op": "if", "c": cond, "then": list(then), "else": list(otherwise or ())}


def _repeat(count: str | int, f: str, body: list[dict]) -> dict:
    return {"op": "repeat", "count": count, "f": f, "body": list(body)}


_STACK = [_when("H", [_bits("top"), _bits("under")])]
_TIME = _when("has_time", [_bits("time_set"), _when("time_set", [_var_b("time")])])
_EXTRA = [_entity("entity"), _bits("has_extra"), _when("has_extra", [_var_b("extra")])]

# Wire payload layouts for every 104319 StateScript state codec family.
CODEC_LAYOUTS: dict[str, tuple[str, list[dict] | None]] = {
    "none": ("VERIFIED", []),
    "026ED8D3": ("UNVERIFIED", None),
    "9E3A8497": ("UNVERIFIED", None),
    "C72FF969": ("UNVERIFIED", None),
    "239B1E64": ("VERIFIED", [_var_b("value"), _bits("flag")]),
    "63B7ADFD": ("VERIFIED", [_bits("a"), _when("a", [_bits("b"), _var_b("value")])]),
    "861D9015": (
        "VERIFIED",
        [
            _bits("a"),
            _when(
                "a",
                [
                    _bits("v0", 16),
                    _bits("v1", 16),
                    _bits("v2", 32),
                    _bits("v3", 32),
                    _bits("v4", 32),
                    _bits("v5", 16),
                    _bits("v6", 16),
                    _bits("has_entity"),
                    _when("has_entity", _EXTRA),
                    _bits("g"),
                ],
            ),
        ],
    ),
    "ability": (
        "VERIFIED",
        [
            _bits("flags", 8, block="7EEFB57A"),
            _link(),
            _bits("has_cur"),
            _when("has_cur", [_f32("cur"), _bits("has_rate"), _when("has_rate", [_f32("rate")]), _var_b("t")]),
        ],
    ),
    "anim": ("VERIFIED", [_link(), _bits("counter", 3), _bits("has_time"), _TIME]),
    "bit": ("VERIFIED", [_bits("value")]),
    "blink": ("VERIFIED", [_when("!H", [_bits("value", 32)])]),
    "button": ("VERIFIED", [_when("H", [_bits("counter", 4)])]),
    "chase": (
        "VERIFIED",
        [
            _bits("vector"),
            _bits("has_remaining"),
            _bits("reached"),
            _f32("x"),
            _when("vector", [_f32("y"), _f32("z")]),
            _when(
                "has_remaining",
                [_bits("big"), _when("big", [_bits("remaining", 32)], [_bits("remaining", 16)])],
            ),
            _bits("has_last"),
            _when("has_last", [_var_b("t")]),
        ],
    ),
    "counter2": ("VERIFIED", [_bits("counter", 2)]),
    "flags13": ("VERIFIED", [_bits("low", 6), _bits("more"), _when("more", [_bits("high", 7)])]),
    "frames": ("VERIFIED", [_bits("target", 32, block="3D70B9F1")]),
    "hits": (
        "VERIFIED",
        [
            _w_u16("n"),
            _repeat(
                "n",
                "hits",
                [
                    _value("value"),
                    _entity("entity"),
                    _when("valid:entity", [_var_b("extra")]),
                    _w_u16("w"),
                    _bits("a"),
                    _bits("b"),
                ],
            ),
            _bits("flag"),
        ],
    ),
    "link": ("VERIFIED", [_link()]),
    "linked_target": (
        "VERIFIED",
        [
            _link(),
            _when(
                "H&full",
                [
                    _bits("has_entity"),
                    _when("has_entity", [_entity("entity")]),
                    _repeat(7, "values", [_bits("set"), _when("set", [_f32("value")])]),
                ],
            ),
        ],
    ),
    "message": (
        "VERIFIED",
        [
            _when("H", [_bits("has_sender"), _when("has_sender", [_bits("sender_kind"), _var_b("sender")])]),
            _bits("stacked"),
        ],
    ),
    "none+u8": ("VERIFIED", [_bits("m_B4802CE3", 8, block="B4802CE3")]),
    "none+u16": ("VERIFIED", [_bits("m_AE2D8911", 16, block="AE2D8911")]),
    "pulser": ("VERIFIED", [_bits("fresh"), _bits("count", 8)]),
    "send": ("VERIFIED", [_bits("has_reply"), _when("has_reply", [_w_u16("reply")])]),
    "shockwave": (
        "VERIFIED",
        [
            _bits("has_list"),
            _when(
                "has_list",
                [_w_u16("n"), _repeat("n", "items", [_w_u16("a"), _bits("b", 16), _bits("c", 32)]), _f32("f")],
            ),
        ],
    ),
    "stack": ("VERIFIED", _STACK),
    "stack_counter": ("VERIFIED", [*_STACK, _w_u16("counter")]),
    "start": ("VERIFIED", [_when("H", [_signed_var("t")])]),
    "subscript": ("VERIFIED", [_when("H", [_w_u16("child"), _bits("flag")])]),
    "switch": ("VERIFIED", [_bits("current")]),
    "targets": (
        "VERIFIED",
        [
            _bits("has_list"),
            _when("has_list", [_w_u16("n"), _repeat("n", "targets", [_bits("entity", 32), _var_b("age")])]),
        ],
    ),
    "time16+s64": ("VERIFIED", [_bits("m_0007BF47", 64, block="0007BF47"), _bits("time", 16)]),
    "two_bits": ("VERIFIED", [_bits("a"), _when("!a", [_bits("b")])]),
    "two_vectors+s64": (
        "VERIFIED",
        [
            _bits("m_0007BF47", 64, block="0007BF47"),
            _bits("a"),
            _bits("b"),
            _when("b", [_repeat(6, "v", [_f32("x")])]),
        ],
    ),
    "value_slot": ("VERIFIED", [_bits("which"), _bits("value", 32)]),
    "volley": (
        "VERIFIED",
        [
            _when(
                "H",
                [
                    _signed_var("t1"),
                    _bits("has_offset"),
                    _when("has_offset", [_signed_var("offset"), _w_u16("subindex")]),
                ],
            ),
            _bits("has_volleys"),
            _when("has_volleys", [_w_u16("volleys")]),
            _bits("counter", 6),
        ],
    ),
}

# Non-empty StateScript state wire codecs for build 104319: (hash, class_name, codec, stec).
NON_TRIVIAL_STATES: tuple[tuple[str, str, str, bool], ...] = (
    ("01D67FA0", "STUStatescriptStateShockwave", "shockwave", False),
    ("026ED8D3", "STU_026ED8D3", "026ED8D3", False),
    ("07AD90EE", "STU_07AD90EE", "hits", False),
    ("1B93D61B", "STU_1B93D61B", "link", False),
    ("239B1E64", "STU_239B1E64", "239B1E64", False),
    ("250A27EF", "STU_250A27EF", "subscript", False),
    ("25CBEFB0", "STU_25CBEFB0", "link", False),
    ("274FF373", "STU_274FF373", "link", False),
    ("2F07848F", "STU_2F07848F", "value_slot", True),
    ("33A46033", "STU_33A46033", "link", False),
    ("35E573E5", "STU_35E573E5", "link", False),
    ("3716E1D4", "STU_3716E1D4", "link", False),
    ("37D754C8", "STU_37D754C8", "subscript", False),
    ("38EE1100", "STU_38EE1100", "send", False),
    ("3BC72DE1", "STU_3BC72DE1", "link", False),
    ("41B3FC2B", "STU_41B3FC2B", "time16+s64", False),
    ("41E3C7A7", "STUStatescriptStateTrackTargets", "targets", False),
    ("43994F1B", "STU_43994F1B", "link", False),
    ("43DB06B1", "STU_43DB06B1", "none+u8", False),
    ("491F6FF1", "STU_491F6FF1", "stack_counter", False),
    ("4A67370F", "STU_4A67370F", "none+u16", False),
    ("4A91CB8C", "STU_4A91CB8C", "link", False),
    ("4BBC5D09", "STU_4BBC5D09", "start", False),
    ("4BD2E886", "STU_4BD2E886", "bit", False),
    ("4C96470A", "STU_4C96470A", "none+u8", False),
    ("4D076571", "STU_4D076571", "link", False),
    ("51BBFB1B", "STU_51BBFB1B", "link", False),
    ("531AE5E5", "STU_531AE5E5", "subscript", False),
    ("550D0C5C", "STU_550D0C5C", "hits", False),
    ("594C2D80", "STUStatescriptStateStack", "stack", False),
    ("6226036A", "STUStatescriptStateCombatModFilter", "link", False),
    ("63B7ADFD", "STU_63B7ADFD", "63B7ADFD", False),
    ("691BFA55", "STU_691BFA55", "counter2", True),
    ("6AE5D1A1", "STUStatescriptStateChaseVar", "chase", False),
    ("746F8777", "STU_746F8777", "two_vectors+s64", False),
    ("75D1C308", "STU_75D1C308", "link", False),
    ("75DE77A6", "STU_75DE77A6", "link", False),
    ("76F0CF47", "STUStatescriptStateBooleanSwitch", "switch", False),
    ("77E13026", "STU_77E13026", "hits", False),
    ("7CA2FBAF", "STU_7CA2FBAF", "counter2", False),
    ("818FAE46", "STU_818FAE46", "subscript", False),
    ("861D9015", "STU_861D9015", "861D9015", False),
    ("9028E035", "STU_9028E035", "link", False),
    ("9163D489", "STU_9163D489", "link", False),
    ("93B8758C", "STU_93B8758C", "link", False),
    ("96DB5529", "STU_96DB5529", "link", False),
    ("96F1DBCF", "STU_96F1DBCF", "flags13", False),
    ("9935FBC8", "STU_9935FBC8", "linked_target", False),
    ("99B885B8", "STUStatescriptStateBlinkEffect", "blink", False),
    ("9D6CF8AC", "STUStatescriptStateGameMessageEntry", "message", False),
    ("9E3A8497", "STU_9E3A8497", "9E3A8497", False),
    ("A875CA49", "STU_A875CA49", "link", False),
    ("A97E6999", "STU_A97E6999", "stack", False),
    ("AA6ADB38", "STU_AA6ADB38", "link", False),
    ("ADE0C4CA", "STU_ADE0C4CA", "link", False),
    ("B480A974", "STUStatescriptStateLogicalButton", "button", True),
    ("B591EBFE", "STU_B591EBFE", "two_bits", False),
    ("B9898052", "STU_B9898052", "pulser", False),
    ("BEAB146F", "STU_BEAB146F", "link", False),
    ("C18969E1", "STU_C18969E1", "hits", False),
    ("C38B92B4", "STUStatescriptStateAbility", "ability", False),
    ("C72FF969", "STU_C72FF969", "C72FF969", False),
    ("CA60E86E", "STUStatescriptStateAnim", "anim", False),
    ("D74D4F47", "STU_D74D4F47", "frames", False),
    ("E3C424E1", "STU_E3C424E1", "link", False),
    ("E3E443AD", "STU_E3E443AD", "link", False),
    ("E4A30BCF", "STU_E4A30BCF", "link", False),
    ("EED6D952", "STUStatescriptStateWeaponVolley", "volley", True),
    ("F43F04CA", "STU_F43F04CA", "link", False),
    ("F4BB90EA", "STU_F4BB90EA", "link", False),
)


def _crc32_stu_name(name: str) -> str:
    """Compute TankLib's CRC32 STU class hash for named classes, or extract hex from STU_XXXXXXXX."""
    match = re.fullmatch(r"STU_([0-9A-Fa-f]{8})", name)
    if match:
        return match.group(1).upper()
    import zlib
    return f"{zlib.crc32(name.encode('ascii')) & 0xFFFFFFFF:08X}"


def _owlib_state_classes(owlib_types: Path) -> dict[str, str]:
    """Scan TankLib/STU/Types for every STUStatescriptState subclass: {hash: name}."""
    if not owlib_types.is_dir():
        return {}
    parents: dict[str, str] = {}
    hashes: dict[str, str] = {}
    attr_re = re.compile(r"\[STU\(0x([0-9A-Fa-f]+)")
    cls_re = re.compile(r"public\s+class\s+(\w+)\s*:\s*(\w+)")
    for path in owlib_types.glob("STU*.cs"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        cm = cls_re.search(text)
        if not cm:
            continue
        name, base = cm.group(1), cm.group(2)
        parents[name] = base
        am = attr_re.search(text)
        if am:
            hashes[name] = f"{int(am.group(1), 16):08X}"
    result: dict[str, str] = {}
    for name in parents:
        cur = name
        seen = set()
        while cur and cur not in seen:
            if cur == "STUStatescriptState":
                key = hashes.get(name) or _crc32_stu_name(name)
                result[key] = name
                break
            seen.add(cur)
            cur = parents.get(cur)
    return result


def build_statescript_codecs(
    graph_bundle: Path | None = None,
    owlib_types: Path | None = None,
) -> dict:
    """Build a complete 104319 statescript_codecs_174.json table."""
    known_names: dict[str, str] = {}
    if owlib_types is not None:
        known_names.update(_owlib_state_classes(owlib_types))

    if graph_bundle is not None and graph_bundle.is_file():
        with gzip.open(graph_bundle, "rt", encoding="utf-8") as stream:
            bundle = json.load(stream)
        for graph in bundle.get("graphs", ()):
            for node in graph.get("nodes", ()):
                if node.get("state") is None:
                    continue
                cls = node.get("class")
                if isinstance(cls, str) and cls:
                    h = _crc32_stu_name(cls)
                    known_names.setdefault(h, cls)

    classes: dict[str, dict] = {}
    # Add all discovered state classes as verified zero-payload ("none") defaults first.
    for h, name in sorted(known_names.items()):
        classes[h] = {
            "name": name,
            "stec": False,
            "codec": "none",
            "layout": [],
            "status": "VERIFIED",
        }

    # Overlay the 70 non-trivial wire codecs and stec flags.
    for h, name, codec, stec in NON_TRIVIAL_STATES:
        status, layout = CODEC_LAYOUTS[codec]
        classes[h] = {
            "name": name,
            "stec": stec,
            "codec": codec,
            "layout": layout,
            "status": status,
        }

    verified = sum(1 for row in classes.values() if row["status"] == "VERIFIED")
    unverified = len(classes) - verified
    return {
        "build": 104319,
        "generator": "scripts/runtime_statescript_codecs.py",
        "stec": sorted(h for h, row in classes.items() if row["stec"]),
        "classes": dict(sorted(classes.items())),
        "counts": {"VERIFIED": verified, "UNVERIFIED": unverified},
    }


def write_statescript_codecs(
    output: Path,
    graph_bundle: Path | None = None,
    owlib_types: Path | None = None,
) -> int:
    table = build_statescript_codecs(graph_bundle=graph_bundle, owlib_types=owlib_types)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(table), encoding="utf-8")
    return len(table["classes"])
