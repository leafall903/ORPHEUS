//! Build native objective presenters from room facts and a 104319 binding profile.

use std::{collections::BTreeMap, io, sync::OnceLock};

use serde::Deserialize;

use super::{
    AssaultRound, ControlRound, EscortRound, FIRST_ATTACKING_TEAM, FIRST_DEFENDING_TEAM,
    MatchPhase, OvertimeFuse, hud_clock_seconds,
};
use crate::{
    room_primitives::{BarrierEntry, BarrierKind, DoorPose, DoorState, MAP_ENTITY_BIT},
    script_codecs::StatePayload,
    script_graph::{Graph, GraphStore, asset_guid, graph_asset_index},
    script_snapshot_wire::ProjectedSnapshot,
    script_wire::{Value, Variables},
    world::GAME_MODE_ENTITY,
};

#[path = "presentation_control.rs"]
pub(crate) mod control;

const OBJECTIVE_KIND: u32 = 0x2931;
const OBJECTIVE_STAGE: u32 = 0x2934;
const CHECKPOINT_FRACTIONS: u32 = 0x1d9e;
const PAYLOAD_OBJECTIVE: Value = Value::Asset(0x0d80_0000_0000_2933);
const CAPTURE_OBJECTIVE: Value = Value::Asset(0x0d80_0000_0000_2932);
const ROUND_CONTEXT: u32 = 0x59dc;
const OVERTIME_ACTIVE: u32 = 0x0d40;
const OVERTIME_REMAINING: u32 = 0x1b3b;
const OVERTIME_CAPACITY: u32 = 0x1e25;
const OVERTIME_FRACTION: u32 = 0x59da;
const TEXT_ASSET: u64 = 0x0de0_0000_0000_0000;
pub(crate) const CHECKPOINT_SOUND_INSTANCE: u16 = 0x100;

pub(crate) struct ObjectiveClock {
    pub now: f64,
    pub remaining: f64,
}

#[derive(Deserialize)]
struct Bindings {
    mode: ModeBindings,
    cart: CartBindings,
    capture: CaptureBindings,
    door: DoorBindings,
    checkpoint_sound: SoundBindings,
    room_visuals: RoomVisualBindings,
    gate_impact: AnimationBinding,
    gate_debris: AnimationBinding,
}

#[derive(Deserialize)]
struct AnimationBinding {
    graph: String,
    clip: u16,
    alias: Option<u16>,
    #[serde(default)]
    duration: f64,
}

#[derive(Deserialize)]
struct CaptureBindings {
    point_graph: String,
    outline_graph: String,
    point_presenter: u16,
    local_ui: u16,
    outline_appearance: u16,
    waypoint_enum: u64,
    active_headings: [u64; 2],
    scoreboard: u16,
    progress_bindings: Vec<u16>,
}

#[derive(Deserialize)]
struct RoomVisualBindings {
    barrier: String,
    overlay: String,
    sign: String,
    show: u16,
    show_sign: u16,
}

#[derive(Deserialize)]
struct SoundBindings {
    graph: String,
    action: u16,
    effects: [String; 2],
}

#[derive(Deserialize)]
struct ModeBindings {
    receiver_attachment: u16,
    hud_values_attachment: u16,
    hud_values: Vec<u16>,
    common: Vec<u16>,
    receiver_common: Vec<u16>,
    phases: BTreeMap<String, PhaseBindings>,
    waiting_hud_attachment: u16,
    waiting_hud_states: Vec<u16>,
    final_music: u16,
    overtime: Vec<u16>,
    overtime_countdown: u16,
    progress_bindings: Vec<u16>,
    cart_changed_action: u16,
}

#[derive(Deserialize)]
struct PhaseBindings {
    mode: Vec<u16>,
    receiver: Vec<u16>,
    heading: u64,
    defend_heading: Option<u64>,
}

#[derive(Deserialize)]
struct CartBindings {
    graph: String,
    locked: u16,
    unlocking: u16,
    forward: u16,
    reverse: u16,
    automatic: u16,
    result: u16,
    waypoint: u16,
    finish_music: u16,
}

#[derive(Deserialize)]
struct DoorBindings {
    graph: String,
    open_clip: u16,
    close_clip: u16,
    open_alias: u16,
    close_alias: u16,
}

fn bindings() -> &'static Bindings {
    static PROFILE: OnceLock<Bindings> = OnceLock::new();
    PROFILE.get_or_init(|| {
        serde_json::from_str(include_str!("payload_presentation.json"))
            .expect("valid bundled payload bindings")
    })
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

fn graph_ref(graphs: &GraphStore, guid: u64) -> io::Result<&Graph> {
    graph_asset_index(guid)
        .and_then(|index| graphs.graph(index))
        .ok_or_else(|| invalid(format!("missing objective presentation graph {guid:016x}")))
}

