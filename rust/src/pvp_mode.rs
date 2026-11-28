//! Data-driven PvP objective, custom mode rule, placeable filtering, and match audio engine
//! composed from the five generic `room_primitives`.

use std::{
    collections::{BTreeMap, BTreeSet},
    time::Duration,
};

use crate::room_primitives::{
    ActorSample, BarrierKind, DoorState, DynamicBarrierRegistry, MAP_ENTITY_BIT, ObbZone,
    SPECTATOR_TEAM, SplineMover, SplineSpeedCurve, SplineTrack, TeamOccupancy, VoiceCue,
    rotate_vec,
};

pub(crate) mod collision;
pub(crate) mod presentation;

pub const FIRST_DEFENDING_TEAM: u8 = 0;
pub const FIRST_ATTACKING_TEAM: u8 = 1;

pub const MODE_ESCORT: u64 = 0x0230_0000_0000_0015;
pub const MODE_ASSAULT: u64 = 0x0230_0000_0000_0014;
pub const MODE_HYBRID: u64 = 0x0230_0000_0000_0016;
pub const MODE_CONTROL: u64 = 0x0230_0000_0000_0017;

pub const SETTING_START_MODE: u32 = 9672;
pub const START_IMMEDIATELY: u32 = 9673;
pub const START_WHEN_FULL: u32 = 9674;
pub const START_MANUALLY: u32 = 9675;
const ASSEMBLY_TIME: Duration = Duration::from_secs(30);

pub const SETTING_CAPTURE_SPEED: u32 = 9518;
pub const SETTING_CONTROL_SCORE_SPEED: u32 = 9519;
pub const SETTING_PAYLOAD_SPEED: u32 = 9520;
pub const SETTING_CONTROL_SCORE_TO_WIN: u32 = 8853;
pub const SETTING_CONTROL_VALID_POINTS: u32 = 9545;

pub const SETUP_SECONDS: f64 = 45.0;
pub const CONTROL_SETUP_SECONDS: f64 = 30.0;
pub const CONTROL_UNLOCK_SECONDS: f64 = 30.0;
pub const CONTROL_ROUND_SUMMARY_DELAY: f64 = 7.0;
pub const CONTROL_INTERMISSION_SECONDS: f64 = 8.0;
pub const CONTROL_ROUND_END_MOVEMENT_SCALE: f64 = 0.375;
pub const OVERTIME_SECONDS: f64 = 5.0;
const OVERTIME_TOUCH_SECONDS: f64 = 3.0;
const OVERTIME_DECAY_DELAY: f64 = 15.0;
const OVERTIME_DECAY_SECONDS: f64 = 15.0;
const OVERTIME_MIN_FRACTION: f64 = 0.3;
const CAPTURE_DECAY_DELAY: f64 = 5.0;
const CAPTURE_RATES: [f64; 3] = [1.0, 1.5, 2.0];
pub const FINISH_WARNING_DISTANCE: f64 = 15.0;

/// The authored objective overtime window and the fuse it arms.
#[derive(Clone, Debug, Default)]
struct OvertimeFuse {
    last_touch: Option<f64>,
    touching: bool,
    started: Option<f64>,
    end: f64,
}

impl OvertimeFuse {
    fn advance(&mut self, now: f64, touching: bool, window: Option<(f64, f64)>) {
        let continuous = self.touching && touching;
        if self.started.is_none() {
            if let Some((opens, closes)) = window {
                if continuous
                    || self
                        .last_touch
                        .is_some_and(|touch| opens - touch < OVERTIME_TOUCH_SECONDS)
                {
                    self.started = Some(opens);
                    self.end = opens + OVERTIME_SECONDS;
                } else if touching && now <= closes {
                    self.started = Some(opens);
                    self.end = now + self.capacity(now);
                }
            }
        }
        if self.started.is_some() && touching && (continuous || now < self.end) {
            self.end = now + self.capacity(now);
        }
        self.touching = touching;
        if touching {
            self.last_touch = Some(now);
        }
    }

    fn capacity(&self, now: f64) -> f64 {
        let decay = self.started.map_or(0.0, |start| {
            ((now - start - OVERTIME_DECAY_DELAY) / OVERTIME_DECAY_SECONDS).clamp(0.0, 1.0)
        });
        let minimum = OVERTIME_SECONDS * OVERTIME_MIN_FRACTION;
        OVERTIME_SECONDS + (minimum - OVERTIME_SECONDS) * decay
    }

    fn remaining(&self, now: f64) -> f64 {
        (self.end - now).clamp(0.0, self.capacity(now))
    }
}

// ---------------------------------------------------------------------------
// 1. Custom Mode Rules
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SettingRow {
    pub id: u32,
    pub value_type: u32,
    pub raw: u32,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ControlRuleSettings {
    pub capture_scale_bits: Option<u32>,
    pub score_scale_bits: Option<u32>,
    pub fixed_stage: Option<usize>,
    pub points_to_win: Option<u32>,
}

impl ControlRuleSettings {
    pub fn capture_scale(&self) -> f64 {
        self.capture_scale_bits
            .map_or(1.0, |b| f64::from(f32::from_bits(b)))
    }
    pub fn score_scale(&self) -> f64 {
        self.score_scale_bits
            .map_or(1.0, |b| f64::from(f32::from_bits(b)))
    }
}

#[derive(Clone, Debug, Default)]
pub struct CustomModeRules {
    pub map_overrides: Vec<SettingRow>,
    pub mode_overrides: Vec<SettingRow>,
}

impl CustomModeRules {
    fn rows(&self) -> impl Iterator<Item = &SettingRow> {
        self.map_overrides.iter().chain(self.mode_overrides.iter())
    }

    pub fn start_mode(&self) -> u32 {
        for row in self.rows() {
            if row.id == SETTING_START_MODE
                && row.value_type == 3
                && matches!(
                    row.raw,
                    START_IMMEDIATELY | START_WHEN_FULL | START_MANUALLY
                )
            {
                return row.raw;
            }
        }
        START_WHEN_FULL
    }

    pub fn objective_rate(&self, key: u32) -> f64 {
        for row in self.rows() {
            if row.id == key && matches!(row.value_type, 1 | 2) {
                let rate = f32::from_bits(row.raw);
                if (0.1..=5.0).contains(&rate) {
                    return f64::from(rate);
                }
            }
        }
        1.0
    }

    pub fn control_settings(&self) -> ControlRuleSettings {
        let mut out = ControlRuleSettings::default();
        let mut picked_valid_points = false;
        for row in self.rows() {
            match row.id {
                SETTING_CAPTURE_SPEED if matches!(row.value_type, 1 | 2) => {
                    let rate = f32::from_bits(row.raw);
                    if (0.1..=5.0).contains(&rate) && out.capture_scale_bits.is_none() {
                        out.capture_scale_bits = Some(row.raw);
                    }
                }
                SETTING_CONTROL_SCORE_SPEED if matches!(row.value_type, 1 | 2) => {
                    let rate = f32::from_bits(row.raw);
                    if (0.1..=5.0).contains(&rate) && out.score_scale_bits.is_none() {
                        out.score_scale_bits = Some(row.raw);
                    }
                }
                SETTING_CONTROL_VALID_POINTS
                    if row.value_type == 3
                        && !picked_valid_points
                        && (0x2576..=0x2579).contains(&row.raw) =>
                {
                    picked_valid_points = true;
                    out.fixed_stage = match row.raw {
                        0x2577 => Some(0),
                        0x2578 => Some(1),
                        0x2579 => Some(2),
                        _ => None,
                    };
                }
                SETTING_CONTROL_SCORE_TO_WIN
                    if row.value_type == 1
                        && (1..=3).contains(&row.raw)
                        && out.points_to_win.is_none() =>
                {
                    out.points_to_win = Some(row.raw);
                }
                _ => {}
            }
        }
        out
    }

    pub fn can_manual_start(custom_start: Option<u32>, already_assembled: bool) -> bool {
        !already_assembled && matches!(custom_start, Some(START_WHEN_FULL | START_MANUALLY))
    }

    pub const fn spectators_fill_playing_slots() -> bool {
        false
    }

    pub const fn practice_start_automatically() -> bool {
        true
    }
}

pub(crate) struct CustomMatchStart {
    choice: u32,
    start_requested: bool,
    assembly_ends: Option<Duration>,
}

impl CustomMatchStart {
    pub(crate) fn new(choice: u32) -> Self {
        Self {
            choice,
            start_requested: false,
            assembly_ends: None,
        }
    }

    pub(crate) fn request_start(&mut self) -> bool {
        if !CustomModeRules::can_manual_start(
            Some(self.choice),
            self.start_requested || self.assembly_ends.is_some(),
        ) {
            return false;
        }
        // The mode's 0789 handler latches the request. Its waiting transition
        // consumes it on the simulation tick alongside the capacity predicate.
        self.start_requested = true;
        true
    }

    pub(crate) fn advance(&mut self, now: Duration, loaded: [usize; 2], capacities: &[u8]) {
        let ready = loaded.iter().any(|&count| count != 0);
        let full = capacities
            .iter()
            .zip(loaded)
            .all(|(&capacity, count)| count >= usize::from(capacity));
        let automatic =
            ready && (self.choice == START_IMMEDIATELY || (self.choice == START_WHEN_FULL && full));
        if self.assembly_ends.is_none() && (self.start_requested || automatic) {
            self.assembly_ends = Some(now.saturating_add(ASSEMBLY_TIME));
        }
    }

    pub(crate) fn assembly_ends(&self) -> Option<Duration> {
        self.assembly_ends
    }

    pub(crate) fn presentation(&self, now: Duration) -> (MatchPhase, u32) {
        match self.assembly_ends {
            None => (MatchPhase::Waiting, 0),
            Some(end) if now < end => (
                MatchPhase::Assemble,
                end.saturating_sub(now).as_nanos().div_ceil(1_000_000_000) as u32,
            ),
            Some(_) => (MatchPhase::Active, 0),
        }
    }
}

// ---------------------------------------------------------------------------
// 2. Objective Placeables & Panel Fitting
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Default)]
pub struct ModePlaceablesCatalog {
    pub base_placeables: Vec<u32>,
    pub placeables_by_mode: BTreeMap<u64, Vec<u32>>,
    pub mirrored: BTreeSet<u32>,
    pub objective_placeables: Vec<u32>,
    pub health_pack_placeables: Vec<u32>,
    pub scene_placeables: Vec<u32>,
}

