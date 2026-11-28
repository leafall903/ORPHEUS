//! Custom-room requests and responses for the 104319 lobby.

use std::{collections::BTreeMap, io};

use crate::{
    game_content::{MapCatalog, PRACTICE_RANGE_MODE},
    game_instance::FREE_FOR_ALL_TEAM,
    jam_codec::{Fields, Value, id16},
    lobby_practice::{CREATE_GAME, GAME_REQUEST},
    lobby_transport::LobbyMessage,
    match_handoff::{MATCH_LOBBY, match_lobby_id},
    pvp_mode::{CustomModeRules, SettingRow},
    room_primitives::{FREE_FOR_ALL_ROSTER_TEAM, SPECTATOR_TEAM, choose_seat},
    scene_assets::{PreparedScene, SceneAssets},
};

use Value::{Array, Bool, String as Text, Struct, Unsigned as U};

pub(crate) const CUSTOM_GAMES: u32 = 0x2128_6b49;
pub(crate) const MATCH_LOBBY_OUT: u32 = 0x8887_16d3;
pub(crate) const CUSTOM_LOBBY: u64 = 1;
pub(crate) const BROWSER: u32 = 0x262e_f305;
pub(crate) const BROWSER_OUT: u32 = 0x783e_e77a;
pub(crate) const BROWSER_RESULTS: u32 = 40603;
const PUBLIC_ROOM: u64 = 3;
const KING_ROW: u64 = 0x0800_0000_0000_00d4;
const MAP_HEADER_TYPE: u64 = 0x0790_0000_0000_0000;
const HYBRID: u64 = 0x0230_0000_0000_0016;
const HYBRID_PRESET: u64 = 0x0fd0_0000_0000_002c;
const TEAM_SIZE: u64 = 6;
const SPECTATOR_SIZE: u64 = 2;
const AUTO_SLOT: u64 = 255;
// The outgoing roster slot is an i8, with negative values reserved.
const MAX_ROSTER_CAPACITY: u64 = i8::MAX as u64 + 1;
const SAVE_SETTINGS: u32 = 24008;
const MAP_SETTINGS: u32 = 24009;
const MODE_SETTINGS: u32 = 24010;
const LAST_SETTINGS_PART: u32 = 24013;
const MOVE_PLAYER: u32 = 24017;
pub(crate) const ROOM_ACTION: u32 = 24018;
const RENAME: u32 = 24019;
const SETTINGS_UPDATED_NOTICE: u64 = 0x0de0_0000_0000_0e3c;
const MAX_SETTINGS_PARTS: usize = 1024;
const CONTINUATION: [u32; 5] = [0x1b8, 0x1b0, 0xa0, 0x1d1, 0x191];

/// The outer option identifies a creation request. The inner option is the
/// optional name carried by the native Game Browser button.
pub(crate) fn create_name((crc, id, fields): &LobbyMessage) -> Option<Option<&str>> {
    if (*crc, *id) != (GAME_REQUEST, CREATE_GAME)
        || !matches!(fields.get(&0x78), Some(U(1) | Value::Signed(1)))
        || !matches!(fields.get(&0xa8), Some(U(4) | Value::Signed(4)))
    {
        return None;
    }
    Some(match fields.get(&0x80) {
        Some(Text(name)) if !name.is_empty() => Some(name),
        _ => None,
    })
}

pub(crate) fn is_edit((crc, id, _): &LobbyMessage) -> bool {
    *crc == GAME_REQUEST
        && matches!(
            *id,
            SAVE_SETTINGS..=LAST_SETTINGS_PART | MOVE_PLAYER | RENAME
        )
}

pub(crate) fn is_move((crc, id, _): &LobbyMessage) -> bool {
    (*crc, *id) == (GAME_REQUEST, MOVE_PLAYER)
}

pub(crate) fn join_id((crc, id, fields): &LobbyMessage) -> Option<[u64; 2]> {
    let key = structure(fields.get(&0x78))?;
    match (*crc, *id) {
        (MATCH_LOBBY_OUT, 52900) if number(fields.get(&0x98)) == CUSTOM_LOBBY => {
            let Array(parts) = key.get(&0)? else {
                return None;
            };
            let [U(lo), U(hi)] = parts.as_slice() else {
                return None;
            };
            Some([*lo, *hi])
        }
        (GAME_REQUEST, 24001) if matches!(fields.get(&0x88), Some(Text(name)) if name.is_empty()) => {
            Some([number(key.get(&0)), number(key.get(&8))])
        }
        _ => None,
    }
}

pub(crate) fn browser_filter(fields: &Fields) -> (u64, Vec<String>) {
    let Some(filter) = structure(fields.get(&0x78)) else {
        return (0, Vec::new());
    };
    let names = struct_rows(filter.get(&0))
        .filter_map(|row| match row.get(&0xa0) {
            Some(Text(name)) if !name.is_empty() => Some(name.to_lowercase()),
            _ => None,
        })
        .collect();
    (number(filter.get(&0x6e)), names)
}

