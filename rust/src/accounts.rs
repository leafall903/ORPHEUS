//! Saved profiles and login-name account IDs for the 1.74 lobby.

use std::{
    collections::{HashMap, HashSet},
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicU8, Ordering},
    },
    time::UNIX_EPOCH,
};

pub(crate) use orpheus_runtime::format::parse_guid;
use orpheus_runtime::format::trim_python_whitespace as trim_name;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::jam_codec::{Fields, Value as JamValue};

pub const DEFAULT_ICON: u64 = 0x0250_0000_0000_02f7;
pub const BOT_NAME: &str = "Bot";
const UNLOCKS_RESET_MARKER: &str = "unlocks_reset_v2";

// The type order determines the IDs in new profiles.
pub(crate) const BOX_TYPES: [(i32, &str, u64); 11] = [
    (0, "Standard", 50),
    (1, "Summer Games", 10),
    (2, "Halloween Terror", 10),
    (3, "Winter Wonderland", 10),
    (4, "Lunar New Year", 10),
    (5, "Archives", 10),
    (6, "Anniversary", 10),
    (7, "Golden", 10),
    (9, "Legendary Anniversary", 10),
    (10, "Wrecking Ball", 0),
    (12, "Legendary", 10),
];

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct LootBox {
    pub id: u64,
    #[serde(rename = "type")]
    pub box_type: i32,
    pub name: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(untagged)]
pub enum GreetedEvent {
    Id(u64),
    Name(String),
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(default)]
pub struct Profile {
    pub player_name: String,
    pub region: String,
    pub game_region: String,
    pub level: i64,
    pub credits: i64,
    pub comp_points: i64,
    pub league_tokens: i64,
    #[serde(with = "guid")]
    pub icon_guid: u64,
    #[serde(with = "guid")]
    pub frame_guid: u64,
    pub next_box_id: u64,
    pub loot_boxes: Vec<LootBox>,
    pub unlocked_items: Vec<Value>,
    pub unlock_all: bool,
    pub bot_chat: bool,
    pub friends: Vec<String>,
    pub friend_requests: Vec<String>,
    pub last_online: i64,
    pub lobby_hero: String,
    pub events: Vec<String>,
    pub greeted_events: Vec<GreetedEvent>,
    pub server_date: String,
    pub challenge: String,
    pub challenge_wins: i64,
    pub endorsement_level: i64,
    #[serde(deserialize_with = "map_or_empty")]
    pub ratings: Map<String, Value>,
    pub season: i64,
    #[serde(deserialize_with = "map_or_empty")]
    pub matches: Map<String, Value>,
    #[serde(deserialize_with = "map_or_empty")]
    pub wins: Map<String, Value>,
    pub sms_protect: bool,
    pub seasons_seen: Vec<i64>,
    #[serde(deserialize_with = "map_or_empty")]
    pub priority_passes: Map<String, Value>,
    #[serde(deserialize_with = "map_or_empty")]
    pub loadouts: Map<String, Value>,
    #[serde(deserialize_with = "map_or_empty")]
    pub settings: Map<String, Value>,
    #[serde(deserialize_with = "map_or_empty")]
    pub ux_states: Map<String, Value>,
    #[serde(deserialize_with = "map_or_empty")]
    pub stats: Map<String, Value>,
}

impl Default for Profile {
    fn default() -> Self {
        Self {
            player_name: "Researcher".into(),
            region: "US".into(),
            game_region: "americas".into(),
            level: 100,
            credits: 50_000,
            comp_points: 6_000,
            league_tokens: 1_000,
            icon_guid: DEFAULT_ICON,
            frame_guid: 0,
            next_box_id: 1,
            loot_boxes: Vec::new(),
            unlocked_items: Vec::new(),
            unlock_all: false,
            bot_chat: false,
            friends: Vec::new(),
            friend_requests: Vec::new(),
            last_online: 0,
            lobby_hero: "random".into(),
            events: vec!["goodbye".into()],
            greeted_events: Vec::new(),
            server_date: String::new(),
            challenge: String::new(),
            challenge_wins: 0,
            endorsement_level: 1,
            ratings: Map::new(),
            season: 32,
            matches: Map::new(),
            wins: Map::new(),
            sms_protect: true,
            seasons_seen: Vec::new(),
            priority_passes: Map::new(),
            loadouts: Map::new(),
            settings: Map::new(),
            ux_states: Map::new(),
            stats: Map::from_iter(
                [
                    "boxes_opened",
                    "legendaries_dropped",
                    "credits_earned_from_duplicates",
                ]
                .map(|key| (key.into(), Value::from(0))),
            ),
        }
    }
}