impl ModePlaceablesCatalog {
    pub fn resolve(&self, mode: u64, include_mode_extras: bool) -> Vec<u32> {
        let mut out: BTreeSet<u32> = self
            .placeables_by_mode
            .get(&mode)
            .unwrap_or(&self.base_placeables)
            .iter()
            .copied()
            .collect();
        if include_mode_extras {
            for &p in &self.objective_placeables {
                if self.mirrored.contains(&p) {
                    out.insert(p);
                }
            }
            for &p in &self.health_pack_placeables {
                if self.mirrored.contains(&p) {
                    out.insert(p);
                }
            }
            for &p in &self.scene_placeables {
                if self.mirrored.contains(&p) {
                    out.insert(p);
                }
            }
        }
        out.into_iter().collect()
    }
}

pub fn active_spawn_candidates(spawns: &[(u8, Option<u32>)]) -> [Vec<u32>; 2] {
    let mut out: [Vec<u32>; 2] = [Vec::new(), Vec::new()];
    for &(team, placeable) in spawns {
        if let (0 | 1, Some(p)) = (team, placeable) {
            out[team as usize].push(MAP_ENTITY_BIT | p);
        }
    }
    out
}

pub fn fit_control_room_panel(
    panel_pos: [f64; 3],
    panel_scale: [f64; 3],
    rim_pos: [f64; 3],
    rim_rot: [f64; 4],
    rim_scale: [f64; 3],
    rim_size: [f64; 2],
    panel_base_size: f64,
) -> ([f32; 3], [f32; 3]) {
    let normal = rotate_vec(rim_rot, [0.0, 0.0, 1.0]);
    let delta = [
        panel_pos[0] - rim_pos[0],
        panel_pos[1] - rim_pos[1],
        panel_pos[2] - rim_pos[2],
    ];
    let dot = normal[0] * delta[0] + normal[1] * delta[1] + normal[2] * delta[2];
    let depth = dot.min(-0.02);
    let pos = [
        (rim_pos[0] + normal[0] * depth) as f32,
        (rim_pos[1] + normal[1] * depth) as f32,
        (rim_pos[2] + normal[2] * depth) as f32,
    ];
    let scale = [
        (rim_size[0] * rim_scale[0] / panel_base_size) as f32,
        (rim_size[1] * rim_scale[1] / panel_base_size) as f32,
        panel_scale[2] as f32,
    ];
    (pos, scale)
}

// ---------------------------------------------------------------------------
// 3. Shared Match Phase & Clock Quantization
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MatchPhase {
    Waiting = 0,
    Assemble = 1,
    Setup = 2,
    Active = 3,
    Unlock = 4,
    Complete = 5,
    RoundEnd = 6,
}

impl MatchPhase {
    pub fn name(self) -> &'static str {
        match self {
            Self::Waiting => "WAITING",
            Self::Assemble => "ASSEMBLE",
            Self::Setup => "SETUP",
            Self::Active => "ACTIVE",
            Self::Unlock => "UNLOCK",
            Self::Complete => "COMPLETE",
            Self::RoundEnd => "ROUND_END",
        }
    }
}

pub fn hud_clock_seconds(remaining: f64) -> f64 {
    let rem = remaining.max(0.0);
    let precision = if rem <= 10.0 { 10.0 } else { 4.0 };
    (rem * precision - 1e-7).ceil().max(0.0) / precision
}

// ---------------------------------------------------------------------------
// 4. Composable CapturePointStage (Assault + Hybrid Point A + Control)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct CapturePointSpec {
    pub placeable: u32,
    pub position: [f64; 3],
    pub zone: ObbZone,
    pub round_seconds: f64,
    pub capture_seconds: f64,
    pub segments: usize,
}

#[derive(Clone, Debug)]
pub struct CapturePointStage {
    pub spec: CapturePointSpec,
    pub progress: f64,
    pub peak: f64,
    pub contested: bool,
    pub capture_scale: f64,
}

impl CapturePointStage {
    pub fn new(spec: CapturePointSpec, capture_scale: f64) -> Self {
        Self {
            spec,
            progress: 0.0,
            peak: 0.0,
            contested: false,
            capture_scale,
        }
    }

    pub fn milestone_floor(&self) -> f64 {
        let seg = self.spec.segments.max(1) as f64;
        (self.peak * seg).floor() / seg
    }

    pub fn captured_segments(&self) -> usize {
        let seg = self.spec.segments.max(1);
        let p = (self.progress as f32) * (seg as f32);
        (p.floor() as usize).min(seg.saturating_sub(1))
    }

    fn gain_progress(&mut self, seconds: f64, multiplier: f64) {
        let gain = seconds * multiplier * self.capture_scale / self.spec.capture_seconds.max(0.01);
        self.progress = (self.progress + gain).min(1.0);
        self.peak = self.peak.max(self.progress);
    }

    fn decay_progress(&mut self, seconds: f64, duration: f64) {
        let floor = self.milestone_floor();
        if self.progress > floor {
            self.progress = (self.progress - seconds / duration).max(floor);
        }
    }
}

#[derive(Clone, Debug)]
pub struct AssaultRound {
    pub points: Vec<CapturePointStage>,
    pub phase: MatchPhase,
    pub stage: usize,
    pub captured: usize,
    pub deadline: f64,
    pub last_update: f64,
    pub last_attacker: f64,
    pub overtime: bool,
    fuse: OvertimeFuse,
    pub winner: Option<u8>,
    pub(crate) occupancy: TeamOccupancy,
    pub(crate) capturing_bodies: Vec<u32>,
    pub(crate) outlines: Vec<Option<u32>>,
    pub(crate) barriers: DynamicBarrierRegistry,
    decay_seconds: Vec<f64>,
}

impl AssaultRound {
    pub fn new(specs: Vec<CapturePointSpec>, capture_scale: f64) -> Self {
        let decay_seconds = specs.iter().map(|spec| spec.capture_seconds).collect();
        let outlines = vec![None; specs.len()];
        let points = specs
            .into_iter()
            .map(|s| CapturePointStage::new(s, capture_scale))
            .collect();
        Self {
            points,
            phase: MatchPhase::Assemble,
            stage: 0,
            captured: 0,
            deadline: 0.0,
            last_update: 0.0,
            last_attacker: 0.0,
            overtime: false,
            fuse: OvertimeFuse::default(),
            winner: None,
            occupancy: TeamOccupancy::default(),
            capturing_bodies: Vec::new(),
            outlines,
            barriers: DynamicBarrierRegistry::default(),
            decay_seconds,
        }
    }

    pub fn start_setup(&mut self, assembly_ends: f64) {
        let previous = self.phase;
        self.phase = MatchPhase::Setup;
        self.deadline = assembly_ends + SETUP_SECONDS;
        self.last_update = assembly_ends;
        transition_spawn_doors(
            &mut self.barriers,
            (previous, self.stage),
            (self.phase, self.stage),
            assembly_ends,
        );
    }

    pub fn current(&self) -> &CapturePointStage {
        &self.points[self.stage.min(self.points.len() - 1)]
    }

    pub fn advance(&mut self, now: f64, actors: &[ActorSample]) {
        let before = (self.phase, self.stage);
        if self.phase == MatchPhase::Setup && now >= self.deadline {
            self.phase = MatchPhase::Active;
            self.last_update = self.deadline;
            self.last_attacker = self.deadline;
            self.deadline += self.points[0].spec.round_seconds;
        }
        if self.phase != MatchPhase::Active {
            return;
        }
        self.occupancy = self.current().spec.zone.occupants(actors);
        let attackers = self.occupancy.team1.len();
        let window = (now >= self.deadline).then_some((self.deadline, self.deadline));
        self.fuse.advance(now, attackers != 0, window);
        self.overtime = self.fuse.started.is_some();
        let end = if self.overtime {
            self.fuse.end
        } else {
            self.deadline
        };
        let elapsed = (now.min(end) - self.last_update).max(0.0);
        self.last_update = now;
        let point = &mut self.points[self.stage];
        point.contested = attackers != 0 && !self.occupancy.team0.is_empty();
        let completed = if attackers != 0 && now < end {
            self.last_attacker = now;
            if !point.contested {
                let speed = CAPTURE_RATES[(attackers - 1).min(CAPTURE_RATES.len() - 1)];
                point.gain_progress(elapsed, speed);
            }
            point.progress >= 1.0 - 1e-7
        } else {
            if attackers == 0 {
                let decay = elapsed.min((now - self.last_attacker - CAPTURE_DECAY_DELAY).max(0.0));
                point.decay_progress(decay, self.decay_seconds[self.stage]);
            }
            false
        };
        if completed {
            point.progress = 1.0;
            point.peak = 1.0;
            point.contested = false;
            if self.stage == 0 {
                self.capturing_bodies.clone_from(&self.occupancy.team1);
            }
            self.occupancy = TeamOccupancy::default();
            self.captured += 1;
            self.overtime = false;
            self.fuse = OvertimeFuse::default();
            if self.stage + 1 >= self.points.len() {
                self.phase = MatchPhase::Complete;
                self.winner = Some(FIRST_ATTACKING_TEAM);
            } else {
                self.stage += 1;
                self.last_attacker = now;
                self.deadline = self.deadline.max(now) + self.points[self.stage].spec.round_seconds;
            }
        } else if now >= end {
            self.phase = MatchPhase::Complete;
            self.overtime = false;
            self.fuse = OvertimeFuse::default();
            self.winner = Some(FIRST_DEFENDING_TEAM);
            self.occupancy = TeamOccupancy::default();
            point.contested = false;
        }
        transition_spawn_doors(&mut self.barriers, before, (self.phase, self.stage), now);
    }

