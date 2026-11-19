"""Private custom game rooms created from the 1.74 game browser."""

from dataclasses import dataclass, field, replace
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from ow174.game import collision
from ow174.game.content import (
    ESCORT_MODE,
    FREE_FOR_ALL_TEAM,
    PRACTICE_RANGE_MODE,
    SPECTATOR_TEAM,
    START_IMMEDIATELY,
    START_MANUALLY,
    START_WHEN_FULL,
    TEAM_DEATHMATCH_MODE,
    GameMap,
    game_map,
)
from ow174.jam.groups import CUSTOM_GAMES, GAME_REQUEST, MATCH_LOBBY
from ow174.jam.values import id16
from ow174.lobby import lineup
from ow174.lobby.router import Router

if TYPE_CHECKING:
    from ow174.lobby.session import Session

ROOM_CREATED = 23304
ROOM_MAP = 23305
ROOM_UPDATED = 23310
ROOM_CLOSED = 23311
ROOM_SETTINGS = 23312
ROOM_NOTICE = 23319
CUSTOM_LOBBY = 1
SETTINGS = 24008
MAP_SETTINGS = 24009
MODE_SETTINGS = 24010
MOVE_PLAYER = 24017
START = 24018
RENAME = 24019
START_GAME = 1  # observed from the Start button in build 104319
RESTART_GAME = 3
RETURN_TO_LOBBY = 4
START_GAME_MODE = 5  # Start Game Mode in the in-game custom lobby
MODE_START_SETTING = 0x25C8  # 0F4A/0EC2 read this identifier as mode entity variable 9672
RESTARTED_NOTICE = 0x0DE0000000000E3B
SETTINGS_UPDATED_NOTICE = 0x0DE0000000000E3C
RETURNED_TO_LOBBY_NOTICE = 0x0DE0000000002B08
KINGS_ROW = 0x08000000000000D4
MAP_HEADER_TYPE = 0x0790000000000000
HYBRID = 0x0230000000000016
FREE_FOR_ALL_ROSTER_TEAM = 2  # 104319 RVA B84F99 indexes the FFA slot array with this roster team.
FREE_FOR_ALL_MODES = {
    0x023000000000001E: "Deathmatch",
    0x0230000000000070: "Bounty Hunter",
}
PLAYABLE_MODES = {
    0x0230000000000007: "Capture the Flag",
    0x0230000000000009: "Elimination",
    0x0230000000000010: "Skirmish",
    0x0230000000000014: "Assault",
    ESCORT_MODE: "Escort",
    HYBRID: "Hybrid",
    0x0230000000000017: "Control",
    PRACTICE_RANGE_MODE: "Practice Range",
    TEAM_DEATHMATCH_MODE: "Team Deathmatch",
    **FREE_FOR_ALL_MODES,
}
MODE_PRESETS = {
    0x0230000000000014: 0x0FD000000000002A,
    ESCORT_MODE: 0x0FD000000000002E,
    HYBRID: 0x0FD000000000002C,
    0x0230000000000017: 0x0FD0000000000030,
}
TEAM_COUNT = 6
SPECTATOR_COUNT = 2
CONTINUATION = {
    MAP_SETTINGS: "+0x1B8", MODE_SETTINGS: "+0x1B0", 24011: "+0xA0", 24012: "+0x1D1", 24013: "+0x191",
}
MAX_SETTINGS_PARTS = 1024
routes = Router()


