//! Match map selection and spawn transforms for the 104319 runtime catalog.
//!
//! Ported from the map portion of ow174/game/content.py.

use std::{
    collections::{BTreeMap, HashMap},
    fs, io,
    path::Path,
};

use indexmap::IndexMap;
use serde::Deserialize;

use crate::accounts::parse_guid;
use crate::pvp_mode::{
    MODE_ASSAULT, MODE_CONTROL, MODE_ESCORT, MODE_HYBRID, ModePlaceablesCatalog,
};
use crate::room_primitives::{BarrierEntry, BarrierKind, SplineNode};

pub const PRACTICE_RANGE_MAP: u64 = 0x0800_0000_0000_0688;
pub const PRACTICE_RANGE_MODE: u64 = 0x0230_0000_0000_0018;
pub(crate) const TUTORIAL_MODE: u64 = 0x0230_0000_0000_0019;
pub const JUNKENSTEIN_MAP: u64 = 0x0800_0000_0000_06c9;
pub const JUNKENSTEIN_REVENGE: u64 = 0x0230_0000_0000_0003;
pub const JUNKENSTEIN_ENDLESS: u64 = 0x0230_0000_0000_002a;
pub(crate) const JUNKENSTEIN_GATE: u32 = 50;
pub const SPAWN_SPACING: f64 = 1.5;