fn child<'a>(graphs: &'a GraphStore, parent: &Graph, attachment: u16) -> io::Result<&'a Graph> {
    let guid = parent
        .state(usize::from(attachment))
        .and_then(|node| node.field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph"))
        .and_then(asset_guid)
        .ok_or_else(|| invalid("missing objective presentation attachment"))?;
    graph_ref(graphs, guid)
}

fn mode_root(graphs: &GraphStore, mode: u64) -> io::Result<&Graph> {
    let index = graphs
        .mode_scripts(mode)
        .and_then(|mode| mode.graph)
        .ok_or_else(|| invalid("selected mode has no authored presentation graph"))?;
    graphs
        .graph(index)
        .ok_or_else(|| invalid("missing selected mode graph"))
}

fn configured_graph<'a>(graphs: &'a GraphStore, guid: &str) -> io::Result<&'a Graph> {
    graph_ref(
        graphs,
        crate::accounts::parse_guid(guid)
            .ok_or_else(|| invalid("invalid objective presentation graph reference"))?,
    )
}

fn plain(state: u16) -> (u16, StatePayload) {
    (state, StatePayload::new())
}

fn animation(state: u16, counter: i64, time: f64) -> (u16, StatePayload) {
    (
        state,
        StatePayload::from([
            ("counter".into(), Value::Integer(counter)),
            (
                "time".into(),
                Value::Integer((time * 1000.0).round_ties_even() as i64),
            ),
        ]),
    )
}

fn entities(ids: impl IntoIterator<Item = u32>) -> Value {
    Value::Array(ids.into_iter().map(Value::Entity).collect())
}

fn round_fraction(value: f64) -> Value {
    Value::Float((value * 1000.0).round_ties_even() / 1000.0)
}

fn project_overtime(
    fuse: &OvertimeFuse,
    now: f64,
    entity: &mut Variables,
    instance: &mut Variables,
    states: &mut Vec<(u16, StatePayload)>,
    active_states: &[u16],
    countdown_state: u16,
) {
    let active = fuse.started.is_some();
    let capacity = (fuse.capacity(now) * 4.0).ceil() / 4.0;
    let remaining = ((fuse.remaining(now) * 4.0).ceil() / 4.0).min(capacity);
    entity.insert(OVERTIME_ACTIVE, Value::Bool(active));
    entity.insert(OVERTIME_REMAINING, Value::Float(remaining));
    instance.insert(OVERTIME_CAPACITY, Value::Float(capacity));
    let context = entity
        .entry(ROUND_CONTEXT)
        .or_insert_with(|| Value::Map(Default::default()));
    if let Value::Map(fields) = context {
        fields.push((
            Value::Integer(i64::from(OVERTIME_FRACTION)),
            round_fraction(remaining / capacity),
        ));
    }
    if active {
        states.extend(active_states.iter().copied().map(plain));
        if !fuse.touching {
            states.push((
                countdown_state,
                StatePayload::from([
                    ("cur".into(), Value::Float(remaining)),
                    (
                        "remaining".into(),
                        Value::Integer((remaining * 1000.0).round() as i64),
                    ),
                ]),
            ));
        }
    }
}