    pub(crate) fn waypoint(&self, index: usize, phase: MatchPhase) -> bool {
        index == self.stage
            && index >= self.captured
            && matches!(phase, MatchPhase::Setup | MatchPhase::Active)
    }
}

pub(crate) fn spawn_door_open(
    entry: &crate::room_primitives::BarrierEntry,
    phase: MatchPhase,
    stage: usize,
) -> bool {
    match phase {
        MatchPhase::Waiting => {
            !entry.payload_unlock && entry.rooms.contains(&(0, FIRST_ATTACKING_TEAM))
        }
        MatchPhase::Active | MatchPhase::Complete => {
            entry.rooms.iter().any(|&(index, _)| index <= stage)
        }
        _ => false,
    }
}

fn transition_spawn_doors(
    barriers: &mut DynamicBarrierRegistry,
    previous: (MatchPhase, usize),
    current: (MatchPhase, usize),
    now: f64,
) {
    if previous == current {
        return;
    }
    for entry in &barriers.entries {
        if entry.kind != BarrierKind::Door || entry.gate_tag.is_some() || entry.payload_unlock {
            continue;
        }
        let open = spawn_door_open(entry, current.0, current.1);
        if open != spawn_door_open(entry, previous.0, previous.1) {
            barriers
                .gate_commands
                .insert(entry.entity, entry.door_command(open, now, 0.0));
        }
    }
}

pub(crate) fn stage_room_visual(
    phase: MatchPhase,
    stage: usize,
    entry: &crate::room_primitives::BarrierEntry,
    team: u8,
) -> bool {
    if team > 1 || phase == MatchPhase::Complete {
        return false;
    }
    match entry.kind {
        BarrierKind::TeamBarrier => entry.rooms.contains(&(stage, 1 - team)),
        BarrierKind::Overlay | BarrierKind::RestrictedSign => {
            entry.viewers.contains(&team)
                && entry
                    .rooms
                    .iter()
                    .any(|&(room_stage, _)| room_stage == stage)
        }
        _ => false,
    }
}

// ---------------------------------------------------------------------------
// 5. EscortRound (SplineMover + DynamicBarrierRegistry)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct EscortRound {
    pub mover: SplineMover,
    pub barriers: DynamicBarrierRegistry,
    pub payload_placeable: Option<u32>,
    pub payload_origin: Option<[f64; 3]>,
    pub payload_rotation: Option<[f64; 4]>,
    pub checkpoints: Vec<(f64, u32)>,
    pub stage_seconds: Vec<f64>,
    pub debris_distances: BTreeMap<u32, f64>,
    pub phase: MatchPhase,
    pub stage: usize,
    pub stage_offset: usize,
    pub deadline: f64,
    pub overtime: bool,
    fuse: OvertimeFuse,
    pub winner: Option<u8>,
    pub gate_impact_started: Option<f64>,
    pub(crate) unlock: Option<PayloadUnlock>,
}

#[derive(Clone, Debug)]
pub(crate) struct PayloadUnlock {
    pub(crate) delay: f64,
    pub(crate) duration: f64,
    pub(crate) handoff_at: Option<f64>,
}

impl PayloadUnlock {
    pub(crate) fn starts_at(&self) -> Option<f64> {
        self.handoff_at.map(|handoff| handoff + self.delay)
    }
}

impl EscortRound {
    pub fn new(
        track: SplineTrack,
        curve: SplineSpeedCurve,
        barriers: DynamicBarrierRegistry,
        stage_seconds: Vec<f64>,
        auto_drive: bool,
    ) -> Self {
        let mut checkpoints = Vec::new();
        let mut debris_distances = BTreeMap::new();
        for (idx, node) in track.nodes.iter().enumerate() {
            if let Some(cp) = node.checkpoint {
                checkpoints.push((track.node_distances[idx], cp));
            }
        }
        for entry in &barriers.entries {
            if entry.kind == BarrierKind::GateDebris {
                if let Some(node_idx) = entry.debris_rail_node {
                    if let Some(&d) = track.node_distances.get(node_idx) {
                        debris_distances.insert(entry.entity, d);
                    }
                }
            }
        }
        let mover = SplineMover::new(track, curve, auto_drive);
        Self {
            mover,
            barriers,
            payload_placeable: None,
            payload_origin: None,
            payload_rotation: None,
            checkpoints,
            stage_seconds,
            debris_distances,
            phase: MatchPhase::Assemble,
            stage: 0,
            stage_offset: 0,
            deadline: 0.0,
            overtime: false,
            fuse: OvertimeFuse::default(),
            winner: None,
            gate_impact_started: None,
            unlock: None,
        }
    }

    pub fn start_setup(&mut self, assembly_ends: f64) {
        let before = self.phase;
        self.phase = MatchPhase::Setup;
        self.deadline = assembly_ends + SETUP_SECONDS;
        self.mover.last_update = assembly_ends;
        self.transition_spawn_doors(before, self.stage, assembly_ends);
    }

    pub(crate) fn start_assembly(&mut self, now: f64) {
        if self.phase == MatchPhase::Waiting {
            self.phase = MatchPhase::Assemble;
            self.transition_spawn_doors(MatchPhase::Waiting, self.stage, now);
        }
    }

    pub fn checkpoint_fractions(&self) -> Vec<f64> {
        let len = self.mover.track.total_length.max(1e-9);
        self.checkpoints.iter().map(|(d, _)| d / len).collect()
    }

    pub(crate) fn stage_progress(&self) -> (usize, f64) {
        let index = self.stage.min(self.checkpoints.len() - 1);
        let start = index
            .checked_sub(1)
            .map_or(0.0, |previous| self.checkpoints[previous].0);
        let end = self.checkpoints[index].0;
        (
            index + self.stage_offset,
            ((self.mover.distance - start) / (end - start)).clamp(0.0, 1.0),
        )
    }

    pub(crate) fn pose(&self) -> crate::room_primitives::SplinePose {
        let mut pose = self.mover.pose();
        let blend = (1.0 - self.mover.distance / crate::room_primitives::RAIL_LOOKAHEAD).max(0.0);
        if let Some(origin) = self.payload_origin.filter(|_| blend > 0.0) {
            for (axis, position) in pose.position.iter_mut().enumerate() {
                *position += (origin[axis] - self.mover.track.nodes[0].position[axis]) * blend;
            }
        }
        if let Some(rotation) = self.payload_rotation.filter(|_| blend > 0.0) {
            let [x, y, z] = rotate_vec(rotation, [0.0, 0.0, 1.0]);
            let units = 65536.0 / std::f64::consts::TAU;
            let yaw = (x.atan2(z) * units).round_ties_even() as i32 as i16;
            let pitch = ((-y).atan2(x.hypot(z)) * units).round_ties_even() as i16;
            pose.yaw = pose.yaw.wrapping_add(
                (f64::from(yaw.wrapping_sub(pose.yaw)) * blend).round_ties_even() as i16,
            );
            pose.pitch = (f64::from(pose.pitch)
                + (f64::from(pitch) - f64::from(pose.pitch)) * blend)
                .round_ties_even() as i16;
            let (sy, cy) = (f64::from(pose.yaw) * std::f64::consts::TAU / 131072.0).sin_cos();
            let (sp, cp) = (f64::from(pose.pitch) * std::f64::consts::TAU / 131072.0).sin_cos();
            pose.rotation =
                crate::room_primitives::quat_mul([0.0, sy, 0.0, cy], [sp, 0.0, 0.0, cp]);
        }
        pose
    }

    pub fn near_finish(&self) -> bool {
        let rem = self.mover.track.total_length - self.mover.distance;
        self.phase == MatchPhase::Active
            && self.mover.distance > 0.0
            && rem > 0.0
            && rem < FINISH_WARNING_DISTANCE
    }

    pub(crate) fn room_stage(&self) -> usize {
        if self.unlocked_objective() {
            self.stage + self.stage_offset
        } else {
            0
        }
    }

    pub(crate) fn unlocked_objective(&self) -> bool {
        self.unlock
            .as_ref()
            .is_none_or(|unlock| unlock.handoff_at.is_some())
    }