@dataclass
class RoomRules:
    options: dict = field(default_factory=lambda: {
        "+0x1D0": {"+0x0": 3, "+0x4": 1, "+0x8": SPECTATOR_COUNT},
        "+0x1E0": {"+0x0": 2, "+0x4": 2, "+0x8": 3},
    })
    maps: list[dict] = field(default_factory=lambda: [{"+0x0": mode_rules(HYBRID), "+0x138": KINGS_ROW}])
    modes: list[dict] = field(default_factory=lambda: [mode_rules(HYBRID)])
    workshop: dict = field(default_factory=dict)
    parts: list[tuple[int, dict]] = field(default_factory=list)

    def start_mode(self, map_rules: dict) -> int:
        """Use the selected map's override, then its mode's setting or the stock default."""
        mode_rules = next((row for row in self.modes if row["+0xB0"] == map_rules["+0xB0"]), {})
        for rule in (map_rules, mode_rules):
            for value in rule.get("+0xD8", ()):
                if value["+0x4"] == MODE_START_SETTING and value["+0x6"] == 3:
                    setting = value["+0x0"]
                    if setting in (START_IMMEDIATELY, START_WHEN_FULL, START_MANUALLY):
                        return setting
        return START_WHEN_FULL

    def update(self, header: dict, parts: list[tuple[int, dict]]) -> "RoomRules":
        options = dict(header["+0x78"])
        maps = options.pop("+0x0", []) + [value["+0x78"] for msg, value in parts if msg == MAP_SETTINGS]
        modes = options.pop("+0x18", []) + [value["+0x78"] for msg, value in parts if msg == MODE_SETTINGS]
        # Continuation flags are assigned when sending. Keep the order because hero overrides
        # are indexed into the map or mode list selected by the preceding part.
        saved_parts = [
            (msg, {key: item for key, item in value.items() if key != CONTINUATION[msg]})
            for msg, value in parts
        ]
        return RoomRules(
            options, maps or self.maps, modes or self.modes, header.get("+0x2B8", {}), saved_parts,
        )

    def settings_packet(self) -> tuple[dict, list[tuple[int, dict]]]:
        """Keep inline modes in the header and send any inline maps as leading map parts."""
        inline_maps = len(self.maps) - sum(message == MAP_SETTINGS for message, _ in self.parts)
        inline_modes = len(self.modes) - sum(message == MODE_SETTINGS for message, _ in self.parts)
        options = dict(self.options, **{"+0x18": self.modes[:inline_modes]})
        maps = [(MAP_SETTINGS, {"+0x78": row}) for row in self.maps[:inline_maps]]
        return options, maps + self.parts

    def enabled_maps(self):
        modes = {entry["+0xB0"]: entry for entry in self.modes}
        for entry in self.maps:
            rules, map_guid = entry["+0x0"], entry["+0x138"]
            mode = rules["+0xB0"]
            mode_options = modes.get(mode, rules)
            if rules["+0x108"] and mode_options["+0x108"]:
                yield map_guid, mode, rules

    def enabled_presets(self) -> tuple[tuple[int, int, str], ...]:
        return tuple(
            (mode["+0xB0"], mode.get("+0xA8", 0), mode.get("+0x110", ""))
            for mode in self.modes if mode["+0x108"]
        )


