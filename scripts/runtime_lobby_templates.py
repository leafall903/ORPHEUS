"""Synthesize clean-room lobby message templates directly from 104319 schemas and local catalogs."""

import json
from pathlib import Path
import struct

# Protocol CRCs matching rust/src/retail_templates.rs & lobby modules.
ARCADE = "00A8654E"
CONFIG = "1732EBF2"
LOBBY = "1A9879A4"
CUSTOM_GAMES = "21286B49"
RANKED = "2372A2E8"
STORE = "4BAD7A7E"
REPLAYS = "5894D085"
HERO_CATALOG = "70E5A247"
OWL_LIVE = "713D7589"
PROGRESSION = "779A8581"
MODE_RULES = "B9DCA497"
FRIENDS = "F470E2AB"
IN_CONNECT = "F5FF548A"

# Protocol constants used by rust/src/lobby_ranked.rs & lobby_presence.rs.
CARD_BASE = 0x0630_0000_0000_0000
ROLE_QUEUE = CARD_BASE | 0x1B3
OPEN_QUEUE = CARD_BASE | 0x1B4
COMPETITIVE_CTF = CARD_BASE | 0x1C3
LUCIO_CUP = CARD_BASE | 0x1AD
TANK = 0x0D80_0000_0000_0850
DAMAGE = 0x0D80_0000_0000_084E
SUPPORT = 0x0D80_0000_0000_0851
PC_PLATFORM = 15_959_616
PC_POOL = 16_025_156
CURRENT_SEASON = 32
SUPPORTED_BUILDS_KEY = 0x0422_7E56
CLIENT_BUILD = 104319

# Presence placeholder IDs rewritten by rust/src/lobby_presence.rs::Presence::records.
RETAIL_ACCOUNT = 0x425A_E13F
RETAIL_APP_ACCOUNT = 0x3A16_9DEF
RETAIL_PRO_ACCOUNT = 0x3A16_9E4C
REDACTED_NAME = "XXXXXXXXXXXXXXX"
PRESENCE_PROGRAM = 16_974
ACCOUNT_HIGH = 0x0100_0000_0000_0000
GAME_ACCOUNT_HIGH = 0x0200_0001_0050_726F

# Valid STU datetime encoding matching rust/src/lobby_clock.rs (year 2022 window, shifted to `now` at login).
# Packed STU datetime: ((year - 1900) << 40) | (month << 32) | (day << 24) | (hour << 16) | (min << 8) | sec
def _stu_dt(year: int, month: int, day: int) -> int:
    return ((year - 1900) << 40) | (month << 32) | (day << 24)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _pb_field_key(group: int, field_id: int, unique_id: int = 0) -> str:
    buf = bytearray()
    for num, val in ((1, PRESENCE_PROGRAM), (2, group), (3, field_id), (4, unique_id)):
        buf.extend(_varint((num << 3) | 0))
        buf.extend(_varint(val))
    return buf.hex()


def _pb_bool_variant(flag: bool) -> str:
    return bytes([0x10, 1 if flag else 0]).hex()


def _pb_int_variant(val: int) -> str:
    return (b"\x18" + _varint(val)).hex()


def _pb_str_variant(text: str) -> str:
    raw = text.encode("utf-8")
    return (b"\x2a" + _varint(len(raw)) + raw).hex()


def _pb_entity_variant(high: int, low: int) -> str:
    inner = b"\x09" + struct.pack("<Q", high) + b"\x11" + struct.pack("<Q", low)
    return (b"\x4a" + _varint(len(inner)) + inner).hex()


def _default_value(field: dict):
    """Build a schema-valid zero/empty JSON value for a JAM schema field."""
    typ = field["type"]
    if field.get("array"):
        return []
    count = field.get("count", 0)
    if count > 1:
        single = _default_value({**field, "count": 0, "array": False})
        return [single for _ in range(count)]
    if typ == 0:
        return False
    if typ in (1, 2, 3, 4, 5, 6, 7, 8, 9):
        return 0
    if typ in (10, 11):
        return 0.0
    if typ in (12, 13):
        return ""
    if typ == 14:
        return _default_struct(field.get("fields", ()))
    if typ == 15:
        return {"$bytes": ""}
    return 0


def _default_struct(fields: list[dict]) -> dict:
    return {f"+0x{f['off']:X}": _default_value(f) for f in fields}


def _schema_msg(schemas: dict, crc: str, msg_id: int) -> dict:
    proto = schemas.get(crc)
    if not proto or str(msg_id) not in proto:
        return {}
    return _default_struct(proto[str(msg_id)])