    pub fn advance(&mut self, now: f64, actors: &[ActorSample]) {
        let (before_phase, before_stage) = (self.phase, self.stage);
        if self.phase == MatchPhase::Setup && now >= self.deadline {
            self.phase = MatchPhase::Active;
            self.mover.last_attacker = self.deadline;
            self.mover.last_update = self.deadline;
            self.deadline += self.stage_seconds[0];
        }
        if self.phase != MatchPhase::Active {
            return;
        }
        let prev_dist = self.mover.distance;
        self.mover.sample_occupants(
            self.pose().position,
            actors,
            FIRST_ATTACKING_TEAM,
            FIRST_DEFENDING_TEAM,
        );
        let window = (now >= self.deadline).then_some((self.deadline, self.deadline));
        self.fuse
            .advance(now, !self.mover.pushers.is_empty(), window);
        self.overtime = self.fuse.started.is_some();
        let end = if self.overtime {
            self.fuse.end
        } else {
            self.deadline
        };
        let events = self.mover.step_sampled(now, end);
        for (tag, wait) in events.open_gates {
            self.barriers.request_route_gate(tag, true, now, wait);
        }
        for tag in events.close_gates {
            self.barriers.request_route_gate(tag, false, now, 0.0);
        }
        if let Some(t) = events.gate_impact_at {
            self.gate_impact_started = Some(t);
        }
        for (&ent, &trigger_d) in &self.debris_distances {
            if prev_dist < trigger_d && self.mover.distance + 1e-9 >= trigger_d {
                self.barriers.debris_started.entry(ent).or_insert(now);
            }
        }
        for _cp in events.reached_checkpoints {
            self.stage += 1;
            self.overtime = false;
            self.fuse = OvertimeFuse::default();
            if self.stage >= self.checkpoints.len() {
                self.phase = MatchPhase::Complete;
                self.winner = Some(FIRST_ATTACKING_TEAM);
                self.mover.pushers.clear();
                self.mover.blockers.clear();
                self.transition_spawn_doors(before_phase, before_stage, now);
                return;
            }
            let extra = self.stage_seconds.get(self.stage).copied().unwrap_or(0.0);
            self.deadline = self.deadline.max(now) + extra;
        }

        if now >= end && self.deadline <= end {
            self.phase = MatchPhase::Complete;
            self.winner = Some(FIRST_DEFENDING_TEAM);
            self.overtime = false;
            self.fuse = OvertimeFuse::default();
            self.mover.pushers.clear();
            self.mover.blockers.clear();
        }
        self.transition_spawn_doors(before_phase, before_stage, now);
    }

    pub fn door_state(&self, entity: u32, now: f64, previous: Option<&DoorState>) -> DoorState {
        self.barriers.door_state(
            entity,
            now,
            self.room_stage(),
            self.phase == MatchPhase::Active
                || self.phase == MatchPhase::Complete
                || self.waiting_door_open(entity),
            previous,
        )
    }

    fn waiting_door_open(&self, entity: u32) -> bool {
        self.phase == MatchPhase::Waiting
            && self.barriers.entries.iter().any(|entry| {
                entry.entity == entity
                    && entry.kind == BarrierKind::Door
                    && !entry.payload_unlock
                    && entry.rooms.contains(&(0, FIRST_ATTACKING_TEAM))
            })
    }

    fn transition_spawn_doors(&mut self, before_phase: MatchPhase, before_stage: usize, now: f64) {
        if (before_phase, before_stage) == (self.phase, self.stage) {
            return;
        }
        let offset = if self.unlocked_objective() {
            self.stage_offset
        } else {
            0
        };
        let stage = self.room_stage();
        transition_spawn_doors(
            &mut self.barriers,
            (before_phase, before_stage + offset),
            (self.phase, stage),
            now,
        );
    }

    pub(crate) fn door_passable(
        &self,
        entry: &crate::room_primitives::BarrierEntry,
        now: f64,
        active: bool,
    ) -> bool {
        self.barriers.door_passable(
            entry.entity,
            now,
            self.room_stage(),
            active || self.waiting_door_open(entry.entity),
        )
    }

    pub(crate) fn room_visual_visible(
        &self,
        entry: &crate::room_primitives::BarrierEntry,
        team: u8,
    ) -> bool {
        stage_room_visual(self.phase, self.room_stage(), entry, team)
    }
}

// ---------------------------------------------------------------------------
// 6. HybridRound (CapturePointStage -> EscortRound)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct HybridRound {
    pub capture: AssaultRound,
    pub escort: EscortRound,
}

impl HybridRound {
    pub fn new(
        capture: CapturePointStage,
        mut escort: EscortRound,
        unlock_delay: f64,
        unlock_seconds: f64,
    ) -> Self {
        escort.stage_offset = 1;
        escort.unlock = Some(PayloadUnlock {
            delay: unlock_delay,
            duration: unlock_seconds,
            handoff_at: None,
        });
        let mut point = AssaultRound::new(vec![capture.spec.clone()], capture.capture_scale);
        point.points[0] = capture;
        Self {
            capture: point,
            escort,
        }
    }

    pub fn start_setup(&mut self, assembly_ends: f64) {
        self.capture.start_setup(assembly_ends);
        self.escort.start_setup(assembly_ends);
    }

    pub fn room_stage(&self) -> usize {
        self.escort.room_stage()
    }

    pub fn payload_active(&self) -> bool {
        self.escort.phase == MatchPhase::Active && self.escort.unlocked_objective()
    }

    pub fn advance(&mut self, now: f64, actors: &[ActorSample]) {
        let round = &mut self.escort;
        let unlock = round
            .unlock
            .as_mut()
            .expect("capture-to-payload transition");
        if unlock.handoff_at.is_none() {
            let previous = round.phase;
            self.capture.advance(now, actors);
            round.phase = self.capture.phase;
            round.deadline = self.capture.deadline;
            round.overtime = self.capture.overtime;
            round.winner = self.capture.winner;
            if self.capture.captured > 0 {
                unlock.handoff_at = Some(now);
                round.phase = MatchPhase::Active;
                round.winner = None;
                round.deadline = round.deadline.max(now) + round.stage_seconds[0];
                round.mover.last_update = now;
                round.mover.last_attacker = now;
            }
            let stage = round.room_stage();
            transition_spawn_doors(
                &mut round.barriers,
                (previous, 0),
                (round.phase, stage),
                now,
            );
            return;
        }
        if round.phase != MatchPhase::Active {
            return;
        }
        let start = unlock.starts_at().expect("completed capture");
        let end = start + unlock.duration;
        if now >= start && round.mover.last_update <= start {
            for entry in round
                .barriers
                .entries
                .iter()
                .filter(|entry| entry.payload_unlock)
            {
                round
                    .barriers
                    .gate_commands
                    .entry(entry.entity)
                    .or_insert_with(|| entry.door_command(true, start, 0.0));
            }
        }
        round.mover.last_update = round.mover.last_update.max(now.min(end));
        if now >= end {
            round.advance(now, actors);
        } else if now >= round.deadline {
            round.phase = MatchPhase::Complete;
            round.winner = Some(FIRST_DEFENDING_TEAM);
        }
    }
}

// ---------------------------------------------------------------------------
// 7. ControlRound (Multi-Round Capture + 0..100 Scoring + Healing Rooms)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct ControlStageSpec {
    pub point: CapturePointSpec,
    pub healing_rooms: [Option<ObbZone>; 2],
}

#[derive(Clone, Debug)]
pub struct ControlRound {
    pub stages: Vec<ControlStageSpec>,
    pub stage_order: Vec<usize>,
    pub round_index: usize,
    pub points_to_win: u32,
    pub capture_scale: f64,
    pub score_scale: f64,
    pub fixed_stage: Option<usize>,
    pub phase: MatchPhase,
    pub owner: i8, // -1 neutral, 0 team0, 1 team1
    pub progress: f64,
    pub scores: [f64; 2],
    pub round_wins: [u32; 2],
    pub contested: bool,
    pub overtime: bool,
    pub round_winner: Option<u8>,
    pub winner: Option<u8>,
    pub deadline: f64,
    pub last_update: f64,
    pub last_capture: f64,
    fuse: OvertimeFuse,
    pub(crate) occupancy: TeamOccupancy,
    pub(crate) capturer: i8,
    pub(crate) capturing: bool,
    pub barriers: DynamicBarrierRegistry,
}

fn random_index(choices: usize) -> usize {
    let mut bytes = [0; 8];
    let _ = getrandom::fill(&mut bytes);
    (u64::from_le_bytes(bytes) % choices as u64) as usize
}

fn control_stage_order(stages: usize, wins: u32) -> Vec<usize> {
    let mut order: Vec<_> = (0..stages).collect();
    for index in (1..stages).rev() {
        order.swap(index, random_index(index + 1));
    }
    if stages > 1 {
        // Once all points have been used, the graph selects from the points
        // other than the one just played.
        while order.len() < (2 * wins - 1) as usize {
            let previous = order[order.len() - 1];
            let next = random_index(stages - 1);
            order.push(next + usize::from(next >= previous));
        }
    }
    order
}

impl ControlRound {
    pub fn new(
        stages: Vec<ControlStageSpec>,
        stage_order: Vec<usize>,
        points_to_win: u32,
        capture_scale: f64,
        score_scale: f64,
        fixed_stage: Option<usize>,
        barriers: DynamicBarrierRegistry,
    ) -> Self {
        Self {
            stages,
            stage_order,
            round_index: 0,
            points_to_win,
            capture_scale,
            score_scale,
            fixed_stage,
            phase: MatchPhase::Assemble,
            owner: -1,
            progress: 0.0,
            scores: [0.0, 0.0],
            round_wins: [0, 0],
            contested: false,
            overtime: false,
            round_winner: None,
            winner: None,
            deadline: 0.0,
            last_update: 0.0,
            last_capture: 0.0,
            fuse: OvertimeFuse::default(),
            occupancy: TeamOccupancy::default(),
            capturer: -1,
            capturing: false,
            barriers,
        }
    }