pub(crate) fn mode(
    graphs: &GraphStore,
    map: &crate::game_content::GameMap,
    round: &EscortRound,
    phase: MatchPhase,
    assembly_seconds: u32,
    now: f64,
    viewer_team: u8,
) -> io::Result<ProjectedSnapshot> {
    let config = &bindings().mode;
    let phase_bindings = config
        .phases
        .get(phase.name())
        .ok_or_else(|| invalid("unsupported payload phase"))?;
    let root = mode_root(graphs, map.mode_guid)?;
    let (stage, section) = round.stage_progress();
    let cart = MAP_ENTITY_BIT
        | round
            .payload_placeable
            .ok_or_else(|| invalid("missing payload placement"))?;
    let current_goal =
        Value::Entity(MAP_ENTITY_BIT | round.checkpoints[stage - round.stage_offset].1);
    let seconds = match phase {
        MatchPhase::Waiting | MatchPhase::Complete => 0.0,
        MatchPhase::Assemble => f64::from(assembly_seconds),
        _ => hud_clock_seconds(round.deadline - now),
    };
    let heading = if viewer_team == FIRST_DEFENDING_TEAM {
        phase_bindings
            .defend_heading
            .unwrap_or(phase_bindings.heading)
    } else {
        phase_bindings.heading
    };
    let goal_ids: Vec<_> = map
        .objectives
        .iter()
        .filter(|goal| goal.kind == "point")
        .filter_map(|goal| goal.placeable)
        .take(round.stage_offset)
        .chain(round.checkpoints.iter().map(|(_, id)| *id))
        .map(|id| MAP_ENTITY_BIT | id)
        .collect();
    let goals = entities(goal_ids.iter().copied());
    let total_progress = if round.stage_offset == 0 {
        round.mover.distance / round.mover.track.total_length
    } else {
        (stage as f64 + section) / goal_ids.len() as f64
    };
    let candidates = spawn_candidates(map, stage);
    let stage = Value::Integer(stage as i64);
    let phase_number = Value::Integer(phase as i64);
    let attack = Value::Integer(i64::from(FIRST_ATTACKING_TEAM));
    let defend = Value::Integer(i64::from(FIRST_DEFENDING_TEAM));
    let scores = Value::Array(vec![
        Value::Integer(0),
        Value::Integer(round.room_stage() as i64),
    ]);
    let mut entity = Variables::from([
        (59, phase_number.clone()),
        (0x0517, Value::Float(seconds)),
        (
            0x0783,
            if heading == 0 {
                Value::Null
            } else {
                Value::Asset(TEXT_ASSET | heading)
            },
        ),
        (0x088e, Value::Entity(cart)),
        (0x09c3, scores.clone()),
        (0x1edd, Value::Integer(0)),
        (0x2548, Value::Bool(false)),
        (OBJECTIVE_KIND, PAYLOAD_OBJECTIVE),
        (OBJECTIVE_STAGE, stage.clone()),
        (0x2a2b, attack.clone()),
        (0x2a2c, defend),
        (0x2f51, Value::Bool(phase == MatchPhase::Setup)),
        (0x3058, goals.clone()),
        (0x349c, round_fraction(section)),
        (0x4c46, Value::Bool(phase == MatchPhase::Complete)),
        (0x4c47, Value::Bool(phase == MatchPhase::Active)),
        (0x4c48, Value::Bool(phase == MatchPhase::Assemble)),
        (0x4c49, Value::Bool(phase == MatchPhase::Waiting)),
        (
            ROUND_CONTEXT,
            Value::Map(
                [
                    (0x2a2b, attack),
                    (0x0517, Value::Float(seconds)),
                    (OBJECTIVE_STAGE, stage),
                    (0x1edd, Value::Integer(0)),
                    (0x2259, Value::Bool(true)),
                ]
                .into_iter()
                .map(|(id, value)| (Value::Integer(i64::from(id)), value))
                .collect(),
            ),
        ),
    ]);
    if let Some(candidates) = candidates {
        entity.insert(0x8006, candidates);
    }
    if round.stage_offset != 0 {
        entity.insert(0x3101, scores.clone());
        entity.insert(0x34f3, round_fraction(total_progress));
    }
    let mut states: Vec<_> = config
        .common
        .iter()
        .chain(&phase_bindings.mode)
        .copied()
        .map(plain)
        .collect();
    if phase == MatchPhase::Active {
        if round.stage_offset != 0 {
            states.push(plain(bindings().capture.scoreboard));
        }
        if seconds > 0.0 && seconds < 30.0 {
            states.push(plain(config.final_music));
        }
    }
    let mut instance = Variables::from([
        (59, phase_number),
        (0x088e, Value::Entity(cart)),
        (0x09c3, scores),
        (0x1edd, Value::Integer(0)),
        (
            0x1d65,
            round
                .winner
                .map_or(Value::Null, |team| Value::Integer(i64::from(team))),
        ),
        (
            0x26ad,
            if round.stage_offset == 0 {
                current_goal.clone()
            } else {
                Value::Null
            },
        ),
        (OBJECTIVE_KIND, PAYLOAD_OBJECTIVE),
        (0x2937, current_goal),
        (0x34f3, round_fraction(total_progress)),
        (0x4f21, Value::Entity(cart)),
    ]);
    if round.stage_offset != 0 {
        instance.extend([
            (0x0517, Value::Float(seconds)),
            (0x2a36, Value::Integer(round.stage_progress().0 as i64)),
            (0x3058, goals),
            (0x349c, round_fraction(section)),
        ]);
    }
    project_overtime(
        &round.fuse,
        now,
        &mut entity,
        &mut instance,
        &mut states,
        &config.overtime,
        config.overtime_countdown,
    );
    let mut out = ProjectedSnapshot::scene(GAME_MODE_ENTITY, entity);
    out.mount(1, root, None, instance, states)
        .map_err(invalid)?;
    mount_phase_receivers(graphs, root, &mut out, phase)?;
    Ok(out)
}