def _parse_guid(text: str) -> int | None:
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.startswith(("0x", "0X")):
        try:
            return int(s, 16)
        except ValueError:
            return None
    if "." in s:
        idx, kind = s.split(".", 1)
        try:
            type_bits = int(f"{int(kind, 16) - 1:012b}"[::-1], 2)
            return (type_bits << 48) | int(idx, 16)
        except ValueError:
            return None
    return None


def _preload_guids(data_dir: Path) -> tuple[list[int], list[int]]:
    maps: list[int] = []
    skins: list[int] = []
    maps_path = data_dir / "extracted_maps.json"
    if maps_path.is_file():
        raw_maps = json.loads(maps_path.read_text(encoding="utf-8-sig"))
        if isinstance(raw_maps, dict):
            for key in raw_maps:
                g = _parse_guid(key)
                if g is not None:
                    maps.append(g)
    items_path = data_dir / "extracted_items.json"
    if items_path.is_file():
        raw_items = json.loads(items_path.read_text(encoding="utf-8-sig"))
        if isinstance(raw_items, dict):
            for key, item in raw_items.items():
                if not isinstance(item, dict):
                    continue
                if item.get("Type") in ("Skin", "SkinTheme", "Hero"):
                    g = _parse_guid(item.get("GUID") or key)
                    if g is not None:
                        skins.append(g)
    return sorted(set(maps)), sorted(set(skins))


def _arcade_busy(data_dir: Path) -> list[dict]:
    cards_path = data_dir / "arcade_cards_174.json"
    if not cards_path.is_file():
        return []
    raw = json.loads(cards_path.read_text(encoding="utf-8-sig"))
    rows = []
    for key in sorted(raw):
        idx = int(key, 16) if not key.lower().startswith("0x") else int(key, 16)
        rows.append({"+0x0": CARD_BASE | idx, "+0x8": 0.5})
    return rows


def _ranked_rating_entry(role: int) -> dict:
    return {
        "+0x0": role,
        "+0x8": 0.0,
        "+0x10": PC_PLATFORM,
        "+0x14": PC_POOL,
        "+0x18": 0,
        "+0x1A": 0,
        "+0x1C": 0,
        "+0x1E": 0,
        "+0x20": 0,
        "+0x22": 0,
        "+0x23": 0,
        "+0x28": [],
        "+0x40": False,
    }


def _ranked_card(card_id: int, roles: list[int]) -> dict:
    return {
        "+0x0": [_ranked_rating_entry(r) for r in roles],
        "+0x18": card_id,
        "+0x20": 0.0,
        "+0x28": 0,
        "+0x2C": False,
    }


def _ranked_season_entry(schemas: dict, card_id: int, season_num: int, parent_card: int = 0) -> dict:
    # Build from the exact 36300 +0x98 struct schema so every field matches 104319 wire layout.
    hist_schema = next(f["fields"] for f in schemas[RANKED]["36300"] if f["off"] == 0x98)
    entry = _default_struct(hist_schema)
    start_dt = _stu_dt(2022, 1, 1)
    end_dt = _stu_dt(2022, 3, 1)
    entry["+0x18"] = card_id
    entry["+0x20"] = {"+0x0": start_dt}
    entry["+0x28"] = {"+0x0": start_dt}
    entry["+0x30"] = {"+0x0": end_dt}
    entry["+0x38"] = {"+0x0": end_dt}
    entry["+0xF0"] = parent_card
    entry["+0x118"] = season_num
    return entry


def _build_ranked_state(schemas: dict) -> dict:
    cards = [
        _ranked_card(ROLE_QUEUE, [TANK, DAMAGE, SUPPORT]),
        _ranked_card(OPEN_QUEUE, [0]),
        _ranked_card(COMPETITIVE_CTF, [0]),
    ]
    history = [
        _ranked_season_entry(schemas, ROLE_QUEUE, CURRENT_SEASON, parent_card=0),
        _ranked_season_entry(schemas, OPEN_QUEUE, 0, parent_card=ROLE_QUEUE),
        _ranked_season_entry(schemas, COMPETITIVE_CTF, 0, parent_card=0),
        _ranked_season_entry(schemas, LUCIO_CUP, 0, parent_card=0),
    ]
    running = [
        {"+0x0": ROLE_QUEUE, "+0x8": 2_592_000.0, "+0xC": True},
        {"+0x0": OPEN_QUEUE, "+0x8": 2_592_000.0, "+0xC": True},
        {"+0x0": COMPETITIVE_CTF, "+0x8": 2_592_000.0, "+0xC": True},
    ]
    return {
        "+0x78": ROLE_QUEUE,
        "+0x80": {"+0x0": cards},
        "+0x98": history,
        "+0xB0": running,
        "+0xC8": [ROLE_QUEUE, OPEN_QUEUE, COMPETITIVE_CTF, LUCIO_CUP],
    }