impl Profile {
    /// Add boxes without reusing an ID if an old profile's next ID fell behind.
    pub fn add_boxes(&mut self, box_type: i32, count: u64) -> io::Result<Vec<LootBox>> {
        let highest = self
            .loot_boxes
            .iter()
            .map(|item| item.id)
            .max()
            .unwrap_or(0);
        let first = self
            .next_box_id
            .max(highest.checked_add(1).ok_or_else(box_id_overflow)?);
        let end = first.checked_add(count).ok_or_else(box_id_overflow)?;
        let name = box_name(box_type);
        let added: Vec<_> = (first..end)
            .map(|id| LootBox {
                id,
                box_type,
                name: name.clone(),
            })
            .collect();
        self.loot_boxes.extend_from_slice(&added);
        self.next_box_id = end;
        Ok(added)
    }

    pub fn unlocked_guids(&self) -> HashSet<u64> {
        self.unlocked_items
            .iter()
            .filter_map(|value| match value {
                Value::String(text) => parse_guid(text),
                Value::Number(number) => number.as_u64().or_else(|| {
                    let value = number.as_f64()?;
                    (value >= 0.0 && value < u64::MAX as f64).then_some(value as u64)
                }),
                Value::Bool(value) => Some(u64::from(*value)),
                _ => None,
            })
            .collect()
    }

    pub fn from_json(data: Value) -> io::Result<Self> {
        let mut profile: Profile = serde_json::from_value(data).map_err(json_error)?;
        profile.normalize_settings()?;
        Ok(profile)
    }

    pub fn normalize_settings(&mut self) -> io::Result<()> {
        if let Some(entries) = self.settings.get_mut("+0x130") {
            if !entries.is_null() {
                let normalized = normalize_setting_entries(entries)?;
                *entries = Value::Array(normalized);
            }
        }
        Ok(())
    }
}

fn parse_i128_field(raw: &Value, field: &str, min: i128, max: i128) -> io::Result<i128> {
    let parsed = match raw {
        Value::Number(number) => number
            .as_i64()
            .map(i128::from)
            .or_else(|| number.as_u64().map(i128::from)),
        Value::String(text) => parse_numeric_str(text.trim()),
        _ => None,
    }
    .ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{field} must be an integer or numeric string"),
        )
    })?;
    if parsed < min || parsed > max {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{field} out of range [{min}, {max}]: {parsed}"),
        ));
    }
    Ok(parsed)
}

fn parse_numeric_str(text: &str) -> Option<i128> {
    if text.is_empty() {
        return None;
    }
    let (neg, digits) = if let Some(rest) = text.strip_prefix('-') {
        (true, rest)
    } else if let Some(rest) = text.strip_prefix('+') {
        (false, rest)
    } else {
        (false, text)
    };
    let mag = if let Some(hex) = digits
        .strip_prefix("0x")
        .or_else(|| digits.strip_prefix("0X"))
    {
        u128::from_str_radix(hex, 16).ok()?
    } else {
        digits.parse::<u128>().ok()?
    };
    if neg {
        i128::try_from(mag).ok().and_then(i128::checked_neg)
    } else {
        i128::try_from(mag).ok()
    }
}

pub fn normalize_setting_key(raw: &Value) -> io::Result<i32> {
    let val = parse_i128_field(raw, "+0x8", i128::from(i32::MIN), i128::from(u32::MAX))?;
    Ok(if val >= i128::from(1u32 << 31) {
        (val as u32) as i32
    } else {
        val as i32
    })
}

pub fn normalize_setting_value(raw: &Value) -> io::Result<Value> {
    let val = parse_i128_field(raw, "+0x0", i128::from(i64::MIN), i128::from(u64::MAX))?;
    let signed = if val >= i128::from(1u64 << 63) {
        (val as u64) as i64
    } else {
        val as i64
    };
    Ok(Value::from(signed))
}

pub fn normalize_setting_entries(raw: &Value) -> io::Result<Vec<Value>> {
    let Value::Array(items) = raw else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "settings['+0x130'] must be an array",
        ));
    };
    let mut normalized = Vec::with_capacity(items.len());
    for item in items {
        let Value::Object(map) = item else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "each settings['+0x130'] entry must be an object",
            ));
        };
        let v = map.get("+0x0").ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidData, "missing '+0x0' in +0x130 entry")
        })?;
        let k = map.get("+0x8").ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidData, "missing '+0x8' in +0x130 entry")
        })?;
        let mut out = Map::new();
        out.insert("+0x0".into(), normalize_setting_value(v)?);
        out.insert("+0x8".into(), Value::from(normalize_setting_key(k)?));
        normalized.push(Value::Object(out));
    }
    Ok(normalized)
}