fn mount_phase_receivers(
    graphs: &GraphStore,
    root: &Graph,
    out: &mut ProjectedSnapshot,
    phase: MatchPhase,
) -> io::Result<()> {
    let config = &bindings().mode;
    let phase_bindings = &config.phases[phase.name()];
    let waiting = if phase == MatchPhase::Waiting {
        let graph = child(graphs, root, config.waiting_hud_attachment)?;
        Some(0x0580_0000_0000_0000 | u64::from(graph.index))
    } else {
        None
    };
    mount_receivers(graphs, root, out, &phase_bindings.receiver, waiting)
}

fn mount_receivers(
    graphs: &GraphStore,
    root: &Graph,
    out: &mut ProjectedSnapshot,
    phase_states: &[u16],
    waiting: Option<u64>,
) -> io::Result<()> {
    let config = &bindings().mode;
    let receiver = child(graphs, root, config.receiver_attachment)?;
    let values_graph = child(graphs, receiver, config.hud_values_attachment)?;
    let receivers = config
        .receiver_common
        .iter()
        .chain(phase_states)
        .map(|&state| {
            let mut payload = StatePayload::new();
            if receiver
                .state(usize::from(state))
                .is_some_and(|node| node.class == "STUStatescriptStateGameMessageEntry")
            {
                payload.insert("stacked".into(), Value::Bool(true));
            }
            (state, payload)
        });
    out.mount(
        2,
        receiver,
        Some((1, config.receiver_attachment)),
        Variables::new(),
        receivers,
    )
    .map_err(invalid)?;
    if let Some(waiting) = waiting {
        let hud = graph_ref(graphs, waiting)?;
        out.mount(
            3,
            hud,
            None,
            Variables::from([
                (0x0783, Value::Asset(TEXT_ASSET | 0x0105)),
                (0x4c49, Value::Bool(true)),
            ]),
            config.waiting_hud_states.iter().copied().map(plain),
        )
        .map_err(invalid)?;
    }
    out.mount(
        5,
        values_graph,
        Some((2, config.hud_values_attachment)),
        Variables::new(),
        config.hud_values.iter().copied().map(plain),
    )
    .map_err(invalid)?;
    Ok(())
}

pub(crate) fn progress_binding(
    graphs: &GraphStore,
    mode: u64,
    capture: bool,
    frame: u32,
    stamp: u32,
) -> io::Result<crate::bits::BitWriter> {
    let config = &bindings().mode;
    let positions = if capture {
        &bindings().capture.progress_bindings
    } else {
        &config.progress_bindings
    };
    crate::script_snapshot_wire::binding_delta(
        mode_root(graphs, mode)?,
        1,
        positions,
        (!capture).then_some(config.cart_changed_action),
        frame,
        stamp,
    )
    .map_err(invalid)
}

