"""Heroes and maps the game server knows."""

import json
import math
import re
from dataclasses import dataclass
from functools import cache

from ow174.paths import DATA_DIR

HEROES_PATH = DATA_DIR / "game_heroes_174.json"
MAPS_PATH = DATA_DIR / "game_maps_174.json"  # tools/extract_game_maps.py
SOLDIER = 0x02E000000000006E
PRACTICE_RANGE_MODE = 0x0230000000000018
TUTORIAL_MODE = 0x0230000000000019
TUTORIAL_MAP = 0x0800000000000661
# Modes whose loading screen waits for a state of the mode's own script (m_0FC17230 = 1 in the
# 0C5 asset; build 104319's loader gate at RVA 706BFF): the script must signal readiness first. Mei's
# Snowball Offensive, two PvE modes, Retribution and Storm Rising.
SCRIPTED_LOADING_MODES = {
    0x0230000000000008,
    0x023000000000000F,
    0x023000000000001A,
    0x0230000000000025,
    0x0230000000000043,
}


@dataclass(frozen=True)
class Hero:
    guid: int  # the 075 hero
    name: str
    role: str
    body: int  # the 003 entity the client plays as (the hero's m_gameplayEntity)


@cache
def heroes() -> dict[int, Hero]:
    table = json.loads(HEROES_PATH.read_text(encoding="utf-8"))
    return {
        int(key, 16): Hero(int(key, 16), entry["name"], entry["class"], int(entry["body"], 16))
        for key, entry in table.items()
    }


def _plain(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower().replace("ö", "o").replace("ú", "u"))


def find_hero(text: str) -> Hero | None:
    """A hero by its name ("soldier", "Torbjörn", "dva") or its GUID."""
    wanted = _plain(text)
    if not wanted:
        return None
    by_name = {_plain(hero.name): hero for hero in heroes().values()}
    if wanted in by_name:
        return by_name[wanted]
    starts = [hero for name, hero in by_name.items() if name.startswith(wanted)]
    if len(starts) == 1:
        return starts[0]
    try:
        return heroes().get(int(text, 16))
    except ValueError:
        return None


@dataclass(frozen=True)
class Spawn:
    team: int | None  # None: any team (free for all)
    position: tuple[float, float, float]
    yaw: float  # degrees


@dataclass(frozen=True)
class GameMap:
    name: str
    map_guid: int  # 002; the type bits reversed: 0x0800...
    mode_guid: int  # the 0C5 game mode it is played with
    placeables: tuple[int, ...]  # map entities the server creates; the loading screen waits for them
    spawns: tuple[Spawn, ...]
    mode_name: str = ""
    # Players per team, from the ruleset. The hero select screen's team list shows the missing ones as
    # empty slots.
    team_sizes: tuple[int, ...] = (6, 6)
    free_for_all: bool = False  # one team, everyone against everyone (its one size is the player count)


# Practice Range (0688.002 with 0018.0C5): its four server-owned placeables, the ones the running
# client has and the rule in tools/extract_game_maps.py gives, and the spawn point. Its
# mode has one player on the blue team and none on the red one.
PRACTICE_RANGE = GameMap(
    name="Practice Range",
    map_guid=0x0800000000000688,
    mode_guid=PRACTICE_RANGE_MODE,
    placeables=(0x88, 0x22E, 0x234, 0x289),
    spawns=(Spawn(None, (54.56507, 1.0, 42.14788), -170.3434),),
    mode_name="Practice Range",
    team_sizes=(1, 0),
)

# Build 104319: the 0C8C mission looks up this server-owned gate by tag 030E.
JUNKENSTEIN_MAP = 0x08000000000006C9
JUNKENSTEIN_MODES = (0x0230000000000003, 0x023000000000002A)
JUNKENSTEIN_GATE = 50  # flat placement index, definition 16F6
JUNKENSTEIN_THREE_THEY_WERE = 0x063000000000016E
JUNKENSTEIN_MYSTERY_SWAP = 0x063000000000016F
JUNKENSTEIN_SHOCKING_SURPRISE = 0x0630000000000170

# Difficulty identifiers from each 104319 Arcade card's m_13E9E420.
_JUNKENSTEIN_CARD_DIFFICULTIES = {
    0x3C: 0x195E, 0x3D: 0x195F, 0x4A: 0x26DF, 0x3E: 0x1960,
    0x43: 0x195E, 0x44: 0x195F, 0x49: 0x26DF, 0x45: 0x1960,
    **dict.fromkeys(range(0x16B, 0x171), 0x26DF),
}

# Each challenge has its own .0C0 ruleset, 0119 through 011E.
_JUNKENSTEIN_CHALLENGE_FLAGS = dict(zip(
    range(0x16B, 0x171), (0x7D53, 0x7D54, 0x7D60, 0x7D61, 0x7D62, 0x7D63), strict=True,
))


def junkenstein_card_difficulty(card: int) -> int | None:
    """Resolve a full Arcade card GUID to its authored difficulty identifier."""
    if card >> 48 == 0x0630:
        identifier = _JUNKENSTEIN_CARD_DIFFICULTIES.get(card & 0xFFFFFFFFFFFF)
        if identifier is not None:
            return 0x0D80000000000000 | identifier
    return None