pub fn box_name(box_type: i32) -> String {
    BOX_TYPES
        .iter()
        .find(|(id, _, _)| *id == box_type)
        .map_or_else(|| format!("Type {box_type}"), |(_, name, _)| (*name).into())
}

fn box_id_overflow() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "profile exhausted the loot box ID range",
    )
}

pub fn new_profile() -> Profile {
    let mut profile = Profile::default();
    profile.events.push("halloween".into());
    for (box_type, _, count) in BOX_TYPES {
        profile
            .add_boxes(box_type, count)
            .expect("starter box IDs fit");
    }
    profile
}

fn map_or_empty<'de, D>(deserializer: D) -> Result<Map<String, Value>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    Ok(Option::deserialize(deserializer)?.unwrap_or_default())
}

mod guid {
    use serde::{Deserialize, Deserializer, Serializer, de::Error};
    use serde_json::Value;

    pub fn deserialize<'de, D: Deserializer<'de>>(deserializer: D) -> Result<u64, D::Error> {
        let input = Value::deserialize(deserializer)?;
        input
            .as_u64()
            .or_else(|| input.as_str().and_then(super::parse_guid))
            .ok_or_else(|| D::Error::custom("expected a GUID integer or text"))
    }

    pub fn serialize<S: Serializer>(value: &u64, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&format!("0x{value:016X}"))
    }
}

pub(crate) fn saved_integer(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => number
            .as_i64()
            .or_else(|| number.as_f64().map(|value| value as i64)),
        Value::Bool(value) => Some(i64::from(*value)),
        Value::String(value) => value.trim().parse().ok(),
        _ => None,
    }
}

fn name_key(name: &str) -> String {
    trim_name(name).to_lowercase()
}

pub fn account_id_for(name: &str) -> u32 {
    0x1000_0000 | (crc32fast::hash(name_key(name).as_bytes()) & 0x0fff_ffff)
}

pub fn battle_tag(nickname: &str, account_lo: u32) -> String {
    format!("{}#{}", trim_name(nickname), 1000 + account_lo % 9000)
}

fn json_error(error: serde_json::Error) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error)
}

fn sanitize_saved_settings(data: &mut Value) {
    let Some(entries) = data
        .get_mut("settings")
        .and_then(|s| s.as_object_mut())
        .and_then(|s| s.get_mut("+0x130"))
        .and_then(|v| v.as_array_mut())
    else {
        return;
    };
    let mut kept = Vec::with_capacity(entries.len());
    for entry in entries.drain(..) {
        match normalize_setting_entries(&Value::Array(vec![entry])) {
            Ok(mut valid) => kept.append(&mut valid),
            Err(error) => eprintln!("[accounts] skipping invalid +0x130 setting entry: {error}"),
        }
    }
    *entries = kept;
}

fn read_profile(path: &Path) -> io::Result<(Profile, Option<String>, bool)> {
    let mut data: Value = serde_json::from_slice(&fs::read(path)?).map_err(json_error)?;
    let account_name = data
        .get("account_name")
        .and_then(Value::as_str)
        .map(trim_name)
        .filter(|name| !name.is_empty())
        .map(str::to_owned);
    let migrate = data.get(UNLOCKS_RESET_MARKER) != Some(&Value::Bool(true));
    sanitize_saved_settings(&mut data);
    let mut profile = Profile::from_json(data)?;
    if migrate {
        profile.unlocked_items.clear();
    }
    Ok((profile, account_name, migrate))
}

fn write_profile_with_account(
    profile: &Profile,
    account_name: Option<&str>,
    path: &Path,
    replace: bool,
) -> io::Result<()> {
    #[derive(Serialize)]
    struct SavedProfile<'a> {
        #[serde(flatten)]
        profile: &'a Profile,
        #[serde(skip_serializing_if = "Option::is_none")]
        account_name: Option<&'a str>,
        unlocks_reset_v2: bool,
    }

    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let bytes = serde_json::to_vec_pretty(&SavedProfile {
        profile,
        account_name,
        unlocks_reset_v2: true,
    })
    .map_err(json_error)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(&bytes)?;
    temporary.as_file().sync_all()?;
    if replace {
        temporary.persist(path)
    } else {
        temporary.persist_noclobber(path)
    }
    .map(|_| ())
    .map_err(|error| error.error)
}

pub fn save_profile(profile: &Profile, path: &Path) -> io::Result<()> {
    write_profile_with_account(profile, None, path, true)
}