pub(crate) fn capture_descriptor(round: &AssaultRound, index: usize) -> ProjectedSnapshot {
    let point = &round.points[index].spec;
    ProjectedSnapshot::scene(
        MAP_ENTITY_BIT | point.placeable,
        Variables::from([
            (OBJECTIVE_KIND, CAPTURE_OBJECTIVE),
            (OBJECTIVE_STAGE, Value::Integer(index as i64)),
            (0x2938, Value::Float(point.round_seconds)),
        ]),
    )
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct CapturePresentation {
    pub(crate) completed: bool,
    pub(crate) active: bool,
    pub(crate) waypoint: bool,
}

impl CapturePresentation {
    pub(crate) fn of(round: &AssaultRound, index: usize, phase: MatchPhase) -> Self {
        Self {
            completed: index < round.captured,
            active: index == round.stage && index >= round.captured && phase == MatchPhase::Active,
            waypoint: round.waypoint(index, phase),
        }
    }
}

pub(crate) fn capture_point(
    graphs: &GraphStore,
    round: &AssaultRound,
    index: usize,
    phase: MatchPhase,
) -> io::Result<ProjectedSnapshot> {
    let config = &bindings().capture;
    let point = &round.points[index];
    let CapturePresentation {
        completed,
        active,
        waypoint,
    } = CapturePresentation::of(round, index, phase);
    let (attackers, defenders) = if active {
        (&round.occupancy.team1[..], &round.occupancy.team0[..])
    } else {
        (&[][..], &[][..])
    };
    let attack = Value::Integer(i64::from(FIRST_ATTACKING_TEAM));
    let defend = Value::Integer(i64::from(FIRST_DEFENDING_TEAM));
    let mut out = ProjectedSnapshot::scene(
        MAP_ENTITY_BIT | point.spec.placeable,
        Variables::from([
            (0x0062, attack.clone()),
            (0x0063, if completed { attack } else { defend.clone() }),
            (0x266c, entities(defenders.iter().copied())),
            (0x266d, entities(attackers.iter().copied())),
            (0x2688, Value::Integer(attackers.len() as i64)),
            (0x2689, Value::Integer(defenders.len() as i64)),
            (0x268a, Value::Float(point.progress)),
            (0x2690, Value::Bool(active && point.contested)),
            (
                0x2694,
                Value::Bool(!attackers.is_empty() && !point.contested),
            ),
            (0x26ac, Value::Float(point.peak)),
            (
                0x28a0,
                entities(
                    round
                        .capturing_bodies
                        .iter()
                        .copied()
                        .filter(|_| index == 0 && completed),
                ),
            ),
            (0x2da4, Value::Bool(true)),
            (OBJECTIVE_KIND, CAPTURE_OBJECTIVE),
            (OBJECTIVE_STAGE, Value::Integer(index as i64)),
            (0x2938, Value::Float(point.spec.round_seconds)),
        ]),
    );
    let graph = configured_graph(graphs, &config.point_graph)?;
    let states = [
        Some(plain(config.point_presenter)),
        waypoint.then(|| plain(config.local_ui)),
    ];
    out.mount(
        1,
        graph,
        None,
        Variables::from([
            (
                0x06d9,
                Value::String(
                    char::from_u32(u32::from('A') + index as u32)
                        .unwrap_or('?')
                        .into(),
                ),
            ),
            (0x1be2, Value::Vector(point.spec.position)),
            (0x2677, Value::Bool(false)),
            (0x268b, Value::Bool(completed)),
            (0x2696, defend),
            (0x27ac, Value::Bool(!active)),
            (0x2f61, Value::Integer(point.spec.segments as i64)),
            (0x2fac, Value::Bool(false)),
            (
                0x3060,
                Value::Float(1.0 / point.spec.segments.max(1) as f64),
            ),
            (0x3067, Value::Float(0.0)),
            (
                0x3f75,
                if waypoint {
                    Value::Asset(config.waypoint_enum)
                } else {
                    Value::Null
                },
            ),
        ]),
        states.into_iter().flatten(),
    )
    .map_err(invalid)?;
    Ok(out)
}

pub(crate) fn capture_outline(
    graphs: &GraphStore,
    entity: u32,
    locked: bool,
) -> io::Result<ProjectedSnapshot> {
    outline(graphs, entity, locked, i64::from(FIRST_DEFENDING_TEAM))
}

pub(crate) fn outline(
    graphs: &GraphStore,
    entity: u32,
    locked: bool,
    owner: i64,
) -> io::Result<ProjectedSnapshot> {
    let config = &bindings().capture;
    let mut out = ProjectedSnapshot::scene(entity, Variables::new());
    out.mount(
        1,
        configured_graph(graphs, &config.outline_graph)?,
        None,
        Variables::from([
            (0x2ec4, Value::Integer(owner)),
            (0x27ac, Value::Bool(locked)),
        ]),
        [plain(config.outline_appearance)],
    )
    .map_err(invalid)?;
    Ok(out)
}

pub(crate) fn capture_mode(
    graphs: &GraphStore,
    map: &crate::game_content::GameMap,
    round: &AssaultRound,
    phase: MatchPhase,
    clock: ObjectiveClock,
    team: u8,
    payload: Option<&EscortRound>,
) -> io::Result<ProjectedSnapshot> {
    let ObjectiveClock {
        now,
        remaining: seconds,
    } = clock;
    let config = &bindings().mode;
    let capture = &bindings().capture;
    let phase_bindings = &config.phases[phase.name()];
    let root = mode_root(graphs, map.mode_guid)?;
    let heading = if phase == MatchPhase::Active {
        capture.active_headings[usize::from(team == FIRST_ATTACKING_TEAM)] + round.stage as u64
    } else if team == FIRST_ATTACKING_TEAM {
        phase_bindings.heading
    } else {
        phase_bindings
            .defend_heading
            .unwrap_or(phase_bindings.heading)
    };
    let stage = Value::Integer(round.stage as i64);
    let phase_number = Value::Integer(phase as i64);
    let attack = Value::Integer(i64::from(FIRST_ATTACKING_TEAM));
    let current = Value::Entity(MAP_ENTITY_BIT | round.current().spec.placeable);
    let goals = entities(
        round
            .points
            .iter()
            .map(|point| point.spec.placeable)
            .chain(
                payload
                    .into_iter()
                    .flat_map(|route| route.checkpoints.iter().map(|(_, id)| *id)),
            )
            .map(|id| MAP_ENTITY_BIT | id),
    );
    let goal_count = round.points.len() + payload.map_or(0, |route| route.checkpoints.len());
    let scores = Value::Array(vec![
        Value::Integer(0),
        Value::Integer(round.captured as i64),
    ]);
    let winner = round
        .winner
        .map_or(Value::Null, |winner| Value::Integer(i64::from(winner)));
    let progress = round.current().progress;
    let mut instance = Variables::from([
        (59, phase_number),
        (0x0517, Value::Float(seconds)),
        (0x09c3, scores.clone()),
        (0x1d65, winner),
        (0x1edd, Value::Integer(0)),
        (0x26ad, current.clone()),
        (OBJECTIVE_KIND, CAPTURE_OBJECTIVE),
        (OBJECTIVE_STAGE, stage.clone()),
        (0x2937, current),
        (0x2a2b, attack.clone()),
        (0x2a36, stage.clone()),
        (0x3058, goals),
        (0x349c, Value::Float(progress)),
    ]);
    let mut entity = instance.clone();
    entity.shift_remove(&0x2a36);
    entity.extend([
        (
            0x0783,
            if heading == 0 {
                Value::Null
            } else {
                Value::Asset(TEXT_ASSET | heading)
            },
        ),
        (0x2548, Value::Bool(false)),
        (0x2a2c, Value::Integer(i64::from(FIRST_DEFENDING_TEAM))),
        (0x2f51, Value::Bool(phase == MatchPhase::Setup)),
        (0x3101, scores),
        (
            0x34f3,
            Value::Float((round.stage as f64 + progress) / goal_count as f64),
        ),
        (0x4c46, Value::Bool(phase == MatchPhase::Complete)),
        (0x4c47, Value::Bool(phase == MatchPhase::Active)),
        (0x4c48, Value::Bool(phase == MatchPhase::Assemble)),
        (0x4c49, Value::Bool(phase == MatchPhase::Waiting)),
        (
            ROUND_CONTEXT,
            Value::Map(
                [
                    (0x2a2b, attack),
                    (0x0517, Value::Float(seconds)),
                    (OBJECTIVE_STAGE, stage),
                    (0x1edd, Value::Integer(0)),
                    (0x2259, Value::Bool(false)),
                ]
                .into_iter()
                .map(|(id, value)| (Value::Integer(i64::from(id)), value))
                .collect(),
            ),
        ),
    ]);
    let mut states: Vec<_> = config
        .common
        .iter()
        .chain(&phase_bindings.mode)
        .copied()
        .map(plain)
        .collect();
    if phase == MatchPhase::Active {
        states.push(plain(capture.scoreboard));
        if seconds > 0.0 && seconds < 30.0 {
            states.push(plain(config.final_music));
        }
    }
    if phase == MatchPhase::Waiting {
        entity.shift_remove(&0x0517);
    }
    if let Some(candidates) = spawn_candidates(map, round.stage) {
        entity.insert(0x8006, candidates);
    }
    project_overtime(
        &round.fuse,
        now,
        &mut entity,
        &mut instance,
        &mut states,
        &config.overtime,
        config.overtime_countdown,
    );
    let mut out = ProjectedSnapshot::scene(GAME_MODE_ENTITY, entity);
    out.mount(1, root, None, instance, states)
        .map_err(invalid)?;
    mount_phase_receivers(graphs, root, &mut out, phase)?;
    Ok(out)
}

fn spawn_candidates(map: &crate::game_content::GameMap, stage: usize) -> Option<Value> {
    let candidates = map.capture_course.as_ref()?.spawn_candidates.get(stage)?;
    candidates.iter().all(|spawns| !spawns.is_empty()).then(|| {
        Value::Array(
            candidates
                .iter()
                .map(|spawns| entities(spawns.iter().map(|id| MAP_ENTITY_BIT | id)))
                .collect(),
        )
    })
}

pub(crate) fn checkpoint_sound(
    graphs: &GraphStore,
    snapshot: &mut ProjectedSnapshot,
    team: u8,
) -> io::Result<(u16, u16)> {
    let config = &bindings().checkpoint_sound;
    let effect =
        crate::accounts::parse_guid(&config.effects[usize::from(team == FIRST_ATTACKING_TEAM)])
            .ok_or_else(|| invalid("invalid checkpoint effect"))?;
    mount_sound_effect(graphs, snapshot, effect)
}

pub(crate) fn mount_sound_effect(
    graphs: &GraphStore,
    snapshot: &mut ProjectedSnapshot,
    effect: u64,
) -> io::Result<(u16, u16)> {
    let config = &bindings().checkpoint_sound;
    let graph = configured_graph(graphs, &config.graph)?;
    snapshot
        .mount(
            CHECKPOINT_SOUND_INSTANCE,
            graph,
            None,
            Variables::from([(0x6ede, Value::Asset(effect))]),
            [],
        )
        .map_err(invalid)?;
    Ok((CHECKPOINT_SOUND_INSTANCE, config.action))
}

pub(crate) fn checkpoints(round: &EscortRound) -> impl Iterator<Item = ProjectedSnapshot> + '_ {
    round
        .checkpoints
        .iter()
        .zip(&round.stage_seconds)
        .enumerate()
        .map(|(stage, ((_, entity), seconds))| {
            ProjectedSnapshot::scene(
                MAP_ENTITY_BIT | entity,
                Variables::from([
                    (OBJECTIVE_KIND, PAYLOAD_OBJECTIVE),
                    (0x2934, Value::Integer((stage + round.stage_offset) as i64)),
                    (0x2938, Value::Float(*seconds)),
                    (
                        0x088e,
                        Value::Entity(
                            MAP_ENTITY_BIT
                                | round
                                    .payload_placeable
                                    .expect("validated payload placement"),
                        ),
                    ),
                ]),
            )
        })
}