@dataclass
class CustomRoom:
    id: tuple[int, int]
    name: str
    match_id: tuple[int, int] | None = None
    team: int = 0
    slot: int = 0
    revision: int = 0
    rules: RoomRules = field(default_factory=RoomRules)
    team_names: dict[int, str] = field(default_factory=dict)
    setting_header: dict | None = None
    setting_parts: list[tuple[int, dict]] = field(default_factory=list)
    active_slots: dict[int, int] = field(default_factory=dict)
    leaving_action: int | None = None

    @classmethod
    def create(cls, name: str) -> "CustomRoom":
        room_id = uuid4().int
        return cls((room_id & ((1 << 64) - 1), room_id >> 64), name)

    def room_info(self, owner_card: dict) -> dict:
        return {
            "+0x0": owner_card,
            "+0x68": owner_card["+0x0"],
            "+0x78": self.id[0],
            "+0x88": self.name,
        }

    def team_name_records(self) -> list[dict]:
        return [{"+0x18": team, "+0x20": name} for team, name in sorted(self.team_names.items())]

    def joined(self, owner_card: dict, options: dict, more: bool) -> dict:
        """23304 begins the room response. The client applies it when the settings stream ends."""
        return {
            "+0x78": self.room_info(owner_card),
            "+0x128": self.team_name_records(),
            "+0x140": options,
            "+0x380": self.rules.workshop,
            "+0x498": more,
        }

    def lobby_state(self) -> dict:
        return {"+0x78": {"+0x30": {"+0x0": list(self.id)}, "+0x60": CUSTOM_LOBBY}}

    def lobby_settings(self) -> dict:
        map_guid, rules = self.preview()
        slots = player_slots(rules)
        options = self.rules.options.get("+0x1D0", {})
        # The room preview resolves the low 32 bits of this map header to its name and artwork.
        map_header = MAP_HEADER_TYPE | (map_guid & 0xFFFFFFFF) if map_guid else 0
        return {"+0x78": {
            "+0x0": {"+0x0": rules, "+0x260": options},
            "+0x278": id16(self.id[0], (self.id[1] + self.revision) & ((1 << 64) - 1)),
            "+0x288": id16(map_header, 0),
            "+0x298": {"+0x0": slots.get(0, 0), "+0x4": slots.get(1, 0), "+0x8": options.get("+0x8", 0)},
        }}

    def prepared_scene(self) -> tuple[GameMap, dict] | None:
        for map_guid, mode, rules in self.rules.enabled_maps():
            if mode not in PLAYABLE_MODES or not has_collision(map_guid):
                continue
            slots = player_slots(rules)
            team_sizes = tuple(slots.values())
            if not any(team_sizes):
                continue
            free_for_all = mode in FREE_FOR_ALL_MODES
            scene = game_map(
                map_guid, mode, PLAYABLE_MODES[mode], free_for_all=free_for_all, team_sizes=team_sizes,
            )
            if scene is not None:
                start = None if scene.mode_guid == PRACTICE_RANGE_MODE else self.rules.start_mode(rules)
                return replace(scene, preset_guid=rules.get("+0xA8", 0), custom_start=start), rules
        return None

    def preview(self) -> tuple[int, dict]:
        prepared = self.prepared_scene()
        if prepared is not None:
            return prepared[0].map_guid, prepared[1]
        selected = next(self.rules.enabled_maps(), None)
        if selected is not None:
            return selected[0], selected[2]
        return 0, next((entry for entry in self.rules.modes if entry["+0x108"]), mode_rules(HYBRID))

    def slots_for(self, rules: dict) -> dict[int, int]:
        return {
            **player_slots(rules),
            SPECTATOR_TEAM: self.rules.options.get("+0x1D0", {}).get("+0x8", 0),
        }

    def fit_slot(self, slots: dict[int, int]) -> None:
        if not slots.get(self.team):
            self.team = next((team for team, size in slots.items() if size), next(iter(slots)))
        self.slot = min(self.slot, max(0, slots[self.team] - 1))


def has_collision(map_guid: int) -> bool:
    return collision.cache_version(collision.cache_path(map_guid)) == collision.VERSION


def player_slots(rules: dict) -> dict[int, int]:
    sizes = {item["+0x60"]: item["+0x61"] for item in rules.get("+0xC0", [])}
    teams = (FREE_FOR_ALL_TEAM,) if rules["+0xB0"] in FREE_FOR_ALL_MODES else (0, 1)
    return {team: sizes.get(team, 0) for team in teams}


def mode_rules(mode: int) -> dict:
    """The stock team-mode preset and its slots as the custom lobby UI reads them."""
    return {
        "+0xA8": MODE_PRESETS[mode], "+0xB0": mode, "+0x108": 1,
        "+0xC0": [{"+0x60": team, "+0x61": TEAM_COUNT} for team in (0, 1)],
        "+0xD8": [{"+0x0": 9552, "+0x4": 9624, "+0x6": 3}],
    }


def send_room(session: "Session", room: CustomRoom) -> None:
    card = session.server.content.player.record(session.profile, session.ident)
    options, parts = room.rules.settings_packet()
    session.send(MATCH_LOBBY, 53000, room.lobby_state())
    session.send(CUSTOM_GAMES, ROOM_CREATED, room.joined(card, options, bool(parts)))
    send_settings_parts(session, ROOM_MAP, parts)
    session.send(MATCH_LOBBY, 53001, room.lobby_settings())
    send_roster(session, room, card)