/// Import Python profiles while the destination server is stopped, keeping existing saves.
pub fn import_python_profiles(source: &Path, data: &Path) -> io::Result<(usize, usize)> {
    let mut entries = fs::read_dir(source)?.collect::<io::Result<Vec<_>>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    let registry = Accounts::new(data.join("profiles"), data.join("profile.json"))?;
    let mut pending = Vec::new();
    let mut names = HashSet::new();
    let mut skipped = 0;
    for entry in entries {
        let path = entry.path();
        if !entry.file_type()?.is_file()
            || !path
                .extension()
                .is_some_and(|ext| ext.eq_ignore_ascii_case("json"))
        {
            continue;
        }
        let stem = path
            .file_stem()
            .and_then(|name| name.to_str())
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidData, "invalid profile filename")
            })?;
        let mut value: Value = serde_json::from_slice(&fs::read(&path)?).map_err(json_error)?;
        let name = trim_name(
            value
                .get("account_name")
                .and_then(Value::as_str)
                .unwrap_or(stem),
        );
        if name.is_empty() || !names.insert(name_key(name)) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "empty or duplicate login name in import",
            ));
        }
        crate::bnet_session_key::session_key(name)?;
        let (destination, disambiguated) = registry.path_for(name)?;
        if destination.try_exists()? {
            skipped += 1;
            continue;
        }
        let saved_name = disambiguated.then(|| name.to_owned());
        let mut checked = value.clone();
        sanitize_saved_settings(&mut checked);
        Profile::from_json(checked)?;
        let object = value.as_object_mut().ok_or_else(|| {
            io::Error::new(io::ErrorKind::InvalidData, "expected a profile object")
        })?;
        object.insert(UNLOCKS_RESET_MARKER.into(), Value::Bool(true));
        if let Some(name) = saved_name {
            object.insert("account_name".into(), Value::String(name));
        }
        pending.push((
            destination,
            serde_json::to_vec_pretty(&value).map_err(json_error)?,
        ));
    }
    let mut imported = 0;
    for (path, bytes) in pending {
        let mut file = tempfile::NamedTempFile::new_in(&registry.directory)?;
        file.write_all(&bytes)?;
        file.as_file().sync_all()?;
        match file.persist_noclobber(path) {
            Ok(_) => imported += 1,
            Err(error) if error.error.kind() == io::ErrorKind::AlreadyExists => skipped += 1,
            Err(error) => return Err(error.error),
        }
    }
    Ok((imported, skipped))
}

fn backup_corrupt_profile(path: &Path) -> io::Result<PathBuf> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let stem = path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or("profile");
    let stamp = std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    for attempt in 0u32.. {
        let suffix = if attempt == 0 {
            format!("{stem}.corrupt.{stamp}.json.bak")
        } else {
            format!("{stem}.corrupt.{stamp}-{attempt}.json.bak")
        };
        let backup = parent.join(suffix);
        if !backup.try_exists()? {
            fs::copy(path, &backup)?;
            return Ok(backup);
        }
    }
    unreachable!("u32 attempt range exhausted")
}

fn load_existing_profile(path: &Path) -> io::Result<(Profile, Option<String>)> {
    match read_profile(path) {
        Ok((profile, account_name, migrate)) => {
            if migrate {
                write_profile_with_account(&profile, account_name.as_deref(), path, true)?;
            }
            Ok((profile, account_name))
        }
        Err(error) if error.kind() == io::ErrorKind::InvalidData => {
            if let Ok(backup) = backup_corrupt_profile(path) {
                eprintln!(
                    "[accounts] corrupt profile {} backed up to {}: {error}",
                    path.display(),
                    backup.display()
                );
            }
            Err(error)
        }
        Err(error) => Err(error),
    }
}

pub fn load_or_create_profile(path: &Path) -> io::Result<Profile> {
    match load_existing_profile(path) {
        Ok((profile, _)) => Ok(profile),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            create_profile(new_profile(), None, path).map(|(profile, _)| profile)
        }
        Err(error) => Err(error),
    }
}

fn create_profile(
    profile: Profile,
    account_name: Option<&str>,
    path: &Path,
) -> io::Result<(Profile, Option<String>)> {
    match write_profile_with_account(&profile, account_name, path, false) {
        Ok(()) => Ok((profile, account_name.map(str::to_owned))),
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => load_existing_profile(path),
        Err(error) => Err(error),
    }
}

pub struct Account {
    pub name: String,
    pub account_lo: u32,
    pub path: Option<PathBuf>,
    pub created: i64,
    saved_account_name: Option<String>,
    profile: Mutex<Profile>,
    status: AtomicU8,
}

impl Account {
    fn new(
        name: String,
        profile: Profile,
        path: Option<PathBuf>,
        created: i64,
        saved_account_name: Option<String>,
    ) -> Self {
        Self {
            account_lo: account_id_for(&name),
            name,
            path,
            created,
            saved_account_name,
            profile: Mutex::new(profile),
            status: AtomicU8::new(1),
        }
    }