    pub fn stage(&self) -> usize {
        if let Some(fixed) = self.fixed_stage {
            return fixed;
        }
        self.stage_order[self.round_index % self.stage_order.len()]
    }

    pub fn movement_scale(&self) -> f64 {
        if self.phase == MatchPhase::RoundEnd {
            CONTROL_ROUND_END_MOVEMENT_SCALE
        } else {
            1.0
        }
    }

    pub fn start_setup(&mut self, now: f64) {
        let previous = (self.phase, self.stage());
        self.phase = MatchPhase::Setup;
        self.deadline = now + CONTROL_SETUP_SECONDS;
        self.last_update = now;
        self.transition_doors(previous, now);
    }

    pub(crate) fn door_open(
        entry: &crate::room_primitives::BarrierEntry,
        phase: MatchPhase,
        stage: usize,
    ) -> bool {
        matches!(phase, MatchPhase::Unlock | MatchPhase::Active)
            && entry.rooms.iter().any(|&(room, _)| room == stage)
    }

    fn transition_doors(&mut self, before: (MatchPhase, usize), now: f64) {
        let after = (self.phase, self.stage());
        for entry in self
            .barriers
            .entries
            .iter()
            .filter(|entry| entry.kind == BarrierKind::Door)
        {
            let open = Self::door_open(entry, after.0, after.1);
            if open != Self::door_open(entry, before.0, before.1) {
                self.barriers
                    .gate_commands
                    .insert(entry.entity, entry.door_command(open, now, 0.0));
            }
        }
    }

    pub fn in_healing_room(&self, team: u8, pos: [f64; 3]) -> bool {
        team < 2
            && matches!(
                self.phase,
                MatchPhase::Setup | MatchPhase::Unlock | MatchPhase::Active
            )
            && self.stages[self.stage()].healing_rooms[team as usize]
                .as_ref()
                .is_some_and(|zone| zone.contains(pos))
    }

    fn clear_overtime(&mut self) {
        self.overtime = false;
        self.fuse = OvertimeFuse::default();
    }

    pub fn next_round(&mut self, now: f64) {
        let previous = (self.phase, self.stage());
        self.round_index += 1;
        self.owner = -1;
        self.scores = [0.0, 0.0];
        self.progress = 0.0;
        self.last_capture = now;
        self.contested = false;
        self.occupancy = TeamOccupancy::default();
        self.capturer = -1;
        self.capturing = false;
        self.clear_overtime();
        self.round_winner = None;
        self.start_setup(now);
        self.transition_doors(previous, now);
    }

    pub fn finish_round(&mut self, winner: u8, now: f64) {
        let previous = (self.phase, self.stage());
        self.round_winner = Some(winner);
        self.round_wins[winner as usize] += 1;
        self.scores[winner as usize] = 100.0;
        self.clear_overtime();
        self.occupancy = TeamOccupancy::default();
        self.contested = false;
        self.capturer = -1;
        self.capturing = false;
        self.progress = 0.0;
        if self.round_wins[winner as usize] >= self.points_to_win {
            self.winner = Some(winner);
            self.phase = MatchPhase::Complete;
            self.deadline = now;
        } else {
            self.phase = MatchPhase::RoundEnd;
            self.deadline = now + CONTROL_ROUND_SUMMARY_DELAY;
        }
        self.transition_doors(previous, now);
    }

    pub fn advance(&mut self, now: f64, actors: &[ActorSample]) {
        let before = (self.phase, self.stage());
        if self.phase == MatchPhase::RoundEnd && now >= self.deadline {
            self.phase = MatchPhase::Complete;
            self.deadline += CONTROL_INTERMISSION_SECONDS;
        }
        if self.phase == MatchPhase::Complete && self.winner.is_none() && now >= self.deadline {
            self.next_round(self.deadline);
        }
        if self.phase == MatchPhase::Setup && now >= self.deadline {
            self.phase = MatchPhase::Unlock;
            self.last_update = self.deadline;
            self.deadline += CONTROL_UNLOCK_SECONDS;
        }
        if self.phase == MatchPhase::Unlock && now >= self.deadline {
            self.phase = MatchPhase::Active;
            self.last_update = self.deadline;
        }
        self.transition_doors(before, now);
        if self.phase != MatchPhase::Active || now < self.last_update {
            return;
        }

        let previous = self.last_update;
        let elapsed = now - previous;
        self.last_update = now;
        self.occupancy = self.stages[self.stage()].point.zone.occupants(actors);
        self.occupancy.team0.sort_unstable();
        self.occupancy.team1.sort_unstable();
        let counts = [self.occupancy.team0.len(), self.occupancy.team1.len()];
        self.contested = counts[0] > 0 && counts[1] > 0;
        if let Ok(owner) = usize::try_from(self.owner) {
            self.score(owner, counts[1 - owner] > 0, previous, now);
        }
        if self.phase == MatchPhase::Active {
            self.capture(counts, elapsed, now);
        }
    }

    fn score(&mut self, owner: usize, challenged: bool, previous: f64, now: f64) {
        let rate = (100.0 / 120.0) * self.score_scale;
        let score = self.scores[owner] + (now - previous) * rate;
        let window = (rate > 0.0 && score >= 99.0).then(|| {
            let threshold = previous + (99.0 - self.scores[owner]) / rate;
            (threshold, threshold + 1.0 / rate)
        });
        self.fuse.advance(now, challenged, window);
        self.overtime = self.fuse.started.is_some();
        if self.overtime {
            self.scores[owner] = 99.99;
            if now >= self.fuse.end {
                self.finish_round(owner as u8, now);
            }
        } else if score >= 100.0 - 1e-7 {
            self.finish_round(owner as u8, now);
        } else {
            self.scores[owner] = score;
        }
    }

    fn capture(&mut self, counts: [usize; 2], elapsed: f64, now: f64) {
        self.capturing = false;
        let team = match (self.capturer, self.owner) {
            (team @ 0..=1, _) if self.progress > 0.0 || counts[team as usize] > 0 => team as usize,
            (_, team @ 0..=1) => 1 - team as usize,
            _ => match counts[0].cmp(&counts[1]) {
                std::cmp::Ordering::Greater => 0,
                std::cmp::Ordering::Less => 1,
                std::cmp::Ordering::Equal if self.contested => random_index(2),
                _ => {
                    self.capturer = -1;
                    return;
                }
            },
        };
        let attackers = counts[team];
        let defenders = counts[1 - team];
        let seconds = self.stages[self.stage()].point.capture_seconds;
        self.capturing = attackers > 0 && defenders == 0;
        if attackers > 0 {
            self.capturer = team as i8;
            self.last_capture = now;
            if self.capturing {
                let multiplier = CAPTURE_RATES[attackers.min(CAPTURE_RATES.len()) - 1];
                self.progress =
                    (self.progress + elapsed * multiplier * self.capture_scale / seconds).min(1.0);
                if self.progress >= 1.0 - 1e-7 {
                    self.owner = self.capturer;
                    self.capturer = -1;
                    self.progress = 0.0;
                    self.capturing = false;
                    self.clear_overtime();
                }
            }
        } else {
            // With a neutral point, the other team first clears the previous claim.
            // Its presence satisfies the authored early exit from the decay wait.
            let decay = if self.owner == -1 && defenders > 0 {
                elapsed
            } else {
                elapsed.min((now - self.last_capture - CAPTURE_DECAY_DELAY).max(0.0))
            };
            let multiplier =
                CAPTURE_RATES[defenders.saturating_sub(1).min(CAPTURE_RATES.len() - 1)];
            self.progress =
                (self.progress - decay * multiplier * self.capture_scale / seconds).max(0.0);
            if self.progress == 0.0 {
                self.capturer = -1;
            }
        }
    }
}

/// Unified data-driven PvP objective runtime selectable purely by mode GUID
/// (`MODE_ASSAULT`, `MODE_ESCORT`, `MODE_HYBRID`, `MODE_CONTROL`).
#[derive(Clone, Debug)]
#[allow(
    clippy::large_enum_variant,
    reason = "Each room stores one runtime and callers can construct the public variants directly"
)]
pub enum PvpObjectiveRuntime {
    Assault(AssaultRound),
    Escort(EscortRound),
    Hybrid(HybridRound),
    Control(ControlRound),
}

impl PvpObjectiveRuntime {
    pub(crate) fn escort_round(&self) -> Option<&EscortRound> {
        match self {
            Self::Escort(round) => Some(round),
            Self::Hybrid(round) => Some(&round.escort),
            _ => None,
        }
    }

    pub(crate) fn wait_for_assembly(&mut self) {
        match self {
            Self::Assault(round) => round.phase = MatchPhase::Waiting,
            Self::Escort(round) => round.phase = MatchPhase::Waiting,
            Self::Hybrid(round) => {
                round.capture.phase = MatchPhase::Waiting;
                round.escort.phase = MatchPhase::Waiting;
            }
            Self::Control(round) => {
                round.phase = MatchPhase::Waiting;
                round.barriers.gate_commands.clear();
            }
        }
    }

    pub(crate) fn start_custom_setup(&mut self, assembly_ends: f64) {
        if !matches!(self.phase(), MatchPhase::Waiting | MatchPhase::Assemble) {
            return;
        }
        match self {
            Self::Assault(round) => round.start_setup(assembly_ends),
            Self::Escort(round) => round.start_setup(assembly_ends),
            Self::Hybrid(round) => round.start_setup(assembly_ends),
            Self::Control(round) => round.start_setup(assembly_ends),
        }
    }