pub(crate) fn cart(
    graphs: &GraphStore,
    round: &EscortRound,
    refresh: bool,
    now: f64,
    previous: Option<&ProjectedSnapshot>,
) -> io::Result<ProjectedSnapshot> {
    let config = &bindings().cart;
    let graph = configured_graph(graphs, &config.graph)?;
    let cart = MAP_ENTITY_BIT
        | round
            .payload_placeable
            .ok_or_else(|| invalid("missing payload placement"))?;
    let moved = round.mover.effective_direction();
    let (stage, section) = round.stage_progress();
    let stage = Value::Integer(stage as i64);
    let active = round.phase == MatchPhase::Active && round.unlocked_objective();
    let complete = round.phase == MatchPhase::Complete;
    let unlock_started = round.unlock.as_ref().and_then(|unlock| unlock.starts_at());
    let unlocking = round
        .unlock
        .as_ref()
        .zip(unlock_started)
        .filter(|(unlock, start)| now >= *start && now < *start + unlock.duration)
        .map(|(_, start)| start);
    let mut out = ProjectedSnapshot::scene(
        cart,
        Variables::from([
            (0x009a, round_fraction(section)),
            (0x01b3, Value::Integer(i64::from(moved))),
            (0x01df, Value::Float(round.mover.distance)),
            (0x02bb, Value::Bool(active)),
            (0x1d9f, Value::Float(round.mover.track.total_length)),
            (0x1ec7, Value::Float(1.0)),
            (OBJECTIVE_STAGE, stage.clone()),
        ]),
    );
    let mut states = Vec::new();
    if active {
        states.push(plain(config.waypoint));
    }
    if round.near_finish() {
        states.push(plain(config.finish_music));
    }
    let clip = if complete {
        Some(animation(config.result, 1, 0.0))
    } else if let Some(start) = unlocking {
        Some(
            previous
                .and_then(|old| old.state_payload(1, config.unlocking))
                .map_or_else(
                    || animation(config.unlocking, 1, now - start),
                    |payload| (config.unlocking, payload.clone()),
                ),
        )
    } else if !active || unlock_started.is_some_and(|start| now < start) {
        Some(animation(config.locked, 1, 0.0))
    } else if round.mover.last_step_scripted && round.mover.speed > 0.0 {
        Some(animation(config.automatic, 1, 0.0))
    } else {
        match moved {
            1 => Some(animation(config.forward, 1, 0.0)),
            -1 => Some(animation(config.reverse, 1, 0.0)),
            _ => None,
        }
    };
    states.extend(clip);
    let mut blockers = if active && !round.mover.scripted_movement() {
        round.mover.blockers.clone()
    } else {
        Vec::new()
    };
    let mut pushers = if active {
        round.mover.effective_pushers().to_vec()
    } else {
        Vec::new()
    };
    pushers.sort_unstable();
    blockers.sort_unstable();
    let fractions = if refresh {
        Vec::new()
    } else {
        round
            .checkpoint_fractions()
            .into_iter()
            .map(Value::Float)
            .collect()
    };
    let mut variables = Variables::from([
        (0x0270, Value::Bool(complete)),
        (0x1a9f, entities(pushers)),
        (0x1aa0, entities(blockers)),
        (CHECKPOINT_FRACTIONS, Value::Array(fractions)),
        (OBJECTIVE_STAGE, stage),
        (0x30a6, Value::Integer(round.stage_offset as i64)),
    ]);
    if let Some(unlock) = &round.unlock {
        variables.extend([
            (0x0846, Value::Float(unlock.duration)),
            (
                0x0854,
                Value::Bool(unlock_started.is_some_and(|start| now >= start)),
            ),
            (0x30a7, Value::Integer(round.checkpoints.len() as i64)),
        ]);
    }
    out.mount(1, graph, None, variables, states)
        .map_err(invalid)?;
    let impact = &bindings().gate_impact;
    if let Some(start) = round
        .gate_impact_started
        .filter(|start| !complete && now < start + impact.duration)
    {
        let clip = previous
            .and_then(|old| old.state_payload(2, impact.clip))
            .map_or_else(
                || animation(impact.clip, 1, now - start),
                |payload| (impact.clip, payload.clone()),
            );
        out.mount(
            2,
            configured_graph(graphs, &impact.graph)?,
            None,
            Variables::new(),
            [clip],
        )
        .map_err(invalid)?;
    }
    Ok(out)
}