    pub fn profile(&self) -> MutexGuard<'_, Profile> {
        self.profile
            .lock()
            .expect("account profile lock was poisoned")
    }

    pub fn status(&self) -> u8 {
        self.status.load(Ordering::Relaxed)
    }

    pub fn set_status(&self, status: u8) {
        self.status.store(status, Ordering::Relaxed);
    }

    pub fn is_virtual(&self) -> bool {
        self.path.is_none()
    }

    pub fn battle_tag(&self) -> String {
        battle_tag(&self.profile().player_name, self.account_lo)
    }

    pub fn account_id_fields(&self) -> Fields {
        Fields::from([
            (0, JamValue::Unsigned(u64::from(self.account_lo))),
            (8, JamValue::Unsigned(0x0100_0000_0000_0000)),
        ])
    }

    pub fn save(&self) -> io::Result<()> {
        if let Some(path) = &self.path {
            write_profile_with_account(
                &self.profile(),
                self.saved_account_name.as_deref(),
                path,
                true,
            )?;
        }
        Ok(())
    }

    pub(crate) fn edit_profile<T>(
        &self,
        edit: impl FnOnce(&mut Profile) -> io::Result<Option<T>>,
    ) -> io::Result<Option<T>> {
        let mut profile = self.profile();
        let mut edited = profile.clone();
        let Some(result) = edit(&mut edited)? else {
            return Ok(None);
        };
        if let Some(path) = &self.path {
            write_profile_with_account(&edited, self.saved_account_name.as_deref(), path, true)?;
        }
        *profile = edited;
        Ok(Some(result))
    }

    /// Persist a completed match's career stats, XP, level-ups, and level-up loot boxes.
    /// Returns the number of levels gained.
    pub fn record_career_match(
        &self,
        record: &crate::game_progression::CareerMatchRecord,
    ) -> io::Result<u32> {
        let gained = self.edit_profile(|profile| {
            let bump_map = |map: &mut Map<String, Value>, key: &str, delta: u64| {
                let prev = map.get(key).and_then(Value::as_u64).unwrap_or(0);
                map.insert(key.to_owned(), Value::from(prev.saturating_add(delta)));
            };
            bump_map(&mut profile.matches, &record.mode_name, 1);
            if record.won {
                bump_map(&mut profile.wins, &record.mode_name, 1);
            }
            bump_map(&mut profile.stats, "eliminations", record.eliminations);
            bump_map(&mut profile.stats, "deaths", record.deaths);
            bump_map(&mut profile.stats, "hero_damage", record.hero_damage);
            bump_map(&mut profile.stats, "healing", record.healing);
            bump_map(
                &mut profile.stats,
                "objective_kills",
                record.objective_kills,
            );
            bump_map(
                &mut profile.stats,
                "objective_time_secs",
                record.objective_time_secs,
            );
            bump_map(&mut profile.stats, "final_blows", record.final_blows);
            bump_map(
                &mut profile.stats,
                "gold_medals",
                u64::from(record.gold_medals),
            );
            bump_map(
                &mut profile.stats,
                "silver_medals",
                u64::from(record.silver_medals),
            );
            bump_map(
                &mut profile.stats,
                "bronze_medals",
                u64::from(record.bronze_medals),
            );
            let prev_xp = profile
                .stats
                .get("xp_progress")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            let total_xp = prev_xp.saturating_add(record.xp.total_xp);
            let levels_gained = (total_xp / crate::game_progression::XP_PER_LEVEL) as u32;
            let remainder_xp = total_xp % crate::game_progression::XP_PER_LEVEL;
            profile
                .stats
                .insert("xp_progress".to_owned(), Value::from(remainder_xp));
            bump_map(&mut profile.stats, "total_xp", record.xp.total_xp);
            if levels_gained > 0 {
                profile.level = profile.level.saturating_add(i64::from(levels_gained));
                for _ in 0..levels_gained {
                    let id = profile.next_box_id;
                    profile.next_box_id = profile.next_box_id.saturating_add(1);
                    profile.loot_boxes.push(LootBox {
                        id,
                        box_type: 0,
                        name: "Standard".into(),
                    });
                }
            }
            Ok(Some(levels_gained))
        })?;
        Ok(gained.unwrap_or(0))
    }
}

pub struct Accounts {
    directory: PathBuf,
    template: PathBuf,
    by_name: Mutex<HashMap<String, Arc<Account>>>,
    pub bot: Arc<Account>,
}