    pub(crate) fn start_custom_assembly(&mut self, now: f64) {
        match self {
            Self::Assault(round) if round.phase == MatchPhase::Waiting => {
                round.phase = MatchPhase::Assemble;
                transition_spawn_doors(
                    &mut round.barriers,
                    (MatchPhase::Waiting, 0),
                    (MatchPhase::Assemble, 0),
                    now,
                );
            }
            Self::Escort(round) if round.payload_origin.is_some() => round.start_assembly(now),
            Self::Control(round) if round.phase == MatchPhase::Waiting => {
                round.phase = MatchPhase::Assemble
            }
            Self::Hybrid(round) => {
                if round.capture.phase == MatchPhase::Waiting {
                    round.capture.phase = MatchPhase::Assemble;
                }
                round.escort.start_assembly(now);
            }
            _ => {}
        }
    }

    pub(crate) fn phase_seconds(&self, now: f64) -> u32 {
        let deadline = match self {
            Self::Assault(round) => round.deadline,
            Self::Escort(round) => round.deadline,
            Self::Hybrid(round) => round.escort.deadline,
            Self::Control(round) => round.deadline,
        };
        (deadline - now).ceil().max(0.0) as u32
    }

    /// Automatically construct a data-driven PvP objective runtime from a retail
    /// `GameMap`'s authored `objectives` list (`game_maps_174.json`).
    pub fn from_game_map(map: &crate::game_content::GameMap) -> Option<Self> {
        if map.objectives.is_empty() || map.free_for_all || map.is_junkenstein() {
            return None;
        }
        let make_zone = |pos: [f64; 3], half: [f64; 3]| {
            ObbZone::new(vec![crate::room_primitives::ObbTrigger {
                rotation: [0.0, 0.0, 0.0, 1.0],
                center: [pos[0], pos[1] + half[1] * 0.5, pos[2]],
                half_extents: half,
            }])
        };
        let capture_spec =
            |goal: &crate::game_content::MapObjective, time, duration| CapturePointSpec {
                placeable: goal.placeable.unwrap_or(0),
                position: goal.position,
                zone: if goal.capture_boxes.is_empty() {
                    make_zone(goal.position, [8.0, 4.0, 8.0])
                } else {
                    ObbZone::new(
                        goal.capture_boxes
                            .iter()
                            .copied()
                            .map(crate::room_primitives::ObbTrigger::from_raw)
                            .collect(),
                    )
                },
                round_seconds: time,
                capture_seconds: positive_seconds(goal.capture_seconds, duration),
                segments: 3,
            };
        if let Some(route) = &map.payload_route {
            let payload = map.objectives.iter().find(|goal| goal.kind == "payload")?;
            let track = SplineTrack::from_nodes(route.nodes.clone());
            let curve = SplineSpeedCurve {
                speed_scale: route.speed_scale * positive_seconds(payload.speed_multiplier, 1.0),
                ..SplineSpeedCurve::default()
            };
            let mut escort = EscortRound::new(
                track,
                curve,
                DynamicBarrierRegistry::new(route.barriers.clone()),
                route.stage_seconds.clone(),
                payload.auto_drive,
            );
            escort.payload_placeable = payload.placeable;
            escort.payload_origin = Some(payload.position);
            escort.payload_rotation = payload.rotation;
            if let Some(course) = &map.capture_course {
                let point = map.objectives.iter().find(|goal| goal.kind == "point")?;
                let time = positive_seconds(point.casual_seconds.or(point.round_seconds), 300.0);
                let capture =
                    CapturePointStage::new(capture_spec(point, time, 30.0), course.speed_scale);
                let mut round = HybridRound::new(
                    capture,
                    escort,
                    payload.unlock_delay,
                    payload.unlock_seconds,
                );
                round.capture.outlines[0] = point.outline;
                round.capture.decay_seconds[0] = positive_seconds(
                    point.decay_seconds,
                    round.capture.current().spec.capture_seconds,
                );
                round.start_setup(0.0);
                return Some(Self::Hybrid(round));
            }
            escort.start_setup(0.0);
            return Some(Self::Escort(escort));
        }
        if map.mode_guid == MODE_CONTROL {
            let course = map.capture_course.as_ref();
            let control = course.and_then(|course| course.control.as_ref());
            let rules = control
                .map(|course| course.rules.clone())
                .unwrap_or_default();
            let mut goals: Vec<_> = map
                .objectives
                .iter()
                .filter(|goal| goal.kind == "control point" && goal.placeable.is_some())
                .collect();
            goals.sort_by_key(|goal| goal.stage);
            let stages: Vec<_> = goals
                .into_iter()
                .enumerate()
                .map(|(stage, goal)| {
                    let zone = |boxes: &[[f64; 10]]| {
                        ObbZone::new(
                            boxes
                                .iter()
                                .copied()
                                .map(crate::room_primitives::ObbTrigger::from_raw)
                                .collect(),
                        )
                    };
                    ControlStageSpec {
                        point: CapturePointSpec {
                            zone: if goal.capture_boxes.is_empty() {
                                make_zone(goal.position, [7.5, 4.0, 7.5])
                            } else {
                                zone(&goal.capture_boxes)
                            },
                            segments: 1,
                            ..capture_spec(goal, 120.0, 8.0)
                        },
                        healing_rooms: std::array::from_fn(|team| {
                            let rooms: Vec<_> = control
                                .into_iter()
                                .flat_map(|course| &course.healing_rooms)
                                .filter(|room| {
                                    room.stage == goal.stage.unwrap_or(stage)
                                        && usize::from(room.team) == team
                                })
                                .flat_map(|room| room.boxes.iter().copied())
                                .collect();
                            (!rooms.is_empty()).then(|| zone(&rooms))
                        }),
                    }
                })
                .collect();
            if stages.is_empty() {
                return None;
            }
            let points_to_win = rules.points_to_win.unwrap_or(2);
            let order = control_stage_order(stages.len(), points_to_win);
            let fixed_stage = rules.fixed_stage.filter(|&stage| stage < stages.len());
            let barriers = DynamicBarrierRegistry::new(
                course
                    .map(|course| course.barriers.clone())
                    .unwrap_or_default(),
            );
            let mut round = ControlRound::new(
                stages,
                order,
                points_to_win,
                rules.capture_scale(),
                rules.score_scale(),
                fixed_stage,
                barriers,
            );
            round.start_setup(0.0);
            return Some(Self::Control(round));
        }

        let mut points: Vec<_> = map
            .objectives
            .iter()
            .filter(|o| o.kind.eq_ignore_ascii_case("point"))
            .collect();
        points.sort_by_key(|goal| goal.objective);
        let checkpoints: Vec<_> = map
            .objectives
            .iter()
            .filter(|o| o.kind.eq_ignore_ascii_case("checkpoint"))
            .collect();
        let payload = map
            .objectives
            .iter()
            .find(|o| o.kind.eq_ignore_ascii_case("payload"));

        let build_escort = |start_pos: [f64; 3]| -> Option<EscortRound> {
            if checkpoints.is_empty() {
                return None;
            }
            let mut nodes = Vec::with_capacity(checkpoints.len() + 1);
            nodes.push(crate::room_primitives::SplineNode::simple(start_pos));
            for (idx, cp) in checkpoints.iter().enumerate() {
                let mut node = crate::room_primitives::SplineNode::simple(cp.position);
                node.checkpoint = Some((idx + 1) as u32);
                nodes.push(node);
            }
            let track = SplineTrack::from_nodes(nodes);
            let stage_seconds = vec![240.0; checkpoints.len().max(1)];
            Some(EscortRound::new(
                track,
                SplineSpeedCurve::default(),
                DynamicBarrierRegistry::default(),
                stage_seconds,
                false,
            ))
        };

        // Hybrid: initial capture point A + payload escort checkpoints
        if let (Some(point_a), Some(pl)) = (points.first(), payload) {
            if let Some(mut escort) = build_escort(pl.position) {
                escort.payload_placeable = pl.placeable;
                let capture = CapturePointStage::new(
                    CapturePointSpec {
                        placeable: point_a.placeable.unwrap_or(0),
                        position: point_a.position,
                        zone: make_zone(point_a.position, [8.0, 4.0, 8.0]),
                        round_seconds: 240.0,
                        capture_seconds: 20.0,
                        segments: 3,
                    },
                    1.0,
                );
                let mut round = HybridRound::new(capture, escort, 2.0, 4.0);
                round.start_setup(0.0);
                return Some(Self::Hybrid(round));
            }
        }

        // Escort: payload + checkpoints
        if let Some(pl) = payload {
            if let Some(mut round) = build_escort(pl.position) {
                round.payload_placeable = pl.placeable;
                round.start_setup(0.0);
                return Some(Self::Escort(round));
            }
        }

        // Assault: 1..N capture points (Point A, Point B)
        if !points.is_empty() {
            let specs: Vec<CapturePointSpec> = points
                .iter()
                .enumerate()
                .map(|(index, o)| {
                    capture_spec(
                        o,
                        positive_seconds(o.round_seconds, if index == 0 { 240.0 } else { 180.0 }),
                        if index == 0 { 30.0 } else { 18.0 },
                    )
                })
                .collect();
            let course = map.capture_course.as_ref();
            let mut round =
                AssaultRound::new(specs, course.map_or(1.0, |course| course.speed_scale));
            round.outlines = points.iter().map(|goal| goal.outline).collect();
            round.decay_seconds = points
                .iter()
                .zip(&round.points)
                .map(|(goal, point)| {
                    positive_seconds(goal.decay_seconds, point.spec.capture_seconds)
                })
                .collect();
            if let Some(course) = course {
                round.barriers = DynamicBarrierRegistry::new(course.barriers.clone());
            }
            round.start_setup(0.0);
            return Some(Self::Assault(round));
        }

        None
    }