pub(crate) fn door(
    graphs: &GraphStore,
    entry: &BarrierEntry,
    state: &DoorState,
    now: f64,
) -> io::Result<ProjectedSnapshot> {
    if entry.kind == BarrierKind::GateDebris {
        let config = &bindings().gate_debris;
        let mut out = ProjectedSnapshot::scene(entry.entity, Variables::new());
        let states = (state.pose != DoorPose::Closed)
            .then(|| {
                animation(
                    config.clip,
                    if state.pose == DoorPose::Open { 2 } else { 1 },
                    (now.min(state.ends_at) - state.starts_at).max(0.0),
                )
            })
            .into_iter()
            .chain(
                config
                    .alias
                    .filter(|_| state.pose != DoorPose::Closed)
                    .map(plain),
            );
        out.mount(
            1,
            configured_graph(graphs, &config.graph)?,
            None,
            Variables::new(),
            states,
        )
        .map_err(invalid)?;
        return Ok(out);
    }
    let config = &bindings().door;
    let graph = configured_graph(graphs, &config.graph)?;
    let opened = matches!(state.pose, DoorPose::Opening | DoorPose::Open);
    let moving = matches!(state.pose, DoorPose::Opening | DoorPose::Closing);
    let settled_open = state.pose == DoorPose::Open;
    let mut out = ProjectedSnapshot::scene(
        entry.entity,
        Variables::from([
            (0x0804, Value::Bool(true)),
            (0x22a5, Value::Bool(opened)),
            (0x475b, Value::Bool(settled_open)),
        ]),
    );
    let mut states = Vec::new();
    if state.timed || opened || !state.states.is_empty() {
        let clip = if opened {
            config.open_clip
        } else {
            config.close_clip
        };
        let (counter, time) = if moving {
            (1, (now - state.starts_at).max(0.0))
        } else {
            let duration = if opened {
                entry.open_seconds
            } else {
                entry.close_seconds
            };
            (2, duration)
        };
        states.push(animation(clip, counter, time));
    }
    if !moving {
        states.push(plain(if opened {
            config.open_alias
        } else {
            config.close_alias
        }));
    }
    out.mount(
        1,
        graph,
        None,
        Variables::from([
            (0x07f0, Value::Float(entry.open_seconds)),
            (0x07f3, Value::Float(entry.close_seconds)),
            (0x09cb, Value::Float(entry.open_delay)),
            (0x09cc, Value::Float(entry.close_delay)),
            (0x1b7f, Value::Integer(1)),
            (0x22a4, Value::Bool(opened)),
            (0x22a5, Value::Bool(opened)),
            (0x02bb, Value::Bool(moving)),
            (0x3df1, Value::Bool(settled_open)),
            (0x3df2, Value::Bool(!moving && !settled_open)),
            (0x475b, Value::Bool(settled_open)),
        ]),
        states,
    )
    .map_err(invalid)?;
    Ok(out)
}