fn room_notice(text: u64) -> LobbyMessage {
    (CUSTOM_GAMES, 23319, Fields::from([(0x78, U(text))]))
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum RoomAction {
    Load,
    Restart,
    Return,
    StartMode,
}

impl RoomAction {
    pub(crate) fn read((crc, id, fields): &LobbyMessage) -> Option<Self> {
        if (*crc, *id) != (GAME_REQUEST, ROOM_ACTION) {
            return None;
        }
        match number(fields.get(&0x78)) {
            1 => Some(Self::Load),
            3 => Some(Self::Restart),
            4 => Some(Self::Return),
            5 => Some(Self::StartMode),
            _ => None,
        }
    }

    pub(crate) fn leave_notice(self) -> LobbyMessage {
        let text = if self == Self::Restart {
            0x0de0_0000_0000_0e3b
        } else {
            0x0de0_0000_0000_2b08
        };
        room_notice(text)
    }
}

#[derive(PartialEq)]
struct RoomSettings {
    options: Fields,
    workshop: Fields,
    parts: Vec<(u32, Fields)>,
}

impl Default for RoomSettings {
    fn default() -> Self {
        let mode = hybrid_rules();
        Self {
            options: Fields::from([
                (0x18, Array(vec![Struct(mode.clone())])),
                (
                    0x1d0,
                    Struct(Fields::from([
                        (0, U(PUBLIC_ROOM)),
                        (4, U(1)),
                        (8, U(SPECTATOR_SIZE)),
                    ])),
                ),
                (
                    0x1e0,
                    Struct(Fields::from([(0, U(2)), (4, U(2)), (8, U(3))])),
                ),
            ]),
            workshop: Fields::new(),
            parts: vec![(
                MAP_SETTINGS,
                Fields::from([(
                    0x78,
                    Struct(Fields::from([(0, Struct(mode)), (0x138, U(KING_ROW))])),
                )]),
            )],
        }
    }
}

impl RoomSettings {
    fn mode_rules(&self, map: &Fields, mode: u64) -> CustomModeRules {
        fn overrides(row: &Fields) -> Vec<SettingRow> {
            struct_rows(row.get(&0xd8))
                .map(|field| SettingRow {
                    id: number(field.get(&4)) as u32,
                    value_type: number(field.get(&6)) as u32,
                    raw: number(field.get(&0)) as u32,
                })
                .collect()
        }
        CustomModeRules {
            map_overrides: overrides(map),
            mode_overrides: self
                .rows(0x18, MODE_SETTINGS)
                .find(|row| number(row.get(&0xb0)) == mode)
                .map_or_else(Vec::new, overrides),
        }
    }

    fn rows(&self, inline: u32, message: u32) -> impl Iterator<Item = &Fields> {
        struct_rows(self.options.get(&inline)).chain(
            self.parts
                .iter()
                .filter(move |(id, _)| *id == message)
                .filter_map(|(_, fields)| structure(fields.get(&0x78))),
        )
    }

    fn preserve_missing_rows(&mut self, previous: &Self) {
        for (inline, message) in [(0, MAP_SETTINGS), (0x18, MODE_SETTINGS)] {
            if self.rows(inline, message).next().is_none() {
                let rows = previous
                    .rows(inline, message)
                    .cloned()
                    .map(Struct)
                    .collect();
                self.options.insert(inline, Array(rows));
            }
        }
    }

    fn presets(&self) -> impl Iterator<Item = (u64, u64, &str)> {
        self.rows(0x18, MODE_SETTINGS)
            .filter(|row| enabled(row))
            .map(|row| {
                let label = match row.get(&0x110) {
                    Some(Text(label)) => label.as_str(),
                    _ => "",
                };
                (number(row.get(&0xb0)), number(row.get(&0xa8)), label)
            })
    }

    fn enabled_maps(&self) -> impl Iterator<Item = (u64, &Fields)> {
        let modes: std::collections::BTreeMap<_, _> = self
            .rows(0x18, MODE_SETTINGS)
            .map(|mode| (number(mode.get(&0xb0)), mode))
            .collect();
        self.rows(0, MAP_SETTINGS).filter_map(move |map| {
            let rules = structure(map.get(&0))?;
            let map_guid = number(map.get(&0x138));
            (map_guid != 0
                && enabled(rules)
                && modes
                    .get(&number(rules.get(&0xb0)))
                    .is_none_or(|mode| enabled(mode)))
            .then_some((map_guid, rules))
        })
    }

    fn preview(&self) -> (u64, Option<&Fields>) {
        self.enabled_maps().next().map_or_else(
            || (0, self.rows(0x18, MODE_SETTINGS).find(|row| enabled(row))),
            |(map, rules)| (map, Some(rules)),
        )
    }

    fn lobby_options(&self) -> Fields {
        structure(self.options.get(&0x1d0))
            .cloned()
            .unwrap_or_default()
    }

    fn packets(&self, first: u32) -> (Fields, Vec<LobbyMessage>) {
        let mut options = self.options.clone();
        let inline_maps = match options.remove(&0) {
            Some(Array(maps)) => maps,
            _ => Vec::new(),
        };
        // Inline maps precede the received stream. Overrides in that stream
        // can refer to the preceding map or mode, so keep the received order.
        let count = inline_maps.len() + self.parts.len();
        let packets = inline_maps
            .into_iter()
            .map(|map| (MAP_SETTINGS, Fields::from([(0x78, map)])))
            .chain(self.parts.iter().cloned())
            .enumerate()
            .map(|(index, (id, mut fields))| {
                let offset = (id - MAP_SETTINGS) as usize;
                fields.insert(CONTINUATION[offset], Bool(index + 1 < count));
                (CUSTOM_GAMES, first + offset as u32, fields)
            })
            .collect();
        (options, packets)
    }
}

fn structure(value: Option<&Value>) -> Option<&Fields> {
    match value {
        Some(Struct(fields)) => Some(fields),
        _ => None,
    }
}

fn struct_rows(value: Option<&Value>) -> impl Iterator<Item = &Fields> {
    let rows = match value {
        Some(Array(rows)) => rows.as_slice(),
        _ => &[],
    };
    rows.iter().filter_map(|value| structure(Some(value)))
}

fn number(value: Option<&Value>) -> u64 {
    match value {
        Some(U(value)) => *value,
        Some(Value::Signed(value)) => u64::try_from(*value).unwrap_or(0),
        _ => 0,
    }
}

fn enabled(rules: &Fields) -> bool {
    number(rules.get(&0x108)) != 0
}

fn roster_team(rules_team: u8) -> u8 {
    if rules_team == FREE_FOR_ALL_TEAM {
        FREE_FOR_ALL_ROSTER_TEAM
    } else {
        rules_team
    }
}

pub(crate) type Capacity = [u64; 4];
pub(crate) type Position = (u8, u8);

pub(crate) struct Member {
    pub card: Fields,
    pub party: [u64; 2],
    pub position: Position,
}

pub(crate) struct PreparedRoom {
    pub scene: PreparedScene,
    pub capacity: Capacity,
    pub positions: BTreeMap<u64, Position>,
}

pub(crate) struct CustomRoom {
    pub id: [u64; 2],
    pub owner: u64,
    pub members: BTreeMap<u64, Member>,
    name: String,
    team_names: [Option<String>; 2],
    revision: u64,
    settings: RoomSettings,
    pending: Option<RoomSettings>,
}

impl CustomRoom {
    pub(crate) fn new(id: [u64; 2], name: &str, owner: u64, member: Member) -> Self {
        Self {
            id,
            owner,
            members: BTreeMap::from([(owner, member)]),
            name: name.to_owned(),
            team_names: [None, None],
            revision: 0,
            settings: RoomSettings::default(),
            pending: None,
        }
    }

    pub(crate) fn prepare_match(
        &self,
        maps: &MapCatalog,
        assets: &SceneAssets,
    ) -> io::Result<Option<PreparedRoom>> {
        if self.pending.is_some() {
            return Ok(None);
        }
        for (map_guid, rules) in self.settings.enabled_maps() {
            let mode = number(rules.get(&0xb0));
            let mode_name = match mode {
                0x0230_0000_0000_0007 => "Capture the Flag",
                0x0230_0000_0000_0009 => "Elimination",
                0x0230_0000_0000_0010 => "Skirmish",
                0x0230_0000_0000_0014 => "Assault",
                0x0230_0000_0000_0015 => "Escort",
                HYBRID => "Hybrid",
                0x0230_0000_0000_0017 => "Control",
                PRACTICE_RANGE_MODE => "Practice Range",
                0x0230_0000_0000_001d => "Team Deathmatch",
                0x0230_0000_0000_001e => "Deathmatch",
                0x0230_0000_0000_0070 => "Bounty Hunter",
                _ => continue,
            };
            let capacity = self.capacity(Some(rules));
            let ffa = capacity[FREE_FOR_ALL_ROSTER_TEAM as usize] > 0;
            let team_sizes = if ffa {
                [capacity[FREE_FOR_ALL_ROSTER_TEAM as usize] as u8, 0]
            } else {
                [capacity[0] as u8, capacity[1] as u8]
            };
            let Some(positions) = self.fitted_positions(capacity) else {
                continue;
            };
            if team_sizes == [0, 0] {
                continue;
            }
            let Some(mut map) = maps.game_map(map_guid, mode, mode_name, ffa, &team_sizes) else {
                continue;
            };
            if map.mode_guid != mode {
                continue;
            }
            map.preset_guid = number(rules.get(&0xa8));
            let rules = self.settings.mode_rules(rules, mode);
            if let Some(route) = &mut map.payload_route {
                route.speed_scale = rules.objective_rate(crate::pvp_mode::SETTING_PAYLOAD_SPEED);
            }
            if let Some(course) = &mut map.capture_course {
                course.speed_scale = rules.objective_rate(crate::pvp_mode::SETTING_CAPTURE_SPEED);
                if let Some(control) = &mut course.control {
                    control.rules = rules.control_settings();
                }
            }
            match assets.prepare_custom(map, positions[&self.owner].1 as i8, rules.start_mode()) {
                Ok(scene) if scene.movement_ready() => {
                    return Ok(Some(PreparedRoom {
                        scene,
                        capacity,
                        positions,
                    }));
                }
                Ok(_) => continue,
                Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
                Err(error) => return Err(error),
            }
        }
        Err(io::Error::new(
            io::ErrorKind::NotFound,
            "no enabled custom map has a prepared scene with space for the room",
        ))
    }

    pub(crate) fn discard_pending_settings(&mut self) {
        self.pending = None;
    }

    fn room_info(&self, card: &Fields) -> Fields {
        Fields::from([
            (0x68, card[&0].clone()),
            (0, Struct(card.clone())),
            (0x78, U(self.id[0])),
            (0x88, Text(self.name.clone())),
        ])
    }

    fn team_name_records(&self) -> Value {
        Array(
            self.team_names
                .iter()
                .enumerate()
                .filter_map(|(team, name)| {
                    let name = name.as_ref()?;
                    Some(Struct(Fields::from([
                        (0x18, U(team as u64)),
                        (0x20, Text(name.clone())),
                    ])))
                })
                .collect(),
        )
    }

    pub(crate) fn messages(&self) -> Vec<LobbyMessage> {
        let state = Fields::from([(0x30, match_lobby_id(self.id)), (0x60, U(CUSTOM_LOBBY))]);
        let (options, parts) = self.settings.packets(23305);
        let joined = Fields::from([
            (
                0x78,
                Struct(self.room_info(&self.members[&self.owner].card)),
            ),
            (0x128, self.team_name_records()),
            (0x140, Struct(options)),
            (0x380, Struct(self.settings.workshop.clone())),
            (0x498, Bool(!parts.is_empty())),
        ]);
        let mut messages = vec![
            (MATCH_LOBBY, 53000, Fields::from([(0x78, Struct(state))])),
            (CUSTOM_GAMES, 23304, joined),
        ];
        messages.extend(parts);
        messages.extend([self.lobby_settings(), self.roster()]);
        messages
    }

    fn capacity(&self, rules: Option<&Fields>) -> Capacity {
        let mut capacity = [0; 4];
        let teams = rules.and_then(|rules| rules.get(&0xc0));
        for team in struct_rows(teams) {
            let id = number(team.get(&0x60));
            if matches!(id, 0 | 1) || id == u64::from(FREE_FOR_ALL_TEAM) {
                capacity[roster_team(id as u8) as usize] =
                    number(team.get(&0x61)).min(MAX_ROSTER_CAPACITY);
            }
        }
        let lobby = structure(self.settings.options.get(&0x1d0));
        capacity[SPECTATOR_TEAM as usize] =
            number(lobby.and_then(|fields| fields.get(&8))).min(MAX_ROSTER_CAPACITY);
        capacity
    }

    pub(crate) fn public(&self) -> bool {
        let options = structure(self.settings.options.get(&0x1d0));
        number(options.and_then(|fields| fields.get(&0))) == PUBLIC_ROOM
    }

    pub(crate) fn browser_row(&self, names: &[String]) -> Option<Value> {
        if !self.public() {
            return None;
        }
        if !names.is_empty() {
            let room_name = self.name.to_lowercase();
            if !names.iter().any(|name| room_name.contains(name)) {
                return None;
            }
        }
        let (map, rules) = self.settings.preview();
        let capacity = self.capacity(rules);
        let owner = &self.members[&self.owner].card;
        let first = if capacity[FREE_FOR_ALL_ROSTER_TEAM as usize] > 0 {
            FREE_FOR_ALL_ROSTER_TEAM
        } else {
            0
        };
        let mut row = Fields::from([
            (0x98, match_lobby_id(self.id)),
            (0xa8, owner[&0].clone()),
            (0xb8, U(map_header(map))),
            (0xc0, U(number(rules.and_then(|rules| rules.get(&0xb0))))),
            (0x100, Text(self.name.clone())),
        ]);
        for (index, team) in [first, 1, SPECTATOR_TEAM].into_iter().enumerate() {
            let count = self
                .members
                .values()
                .filter(|member| member.position.0 == team)
                .count();
            row.insert(0xc8 + 2 * index as u32, U(count.min(127) as u64));
            row.insert(0xc9 + 2 * index as u32, U(capacity[team as usize].min(127)));
        }
        Some(Struct(Fields::from([
            (0, Struct(row)),
            (0x128, Struct(owner.clone())),
        ])))
    }

    pub(crate) fn join_position(&self) -> Option<Position> {
        free_position(
            self.capacity(self.settings.preview().1),
            self.members.values().map(|member| member.position),
            false,
        )
    }

    fn fitted_positions(&self, capacity: Capacity) -> Option<BTreeMap<u64, Position>> {
        let mut fitted = BTreeMap::new();
        for (&session, member) in &self.members {
            let (team, slot) = member.position;
            if u64::from(slot) < capacity[team as usize]
                && !fitted.values().any(|&seat| seat == member.position)
            {
                fitted.insert(session, member.position);
            }
        }
        for &session in self.members.keys() {
            if !fitted.contains_key(&session) {
                let (team, old_slot) = self.members[&session].position;
                let position = (0..capacity[team as usize].min(u64::from(old_slot) + 1))
                    .rev()
                    .map(|slot| (team, slot as u8))
                    .find(|seat| !fitted.values().any(|used| used == seat))
                    .or_else(|| free_position(capacity, fitted.values().copied(), false))
                    .or_else(|| free_position(capacity, fitted.values().copied(), true))?;
                fitted.insert(session, position);
            }
        }
        Some(fitted)
    }

    pub(crate) fn apply_positions(&mut self, positions: BTreeMap<u64, Position>) {
        for (session, position) in positions {
            if let Some(member) = self.members.get_mut(&session) {
                member.position = position;
            }
        }
    }

    pub(crate) fn fit_seats(&mut self) {
        if let Some(positions) = self.fitted_positions(self.capacity(self.settings.preview().1)) {
            self.apply_positions(positions);
        }
    }

    fn lobby_settings(&self) -> LobbyMessage {
        let (map, rules) = self.settings.preview();
        let capacity = self.capacity(rules);
        let header = map_header(map);
        let settings = Fields::from([
            (
                0,
                Struct(Fields::from([
                    (0, Struct(rules.cloned().unwrap_or_default())),
                    (0x260, Struct(self.settings.lobby_options())),
                ])),
            ),
            (
                0x278,
                Struct(id16(self.id[0], self.id[1].wrapping_add(self.revision))),
            ),
            (0x288, Struct(id16(header, 0))),
            (
                0x298,
                Struct(Fields::from([
                    (0, U(capacity[0])),
                    (4, U(capacity[1])),
                    (8, U(capacity[SPECTATOR_TEAM as usize])),
                ])),
            ),
        ]);
        (MATCH_LOBBY, 53001, Fields::from([(0x78, Struct(settings))]))
    }

    pub(crate) fn roster(&self) -> LobbyMessage {
        let members = std::iter::once(&self.members[&self.owner])
            .chain(
                self.members
                    .iter()
                    .filter_map(|(&id, member)| (id != self.owner).then_some(member)),
            )
            .map(|member| {
                Struct(Fields::from([
                    (0, Struct(member.card.clone())),
                    (
                        0x68,
                        Struct(Fields::from([
                            (0, Array(Vec::new())),
                            (0x18, U(0)),
                            (0x20, U(0)),
                        ])),
                    ),
                    (0x90, Struct(id16(member.party[0], member.party[1]))),
                    (0xa8, U(u64::from(member.position.0))),
                    (0xaf, U(u64::from(member.position.1))),
                    (0xb1, U(0)),
                ]))
            })
            .collect();
        (MATCH_LOBBY, 53002, Fields::from([(0x78, Array(members))]))
    }

    pub(crate) fn edit(
        &mut self,
        (_, id, fields): &LobbyMessage,
        in_match: bool,
    ) -> Vec<LobbyMessage> {
        match *id {
            RENAME => self.rename(fields).into_iter().collect(),
            SAVE_SETTINGS..=LAST_SETTINGS_PART => self.save_settings(*id, fields, in_match),
            _ => Vec::new(),
        }
    }

    fn rename(&mut self, fields: &Fields) -> Option<LobbyMessage> {
        let Some(Text(name)) = fields.get(&0x80) else {
            return None;
        };
        match number(fields.get(&0x78)) {
            1 => self.name.clone_from(name),
            kind @ (2 | 3) => self.team_names[(kind - 2) as usize] = Some(name.clone()),
            _ => return None,
        }
        Some((
            CUSTOM_GAMES,
            23310,
            Fields::from([
                (
                    0x88,
                    Struct(self.room_info(&self.members[&self.owner].card)),
                ),
                (0x138, self.team_name_records()),
            ]),
        ))
    }

    pub(crate) fn seat_request(
        &self,
        actor: u64,
        fields: &Fields,
    ) -> Option<(u64, u8, Option<u8>)> {
        let account = fields.get(&0x78)?;
        let (&target, _) = self
            .members
            .iter()
            .find(|(_, member)| member.card.get(&0) == Some(account))?;
        if actor != self.owner && actor != target {
            return None;
        }
        let team = u8::try_from(number(fields.get(&0x89))).ok()?;
        let U(slot) = fields.get(&0x8a)? else {
            return None;
        };
        let slot = match *slot {
            AUTO_SLOT => None,
            slot if slot < MAX_ROSTER_CAPACITY => Some(slot as u8),
            _ => return None,
        };
        Some((target, team, slot))
    }

    pub(crate) fn choose_slot(&self, target: u64, team: u8, slot: Option<u8>) -> Option<u8> {
        let capacity = self.capacity(self.settings.preview().1);
        let previous = self.members.get(&target)?.position;
        choose_seat(
            *capacity.get(team as usize)? as u8,
            slot,
            (previous.0 == team).then_some(previous.1),
            |slot| {
                self.members
                    .iter()
                    .any(|(&id, member)| id != target && member.position == (team, slot))
            },
        )
    }

    fn save_settings(&mut self, id: u32, fields: &Fields, in_match: bool) -> Vec<LobbyMessage> {
        let continued = if id == SAVE_SETTINGS {
            self.pending = structure(fields.get(&0x78)).map(|options| RoomSettings {
                options: options.clone(),
                workshop: structure(fields.get(&0x2b8)).cloned().unwrap_or_default(),
                parts: Vec::new(),
            });
            fields.get(&0x3d0) == Some(&Bool(true))
        } else {
            let Some(pending) = self.pending.as_mut() else {
                return Vec::new();
            };
            if pending.parts.len() >= MAX_SETTINGS_PARTS {
                self.pending = None;
                return Vec::new();
            }
            let offset = CONTINUATION[(id - MAP_SETTINGS) as usize];
            let mut part = fields.clone();
            let continued = part.remove(&offset) == Some(Bool(true));
            pending.parts.push((id, part));
            continued
        };
        if continued {
            return Vec::new();
        }
        let Some(mut settings) = self.pending.take() else {
            return Vec::new();
        };
        settings.preserve_missing_rows(&self.settings);
        let preset_changed = !self.settings.presets().eq(settings.presets());
        let changed = self.settings != settings;
        if changed {
            self.settings = settings;
            self.revision = self.revision.wrapping_add(1);
            if !in_match {
                self.fit_seats();
            }
        }
        if in_match && !changed {
            return Vec::new();
        }
        if preset_changed && !in_match {
            return self.messages();
        }
        let (options, parts) = self.settings.packets(23313);
        let header = Fields::from([
            (0x78, Struct(options)),
            (0x2b8, Struct(self.settings.workshop.clone())),
            (0x3d0, Bool(!parts.is_empty())),
        ]);
        let mut messages = vec![(CUSTOM_GAMES, 23312, header)];
        messages.extend(parts);
        if in_match {
            messages.push(room_notice(SETTINGS_UPDATED_NOTICE));
        } else {
            messages.extend([self.lobby_settings(), self.roster()]);
        }
        messages
    }

    pub(crate) fn is_leave(&self, (crc, id, fields): &LobbyMessage) -> bool {
        (*crc, *id) == (MATCH_LOBBY_OUT, 52902)
            && fields.get(&0x78) == Some(&match_lobby_id(self.id))
            && fields.get(&0x89) != Some(&Bool(true))
    }

    pub(crate) fn close(&self) -> [LobbyMessage; 2] {
        [
            (
                MATCH_LOBBY,
                53003,
                Fields::from([(0x78, match_lobby_id(self.id))]),
            ),
            (CUSTOM_GAMES, 23311, Fields::new()),
        ]
    }
}

fn map_header(map: u64) -> u64 {
    if map == 0 {
        0
    } else {
        MAP_HEADER_TYPE | (map & 0xffff_ffff)
    }
}

fn free_position(
    capacity: Capacity,
    occupied: impl IntoIterator<Item = Position>,
    spectator: bool,
) -> Option<Position> {
    let occupied: Vec<_> = occupied.into_iter().collect();
    let mut teams = [0, 1, FREE_FOR_ALL_ROSTER_TEAM, SPECTATOR_TEAM];
    teams.sort_by_key(|&team| occupied.iter().filter(|seat| seat.0 == team).count());
    teams
        .into_iter()
        .filter(|&team| (team == SPECTATOR_TEAM) == spectator)
        .flat_map(|team| (0..capacity[team as usize] as u8).map(move |slot| (team, slot)))
        .find(|seat| !occupied.contains(seat))
}

pub(crate) fn match_team(team: u8) -> u8 {
    if team == FREE_FOR_ALL_ROSTER_TEAM {
        0
    } else {
        team
    }
}

fn hybrid_rules() -> Fields {
    let teams = [0, 1].map(|team| Struct(Fields::from([(0x60, U(team)), (0x61, U(TEAM_SIZE))])));
    let start = Struct(Fields::from([(0, U(9552)), (4, U(9624)), (6, U(3))]));
    Fields::from([
        (0xa8, U(HYBRID_PRESET)),
        (0xb0, U(HYBRID)),
        (0xc0, Array(teams.into())),
        (0xd8, Array(vec![start])),
        (0x108, U(1)),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jam_codec::Schemas;
    use serde::Deserialize;
    use sha2::{Digest, Sha256};

    fn host(id: [u64; 2], name: &str, card: Fields, party: [u64; 2]) -> CustomRoom {
        CustomRoom::new(
            id,
            name,
            1,
            Member {
                card,
                party,
                position: (0, 0),
            },
        )
    }

    #[test]
    fn public_listing_and_shared_seats_follow_the_multiplayer_contract() {
        #[derive(Deserialize)]
        struct Contract {
            schema_version: u32,
            room_id: [u64; 2],
            host_account: [u64; 2],
            guest_account: [u64; 2],
            room_name: String,
            browser_header_hex: String,
            browser_initial_counts: Vec<u64>,
            initial_positions: [Position; 2],
            same_team_positions: [Position; 2],
        }
        let contract: Contract = serde_json::from_str(include_str!(
            "../../fixtures/behavior/custom-room-multiplayer-v1.json"
        ))
        .unwrap();
        assert_eq!(contract.schema_version, 1);
        let account = |[lo, hi]: [u64; 2]| Struct(id16(lo, hi));
        let card = |id| Fields::from([(0, account(id))]);
        let mut room = host(
            contract.room_id,
            &contract.room_name,
            card(contract.host_account),
            [1, 2],
        );

        let schemas = Schemas::from_json(r#"{"262EF305":{"40600":[],"40603":[]}}"#).unwrap();
        let header = schemas.header(BROWSER, 9, BROWSER_RESULTS).unwrap();
        assert_eq!(
            header
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>(),
            contract.browser_header_hex,
        );
        let (kind, names) = browser_filter(&Fields::from([(
            0x78,
            Struct(Fields::from([
                (
                    0,
                    Array(vec![Struct(Fields::from([(
                        0xa0,
                        Text(contract.room_name.to_uppercase()),
                    )]))]),
                ),
                (0x6e, U(3)),
            ])),
        )]));
        assert_eq!(kind, 3);
        let listing = room.browser_row(&names).unwrap();
        let row = structure(structure(Some(&listing)).unwrap().get(&0)).unwrap();
        assert_eq!(row.get(&0xa8), Some(&account(contract.host_account)));
        assert_eq!(row.get(&0x100), Some(&Text(contract.room_name)));
        assert_eq!(
            (0xc8..=0xcd)
                .map(|offset| number(row.get(&offset)))
                .collect::<Vec<_>>(),
            contract.browser_initial_counts,
        );
        assert!(room.browser_row(&["other".into()]).is_none());
        assert_eq!(
            join_id(&(
                MATCH_LOBBY_OUT,
                52900,
                Fields::from([(0x78, row[&0x98].clone()), (0x98, Value::Signed(1)),])
            )),
            Some(contract.room_id),
        );

        let position = room.join_position().unwrap();
        room.members.insert(
            2,
            Member {
                card: card(contract.guest_account),
                party: [3, 4],
                position,
            },
        );
        let positions = |room: &CustomRoom| {
            struct_rows(room.roster().2.get(&0x78))
                .map(|row| (number(row.get(&0xa8)) as u8, number(row.get(&0xaf)) as u8))
                .collect::<Vec<_>>()
        };
        assert_eq!(positions(&room), contract.initial_positions);
        let request = |id| Fields::from([(0x78, account(id)), (0x89, U(0)), (0x8a, U(AUTO_SLOT))]);
        assert!(
            room.seat_request(2, &request(contract.host_account))
                .is_none()
        );
        let (target, team, slot) = room
            .seat_request(2, &request(contract.guest_account))
            .unwrap();
        assert_eq!(room.choose_slot(target, team, Some(0)), None);
        room.members.get_mut(&target).unwrap().position =
            (team, room.choose_slot(target, team, slot).unwrap());
        assert_eq!(positions(&room), contract.same_team_positions);
        assert_eq!(room.choose_slot(target, team, None), Some(1));
    }

    #[test]
    fn live_settings_notify_after_the_last_part_and_apply_seats_after_the_match() {
        let contract: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/custom-room-start-v1.json"
        ))
        .unwrap();
        let expected = &contract["controls"];
        let next_team_size = expected["next_match_team_size"].as_u64().unwrap();
        let notice =
            u64::from_str_radix(expected["settings_updated_notice"].as_str().unwrap(), 16).unwrap();
        let notice_id = expected["notice_message"].as_u64().unwrap() as u32;

        let mut room = host([1, 2], "Fixture", Fields::new(), [1, 2]);
        room.members.get_mut(&1).unwrap().position.1 = TEAM_SIZE as u8 - 1;
        let mut rules = hybrid_rules();
        rules.insert(
            0xc0,
            Array(
                [0, 1]
                    .into_iter()
                    .map(|team| Struct(Fields::from([(0x60, U(team)), (0x61, U(next_team_size))])))
                    .collect(),
            ),
        );
        let lobby = Struct(Fields::from([(0, U(3)), (4, U(1)), (8, U(1))]));
        let header = (
            GAME_REQUEST,
            SAVE_SETTINGS,
            Fields::from([
                (0x78, Struct(Fields::from([(0x1d0, lobby)]))),
                (0x3d0, Bool(true)),
            ]),
        );
        let map = (
            GAME_REQUEST,
            MAP_SETTINGS,
            Fields::from([
                (
                    0x78,
                    Struct(Fields::from([
                        (0, Struct(rules.clone())),
                        (0x138, U(KING_ROW)),
                    ])),
                ),
                (0x1b8, Bool(true)),
            ]),
        );
        let mode = (
            GAME_REQUEST,
            MODE_SETTINGS,
            Fields::from([(0x78, Struct(rules))]),
        );

        for expected_ids in [&[23312, 23313, 23314, notice_id][..], &[]] {
            assert!(room.edit(&header, true).is_empty());
            assert!(room.edit(&map, true).is_empty());
            let replies = room.edit(&mode, true);
            assert_eq!(
                replies.iter().map(|(_, id, _)| *id).collect::<Vec<_>>(),
                expected_ids
            );
            if let Some(last) = replies.last() {
                assert_eq!(
                    last,
                    &(CUSTOM_GAMES, notice_id, Fields::from([(0x78, U(notice))]))
                );
            }
            assert_eq!(room.members[&1].position.1, TEAM_SIZE as u8 - 1);
        }
        room.fit_seats();
        assert_eq!(room.members[&1].position.1, next_team_size as u8 - 1);
    }

    #[test]
    fn inline_settings_switch_the_host_to_the_free_for_all_roster() {
        let card = Fields::from([(0, Struct(id16(3, 4)))]);
        let mut room = host([1, 2], "Room", card.clone(), [5, 6]);
        let rules = Struct(Fields::from([
            (0xa8, U(0x0fd0_0000_0000_006f)),
            (0xb0, U(0x0230_0000_0000_001e)),
            (0x108, U(1)),
            (
                0xc0,
                Array(vec![Struct(Fields::from([
                    (0x60, U(u64::from(FREE_FOR_ALL_TEAM))),
                    (0x61, U(8)),
                ]))]),
            ),
        ]));
        let request = (
            GAME_REQUEST,
            SAVE_SETTINGS,
            Fields::from([(
                0x78,
                Struct(Fields::from([
                    (
                        0,
                        Array(vec![Struct(Fields::from([
                            (0, rules.clone()),
                            (0x138, U(KING_ROW)),
                        ]))]),
                    ),
                    (0x18, Array(vec![rules])),
                ])),
            )]),
        );
        let messages = room.edit(&request, false);
        assert_eq!(
            messages.iter().map(|message| message.1).collect::<Vec<_>>(),
            [53000, 23304, 23305, 53001, 53002]
        );
        assert_eq!(room.members[&1].position, (FREE_FOR_ALL_ROSTER_TEAM, 0));
        assert_eq!(room.capacity(room.settings.preview().1), [0, 0, 8, 0]);
        let move_to = Fields::from([
            (0x78, card[&0].clone()),
            (0x89, U(u64::from(FREE_FOR_ALL_ROSTER_TEAM))),
            (0x8a, U(7)),
        ]);
        let (member, team, slot) = room.seat_request(1, &move_to).unwrap();
        assert_eq!(room.choose_slot(member, team, slot), Some(7));
    }

    #[test]
    #[ignore = "requires the local 104319 lobby schemas"]
    fn custom_room_responses_match_the_reference_wire() {
        #[derive(Deserialize)]
        struct Input {
            create_body: String,
            room_id: [u64; 2],
            account_id: [u64; 2],
            party_id: [u64; 2],
            display_name: String,
        }

        #[derive(Deserialize)]
        struct Contract {
            schema_version: u32,
            input: Input,
            expected: Vec<(u32, String)>,
        }

        let contract: Contract = serde_json::from_str(include_str!(
            "../../fixtures/behavior/custom-room-create-v1.json"
        ))
        .unwrap();
        assert_eq!(contract.schema_version, 1);
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let schemas = Schemas::load(runtime.join("data/schemas_174.json")).unwrap();
        let input = contract.input;
        let bytes: Vec<_> = (0..input.create_body.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&input.create_body[index..index + 2], 16).unwrap())
            .collect();
        let fields = schemas
            .decode(GAME_REQUEST, CREATE_GAME, &bytes, true)
            .unwrap();
        let request = (GAME_REQUEST, CREATE_GAME, fields);
        assert!(!crate::lobby_practice::is_request(&request));
        let name = create_name(&request).unwrap().unwrap();
        let account = Struct(id16(input.account_id[0], input.account_id[1]));
        let card = Fields::from([
            (0, account.clone()),
            (0x10, account),
            (0x40, Text(input.display_name)),
        ]);
        let room = host(input.room_id, name, card, input.party_id);
        let joined = room.messages();
        assert_eq!(room.messages(), joined);
        for (room_id, cancelled, expected) in [
            (input.room_id, false, true),
            (input.room_id, true, false),
            ([77, 88], false, false),
        ] {
            let request = (
                MATCH_LOBBY_OUT,
                52902,
                Fields::from([(0x78, match_lobby_id(room_id)), (0x89, Bool(cancelled))]),
            );
            assert_eq!(room.is_leave(&request), expected);
        }
        let actual = joined
            .into_iter()
            .chain(room.close())
            .map(|(crc, id, fields)| {
                let bytes = schemas.encode(crc, id, &fields).unwrap();
                (id, format!("{:x}", Sha256::digest(bytes)))
            })
            .collect::<Vec<_>>();
        assert_eq!(actual, contract.expected);
    }
}