def junkenstein_card_mode_values(card: int) -> dict[int, bool | float]:
    """The challenge ruleset's mode-level overrides, separate from team settings."""
    if card >> 48 != 0x0630:
        return {}
    index = card & 0xFFFFFFFFFFFF
    flag = _JUNKENSTEIN_CHALLENGE_FLAGS.get(index)
    if flag is None:
        return {}
    values: dict[int, bool | float] = {flag: True}
    if index == 0x16F:
        values[0x7E0F] = 1.0
    return values


@cache
def _map_entries() -> dict[int, dict]:
    if not MAPS_PATH.is_file():
        return {}
    return {int(key, 16): entry for key, entry in json.loads(MAPS_PATH.read_text(encoding="utf-8")).items()}


def map_catalog() -> list[dict]:
    """Every map the data knows (plus the Practice Range), for the dashboard's map picker:
    {guid hex, name}, sorted by name."""
    maps = [{"guid": f"0x{PRACTICE_RANGE.map_guid:X}", "name": PRACTICE_RANGE.name}]
    maps += [
        {"guid": f"0x{guid:X}", "name": entry.get("name") or f"0x{guid:X}"}
        for guid, entry in _map_entries().items()
        if guid != PRACTICE_RANGE.map_guid
    ]
    maps.sort(key=lambda item: item["name"])
    return maps


def map_name(guid: int) -> str | None:
    """A map's name, or None when the data does not know it."""
    if guid == PRACTICE_RANGE.map_guid:
        return PRACTICE_RANGE.name
    entry = _map_entries().get(guid)
    return entry.get("name") if entry else None


def _first_round_spawns(entry: dict, free_for_all: bool) -> tuple[Spawn, ...]:
    """Each team's spawn points of the first objective (team 1 attacks it, team 0 defends), or of any
    objective when the map names none; the free-for-all points in a free-for-all mode."""
    spawns = entry.get("spawns") or []
    if free_for_all:
        chosen = [spawn for spawn in spawns if spawn.get("ffa") or spawn.get("team") is None]
    else:
        chosen = []
        for team in (0, 1):
            mine = [spawn for spawn in spawns if spawn.get("team") == team and not spawn.get("ffa")]
            first = [spawn for spawn in mine if any(goal[0] == 0 for goal in spawn.get("objectives") or [])]
            chosen += first or mine
    return tuple(
        Spawn(spawn.get("team"), tuple(spawn["position"]), float(spawn.get("yaw", 0.0))) for spawn in chosen
    )


def game_map(
    map_guid: int,
    mode_guid: int,
    mode_name: str = "",
    free_for_all: bool = False,
    team_sizes: tuple[int, ...] = (6, 6),
) -> GameMap | None:
    """The map as it is played in that mode with that many players per team, or None when the server
    can't host it: no data, a mode the map doesn't support, no spawn points, or a mode whose loading
    needs its own script."""
    if map_guid == PRACTICE_RANGE.map_guid:
        return PRACTICE_RANGE
    entry = _map_entries().get(map_guid)
    if entry is None:
        return None
    if mode_guid in SCRIPTED_LOADING_MODES:
        from ow174.game.pve import entrance

        if entrance(mode_guid, map_guid) is None:
            return None
    if mode_guid not in {int(mode, 16) for mode in entry.get("modes", [])}:
        return None
    by_mode = {int(mode, 16): indexes for mode, indexes in (entry.get("placeables_by_mode") or {}).items()}
    placeables = by_mode.get(mode_guid, entry.get("placeables", []))
    if (map_guid == JUNKENSTEIN_MAP and mode_guid in JUNKENSTEIN_MODES
            and JUNKENSTEIN_GATE in entry.get("mirrored", ()) and JUNKENSTEIN_GATE not in placeables):
        placeables = sorted([*placeables, JUNKENSTEIN_GATE])
    spawns = _first_round_spawns(entry, free_for_all)
    if not spawns:
        return None
    sizes = tuple(team_sizes)
    return GameMap(
        entry["name"], map_guid, mode_guid, tuple(placeables), spawns, mode_name, sizes, free_for_all
    )


SPAWN_SPACING = 1.5  # metres between players who get the same spawn point


def spawn_point(game_map: GameMap, team: int, number: int) -> tuple[tuple[float, float, float], float]:
    """Where the number-th player of a team starts: that team's spawn points in turn, and players who
    share a point stand side by side along its right-hand axis (-cos yaw, 0, sin yaw)."""
    points = [spawn for spawn in game_map.spawns if spawn.team in (team, None)] or list(game_map.spawns)
    spawn = points[number % len(points)]
    row = number // len(points)
    side = (row + 1) // 2 * (1 if row % 2 else -1) * SPAWN_SPACING
    yaw = math.radians(spawn.yaw)
    x, y, z = spawn.position
    return (x - math.cos(yaw) * side, y, z + math.sin(yaw) * side), spawn.yaw