pub(crate) fn room_visual(
    graphs: &GraphStore,
    entry: &BarrierEntry,
    team: u8,
) -> io::Result<ProjectedSnapshot> {
    let config = &bindings().room_visuals;
    let visible = || Variables::from([(0x0877, Value::Bool(false)), (0x0878, Value::Bool(false))]);
    let (graph, state, values) = match entry.kind {
        BarrierKind::TeamBarrier => (
            &config.barrier,
            config.show,
            Variables::from([(0x06f1, Value::Integer(i64::from(team)))]),
        ),
        BarrierKind::Overlay => (&config.overlay, config.show, visible()),
        BarrierKind::RestrictedSign => (&config.sign, config.show_sign, visible()),
        _ => return Err(invalid("placement is not a room visual")),
    };
    let graph = configured_graph(graphs, graph)?;
    // 00F6's 0071 branch suppresses the sign. This connection has already
    // passed the room and team filter, so select the enabled sign branch.
    let entity = if entry.kind == BarrierKind::RestrictedSign {
        Variables::from([(0x0786, Value::Bool(false))])
    } else {
        Variables::new()
    };
    let mut out = ProjectedSnapshot::scene(entry.entity, entity);
    out.mount(1, graph, None, values, [plain(state)])
        .map_err(invalid)?;
    Ok(out)
}