def _presence_field(group: int, field_id: int, value_hex: str) -> dict:
    return {
        "+0x0": 0,
        "+0x8": {"$bytes": _pb_field_key(group, field_id)},
        "+0x30": {"$bytes": value_hex},
    }


def _build_presence_messages() -> list[list]:
    now_us = 1_700_000_000 * 1_000_000
    account_record = {
        "+0x0": {"+0x0": RETAIL_ACCOUNT, "+0x8": ACCOUNT_HIGH},
        "+0x10": {"+0x0": 0, "+0x8": 0},
        "+0x20": [
            _presence_field(1, 1, _pb_str_variant("ExampleUser")),
            _presence_field(1, 4, _pb_str_variant(f"{REDACTED_NAME}#0001")),
            _presence_field(1, 7, _pb_bool_variant(False)),
            _presence_field(1, 11, _pb_bool_variant(False)),
            _presence_field(1, 12, _pb_bool_variant(False)),
            _presence_field(1, 6, _pb_int_variant(now_us)),
        ],
    }
    app_record = {
        "+0x0": {"+0x0": RETAIL_APP_ACCOUNT, "+0x8": GAME_ACCOUNT_HIGH},
        "+0x10": {"+0x0": 0, "+0x8": 0},
        "+0x20": [
            _presence_field(2, 1, _pb_bool_variant(True)),
            _presence_field(2, 2, _pb_bool_variant(False)),
            _presence_field(2, 3, _pb_int_variant(now_us)),
            _presence_field(2, 5, _pb_entity_variant(ACCOUNT_HIGH, RETAIL_ACCOUNT)),
        ],
    }
    pro_record = {
        "+0x0": {"+0x0": RETAIL_PRO_ACCOUNT, "+0x8": GAME_ACCOUNT_HIGH},
        "+0x10": {"+0x0": 0, "+0x8": 0},
        "+0x20": [
            _presence_field(2, 1, _pb_bool_variant(True)),
            _presence_field(2, 2, _pb_bool_variant(False)),
            _presence_field(2, 3, _pb_int_variant(now_us)),
            _presence_field(2, 5, _pb_entity_variant(ACCOUNT_HIGH, RETAIL_ACCOUNT)),
        ],
    }
    return [
        [FRIENDS, 27113, {"+0x78": [account_record]}],
        [FRIENDS, 27113, {"+0x78": [app_record]}],
        [FRIENDS, 27113, {"+0x78": [pro_record]}],
    ]