    pub fn is_on_objective(&self, pos: [f64; 3]) -> bool {
        match self {
            Self::Assault(round) => round.current().spec.zone.contains(pos),
            Self::Escort(round) => {
                let p = round.mover.pose().position;
                let dx = pos[0] - p[0];
                let dy = pos[1] - p[1];
                let dz = pos[2] - p[2];
                dx * dx + dz * dz <= 8.5 * 8.5 && dy.abs() <= 4.5
            }
            Self::Hybrid(round) => {
                if round.payload_active() {
                    let p = round.escort.mover.pose().position;
                    let dx = pos[0] - p[0];
                    let dy = pos[1] - p[1];
                    let dz = pos[2] - p[2];
                    dx * dx + dz * dz <= 8.5 * 8.5 && dy.abs() <= 4.5
                } else {
                    round.capture.current().spec.zone.contains(pos)
                }
            }
            Self::Control(round) => round.stages[round.stage()].point.zone.contains(pos),
        }
    }

    pub fn objective_occupants(&self, actors: &[ActorSample]) -> Vec<u32> {
        if self.phase() != MatchPhase::Active {
            return Vec::new();
        }
        actors
            .iter()
            .filter(|a| a.alive && self.is_on_objective(a.position))
            .map(|a| a.entity)
            .collect()
    }

    /// Live payload vehicle world pose (`MAP_ENTITY_BIT | placeable`) for Escort/Hybrid modes.
    pub fn payload_entity_update(&self) -> Option<([f64; 3], crate::world::EntityUpdate)> {
        let escort = match self {
            Self::Escort(round) => round,
            Self::Hybrid(round)
                if round.escort.payload_origin.is_some() || round.escort.unlocked_objective() =>
            {
                &round.escort
            }
            _ => return None,
        };
        let placeable = escort.payload_placeable?;
        let pose = escort.pose();
        let radians = std::f64::consts::TAU / 65536.0;
        let heading = f64::from(pose.yaw) * radians;
        let incline = -f64::from(pose.pitch) * radians;
        let speed = if escort.phase == MatchPhase::Active {
            escort.mover.speed
        } else {
            0.0
        };
        let horizontal = speed * incline.cos();
        let update = crate::world::EntityUpdate {
            entity: MAP_ENTITY_BIT | placeable,
            movement: Some(crate::world::Movement {
                position: pose.position,
                yaw: i32::from(pose.yaw),
                body_pitch: i32::from(pose.pitch),
                velocity: [
                    horizontal * heading.sin(),
                    speed * incline.sin(),
                    horizontal * heading.cos(),
                ],
                ..crate::world::Movement::default()
            }),
            ..crate::world::EntityUpdate::default()
        };
        Some((pose.position, update))
    }

    /// Compact state key `(phase, countdown_tenths, stage, progress_pct)` for dirty HUD replication.
    pub fn hud_state_key(&self, now: f64) -> (u8, i32, u8, u8) {
        let (deadline, stage, progress) = match self {
            Self::Assault(r) => (
                r.deadline,
                r.stage as u8,
                (r.current().progress * 100.0) as u8,
            ),
            Self::Escort(r) => {
                let len = r.mover.track.total_length.max(1e-6);
                (
                    r.deadline,
                    r.stage as u8,
                    ((r.mover.distance / len) * 100.0).clamp(0.0, 100.0) as u8,
                )
            }
            Self::Hybrid(r) => {
                let pct = if r.payload_active() {
                    let len = r.escort.mover.track.total_length.max(1e-6);
                    ((r.escort.mover.distance / len) * 100.0).clamp(0.0, 100.0) as u8
                } else {
                    (r.capture.current().progress * 100.0) as u8
                };
                (r.escort.deadline, r.room_stage() as u8, pct)
            }
            Self::Control(r) => (
                r.deadline,
                r.stage() as u8,
                r.scores[0].max(r.scores[1]).clamp(0.0, 100.0) as u8,
            ),
        };
        let rem = hud_clock_seconds((deadline - now).max(0.0));
        (
            self.phase() as u8,
            (rem * 10.0).round() as i32,
            stage,
            progress,
        )
    }

    /// Build `GAME_MODE_ENTITY` StateScript variables (`COUNTDOWN = 1303`) for live PvP HUD sync.
    pub fn hud_variables(&self, now: f64) -> crate::script_wire::Variables {
        let deadline = match self {
            Self::Assault(r) => r.deadline,
            Self::Escort(r) => r.deadline,
            Self::Hybrid(r) => r.escort.deadline,
            Self::Control(r) => r.deadline,
        };
        let rem = hud_clock_seconds((deadline - now).max(0.0));
        crate::script_wire::Variables::from([(1303, crate::script_wire::Value::Float(rem))])
    }

    pub fn mode_guid(&self) -> u64 {
        match self {
            Self::Assault(_) => MODE_ASSAULT,
            Self::Escort(_) => MODE_ESCORT,
            Self::Hybrid(_) => MODE_HYBRID,
            Self::Control(_) => MODE_CONTROL,
        }
    }

    pub fn phase(&self) -> MatchPhase {
        match self {
            Self::Assault(round) => round.phase,
            Self::Escort(round) => round.phase,
            Self::Hybrid(round) => round.escort.phase,
            Self::Control(round) => round.phase,
        }
    }

    pub fn winner(&self) -> Option<u8> {
        match self {
            Self::Assault(round) => round.winner,
            Self::Escort(round) => round.winner,
            Self::Hybrid(round) => round.escort.winner,
            Self::Control(round) => round.winner,
        }
    }

    pub fn movement_scale(&self) -> f64 {
        match self {
            Self::Control(round) => round.movement_scale(),
            _ => 1.0,
        }
    }

    pub fn in_healing_room(&self, team: u8, pos: [f64; 3]) -> bool {
        match self {
            Self::Control(round) => round.in_healing_room(team, pos),
            _ => false,
        }
    }

    pub fn advance(&mut self, now: f64, actors: &[ActorSample]) {
        match self {
            Self::Assault(round) => round.advance(now, actors),
            Self::Escort(round) => round.advance(now, actors),
            Self::Hybrid(round) => round.advance(now, actors),
            Self::Control(round) => round.advance(now, actors),
        }
    }
}

fn positive_seconds(value: Option<f64>, fallback: f64) -> f64 {
    value
        .filter(|seconds| seconds.is_finite() && *seconds > 0.0)
        .unwrap_or(fallback)
}

// ---------------------------------------------------------------------------
// 8. MatchAudioScheduler
// ---------------------------------------------------------------------------

pub const CUE_SELECT_HERO: VoiceCue = VoiceCue::new(0x1B3, 0x0646);
pub const CUE_PREPARE: [VoiceCue; 2] = [VoiceCue::new(0x168, 0x05DB), VoiceCue::new(0x167, 0x05DA)];
pub const CUE_ATTACK_STARTS: [VoiceCue; 2] =
    [VoiceCue::new(0x05D, 0x05F2), VoiceCue::new(0x06B, 0x05F1)];
pub const CUE_PAYLOAD: [VoiceCue; 2] = [VoiceCue::new(0x19B, 0x0620), VoiceCue::new(0x194, 0x0619)];
pub const CUE_OBJECTIVE_A: [VoiceCue; 2] =
    [VoiceCue::new(0x17B, 0x05F8), VoiceCue::new(0x178, 0x05F5)];
pub const CUE_OBJECTIVE_B: [VoiceCue; 2] =
    [VoiceCue::new(0x17C, 0x05FA), VoiceCue::new(0x179, 0x05F6)];
pub const CUE_POINT_A_TAKEN: [VoiceCue; 2] =
    [VoiceCue::new(0x061, 0x0613), VoiceCue::new(0x060, 0x060D)];
pub const CUE_CHECKPOINT: VoiceCue = VoiceCue::new(0x198, 0x061D);
pub const CUE_NEAR_FINISH: VoiceCue = VoiceCue::new(0x199, 0x061C);
pub const CUE_OVERTIME: VoiceCue = VoiceCue::new(0x066, 0x0625);
pub const CUE_RESULT: [VoiceCue; 2] = [VoiceCue::new(0x19F, 0x0628), VoiceCue::new(0x19E, 0x0627)];
pub const CUE_MATCH_COMPLETE: VoiceCue = VoiceCue::new(0x6C2, 0x8B45);
pub const CUE_PREPARE_FOR_BATTLE: VoiceCue = VoiceCue::new(0x70B, 0x8D9D);
pub const CUE_CONTROL_START: VoiceCue = VoiceCue::new(0x4FA, 0x7B53);
pub const CUE_CONTROL_ROUNDS: [VoiceCue; 5] = [
    CUE_CONTROL_START,
    VoiceCue::new(0x4FB, 0x7B54),
    VoiceCue::new(0x4FC, 0x7B55),
    VoiceCue::new(0x5D0, 0x8253),
    VoiceCue::new(0x5D1, 0x8254),
];
pub const CUE_CONTROL_UNLOCKED: VoiceCue = VoiceCue::new(0x464, 0x81F0);
pub const CUE_CONTROL_ROUND_RESULT: [[VoiceCue; 2]; 4] = [
    [VoiceCue::new(0x46A, 0x77BC), VoiceCue::new(0x469, 0x77BE)],
    [VoiceCue::new(0x47C, 0x7BEA), VoiceCue::new(0x47A, 0x7BEC)],
    [VoiceCue::new(0x5C4, 0x8244), VoiceCue::new(0x5C7, 0x8247)],
    [VoiceCue::new(0x5C5, 0x8245), VoiceCue::new(0x5C8, 0x8248)],
];
pub const CUE_CONTROL_SCORE: VoiceCue = VoiceCue::new(0x63B, 0x8447);
pub const CUE_CONTROL_SCORE_FIRST: [VoiceCue; 3] = [
    VoiceCue::new(0x61B, 0x8427),
    VoiceCue::new(0x5FD, 0x8408),
    VoiceCue::new(0x5FE, 0x840A),
];
pub const CUE_CONTROL_SCORE_SECOND: [VoiceCue; 3] = [
    VoiceCue::new(0x61C, 0x8431),
    VoiceCue::new(0x61D, 0x8426),
    VoiceCue::new(0x61E, 0x842A),
];