impl Accounts {
    pub fn new(directory: impl Into<PathBuf>, template: impl Into<PathBuf>) -> io::Result<Self> {
        let directory = directory.into();
        fs::create_dir_all(&directory)?;
        let bot = Profile {
            player_name: BOT_NAME.into(),
            level: 250,
            endorsement_level: 5,
            ..Profile::default()
        };
        Ok(Self {
            directory,
            template: template.into(),
            by_name: Mutex::new(HashMap::new()),
            bot: Arc::new(Account::new(BOT_NAME.into(), bot, None, 0, None)),
        })
    }

    pub fn get(&self, name: &str) -> io::Result<Arc<Account>> {
        let name = trim_name(name);
        let key = name_key(name);
        let mut accounts = self
            .by_name
            .lock()
            .expect("account registry lock was poisoned");
        if let Some(account) = accounts.get(&key) {
            return Ok(Arc::clone(account));
        }
        let (path, disambiguated) = self.path_for(name)?;
        let saved_account_name = disambiguated.then(|| name.to_owned());
        let (profile, loaded_account_name) = match load_existing_profile(&path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                let mut profile = match read_profile(&self.template) {
                    Ok((profile, _, _)) => profile,
                    Err(error)
                        if matches!(
                            error.kind(),
                            io::ErrorKind::NotFound | io::ErrorKind::InvalidData
                        ) =>
                    {
                        new_profile()
                    }
                    Err(error) => return Err(error),
                };
                profile.player_name = name.into();
                create_profile(profile, saved_account_name.as_deref(), &path)?
            }
            result => result?,
        };
        let created = creation_seconds(&fs::metadata(&path)?);
        let account = Arc::new(Account::new(
            name.into(),
            profile,
            Some(path),
            created,
            loaded_account_name.or(saved_account_name),
        ));
        accounts.insert(key, Arc::clone(&account));
        Ok(account)
    }

    pub fn all_saved(&self) -> io::Result<Vec<String>> {
        let mut names: HashSet<_> = self
            .saved_paths()?
            .filter_map(|path| {
                if let Ok((_, Some(account_name), _)) = read_profile(&path) {
                    return Some(account_name);
                }
                path.file_stem()?.to_str().map(str::to_owned)
            })
            .collect();
        names.extend(
            self.by_name
                .lock()
                .expect("account registry lock was poisoned")
                .values()
                .map(|account| account.name.clone()),
        );
        let mut names: Vec<_> = names.into_iter().collect();
        names.sort_by_cached_key(|name| (name.to_lowercase(), name.clone()));
        Ok(names)
    }

    pub fn by_battle_tag(&self, tag: &str) -> io::Result<Option<Arc<Account>>> {
        let wanted = name_key(tag);
        if !wanted.contains('#') {
            return Ok(None);
        }
        if self.bot.battle_tag().to_lowercase() == wanted {
            return Ok(Some(Arc::clone(&self.bot)));
        }
        for name in self.all_saved()? {
            let account = self.get(&name)?;
            if account.battle_tag().to_lowercase() == wanted {
                return Ok(Some(account));
            }
        }
        Ok(None)
    }

    pub fn by_id(&self, account_lo: u32) -> io::Result<Option<Arc<Account>>> {
        if account_lo == self.bot.account_lo {
            return Ok(Some(Arc::clone(&self.bot)));
        }
        if let Some(account) = self
            .by_name
            .lock()
            .expect("account registry lock was poisoned")
            .values()
            .find(|account| account.account_lo == account_lo)
        {
            return Ok(Some(Arc::clone(account)));
        }
        for name in self.all_saved()? {
            if account_id_for(&name) == account_lo {
                return self.get(&name).map(Some);
            }
        }
        Ok(None)
    }

    fn path_for(&self, name: &str) -> io::Result<(PathBuf, bool)> {
        let trimmed = trim_name(name);
        let safe: String = trimmed
            .chars()
            .filter(|character| character.is_alphanumeric() || matches!(character, '-' | '_' | ' '))
            .collect();
        let safe = trim_name(&safe);
        let disambiguated = safe.is_empty() || !safe.eq_ignore_ascii_case(trimmed);
        let safe = if safe.is_empty() { "player" } else { safe };
        let filename = if disambiguated {
            format!("{safe}-{:08x}.json", account_id_for(trimmed))
        } else {
            format!("{safe}.json")
        };
        let path = self.directory.join(&filename);
        if path.try_exists()? {
            return Ok((path, disambiguated));
        }
        let key = filename.to_lowercase();
        // Find profiles created with a different letter case even on case-sensitive filesystems.
        let existing = self
            .saved_paths()?
            .find(|saved| {
                saved
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.to_lowercase() == key)
            })
            .unwrap_or(path);
        Ok((existing, disambiguated))
    }

    fn saved_paths(&self) -> io::Result<impl Iterator<Item = PathBuf>> {
        Ok(fs::read_dir(&self.directory)?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| {
                path.extension()
                    .is_some_and(|extension| extension.eq_ignore_ascii_case("json"))
                    && path.is_file()
            }))
    }
}