def _playable_heroes(data_dir: Path) -> list[int]:
    """Return sorted playable hero GUIDs from game_heroes_174.json or extracted_heroes.json."""
    gh_path = data_dir / "game_heroes_174.json"
    if gh_path.is_file():
        raw = json.loads(gh_path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            guids = [_parse_guid(k) for k in raw]
            out = sorted({g for g in guids if g is not None})
            if out:
                return out
    eh_path = data_dir / "extracted_heroes.json"
    if eh_path.is_file():
        raw = json.loads(eh_path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            guids = [
                _parse_guid(item.get("GUID") or key)
                for key, item in raw.items()
                if isinstance(item, dict) and item.get("IsHero")
            ]
            out = sorted({g for g in guids if g is not None})
            if out:
                return out
    return []


def _build_hero_catalog(schemas: dict, data_dir: Path) -> dict:
    msg = _schema_msg(schemas, HERO_CATALOG, 24900)
    heroes = _playable_heroes(data_dir)
    hero_records = []
    store_records = []
    for hero in heroes:
        hero_records.append({
            "+0x0": [],
            "+0x18": [],
            "+0x30": hero,
            "+0x38": 0,
            "+0x40": 0,
            "+0x48": 0,
            "+0x50": 0,
            "+0x58": [0, 0, 0, 0],
            "+0x70": [0, 0, 0, 0],
            "+0x88": [0, 0, 0, 0],
        })
        store_records.append({"+0x0": [], "+0x18": hero})
    msg["+0x78"] = 0
    msg["+0x80"] = hero_records
    msg["+0x98"] = store_records
    msg["+0xB0"] = []
    return msg


def synthesize_lobby_templates(schemas_path: Path, data_dir: Path) -> dict:
    """Synthesize a clean-room format-1 lobby template corpus validated against schemas_174.json."""
    schemas = json.loads(schemas_path.read_text(encoding="utf-8"))
    maps, skins = _preload_guids(data_dir)

    arcade_39802 = _schema_msg(schemas, ARCADE, 39802)
    comp_card_schema = next(
        f["fields"]
        for f in next(f["fields"] for f in schemas[ARCADE]["39802"] if f["off"] == 0x78)
        if f["off"] == 0xD0
    )
    main_comp_card = _default_struct(comp_card_schema)
    main_comp_card["+0x0"] = ROLE_QUEUE
    main_comp_card["+0x88"] = [OPEN_QUEUE]
    arcade_39802["+0x78"]["+0xD0"] = [main_comp_card]

    lobby_20814 = _schema_msg(schemas, LOBBY, 20814)
    if "+0x79" in lobby_20814:
        lobby_20814["+0x79"] = True

    server: list[list] = [
        [IN_CONNECT, 20504, {"+0x78": [], "+0x90": []}],
        [IN_CONNECT, 20505, {"+0x78": maps, "+0x90": skins, "+0xA8": [], "+0xC0": []}],
        [PROGRESSION, 24300, _schema_msg(schemas, PROGRESSION, 24300)],
        [HERO_CATALOG, 24900, _build_hero_catalog(schemas, data_dir)],
        [MODE_RULES, 27202, {"+0x78": [], "+0x90": [], "+0xA8": [], "+0xC0": []}],
        [LOBBY, 20814, lobby_20814],
        [LOBBY, 20806, _schema_msg(schemas, LOBBY, 20806)],
        [LOBBY, 20813, _schema_msg(schemas, LOBBY, 20813)],
        [LOBBY, 20821, _schema_msg(schemas, LOBBY, 20821)],
        ["AA91BE18", 40900, _schema_msg(schemas, "AA91BE18", 40900)],
        ["AA91BE18", 40914, _schema_msg(schemas, "AA91BE18", 40914)],
        [OWL_LIVE, 43200, _schema_msg(schemas, OWL_LIVE, 43200)],
        [ARCADE, 39800, _schema_msg(schemas, ARCADE, 39800)],
        [ARCADE, 39801, _schema_msg(schemas, ARCADE, 39801)],
        [ARCADE, 39802, arcade_39802],
        [ARCADE, 39803, _schema_msg(schemas, ARCADE, 39803)],
        [ARCADE, 39806, _schema_msg(schemas, ARCADE, 39806)],
        [ARCADE, 39807, _schema_msg(schemas, ARCADE, 39807)],
        [ARCADE, 39804, {"+0x78": _arcade_busy(data_dir)}],
        ["267FDE9E", 28001, _schema_msg(schemas, "267FDE9E", 28001)],
        ["0DFEEFEF", 38300, _schema_msg(schemas, "0DFEEFEF", 38300)],
        [CONFIG, 36600, {"+0x78": [{"+0x0": SUPPORTED_BUILDS_KEY, "+0x8": f"[{CLIENT_BUILD}]"}]}],
        [CONFIG, 36603, _schema_msg(schemas, CONFIG, 36603)],
        [CONFIG, 36602, {"+0x78": 1_700_000_000}],
        [REPLAYS, 51804, _schema_msg(schemas, REPLAYS, 51804)],
        [REPLAYS, 51807, _schema_msg(schemas, REPLAYS, 51807)],
        [REPLAYS, 51810, _schema_msg(schemas, REPLAYS, 51810)],
        [REPLAYS, 51817, _schema_msg(schemas, REPLAYS, 51817)],
        [REPLAYS, 51812, _schema_msg(schemas, REPLAYS, 51812)],
        ["34BB385D", 43301, _schema_msg(schemas, "34BB385D", 43301)],
        [STORE, 26404, _schema_msg(schemas, STORE, 26404)],
        [STORE, 26400, _schema_msg(schemas, STORE, 26400)],
        [FRIENDS, 27104, _schema_msg(schemas, FRIENDS, 27104)],
        [LOBBY, 20805, _schema_msg(schemas, LOBBY, 20805)],
        [LOBBY, 20820, _schema_msg(schemas, LOBBY, 20820)],
        [CUSTOM_GAMES, 23301, _schema_msg(schemas, CUSTOM_GAMES, 23301)],
        [RANKED, 36300, _build_ranked_state(schemas)],
        *_build_presence_messages(),
    ]

    return {
        "format": 1,
        "description": "Clean-room synthesized 104319 lobby message templates derived from local schemas and catalogs.",
        "server": server,
    }


def write_lobby_templates(schemas_path: Path, data_dir: Path, output: Path) -> int:
    corpus = synthesize_lobby_templates(schemas_path, data_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(corpus), encoding="utf-8")
    return len(corpus["server"])