const PRACTICE_RANGE_NAME: &str = "Practice Range";
// The retail loader waits for these modes to publish their own script state.
const SCRIPTED_LOADING_MODES: [u64; 5] = [
    0x0230_0000_0000_0008,
    0x0230_0000_0000_000f,
    0x0230_0000_0000_001a,
    0x0230_0000_0000_0025,
    0x0230_0000_0000_0043,
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct JunkensteinCard {
    pub mode: u64,
    pub players: u8,
    pub difficulty: u64,
    pub challenge: Option<u32>,
}

impl JunkensteinCard {
    pub(crate) fn from_index(index: u32) -> Option<Self> {
        let difficulty = match index {
            0x3c | 0x43 => 0x195e,
            0x3d | 0x44 => 0x195f,
            0x4a | 0x49 | 0x16b..=0x170 => 0x26df,
            0x3e | 0x45 => 0x1960,
            _ => return None,
        };
        let challenge = match index {
            0x16b => Some(0x7d53),
            0x16c => Some(0x7d54),
            0x16d => Some(0x7d60),
            0x16e => Some(0x7d61),
            0x16f => Some(0x7d62),
            0x170 => Some(0x7d63),
            _ => None,
        };
        let mode = match index {
            0x43 | 0x44 | 0x49 | 0x45 => JUNKENSTEIN_ENDLESS,
            _ => JUNKENSTEIN_REVENGE,
        };
        Some(Self {
            mode,
            players: if index == 0x16e { 3 } else { 4 },
            difficulty: 0x0d80_0000_0000_0000 | difficulty,
            challenge,
        })
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
pub struct Spawn {
    pub team: Option<u8>,
    pub position: [f64; 3],
    #[serde(default)]
    pub yaw: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct MapObjective {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub placeable: Option<u32>,
    #[serde(default)]
    pub position: [f64; 3],
    #[serde(default)]
    pub rotation: Option<[f64; 4]>,
    #[serde(default)]
    pub objective: Option<u32>,
    #[serde(default)]
    pub stage: Option<usize>,
    #[serde(default)]
    pub submap_name: Option<String>,
    #[serde(default)]
    pub outlines: Vec<u32>,
    #[serde(default)]
    pub escort_seconds: Option<f64>,
    #[serde(default)]
    pub casual_seconds: Option<f64>,
    #[serde(default)]
    pub unlock_delay: f64,
    #[serde(default)]
    pub unlock_seconds: f64,
    #[serde(default)]
    pub auto_drive: bool,
    #[serde(default)]
    pub speed_multiplier: Option<f64>,
    #[serde(default)]
    pub round_seconds: Option<f64>,
    #[serde(default)]
    pub capture_seconds: Option<f64>,
    #[serde(default)]
    pub decay_seconds: Option<f64>,
    #[serde(default)]
    pub capture_boxes: Vec<[f64; 10]>,
    #[serde(default)]
    pub outline: Option<u32>,
    #[serde(default)]
    pub collision: Vec<serde_json::Value>,
    #[serde(default = "unit_scale")]
    pub scale: [f32; 3],
}

fn unit_scale() -> [f32; 3] {
    [1.0; 3]
}

#[derive(Clone, Debug, PartialEq)]
pub struct ObjectiveModel {
    pub position: [f32; 3],
    pub rotation: [f32; 4],
    pub scale: [f32; 3],
    pub shapes: Vec<serde_json::Value>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct PayloadRoute {
    pub nodes: Vec<SplineNode>,
    pub barriers: Vec<BarrierEntry>,
    pub stage_seconds: Vec<f64>,
    pub later_spawns: Vec<Vec<Spawn>>,
    pub speed_scale: f64,
    pub cart_model: ObjectiveModel,
    pub room_models: BTreeMap<u32, ObjectiveModel>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CaptureCourse {
    pub barriers: Vec<BarrierEntry>,
    pub later_spawns: Vec<Vec<Spawn>>,
    pub room_models: BTreeMap<u32, ObjectiveModel>,
    pub speed_scale: f64,
    pub spawn_candidates: Vec<[Vec<u32>; 2]>,
    pub control: Option<ControlCourse>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ControlCourse {
    pub rules: crate::pvp_mode::ControlRuleSettings,
    pub healing_rooms: Vec<ControlHealingRoom>,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct ControlHealingRoom {
    pub stage: usize,
    pub team: u8,
    pub boxes: Vec<[f64; 10]>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct GameMap {
    pub name: String,
    pub map_guid: u64,
    pub mode_guid: u64,
    pub preset_guid: u64,
    pub placeables: Vec<u32>,
    pub first_entity: Option<u32>,
    pub spawns: Vec<Spawn>,
    pub objectives: Vec<MapObjective>,
    pub payload_route: Option<PayloadRoute>,
    pub capture_course: Option<CaptureCourse>,
    pub mode_name: String,
    pub team_sizes: Vec<u8>,
    pub free_for_all: bool,
}

impl GameMap {
    pub(crate) fn is_junkenstein(&self) -> bool {
        self.map_guid == JUNKENSTEIN_MAP && is_junkenstein_mode(self.mode_guid)
    }
    /// The training route has an explicit map, mode, and first spawn.
    pub fn practice_range() -> Self {
        Self {
            name: PRACTICE_RANGE_NAME.into(),
            map_guid: PRACTICE_RANGE_MAP,
            mode_guid: PRACTICE_RANGE_MODE,
            preset_guid: 0,
            placeables: vec![0x88, 0x22e, 0x234, 0x289],
            first_entity: None,
            spawns: vec![Spawn {
                team: None,
                position: [54.56507, 1.0, 42.14788],
                yaw: -170.3434,
            }],
            objectives: Vec::new(),
            payload_route: None,
            capture_course: None,
            mode_name: PRACTICE_RANGE_NAME.into(),
            team_sizes: vec![1, 0],
            free_for_all: false,
        }
    }

    /// Cycle through a team's points (or all points if that team has none).
    /// Players sharing a point alternate along its local right-hand axis.
    pub fn spawn_point(&self, team: u8, number: usize) -> Option<([f64; 3], f64)> {
        spawn_point(&self.spawns, team, number)
    }

    pub(crate) fn spawn_for_stage(
        &self,
        team: u8,
        number: usize,
        stage: usize,
    ) -> Option<([f64; 3], f64)> {
        self.payload_route
            .as_ref()
            .map(|route| &route.later_spawns)
            .or_else(|| {
                self.capture_course
                    .as_ref()
                    .map(|course| &course.later_spawns)
            })
            .and_then(|spawns| spawns.get(stage.checked_sub(1)?))
            .filter(|spawns| spawns.iter().any(|spawn| spawn.team == Some(team)))
            .and_then(|spawns| spawn_point(spawns, team, number))
            .or_else(|| self.spawn_point(team, number))
    }
}

fn spawn_point(spawns: &[Spawn], team: u8, number: usize) -> Option<([f64; 3], f64)> {
    let for_team = |spawn: &&Spawn| spawn.team.is_none() || spawn.team == Some(team);
    let count = spawns.iter().filter(for_team).count();
    let row_size = if count == 0 { spawns.len() } else { count };
    let index = number.checked_rem(row_size)?;
    let spawn = spawns
        .iter()
        .filter(|spawn| count == 0 || for_team(spawn))
        .nth(index)?;
    let row = number / row_size;
    let side = row.div_ceil(2) as f64 * SPAWN_SPACING;
    let side = if row != 0 && row % 2 == 0 {
        -side
    } else {
        side
    };
    let (sin, cos) = spawn.yaw.to_radians().sin_cos();
    let [x, y, z] = spawn.position;
    Some(([x - cos * side, y, z + sin * side], spawn.yaw))
}

pub(crate) fn is_junkenstein_mode(mode: u64) -> bool {
    matches!(mode, JUNKENSTEIN_REVENGE | JUNKENSTEIN_ENDLESS)
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MapListing {
    pub guid: u64,
    pub name: String,
}

#[derive(Debug, Default)]
pub struct MapCatalog {
    entries: IndexMap<u64, MapEntry>,
}

impl MapCatalog {
    /// Read game_maps_174.json from the selected runtime. A missing catalog
    /// still leaves the built-in training route available.
    pub fn load(path: impl AsRef<Path>) -> io::Result<Self> {
        let path = path.as_ref();
        match path.metadata() {
            Ok(metadata) if !metadata.is_file() => Ok(Self::default()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error),
            Ok(_) => Self::from_json(&fs::read_to_string(path)?),
        }
    }

    pub fn from_json(text: &str) -> io::Result<Self> {
        let raw: IndexMap<String, RawMap> = serde_json::from_str(text)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        let mut entries = IndexMap::with_capacity(raw.len());
        for (guid, raw) in raw {
            let modes = raw
                .modes
                .iter()
                .map(|mode| hex_guid(mode))
                .collect::<io::Result<_>>()?;
            let placeables_by_mode = raw
                .placeables_by_mode
                .unwrap_or_default()
                .into_iter()
                .map(|(mode, indexes)| Ok((hex_guid(&mode)?, indexes)))
                .collect::<io::Result<_>>()?;
            let spawns = raw
                .spawns
                .unwrap_or_default()
                .into_iter()
                .map(|entry| SpawnEntry {
                    spawn: entry.spawn,
                    stage: entry.stage,
                    placeable: entry.placeable,
                    free_for_all: entry.ffa.unwrap_or_default(),
                    objectives: entry.objectives.unwrap_or_default(),
                })
                .collect();
            entries.insert(
                hex_guid(&guid)?,
                MapEntry {
                    name: raw.name,
                    modes,
                    placeables: raw.placeables,
                    placeables_by_mode,
                    mirrored: raw.mirrored,
                    first_entity: raw.first_entity,
                    spawns,
                    objectives: raw.objectives.unwrap_or_default(),
                    escort_rail: raw.escort_rail,
                    escort_scene_placeables: raw.escort_scene_placeables,
                    escort_room_placeables: raw.escort_room_placeables,
                    escort_room_models: raw.escort_room_models,
                    assault_scene_placeables: raw.assault_scene_placeables,
                    assault_room_placeables: raw.assault_room_placeables,
                    assault_room_models: raw.assault_room_models,
                    hybrid_rail: raw.hybrid_rail,
                    hybrid_scene_placeables: raw.hybrid_scene_placeables,
                    hybrid_room_placeables: raw.hybrid_room_placeables,
                    hybrid_room_models: raw.hybrid_room_models,
                    control_scene_placeables: raw.control_scene_placeables,
                    control_room_placeables: raw.control_room_placeables,
                    control_room_models: raw.control_room_models,
                    control_healing_rooms: raw.control_healing_rooms,
                    control_objective_placeables: raw.control_objective_placeables,
                    health_packs: raw.health_packs,
                },
            );
        }
        Ok(Self { entries })
    }

    /// Catalog order is stable among entries with the same display name.
    pub fn map_catalog(&self) -> Vec<MapListing> {
        let mut maps = vec![MapListing {
            guid: PRACTICE_RANGE_MAP,
            name: PRACTICE_RANGE_NAME.into(),
        }];
        maps.extend(self.entries.iter().filter_map(|(&guid, entry)| {
            if guid == PRACTICE_RANGE_MAP {
                return None;
            }
            let name = match entry.name.as_deref() {
                Some(name) if !name.is_empty() => name.to_owned(),
                _ => format!("0x{guid:X}"),
            };
            Some(MapListing { guid, name })
        }));
        maps.sort_by(|left, right| left.name.cmp(&right.name));
        maps
    }

    pub fn map_name(&self, guid: u64) -> Option<&str> {
        if guid == PRACTICE_RANGE_MAP {
            return Some(PRACTICE_RANGE_NAME);
        }
        self.entries.get(&guid)?.name.as_deref()
    }

    pub fn game_map(
        &self,
        map_guid: u64,
        mode_guid: u64,
        mode_name: &str,
        free_for_all: bool,
        team_sizes: &[u8],
    ) -> Option<GameMap> {
        if map_guid == PRACTICE_RANGE_MAP {
            return Some(GameMap::practice_range());
        }
        let entry = self.entries.get(&map_guid)?;
        if SCRIPTED_LOADING_MODES.contains(&mode_guid) || !entry.modes.contains(&mode_guid) {
            return None;
        }
        let mut spawns = entry.first_round_spawns(free_for_all);
        if spawns.is_empty() {
            return None;
        }
        let mut placeables = entry
            .placeables_by_mode
            .get(&mode_guid)
            .unwrap_or(&entry.placeables)
            .clone();
        let mut objectives = entry.objectives.clone();
        let hybrid = mode_guid == MODE_HYBRID && !free_for_all;
        let control = mode_guid == MODE_CONTROL && !free_for_all;
        let capture_course = if control {
            objectives.retain(|goal| {
                goal.kind == "control point"
                    && goal.placeable.is_some_and(|id| {
                        (entry.mirrored.contains(&id) || placeables.contains(&id))
                            && (entry.control_objective_placeables.is_empty()
                                || entry.control_objective_placeables.contains(&id))
                    })
            });
            objectives.sort_by_key(|goal| goal.stage);
            let by_stage: Vec<_> = objectives
                .iter()
                .enumerate()
                .map(|(stage, goal)| {
                    let tag = goal.stage.unwrap_or(stage);
                    entry
                        .spawns
                        .iter()
                        .filter(|spawn| !spawn.free_for_all && spawn.stage == Some(tag))
                        .collect::<Vec<_>>()
                })
                .collect();
            if let Some(first) = by_stage.first().filter(|first| !first.is_empty()) {
                spawns = first.iter().map(|entry| entry.spawn).collect();
            }
            (!objectives.is_empty()).then(|| CaptureCourse {
                barriers: entry
                    .control_room_placeables
                    .iter()
                    .filter_map(RawBarrier::entry)
                    .collect(),
                later_spawns: by_stage
                    .iter()
                    .skip(1)
                    .map(|entries| entries.iter().map(|entry| entry.spawn).collect())
                    .collect(),
                room_models: room_models(
                    &entry.control_room_placeables,
                    &entry.control_room_models,
                ),
                speed_scale: 1.0,
                spawn_candidates: by_stage
                    .iter()
                    .map(|entries| {
                        std::array::from_fn(|team| {
                            entries
                                .iter()
                                .filter(|entry| entry.spawn.team == Some(team as u8))
                                .filter_map(|entry| entry.placeable)
                                .collect()
                        })
                    })
                    .collect(),
                control: Some(ControlCourse {
                    rules: Default::default(),
                    healing_rooms: entry.control_healing_rooms.clone(),
                }),
            })
        } else if (mode_guid == MODE_ASSAULT && !free_for_all)
            || (hybrid && !entry.hybrid_rail.is_empty())
        {
            objectives.retain(|goal| {
                (goal.kind == "point" || hybrid)
                    && goal
                        .placeable
                        .is_some_and(|id| entry.mirrored.contains(&id))
            });
            objectives.sort_by_key(|goal| goal.objective);
            let goals: Vec<_> = objectives
                .iter()
                .filter(|goal| goal.kind != "payload")
                .collect();
            let outlines: Vec<_> = objectives.iter().filter_map(|goal| goal.outline).collect();
            placeables.retain(|id| !outlines.contains(id));
            goals
                .iter()
                .any(|goal| goal.kind == "point")
                .then(|| CaptureCourse {
                    barriers: entry
                        .assault_room_placeables
                        .iter()
                        .filter(|placement| entry.mirrored.contains(&placement.placeable))
                        .filter_map(RawBarrier::entry)
                        .collect(),
                    later_spawns: entry.later_objective_spawns(goals.iter().copied()),
                    room_models: room_models(
                        &entry.assault_room_placeables,
                        &entry.assault_room_models,
                    ),
                    speed_scale: 1.0,
                    spawn_candidates: goals
                        .iter()
                        .map(|goal| {
                            std::array::from_fn(|team| {
                                entry
                                    .objective_spawns(goal, team as u8)
                                    .filter_map(|spawn| spawn.placeable)
                                    .collect()
                            })
                        })
                        .collect(),
                    control: None,
                })
        } else {
            None
        };
        let objective_scene = match (mode_guid, free_for_all) {
            (MODE_CONTROL, false) => Some((
                &entry.control_scene_placeables,
                &entry.control_room_placeables[..],
                &["control point"][..],
            )),
            (MODE_ESCORT, false) => Some((
                &entry.escort_scene_placeables,
                &[][..],
                &["payload", "checkpoint"][..],
            )),
            (MODE_ASSAULT, false) => Some((
                &entry.assault_scene_placeables,
                &entry.assault_room_placeables[..],
                &["point"][..],
            )),
            (MODE_HYBRID, false) if capture_course.is_some() => Some((
                &entry.hybrid_scene_placeables,
                &entry.hybrid_room_placeables[..],
                &["point", "payload", "checkpoint"][..],
            )),
            _ => None,
        };
        if let Some((scene, rooms, kinds)) = objective_scene {
            use std::collections::BTreeSet;
            let outlines: BTreeSet<_> = objectives
                .iter()
                .flat_map(|goal| {
                    goal.outline
                        .into_iter()
                        .chain(goal.outlines.iter().copied())
                })
                .collect();
            if control {
                let dynamic: BTreeSet<_> = rooms
                    .iter()
                    .filter(|room| room.kind != "door")
                    .map(|room| room.placeable)
                    .chain(outlines.iter().copied())
                    .collect();
                placeables.retain(|id| !dynamic.contains(id));
            }
            placeables = ModePlaceablesCatalog {
                base_placeables: placeables,
                placeables_by_mode: BTreeMap::new(),
                mirrored: entry.mirrored.iter().copied().collect::<BTreeSet<_>>(),
                objective_placeables: objectives
                    .iter()
                    .filter(|goal| kinds.contains(&goal.kind.as_str()))
                    .filter_map(|goal| goal.placeable)
                    .collect(),
                health_pack_placeables: entry
                    .health_packs
                    .iter()
                    .map(|pack| pack.placeable)
                    .collect(),
                scene_placeables: scene
                    .iter()
                    .copied()
                    .chain(
                        rooms
                            .iter()
                            .filter_map(|room| (room.kind == "door").then_some(room.placeable)),
                    )
                    .filter(|id| {
                        !outlines.contains(id)
                            && (!control
                                || !rooms
                                    .iter()
                                    .any(|room| room.placeable == *id && room.kind != "door"))
                    })
                    .collect(),
            }
            .resolve(mode_guid, true);
        }
        let payload_route = ((mode_guid == MODE_ESCORT && !free_for_all)
            || (hybrid && capture_course.is_some()))
        .then(|| entry.payload_route(&placeables, hybrid))
        .flatten();
        let capture_course = capture_course.filter(|_| !hybrid || payload_route.is_some());
        if map_guid == JUNKENSTEIN_MAP
            && is_junkenstein_mode(mode_guid)
            && entry.mirrored.contains(&JUNKENSTEIN_GATE)
            && !placeables.contains(&JUNKENSTEIN_GATE)
        {
            placeables.push(JUNKENSTEIN_GATE);
            placeables.sort_unstable();
        }
        Some(GameMap {
            name: entry.name.clone()?,
            map_guid,
            mode_guid,
            preset_guid: 0,
            placeables,
            first_entity: entry.first_entity,
            spawns,
            objectives,
            payload_route,
            capture_course,
            mode_name: mode_name.into(),
            team_sizes: team_sizes.into(),
            free_for_all,
        })
    }
}

#[derive(Debug)]
struct MapEntry {
    name: Option<String>,
    modes: Vec<u64>,
    placeables: Vec<u32>,
    placeables_by_mode: HashMap<u64, Vec<u32>>,
    mirrored: Vec<u32>,
    first_entity: Option<u32>,
    spawns: Vec<SpawnEntry>,
    objectives: Vec<MapObjective>,
    escort_rail: Vec<SplineNode>,
    escort_scene_placeables: Vec<u32>,
    escort_room_placeables: Vec<RawBarrier>,
    escort_room_models: HashMap<u32, Vec<serde_json::Value>>,
    assault_scene_placeables: Vec<u32>,
    assault_room_placeables: Vec<RawBarrier>,
    assault_room_models: HashMap<u32, Vec<serde_json::Value>>,
    hybrid_rail: Vec<SplineNode>,
    hybrid_scene_placeables: Vec<u32>,
    hybrid_room_placeables: Vec<RawBarrier>,
    hybrid_room_models: HashMap<u32, Vec<serde_json::Value>>,
    control_scene_placeables: Vec<u32>,
    control_room_placeables: Vec<RawBarrier>,
    control_room_models: HashMap<u32, Vec<serde_json::Value>>,
    control_healing_rooms: Vec<ControlHealingRoom>,
    control_objective_placeables: Vec<u32>,
    health_packs: Vec<RawPlacement>,
}

impl MapEntry {
    fn objective_spawns(&self, goal: &MapObjective, team: u8) -> impl Iterator<Item = &SpawnEntry> {
        let stage = goal.objective;
        let role = if team == 0 { "defense" } else { "attack" };
        self.spawns.iter().filter(move |entry| {
            entry.spawn.team == Some(team)
                && !entry.free_for_all
                && entry
                    .objectives
                    .iter()
                    .any(|(index, name)| Some(*index) == stage && name == role)
        })
    }

    fn later_objective_spawns<'a>(
        &self,
        goals: impl IntoIterator<Item = &'a MapObjective>,
    ) -> Vec<Vec<Spawn>> {
        goals
            .into_iter()
            .skip(1)
            .map(|goal| {
                [0, 1]
                    .into_iter()
                    .flat_map(|team| self.objective_spawns(goal, team))
                    .map(|entry| entry.spawn)
                    .collect()
            })
            .collect()
    }

    fn first_round_spawns(&self, free_for_all: bool) -> Vec<Spawn> {
        if free_for_all {
            return self
                .spawns
                .iter()
                .filter(|entry| entry.free_for_all || entry.spawn.team.is_none())
                .map(|entry| entry.spawn)
                .collect();
        }
        let mut chosen = Vec::new();
        for team in [0, 1] {
            let mine = self
                .spawns
                .iter()
                .filter(|entry| !entry.free_for_all && entry.spawn.team == Some(team));
            let has_first = mine
                .clone()
                .any(|entry| entry.objectives.iter().any(|(stage, _)| *stage == 0));
            chosen.extend(
                mine.filter(|entry| {
                    !has_first || entry.objectives.iter().any(|(stage, _)| *stage == 0)
                })
                .map(|entry| entry.spawn),
            );
        }
        chosen
    }

    fn payload_route(&self, placeables: &[u32], capture_first: bool) -> Option<PayloadRoute> {
        let mut carts = self.objectives.iter().filter(|goal| goal.kind == "payload");
        let cart = carts.next()?;
        if carts.next().is_some() || !placeables.contains(&cart.placeable?) {
            return None;
        }
        let mut checkpoints: Vec<_> = self
            .objectives
            .iter()
            .filter(|goal| goal.kind == "checkpoint")
            .collect();
        checkpoints.sort_by_key(|goal| goal.objective);
        let goals = checkpoints
            .iter()
            .map(|goal| goal.placeable)
            .collect::<Option<Vec<_>>>()?;
        let (nodes, rooms, models) = if capture_first {
            (
                &self.hybrid_rail,
                &self.hybrid_room_placeables,
                &self.hybrid_room_models,
            )
        } else {
            (
                &self.escort_rail,
                &self.escort_room_placeables,
                &self.escort_room_models,
            )
        };
        if goals.is_empty()
            || nodes.first()?.checkpoint.is_some()
            || nodes.last()?.checkpoint != goals.last().copied()
            || nodes
                .iter()
                .filter_map(|node| node.checkpoint)
                .ne(goals.iter().copied())
            || goals.iter().any(|goal| !placeables.contains(goal))
            || !nodes.iter().enumerate().all(|(index, node)| {
                node.position.iter().all(|v| v.is_finite())
                    && node.wait_seconds.is_finite()
                    && node.wait_seconds >= 0.0
                    && node
                        .auto_drive_to
                        .is_none_or(|next| index < next && next < nodes.len())
            })
            || !nodes.windows(2).all(|pair| {
                let [from, to] = pair else { unreachable!() };
                from.position != to.position
                    && from.curve.is_some_and(|curve| {
                        curve.iter().flatten().all(|v| v.is_finite())
                            && (0..3).all(|axis| {
                                let end = from.position[axis]
                                    + curve.iter().map(|term| term[axis]).sum::<f64>();
                                (end - to.position[axis]).abs() < 0.01
                            })
                    })
            })
        {
            return None;
        }
        let default_seconds = [240.0, 150.0, 90.0];
        let stage_seconds: Vec<_> = checkpoints
            .iter()
            .enumerate()
            .map(|(index, goal)| {
                (if capture_first {
                    goal.casual_seconds
                } else {
                    goal.escort_seconds
                })
                .unwrap_or(default_seconds[index.min(default_seconds.len() - 1)])
            })
            .collect();
        if stage_seconds
            .iter()
            .any(|seconds| !seconds.is_finite() || *seconds <= 0.0)
        {
            return None;
        }
        let later_spawns = if capture_first {
            self.later_objective_spawns(
                self.objectives
                    .iter()
                    .filter(|goal| goal.kind == "point")
                    .chain(checkpoints),
            )
        } else {
            self.later_objective_spawns(checkpoints)
        };
        let nodes = nodes
            .iter()
            .cloned()
            .enumerate()
            .map(|(index, mut node)| {
                // The map stores the cubic, quadratic, and linear coefficients.
                // Preserve the exact authored anchor at the end of each segment.
                if let (Some([_, quadratic, linear]), Some(next)) =
                    (node.curve, nodes.get(index + 1))
                {
                    node.curve = Some([
                        linear.map(|value| value / 3.0),
                        std::array::from_fn(|axis| (2.0 * linear[axis] + quadratic[axis]) / 3.0),
                        std::array::from_fn(|axis| next.position[axis] - node.position[axis]),
                    ]);
                }
                node
            })
            .collect();
        Some(PayloadRoute {
            nodes,
            barriers: rooms.iter().filter_map(RawBarrier::entry).collect(),
            stage_seconds,
            later_spawns,
            speed_scale: 1.0,
            cart_model: ObjectiveModel {
                position: cart.position.map(|axis| axis as f32),
                rotation: cart
                    .rotation
                    .unwrap_or([0.0, 0.0, 0.0, 1.0])
                    .map(|axis| axis as f32),
                scale: cart.scale,
                shapes: cart.collision.clone(),
            },
            room_models: room_models(rooms, models),
        })
    }
}

fn room_models(
    rooms: &[RawBarrier],
    models: &HashMap<u32, Vec<serde_json::Value>>,
) -> BTreeMap<u32, ObjectiveModel> {
    rooms
        .iter()
        .filter_map(|placement| {
            Some((
                placement.placeable,
                ObjectiveModel {
                    position: placement.position?.map(|axis| axis as f32),
                    rotation: placement.rotation?,
                    scale: placement.scale,
                    shapes: models.get(&placement.model?)?.clone(),
                },
            ))
        })
        .collect()
}

#[derive(Debug)]
struct SpawnEntry {
    spawn: Spawn,
    stage: Option<usize>,
    placeable: Option<u32>,
    free_for_all: bool,
    objectives: Vec<(u32, String)>,
}

#[derive(Deserialize)]
struct RawMap {
    name: Option<String>,
    #[serde(default)]
    modes: Vec<String>,
    #[serde(default)]
    placeables: Vec<u32>,
    #[serde(default)]
    mirrored: Vec<u32>,
    first_entity: Option<u32>,
    placeables_by_mode: Option<IndexMap<String, Vec<u32>>>,
    spawns: Option<Vec<RawSpawn>>,
    objectives: Option<Vec<MapObjective>>,
    #[serde(default)]
    escort_rail: Vec<SplineNode>,
    #[serde(default)]
    escort_scene_placeables: Vec<u32>,
    #[serde(default)]
    escort_room_placeables: Vec<RawBarrier>,
    #[serde(default)]
    escort_room_models: HashMap<u32, Vec<serde_json::Value>>,
    #[serde(default)]
    assault_scene_placeables: Vec<u32>,
    #[serde(default)]
    assault_room_placeables: Vec<RawBarrier>,
    #[serde(default)]
    assault_room_models: HashMap<u32, Vec<serde_json::Value>>,
    #[serde(default)]
    hybrid_rail: Vec<SplineNode>,
    #[serde(default)]
    hybrid_scene_placeables: Vec<u32>,
    #[serde(default)]
    hybrid_room_placeables: Vec<RawBarrier>,
    #[serde(default)]
    hybrid_room_models: HashMap<u32, Vec<serde_json::Value>>,
    #[serde(default)]
    control_scene_placeables: Vec<u32>,
    #[serde(default)]
    control_room_placeables: Vec<RawBarrier>,
    #[serde(default)]
    control_room_models: HashMap<u32, Vec<serde_json::Value>>,
    #[serde(default)]
    control_healing_rooms: Vec<ControlHealingRoom>,
    #[serde(default)]
    control_objective_placeables: Vec<u32>,
    #[serde(default)]
    health_packs: Vec<RawPlacement>,
}

#[derive(Debug, Deserialize)]
struct RawPlacement {
    placeable: u32,
}

#[derive(Debug, Deserialize)]
struct RawBarrier {
    placeable: u32,
    kind: String,
    rooms: Vec<(usize, u8)>,
    position: Option<[f64; 3]>,
    rotation: Option<[f32; 4]>,
    #[serde(default = "unit_scale")]
    scale: [f32; 3],
    model: Option<u32>,
    #[serde(default)]
    viewers: Vec<u8>,
    open_seconds: Option<f64>,
    close_seconds: Option<f64>,
    open_delay: Option<f64>,
    close_delay: Option<f64>,
    gate_tag: Option<u32>,
    debris_rail_node: Option<usize>,
    #[serde(default)]
    payload_unlock: bool,
}

impl RawBarrier {
    fn entry(&self) -> Option<BarrierEntry> {
        let kind = match self.kind.as_str() {
            "door" => BarrierKind::Door,
            "checkpoint_gate" => BarrierKind::CheckpointGate,
            "gate_debris" => BarrierKind::GateDebris,
            "barrier" => BarrierKind::TeamBarrier,
            "overlay" => BarrierKind::Overlay,
            "restricted_sign" => BarrierKind::RestrictedSign,
            _ => return None,
        };
        let mut entry = BarrierEntry::new(self.placeable, kind, self.rooms.clone());
        entry.position = self.position.unwrap_or_default();
        entry.rotation = self.rotation.map(|rotation| rotation.map(f64::from));
        entry.viewers.clone_from(&self.viewers);
        entry.open_seconds = self.open_seconds.unwrap_or(entry.open_seconds);
        entry.close_seconds = self.close_seconds.unwrap_or(entry.close_seconds);
        entry.open_delay = self.open_delay.unwrap_or_default();
        entry.close_delay = self.close_delay.unwrap_or_default();
        entry.gate_tag = self.gate_tag;
        entry.debris_rail_node = self.debris_rail_node;
        entry.gate_debris = kind == BarrierKind::GateDebris;
        entry.payload_unlock = self.payload_unlock;
        Some(entry)
    }
}

#[derive(Deserialize)]
struct RawSpawn {
    #[serde(flatten)]
    spawn: Spawn,
    stage: Option<usize>,
    placeable: Option<u32>,
    ffa: Option<bool>,
    objectives: Option<Vec<(u32, String)>>,
}

fn hex_guid(text: &str) -> io::Result<u64> {
    let trimmed = text.trim();
    let digits = trimmed.strip_prefix('+').unwrap_or(trimmed);
    let parsed = match digits.as_bytes() {
        [b'0', b'x' | b'X', ..] => parse_guid(digits),
        [b'_', ..] => None,
        _ => parse_guid(&format!("0x{digits}")),
    };
    parsed.ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid map or mode GUID {text:?}"),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const ARENA: &str = r#"{
        "0x80": {
            "name": "Fixture Arena",
            "modes": ["20", "0x21", "0X22", "0x0230000000000025"],
            "placeables": [8, 4, 8],
            "placeables_by_mode": {"0x20": [], "21": [3, 1, 3]},
            "spawns": [
                {"team": 1, "position": [8, 1, 0], "objectives": [[1, "attack"]]},
                {"team": 0, "position": [1, 1, 0], "objectives": [[2, "defense"]]},
                {"team": 0, "position": [9, 1, 0], "objectives": [[0, "defense"]], "ffa": true},
                {"team": 0, "position": [2, 1, 0], "objectives": [[0, "defense"]], "yaw": 90},
                {"team": 1, "position": [3, 1, 0], "objectives": null},
                {"team": 0, "position": [4, 1, 0], "objectives": [[1, "defense"], [0, "defense"]]},
                {"team": null, "position": [5, 1, 0]},
                {"position": [6, 1, 0], "ffa": false},
                {"team": 1, "position": [7, 1, 0], "ffa": true}
            ]
        },
        "0x81": {"name": "No points", "modes": ["20"], "spawns": null}
    }"#;

    #[test]
    fn loads_external_catalog_and_keeps_the_practice_fallback() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("game_maps_174.json");
        let missing = MapCatalog::load(&path).unwrap();
        let practice = GameMap::practice_range();
        assert_eq!(missing.map_name(PRACTICE_RANGE_MAP), Some("Practice Range"));
        assert_eq!(
            missing.game_map(
                PRACTICE_RANGE_MAP,
                SCRIPTED_LOADING_MODES[0],
                "Ignored",
                true,
                &[9]
            ),
            Some(practice.clone())
        );
        assert_eq!(practice.placeables, [0x88, 0x22e, 0x234, 0x289]);
        assert_eq!(practice.team_sizes, [1, 0]);
        assert_eq!(
            practice.spawn_point(0, 0),
            Some(([54.56507, 1.0, 42.14788], -170.3434))
        );

        fs::write(&path, ARENA).unwrap();
        assert_eq!(
            MapCatalog::load(&path).unwrap().map_name(0x80),
            Some("Fixture Arena")
        );
        fs::write(&path, "{").unwrap();
        assert_eq!(
            MapCatalog::load(&path).unwrap_err().kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn dashboard_listing_sorts_stably_and_deduplicates_practice() {
        let catalog = MapCatalog::from_json(
            r#"{
            "0x9": {"name": "Same"},
            "0008": {"name": "Same"},
            "0x7": {},
            "0x0800000000000688": {"name": "Override"},
            "6": {"name": ""}
        }"#,
        )
        .unwrap();
        let listing = catalog.map_catalog();
        assert_eq!(
            listing
                .iter()
                .map(|entry| (entry.guid, entry.name.as_str()))
                .collect::<Vec<_>>(),
            [
                (6, "0x6"),
                (7, "0x7"),
                (PRACTICE_RANGE_MAP, "Practice Range"),
                (9, "Same"),
                (8, "Same")
            ]
        );
        assert_eq!(catalog.map_name(6), Some(""));
        assert_eq!(catalog.map_name(7), None);
        assert_eq!(catalog.map_name(50), None);
    }

    #[test]
    fn uses_mode_placeables_and_first_objective_in_team_order() {
        let catalog = MapCatalog::from_json(ARENA).unwrap();
        let arena = catalog
            .game_map(0x80, 0x20, "Team Fixture", false, &[6, 5])
            .unwrap();
        assert_eq!(arena.mode_name, "Team Fixture");
        assert_eq!(arena.team_sizes, [6, 5]);
        assert!(arena.placeables.is_empty());
        assert_eq!(
            arena
                .spawns
                .iter()
                .map(|spawn| (spawn.team, spawn.position[0]))
                .collect::<Vec<_>>(),
            [
                (Some(0), 2.0),
                (Some(0), 4.0),
                (Some(1), 8.0),
                (Some(1), 3.0)
            ]
        );
        let mode_placeables = catalog.game_map(0x80, 0x21, "", false, &[]).unwrap();
        assert_eq!(mode_placeables.placeables, [3, 1, 3]);
        let fallback = catalog.game_map(0x80, 0x22, "", false, &[]).unwrap();
        assert_eq!(fallback.placeables, [8, 4, 8]);
        assert!(catalog.game_map(0x80, 0x23, "", false, &[]).is_none());
        assert!(
            catalog
                .game_map(0x80, 0x0230_0000_0000_0025, "", false, &[])
                .is_none()
        );
        assert!(catalog.game_map(0x81, 0x20, "", false, &[]).is_none());
    }

    #[test]
    fn free_for_all_keeps_tagged_or_teamless_points_in_source_order() {
        let catalog = MapCatalog::from_json(ARENA).unwrap();
        let arena = catalog
            .game_map(0x80, 0x20, "FFA Fixture", true, &[8])
            .unwrap();
        assert!(arena.free_for_all);
        assert_eq!(arena.team_sizes, [8]);
        assert_eq!(
            arena
                .spawns
                .iter()
                .map(|spawn| (spawn.team, spawn.position[0]))
                .collect::<Vec<_>>(),
            [(Some(0), 9.0), (None, 5.0), (None, 6.0), (Some(1), 7.0)]
        );
        assert_eq!(arena.spawn_point(3, 0), Some(([5.0, 1.0, 0.0], 0.0)));
        assert_eq!(arena.spawn_point(0, 0), Some(([9.0, 1.0, 0.0], 0.0)));
    }

    #[test]
    fn repeated_spawns_offset_along_local_right_and_left() {
        let catalog = MapCatalog::from_json(ARENA).unwrap();
        let arena = catalog.game_map(0x80, 0x20, "", false, &[]).unwrap();
        for (number, expected_z) in [(0, 0.0), (2, 1.5), (4, -1.5), (6, 3.0), (8, -3.0)] {
            let ([x, y, z], yaw) = arena.spawn_point(0, number).unwrap();
            assert!((x - 2.0).abs() < 1.0e-12);
            assert_eq!(y, 1.0);
            assert_eq!(z, expected_z);
            assert_eq!(yaw, 90.0);
        }
        assert_eq!(arena.spawn_point(0, 3), Some(([2.5, 1.0, 0.0], 0.0)));
        assert_eq!(arena.spawn_point(2, 3), Some(([3.0, 1.0, 0.0], 0.0)));
        assert_eq!(arena.spawn_point(2, 7), Some(([1.5, 1.0, 0.0], 0.0)));
        let empty = GameMap {
            spawns: Vec::new(),
            ..arena
        };
        assert_eq!(empty.spawn_point(0, 0), None);
    }

    #[test]
    fn malformed_guids_are_invalid_data() {
        for input in [
            r#"{"not-hex":{}}"#,
            r#"{"0x1":{"modes":["not-hex"]}}"#,
            r#"{"0x1":{"placeables_by_mode":{"not-hex":[]}}}"#,
        ] {
            assert_eq!(
                MapCatalog::from_json(input).unwrap_err().kind(),
                io::ErrorKind::InvalidData
            );
        }
    }

    #[test]
    #[ignore = "requires the separately prepared 104319 map catalog"]
    fn map_catalog_and_mode_selections_match_the_python_corpus() {
        use sha2::{Digest, Sha256};
        use std::path::PathBuf;

        fn count(digest: &mut Sha256, value: usize) {
            digest.update(u32::try_from(value).unwrap().to_le_bytes());
        }

        fn text(digest: &mut Sha256, value: &str) {
            count(digest, value.len());
            digest.update(value.as_bytes());
        }

        let root = std::env::var_os("ORPHEUS_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).to_owned());
        let catalog = MapCatalog::load(root.join("data/game_maps_174.json")).unwrap();
        let listing = catalog.map_catalog();
        let mut digest = Sha256::new();
        count(&mut digest, listing.len());
        let mut cases = 0;
        let mut accepted = 0;
        for entry in &listing {
            digest.update(entry.guid.to_le_bytes());
            text(&mut digest, &entry.name);
            let name = catalog.map_name(entry.guid);
            digest.update([u8::from(name.is_some())]);
            if let Some(name) = name {
                text(&mut digest, name);
            }
            let modes = if entry.guid == PRACTICE_RANGE_MAP {
                &[PRACTICE_RANGE_MODE]
            } else {
                catalog.entries[&entry.guid].modes.as_slice()
            };
            count(&mut digest, modes.len());
            for &mode in modes {
                digest.update(mode.to_le_bytes());
                for ffa in [false, true] {
                    cases += 1;
                    let sizes: &[u8] = if ffa { &[8] } else { &[6, 6] };
                    let game = catalog.game_map(entry.guid, mode, "Fixture", ffa, sizes);
                    digest.update([u8::from(game.is_some())]);
                    let Some(game) = game else { continue };
                    accepted += 1;
                    digest.update(game.map_guid.to_le_bytes());
                    digest.update(game.mode_guid.to_le_bytes());
                    text(&mut digest, &game.name);
                    text(&mut digest, &game.mode_name);
                    digest.update([u8::from(game.free_for_all)]);
                    count(&mut digest, game.team_sizes.len());
                    digest.update(&game.team_sizes);
                    count(&mut digest, game.placeables.len());
                    for placeable in game.placeables {
                        digest.update(placeable.to_le_bytes());
                    }
                    count(&mut digest, game.spawns.len());
                    for spawn in game.spawns {
                        digest.update([spawn.team.unwrap_or(u8::MAX)]);
                        for value in spawn.position.into_iter().chain([spawn.yaw]) {
                            digest.update((value as f32).to_le_bytes());
                        }
                    }
                }
            }
        }
        assert_eq!((listing.len(), cases), (95, 698));
        let expected = match accepted {
            551 => "414daebdec686669243271e7cadf40c5ca7f0cb2a0e41b7c3d434c7c82b17e4a",
            // Python cc620be with native-unsupported scripted mission entrances
            // excluded. Four further Python selections still need a Rust port.
            553 => "cc5274f79b5eb1ed76f9255ff72cf02ba59e6647bcebf270cb148136ffad4ecd",
            count => panic!("no Python reference vector for the {count}-selection map corpus"),
        };
        // Python game.content catalog and selection outputs, with geometry as LE f32.
        assert_eq!(format!("{:x}", digest.finalize()), expected);
    }
}