pub fn setup_clock_cue(seconds: i64, attacks: bool) -> Option<VoiceCue> {
    match seconds {
        60 => Some(if attacks {
            VoiceCue::new(0x169, 0x05DD)
        } else {
            VoiceCue::new(0x16A, 0x05DE)
        }),
        30 => Some(if attacks {
            VoiceCue::new(0x16B, 0x05DF)
        } else {
            VoiceCue::new(0x16C, 0x05E0)
        }),
        _ => None,
    }
}

pub fn round_clock_cue(seconds: i64) -> Option<VoiceCue> {
    match seconds {
        60 => Some(VoiceCue::new(0x19D, 0x0622)),
        30 => Some(VoiceCue::new(0x067, 0x0623)),
        10 => Some(VoiceCue::new(0x068, 0x0624)),
        _ => None,
    }
}

pub fn number_cue(seconds: i64) -> Option<VoiceCue> {
    match seconds {
        5 => Some(VoiceCue::new(0x057, 0x05EC)),
        4 => Some(VoiceCue::new(0x058, 0x05EB)),
        3 => Some(VoiceCue::new(0x059, 0x05EA)),
        2 => Some(VoiceCue::new(0x05B, 0x05E9)),
        1 => Some(VoiceCue::new(0x05C, 0x05E7)),
        _ => None,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AudioTimeline {
    pub phase: MatchPhase,
    pub seconds: i64,
    pub stage: usize,
    pub overtime: bool,
    pub winner: Option<u8>,
    pub near_finish: bool,
    pub control_wins: Option<[u8; 2]>,
    pub round_winner: Option<u8>,
}

pub fn match_speech(
    previous: Option<&AudioTimeline>,
    current: &AudioTimeline,
    mode: u64,
    team: u8,
    single_countdown: bool,
) -> Vec<VoiceCue> {
    let attacks = team == FIRST_ATTACKING_TEAM;
    let spectator = team == SPECTATOR_TEAM;
    let att = usize::from(attacks);

    if current.phase == MatchPhase::Assemble
        && previous.is_none_or(|p| p.phase == MatchPhase::Waiting)
    {
        if spectator || current.seconds <= 2 {
            return Vec::new();
        }
        let prep = if mode == MODE_CONTROL {
            CUE_PREPARE_FOR_BATTLE
        } else {
            CUE_PREPARE[att]
        };
        return vec![prep, CUE_SELECT_HERO];
    }

    let Some(prev) = previous else {
        return Vec::new();
    };

    if mode == MODE_CONTROL && !single_countdown {
        if current.phase == MatchPhase::RoundEnd
            && prev.phase == MatchPhase::Active
            && current.stage == prev.stage
        {
            if let Some(rw) = current.round_winner {
                let won = usize::from(spectator || team == rw);
                return vec![
                    CUE_CONTROL_ROUND_RESULT[current.stage.min(CUE_CONTROL_ROUND_RESULT.len() - 1)]
                        [won],
                ];
            }
        }
        if current.phase == MatchPhase::Complete && prev.phase != MatchPhase::Complete {
            if let Some(w) = current.winner {
                return vec![if spectator {
                    CUE_MATCH_COMPLETE
                } else {
                    CUE_RESULT[usize::from(team == w)]
                }];
            }
            if let Some([w0, w1]) = current.control_wins {
                let (first, second) = if team == FIRST_ATTACKING_TEAM {
                    (w1 as usize, w0 as usize)
                } else {
                    (w0 as usize, w1 as usize)
                };
                return vec![
                    CUE_CONTROL_SCORE,
                    CUE_CONTROL_SCORE_FIRST[first.min(2)],
                    CUE_CONTROL_SCORE_SECOND[second.min(2)],
                ];
            }
        }
        if current.overtime && !prev.overtime {
            return vec![CUE_OVERTIME];
        }
        if current.phase == MatchPhase::Setup
            && prev.phase == MatchPhase::Setup
            && current.stage == prev.stage
            && current.seconds < prev.seconds
        {
            if let Some(c) = number_cue(current.seconds) {
                return vec![c];
            }
        }
        if current.phase == MatchPhase::Unlock
            && current.seconds > 5
            && (prev.phase != MatchPhase::Unlock || prev.stage != current.stage)
        {
            return CUE_CONTROL_ROUNDS
                .get(current.stage)
                .copied()
                .into_iter()
                .collect();
        }
        if current.phase == MatchPhase::Active
            && prev.phase == MatchPhase::Unlock
            && current.stage == prev.stage
        {
            return vec![CUE_CONTROL_UNLOCKED];
        }
        return Vec::new();
    }

    if current.phase == MatchPhase::Complete && prev.phase != MatchPhase::Complete {
        return vec![if spectator {
            CUE_MATCH_COMPLETE
        } else {
            CUE_RESULT[usize::from(current.winner == Some(team))]
        }];
    }

    let mut finish = Vec::new();
    if current.near_finish && !prev.near_finish {
        finish.push(CUE_NEAR_FINISH);
    }

    if current.overtime && (!prev.overtime || prev.stage != current.stage) {
        let mut out = vec![CUE_OVERTIME];
        out.extend(finish);
        return out;
    }

    if current.phase == MatchPhase::Active
        && matches!(prev.phase, MatchPhase::Assemble | MatchPhase::Setup)
    {
        if spectator || current.stage > 0 {
            return Vec::new();
        }
        if mode == MODE_CONTROL {
            return vec![CUE_CONTROL_START];
        }
        let obj = if mode == MODE_ESCORT {
            CUE_PAYLOAD[att]
        } else {
            CUE_OBJECTIVE_A[att]
        };
        return vec![CUE_ATTACK_STARTS[att], obj];
    }

    if current.phase == MatchPhase::Active && prev.phase == MatchPhase::Active {
        if prev.stage < current.stage {
            if mode == MODE_HYBRID && prev.stage == 0 {
                return if spectator {
                    Vec::new()
                } else {
                    vec![CUE_POINT_A_TAKEN[att], CUE_PAYLOAD[att]]
                };
            }
            if mode == MODE_ESCORT || mode == MODE_HYBRID {
                let mut out = vec![CUE_CHECKPOINT];
                out.extend(finish);
                return out;
            }
            return if spectator {
                Vec::new()
            } else {
                vec![CUE_POINT_A_TAKEN[att], CUE_OBJECTIVE_B[att]]
            };
        }
        if !finish.is_empty() {
            return finish;
        }
        if !current.overtime && prev.stage == current.stage && current.seconds < prev.seconds {
            return round_clock_cue(current.seconds).into_iter().collect();
        }
    }

    if current.phase == prev.phase && current.seconds < prev.seconds {
        if current.phase == MatchPhase::Setup {
            if !spectator {
                if let Some(c) = setup_clock_cue(current.seconds, attacks) {
                    return vec![c];
                }
            }
            return number_cue(current.seconds).into_iter().collect();
        }
        if current.phase == MatchPhase::Assemble && single_countdown {
            return number_cue(current.seconds).into_iter().collect();
        }
    }

    Vec::new()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ModeActionSnapshot {
    pub phase: MatchPhase,
    pub stage: usize,
    pub seconds: f64,
    pub overtime: bool,
}

pub fn mode_action(prev: ModeActionSnapshot, cur: ModeActionSnapshot) -> Option<u16> {
    if prev.overtime && !cur.overtime {
        return Some(473);
    }
    if cur.overtime && !prev.overtime {
        return Some(471);
    }
    if prev.phase != cur.phase || prev.stage != cur.stage {
        return None;
    }
    if !(0.0 < cur.seconds && cur.seconds < prev.seconds) {
        return None;
    }
    match cur.phase {
        MatchPhase::Assemble if cur.seconds <= 10.0 && 10.0 < prev.seconds => Some(469),
        MatchPhase::Setup => {
            let sec = cur.seconds.ceil() as i64;
            if (1..=5).contains(&sec) && sec < (prev.seconds.ceil() as i64) {
                return Some((534 - sec) as u16); // 5->529, 4->530, 3->531, 2->532, 1->533
            }
            if 5.0 < cur.seconds && cur.seconds <= 15.0 && 15.0 < prev.seconds {
                return Some(470);
            }
            None
        }
        MatchPhase::Active if cur.seconds <= 30.0 && 30.0 < prev.seconds => Some(472),
        _ => None,
    }
}

#[cfg(test)]
mod tests;