fn creation_seconds(metadata: &fs::Metadata) -> i64 {
    match metadata.created() {
        Ok(created) => match created.duration_since(UNIX_EPOCH) {
            Ok(age) => age.as_secs() as i64,
            Err(error) => -(error.duration().as_secs() as i64),
        },
        #[cfg(unix)]
        Err(_) => std::os::unix::fs::MetadataExt::ctime(metadata),
        #[cfg(not(unix))]
        Err(_) => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn starter_boxes_and_ids_match_the_python_profile() {
        let mut profile = new_profile();
        assert_eq!(profile.loot_boxes.len(), 140);
        assert_eq!(profile.next_box_id, 141);
        assert_eq!(profile.loot_boxes[49].box_type, 0);
        assert_eq!(profile.loot_boxes[50].box_type, 1);
        assert_eq!(profile.loot_boxes[139].box_type, 12);
        profile.next_box_id = 3;
        let added = profile.add_boxes(99, 2).unwrap();
        assert_eq!(
            added[0],
            LootBox {
                id: 141,
                box_type: 99,
                name: "Type 99".into()
            }
        );
        assert_eq!(added[1].id, 142);
        assert_eq!(profile.next_box_id, 143);
    }

    #[test]
    fn reads_old_guid_encodings_and_migrates_unlocks_once() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("old.json");
        fs::write(
            &path,
            br#"{
            "icon_guid": "0x02500000000002F7",
            "frame_guid": 42,
            "ratings": null,
            "unlocked_items": ["0x10"],
            "settings": {"+0x78": {"list": [1, true, "0xA"]}},
            "unknown": 5
        }"#,
        )
        .unwrap();
        let mut profile = load_or_create_profile(&path).unwrap();
        assert_eq!(profile.icon_guid, DEFAULT_ICON);
        assert_eq!(profile.frame_guid, 42);
        assert!(profile.unlocked_items.is_empty());
        assert!(profile.ratings.is_empty());
        assert_eq!(profile.settings["+0x78"], json!({"list": [1, true, "0xA"]}));
        profile.unlocked_items = vec![json!("0x10"), json!(17), json!("bad")];
        save_profile(&profile, &path).unwrap();
        let on_disk: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(on_disk["frame_guid"], "0x000000000000002A");
        assert_eq!(on_disk[UNLOCKS_RESET_MARKER], true);
        assert!(!on_disk.as_object().unwrap().contains_key("unknown"));
        let again = load_or_create_profile(&path).unwrap();
        assert_eq!(again, profile);
        assert_eq!(again.unlocked_guids(), HashSet::from([16, 17]));
    }

    #[test]
    fn accounts_keep_nicknames_and_identity_across_restarts() {
        let directory = tempfile::tempdir().unwrap();
        let template = directory.path().join("template.json");
        save_profile(
            &Profile {
                credits: 123,
                ..Profile::default()
            },
            &template,
        )
        .unwrap();
        let profiles = directory.path().join("profiles");
        let registry = Accounts::new(&profiles, &template).unwrap();
        let account = registry.get("  Alpha  ").unwrap();
        assert_eq!(account.account_lo, 0x10e0_396a);
        assert_eq!(
            account.account_id_fields()[&0],
            JamValue::Unsigned(0x10e0_396a)
        );
        assert_eq!(account.profile().credits, 123);
        assert_eq!(account.profile().player_name, "Alpha");
        assert!(account.created > 0);
        assert!(Arc::ptr_eq(&account, &registry.get("alpha").unwrap()));
        account.profile().player_name = "NewNick".into();
        account.save().unwrap();

        let registry = Accounts::new(profiles, template).unwrap();
        let again = registry.get("alpha").unwrap();
        assert_eq!(again.profile().player_name, "NewNick");
        assert_eq!(again.account_lo, account.account_lo);
        let tag = again.battle_tag();
        assert!(Arc::ptr_eq(
            &again,
            &registry
                .by_battle_tag(&tag.to_uppercase())
                .unwrap()
                .unwrap()
        ));
        assert!(Arc::ptr_eq(
            &again,
            &registry.by_id(again.account_lo).unwrap().unwrap()
        ));
        assert!(registry.bot.is_virtual());
        assert_eq!(registry.bot.profile().level, 250);
    }

    #[test]
    fn a_login_cannot_escape_the_profiles_directory() {
        let directory = tempfile::tempdir().unwrap();
        let profiles = directory.path().join("profiles");
        let registry = Accounts::new(&profiles, directory.path().join("missing.json")).unwrap();
        let account = registry.get("../ A/B\\ ? ").unwrap();
        let expected_id = account_id_for("../ A/B\\ ? ");
        let expected_file = format!("AB-{expected_id:08x}.json");
        assert_eq!(
            account.path.as_deref(),
            Some(profiles.join(expected_file).as_path())
        );
        assert_eq!(account.profile().player_name, "../ A/B\\ ?");
        let plain = registry.get("AB").unwrap();
        assert_ne!(account.path, plain.path);
        assert_ne!(account.account_lo, plain.account_lo);
        assert_eq!(registry.all_saved().unwrap().len(), 2);
        assert_eq!(account.profile().loot_boxes.len(), 140);
    }

    #[test]
    fn python_import_keeps_legacy_greetings_and_inventory_without_clobbering() {
        let source = tempfile::tempdir().unwrap();
        let state = tempfile::tempdir().unwrap();
        let path = source.path().join("ImportProbe.json");
        let fixture = include_bytes!("../../fixtures/behavior/python-profile-import-v1.json");
        fs::write(&path, fixture).unwrap();

        assert_eq!(
            import_python_profiles(source.path(), state.path()).unwrap(),
            (1, 0)
        );
        let registry = Accounts::new(
            state.path().join("profiles"),
            state.path().join("profile.json"),
        )
        .unwrap();
        let account = registry.get("ImportProbe").unwrap();
        {
            let profile = account.profile();
            assert_eq!(profile.player_name, "Migration Probe");
            assert_eq!(profile.credits, 125);
            assert_eq!(
                profile.greeted_events,
                [
                    GreetedEvent::Id(7),
                    GreetedEvent::Name("synthetic-event".into())
                ]
            );
            assert!(profile.unlocked_guids().contains(&0x0250_0000_0000_00aa));
        }
        account
            .edit_profile(|profile| {
                profile.credits = 250;
                Ok(Some(()))
            })
            .unwrap();
        assert_eq!(
            import_python_profiles(source.path(), state.path()).unwrap(),
            (0, 1)
        );
        let saved = load_existing_profile(account.path.as_deref().unwrap())
            .unwrap()
            .0;
        assert_eq!(saved.credits, 250);
        assert_eq!(saved.greeted_events, account.profile().greeted_events);
        assert_eq!(fs::read(path).unwrap(), fixture);
    }

    #[test]
    fn corrupt_profile_is_backed_up_and_not_overwritten_by_default() {
        let directory = tempfile::tempdir().unwrap();
        let profiles = directory.path().join("profiles");
        fs::create_dir_all(&profiles).unwrap();
        let path = profiles.join("CorruptUser.json");
        let raw_corrupt = b"{not valid json";
        fs::write(&path, raw_corrupt).unwrap();
        let registry = Accounts::new(&profiles, directory.path().join("missing.json")).unwrap();
        let err = match registry.get("CorruptUser") {
            Ok(_) => panic!("corrupt profile must fail instead of resetting to default"),
            Err(err) => err,
        };
        assert_eq!(err.kind(), io::ErrorKind::InvalidData);
        assert_eq!(fs::read(&path).unwrap(), raw_corrupt);
        let backups: Vec<_> = fs::read_dir(&profiles)
            .unwrap()
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.starts_with("CorruptUser.corrupt.") && n.ends_with(".bak"))
            })
            .collect();
        assert_eq!(backups.len(), 1);
        assert_eq!(fs::read(&backups[0]).unwrap(), raw_corrupt);
    }

    #[test]
    fn equipped_loadout_and_loot_box_count_persist_across_accounts_restart() {
        let directory = tempfile::tempdir().unwrap();
        let profiles = directory.path().join("profiles");
        let template = directory.path().join("template.json");
        let registry = Accounts::new(&profiles, &template).unwrap();
        let account = registry.get("rust").unwrap();
        let initial_boxes = account.profile().loot_boxes.len();
        account
            .edit_profile(|profile| {
                profile.loot_boxes.pop();
                profile.loadouts.insert(
                    "0x02E0000000000003".into(),
                    json!({ "+0x38": "0x0250000000004D33" }),
                );
                Ok(Some(()))
            })
            .unwrap();
        drop(registry);

        let reopened = Accounts::new(&profiles, &template).unwrap();
        let reloaded = reopened.get("rust").unwrap();
        assert_eq!(reloaded.profile().loot_boxes.len(), initial_boxes - 1);
        assert_eq!(
            reloaded.profile().loadouts["0x02E0000000000003"]["+0x38"],
            json!("0x0250000000004D33")
        );
    }
}