def send_roster(session: "Session", room: CustomRoom, card: dict | None = None) -> None:
    if card is None:
        card = session.server.content.player.record(session.profile, session.ident)
    team = FREE_FOR_ALL_ROSTER_TEAM if room.team == FREE_FOR_ALL_TEAM else room.team
    roster = lineup.record(card, team, 0, None, tuple(session.ident.party_id), room.slot)
    session.send(MATCH_LOBBY, 53002, {"+0x78": [roster]})


def create_room(session: "Session", name: str) -> None:
    """Create one room per connected owner, resending its state on a repeated request."""
    needed = {CUSTOM_GAMES, MATCH_LOBBY}
    if not needed.issubset(session.wire_of):
        session.log("[custom game] Client did not announce the room and match lobby protocols")
        return
    room = session.custom_room or CustomRoom.create(name or session.account.name)
    if room.match_id is not None:
        return
    send_room(session, room)
    if session.custom_room is None:
        session.log("[custom game] Created a private room and sent its lobby state")
    session.custom_room = room


def close_room(session: "Session", room: CustomRoom) -> None:
    session.custom_room = None
    session.send(MATCH_LOBBY, 53003, {"+0x78": {"+0x0": list(room.id)}})
    session.send(CUSTOM_GAMES, ROOM_CLOSED, {})
    session.log("[custom game] Closed the private room")


def match_left(session: "Session", match_id: tuple[int, int]) -> None:
    room = session.custom_room
    if room is not None and room.match_id == match_id:
        action, room.leaving_action = room.leaving_action, None
        room.match_id = None
        room.active_slots.clear()
        room.setting_header = None
        room.setting_parts.clear()
        prepared = room.prepared_scene() if action == RESTART_GAME else None
        rules = prepared[1] if prepared is not None else room.preview()[1]
        room.fit_slot(room.slots_for(rules))
        send_room(session, room)
        if prepared is not None:
            start_match(session, room, prepared)
            if room.match_id is not None:
                return
        session.log("[custom game] Returned the owner to the private room")


def send_rules(session: "Session", room: CustomRoom) -> None:
    """Publish the complete settings in the same multipart form as the native request."""
    options, parts = room.rules.settings_packet()
    session.send(CUSTOM_GAMES, ROOM_SETTINGS, {
        "+0x78": options, "+0x2B8": room.rules.workshop, "+0x3D0": bool(parts),
    })
    send_settings_parts(session, ROOM_SETTINGS + 1, parts)


def send_notice(session: "Session", text: int) -> None:
    # The recipient's client resolves the localized text and posts it to system chat.
    session.send(CUSTOM_GAMES, ROOM_NOTICE, {"+0x78": text})


def send_settings_parts(session: "Session", first: int, parts: list[tuple[int, dict]]) -> None:
    for index, (message, part) in enumerate(parts, start=1):
        packet = dict(part, **{CONTINUATION[message]: index < len(parts)})
        session.send(CUSTOM_GAMES, first + message - MAP_SETTINGS, packet)


def commit_rules(session: "Session", room: CustomRoom) -> None:
    rules = room.rules.update(room.setting_header, room.setting_parts)
    room.setting_header = None
    room.setting_parts.clear()
    if room.rules != rules:
        preset_changed = room.rules.enabled_presets() != rules.enabled_presets()
        room.rules = rules
        room.revision += 1
        if room.match_id is not None:
            send_rules(session, room)
            send_notice(session, SETTINGS_UPDATED_NOTICE)
        else:
            room.fit_slot(room.slots_for(room.preview()[1]))
            if preset_changed:
                send_room(session, room)
            else:
                send_rules(session, room)
                session.send(MATCH_LOBBY, 53001, room.lobby_settings())
                send_roster(session, room)
        session.log("[custom game] Applied the room settings")


@routes.on(GAME_REQUEST, SETTINGS)
def begin_rules(session: "Session", value: dict) -> None:
    room = session.custom_room
    if room is not None and room.leaving_action is None:
        room.setting_header = value
        room.setting_parts.clear()
        if not value["+0x3D0"]:
            commit_rules(session, room)


def receive_rules_part(message: int, session: "Session", value: dict) -> None:
    room = session.custom_room
    if room is None or room.leaving_action is not None or room.setting_header is None:
        return
    if len(room.setting_parts) >= MAX_SETTINGS_PARTS:
        room.setting_header = None
        room.setting_parts.clear()
        return
    room.setting_parts.append((message, value))
    if not value[CONTINUATION[message]]:
        commit_rules(session, room)


for _message in CONTINUATION:
    routes.add(GAME_REQUEST, _message, partial(receive_rules_part, _message))


@routes.on(GAME_REQUEST, RENAME)
def rename_room(session: "Session", value: dict) -> None:
    room = session.custom_room
    if room is None:
        return
    kind, name = value["+0x78"], value["+0x80"]
    if kind == 1:
        room.name = name
    elif kind in (2, 3):
        room.team_names[kind - 2] = name
    else:
        return
    # 104319's room-update handler copies the name and team strings into the current lobby.
    card = session.server.content.player.record(session.profile, session.ident)
    session.send(CUSTOM_GAMES, ROOM_UPDATED, {
        "+0x88": room.room_info(card), "+0x138": room.team_name_records(),
    })


@routes.on(GAME_REQUEST, MOVE_PLAYER)
def move_player(session: "Session", value: dict) -> None:
    room = session.custom_room
    team, slot = value["+0x89"], value["+0x8A"]
    if (
        room is None or room.leaving_action is not None
        or value["+0x78"] != session.ident.account
    ):
        return
    if team == FREE_FOR_ALL_ROSTER_TEAM:
        team = FREE_FOR_ALL_TEAM
    slots = room.active_slots if room.match_id is not None else room.slots_for(room.preview()[1])
    slot = 0 if slot == 255 else slot
    if slot >= slots.get(team, 0):
        return
    if room.match_id is not None and team != room.team:
        game = session.server.game
        match_team = 0 if team == FREE_FOR_ALL_TEAM else team
        if game is None or not game.move_custom_player(session.account.account_lo, room.match_id, match_team):
            return
    room.team, room.slot = team, slot
    send_roster(session, room)
    session.log(f"[custom game] Moved the owner to slot {slot} (team id {team})")


@routes.on(GAME_REQUEST, START)
def start_room(session: "Session", value: dict) -> None:
    room = session.custom_room
    action = value["+0x78"]
    if room is None or room.leaving_action is not None:
        return
    if room.setting_header is not None and action != RETURN_TO_LOBBY:
        return
    if room.match_id is not None:
        game = session.server.game
        if action == START_GAME_MODE:
            if game is not None:
                game.start_custom_mode(session.account.account_lo, room.match_id)
            return
        if action not in (RETURN_TO_LOBBY, RESTART_GAME):
            return
        if action == RESTART_GAME and room.prepared_scene() is None:
            session.log("[custom game] No enabled supported map has prepared collision data")
            return
        player = game.player_of(session.account.account_lo) if game is not None else None
        if player is not None and player.match.id == room.match_id:
            room.leaving_action = action
            send_notice(session, RESTARTED_NOTICE if action == RESTART_GAME else RETURNED_TO_LOBBY_NOTICE)
            game.take_out(player, "custom game restarted" if action == RESTART_GAME else "returned to lobby")
        return
    if action != START_GAME:
        return
    prepared = room.prepared_scene()
    if prepared is None:
        session.log("[custom game] No enabled supported map has prepared collision data")
        return
    start_match(session, room, prepared)


def start_match(session: "Session", room: CustomRoom, prepared: tuple[GameMap, dict]) -> None:
    scene, rules = prepared
    slots = room.slots_for(rules)
    if slots.get(room.team):
        session.server.matchmaker.custom(session, room, scene)
        if room.match_id is not None:
            room.active_slots = slots
