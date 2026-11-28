use super::*;

const KIND: Value = Value::Asset(0x0d80_0000_0000_2fdb);
const SUMMARY: u16 = 4;
const RESULT_INTRO: u16 = 6;

#[derive(Deserialize)]
struct Phase {
    phase: i64,
    #[serde(default)]
    heading: u64,
    mode: Vec<u16>,
    receivers: Vec<u16>,
}

#[derive(Deserialize)]
struct ControlBindings {
    waiting: Phase,
    assemble: Phase,
    setup: Phase,
    unlock: Phase,
    active: Phase,
    round_result: Phase,
    match_result: Phase,
    summary_attachment: u16,
    slow_motion: u16,
    overtime: Vec<u16>,
    overtime_countdown: u16,
    result_intro_graph: String,
    result_intro_state: u16,
    marker: u64,
    assembly_music: u16,
    setup_countdown: [u16; 5],
    unlock_sound: u16,
    capture_sound: u16,
    score_sounds: [u16; 2],
    round_result_actions: [u16; 2],
    intermission_setup_music: u64,
}

fn control_bindings() -> &'static ControlBindings {
    static PROFILE: OnceLock<ControlBindings> = OnceLock::new();
    PROFILE.get_or_init(|| {
        serde_json::from_str(include_str!("control_presentation.json"))
            .expect("valid bundled control bindings")
    })
}

fn submap(goal: &crate::game_content::MapObjective) -> Value {
    goal.submap_name
        .as_deref()
        .and_then(crate::accounts::parse_guid)
        .map_or(Value::Null, Value::Asset)
}

pub(crate) fn point_descriptor(
    round: &ControlRound,
    map: &crate::game_content::GameMap,
    index: usize,
) -> ProjectedSnapshot {
    let point = &round.stages[index].point;
    ProjectedSnapshot::scene(
        MAP_ENTITY_BIT | point.placeable,
        Variables::from([
            (OBJECTIVE_KIND, KIND),
            (OBJECTIVE_STAGE, Value::Integer(index as i64)),
            (0x1ddb, submap(&map.objectives[index])),
        ]),
    )
}

pub(crate) fn point_status(round: &ControlRound, index: usize) -> CapturePresentation {
    CapturePresentation {
        completed: false,
        active: index == round.stage() && round.phase == MatchPhase::Active,
        waypoint: index == round.stage()
            && matches!(
                round.phase,
                MatchPhase::Setup | MatchPhase::Unlock | MatchPhase::Active
            ),
    }
}

pub(crate) fn point(
    graphs: &GraphStore,
    round: &ControlRound,
    map: &crate::game_content::GameMap,
    index: usize,
) -> io::Result<ProjectedSnapshot> {
    let point = &round.stages[index].point;
    let CapturePresentation {
        active, waypoint, ..
    } = point_status(round, index);
    let sides = if active {
        [&round.occupancy.team0[..], &round.occupancy.team1[..]]
    } else {
        [&[][..]; 2]
    };
    let capture_team = if active { round.capturer } else { -1 };
    let (attackers, defenders) = usize::try_from(capture_team).ok().map_or((0, 0), |team| {
        (sides[team].len() as i64, sides[1 - team].len() as i64)
    });
    let owner = if index == round.stage() {
        round.owner
    } else {
        -1
    };
    let mut out = ProjectedSnapshot::scene(
        MAP_ENTITY_BIT | point.placeable,
        Variables::from([
            (0x0062, Value::Integer(i64::from(capture_team))),
            (0x0063, Value::Integer(i64::from(owner))),
            (0x266c, entities(sides[0].iter().copied())),
            (0x266d, entities(sides[1].iter().copied())),
            (0x2688, Value::Integer(attackers)),
            (0x2689, Value::Integer(defenders)),
            (
                0x268a,
                Value::Float(if active { round.progress } else { 0.0 }),
            ),
            (0x2690, Value::Bool(active && round.contested)),
            (0x2694, Value::Bool(active && round.capturing)),
            (0x2da4, Value::Bool(waypoint)),
            (OBJECTIVE_KIND, KIND),
            (OBJECTIVE_STAGE, Value::Integer(index as i64)),
            (0x1ddb, submap(&map.objectives[index])),
        ]),
    );
    let capture = &bindings().capture;
    out.mount(
        1,
        configured_graph(graphs, &capture.point_graph)?,
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
            (0x1be2, Value::Vector(point.position)),
            (0x1ddb, submap(&map.objectives[index])),
            (0x2677, Value::Bool(true)),
            (0x268b, Value::Bool(false)),
            (0x2696, Value::Integer(-1)),
            (0x27ac, Value::Bool(!active)),
            (0x2f61, Value::Integer(0)),
            (
                0x3f75,
                if waypoint {
                    Value::Asset(control_bindings().marker)
                } else {
                    Value::Null
                },
            ),
        ]),
        [
            Some(plain(capture.point_presenter)),
            waypoint.then(|| plain(capture.local_ui)),
        ]
        .into_iter()
        .flatten(),
    )
    .map_err(invalid)?;
    Ok(out)
}

pub(crate) fn clock(round: &ControlRound, assembly_seconds: u32, now: f64) -> f64 {
    match round.phase {
        MatchPhase::Assemble => f64::from(assembly_seconds),
        MatchPhase::Setup | MatchPhase::Unlock => hud_clock_seconds(round.deadline - now),
        _ => 0.0,
    }
}

pub(crate) fn mode(
    graphs: &GraphStore,
    map: &crate::game_content::GameMap,
    round: &ControlRound,
    clock: ObjectiveClock,
    viewer_team: u8,
    previous: (
        Option<&ProjectedSnapshot>,
        Option<&super::super::AudioTimeline>,
    ),
    command_frame: u32,
) -> io::Result<ProjectedSnapshot> {
    let (previous, previous_audio) = previous;
    let ObjectiveClock { now, remaining } = clock;
    let config = control_bindings();
    let phase = round.phase;
    let round_result = round.round_winner.is_some() && round.winner.is_none();
    let final_result = round.winner.is_some();
    let profile = match phase {
        MatchPhase::Waiting => &config.waiting,
        MatchPhase::Assemble => &config.assemble,
        MatchPhase::Setup => &config.setup,
        MatchPhase::Unlock => &config.unlock,
        MatchPhase::Active => &config.active,
        MatchPhase::RoundEnd => &config.round_result,
        MatchPhase::Complete if final_result => &config.match_result,
        MatchPhase::Complete => &config.round_result,
    };
    let mut scores = round.scores.map(|score| {
        if score == 99.99 {
            score
        } else {
            (score * 4.0).floor() / 4.0
        }
    });
    if final_result {
        for (index, score) in scores.iter_mut().enumerate() {
            *score = previous
                .and_then(|snapshot| snapshot.instance_variables(1))
                .and_then(|vars| vars.get(&(0x1330 + index as u32)))
                .and_then(|value| match value {
                    Value::Float(value) => Some(*value),
                    _ => None,
                })
                .unwrap_or_else(|| score.min(99.0));
        }
    }
    let wins = round.round_wins.map(|wins| Value::Integer(i64::from(wins)));
    let total_wins = Value::Integer(i64::from(round.round_wins.iter().sum::<u32>()));
    let round_winner = round
        .round_winner
        .map_or(Value::Null, |team| Value::Integer(i64::from(team)));
    let winner = round
        .winner
        .map_or(Value::Null, |team| Value::Integer(i64::from(team)));
    let stage = round.stage();
    let goal = Value::Entity(MAP_ENTITY_BIT | round.stages[stage].point.placeable);
    let submap = submap(&map.objectives[stage]);
    let hud_owner = Value::Integer(if round_result {
        -1
    } else {
        i64::from(round.owner)
    });
    let progress = Value::Float(
        usize::try_from(round.owner)
            .ok()
            .map_or(0.0, |team| scores[team] / 100.0),
    );
    let mut banners = [0x1846, 0x1847].map(|key| {
        previous
            .and_then(|snapshot| snapshot.entity_variables().get(&key))
            .cloned()
            .unwrap_or(Value::Integer(0))
    });
    if round_result
        && previous_audio.is_some_and(|old| old.phase == MatchPhase::Active && old.stage == stage)
    {
        let winner = round.round_winner.expect("round result has a winner");
        banners[usize::from(viewer_team < 2 && viewer_team != winner)] =
            wins[winner as usize].clone();
    }
    let mut entity = Variables::from([
        (59, Value::Integer(profile.phase)),
        (
            0x0783,
            if profile.heading == 0 {
                Value::Null
            } else {
                Value::Asset(TEXT_ASSET | profile.heading)
            },
        ),
        (0x09c3, Value::Array(wins.to_vec())),
        (0x1332, hud_owner.clone()),
        (0x13e4, wins[0].clone()),
        (0x13e5, wins[1].clone()),
        (0x1846, banners[0].clone()),
        (0x1847, banners[1].clone()),
        (0x1d65, winner.clone()),
        (0x1edd, total_wins.clone()),
        (0x2295, Value::Integer(i64::from(round.points_to_win))),
        (0x26ad, goal.clone()),
        (0x27ac, Value::Bool(phase == MatchPhase::Unlock)),
        (OBJECTIVE_STAGE, Value::Integer(stage as i64)),
        (0x349c, progress.clone()),
        (0x34f3, progress.clone()),
        (
            0x4c73,
            Value::Array(scores.into_iter().map(Value::Float).collect()),
        ),
        (0x4c49, Value::Bool(phase == MatchPhase::Waiting)),
        (0x4c48, Value::Bool(phase == MatchPhase::Assemble)),
        (0x2f51, Value::Bool(phase == MatchPhase::Setup)),
        (
            0x4c47,
            Value::Bool(round_result || matches!(phase, MatchPhase::Unlock | MatchPhase::Active)),
        ),
        (0x4c46, Value::Bool(final_result)),
        (
            ROUND_CONTEXT,
            Value::Map(vec![
                (Value::Integer(0x5af0), hud_owner.clone()),
                (
                    Value::Integer(0x5a8f),
                    Value::Integer(i64::from(round.capturer)),
                ),
                (
                    Value::Integer(0x06da),
                    Value::Bool(round.contested || round.capturing),
                ),
                (Value::Integer(0x59c0), Value::Float(round.progress)),
                (
                    Value::Integer(0x06d9),
                    Value::Asset(TEXT_ASSET | (0x5e3c + stage as u64)),
                ),
                (
                    Value::Integer(0x2a56),
                    Value::Bool(matches!(
                        phase,
                        MatchPhase::Waiting | MatchPhase::Setup | MatchPhase::Unlock
                    )),
                ),
                (Value::Integer(0x2690), Value::Bool(round.contested)),
            ]),
        ),
    ]);
    if phase != MatchPhase::Waiting {
        entity.insert(0x0517, Value::Float(remaining));
    }
    if let Some(candidates) = spawn_candidates(map, stage) {
        entity.insert(0x8006, candidates);
    }
    let mut instance = Variables::from([
        (59, Value::Integer(profile.phase)),
        (0x0517, Value::Float(remaining)),
        (0x09c3, Value::Array(wins.to_vec())),
        (0x1330, Value::Float(scores[0])),
        (0x1331, Value::Float(scores[1])),
        (0x1332, hud_owner),
        (0x13e4, wins[0].clone()),
        (0x13e5, wins[1].clone()),
        (0x1d65, winner),
        (0x1ddb, submap.clone()),
        (0x1e4f, round_winner.clone()),
        (0x1edd, total_wins.clone()),
        (0x2295, Value::Integer(i64::from(round.points_to_win))),
        (
            0x22fc,
            Value::Float(crate::pvp_mode::CONTROL_ROUND_END_MOVEMENT_SCALE),
        ),
        (0x26ad, goal),
        (0x27a2, Value::Integer(stage as i64)),
        (
            0x27b4,
            entities(
                round
                    .stages
                    .iter()
                    .map(|stage| MAP_ENTITY_BIT | stage.point.placeable),
            ),
        ),
        (0x349c, progress),
    ]);
    let mut states: Vec<_> = profile.mode.iter().copied().map(plain).collect();
    let previous_boundary =
        previous.and_then(|snapshot| snapshot.state_payload(1, config.slow_motion));
    let slowing = phase == MatchPhase::RoundEnd;
    let releasing = !slowing
        && previous_boundary
            .is_some_and(|payload| payload.get("which") == Some(&Value::Bool(true)));
    if slowing || releasing {
        let frame = previous_boundary
            .filter(|_| slowing)
            .and_then(|payload| payload.get("value"))
            .cloned()
            .unwrap_or(Value::Integer(i64::from(command_frame)));
        states.push((
            config.slow_motion,
            StatePayload::from([
                ("which".into(), Value::Bool(slowing)),
                ("value".into(), frame),
            ]),
        ));
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

    let root = mode_root(graphs, map.mode_guid)?;
    let mut out = ProjectedSnapshot::scene(GAME_MODE_ENTITY, entity);
    out.mount(1, root, None, instance, states)
        .map_err(invalid)?;
    if releasing {
        out.mark_state_inactive(1, config.slow_motion);
    }
    mount_receivers(
        graphs,
        root,
        &mut out,
        &profile.receivers,
        (phase == MatchPhase::Waiting).then_some(0x0580_0000_0000_1feb),
    )?;
    if phase == MatchPhase::Complete && round_result {
        let graph = child(graphs, root, config.summary_attachment)?;
        out.mount(
            SUMMARY,
            graph,
            Some((1, config.summary_attachment)),
            Variables::from([
                (0x0896, Value::Bool(false)),
                (0x1330, wins[0].clone()),
                (0x1331, wins[1].clone()),
                (0x1d65, round_winner),
                (
                    0x1dc3,
                    Value::Integer(i64::from(round.points_to_win * 2 - 1)),
                ),
                (0x1edd, total_wins),
                (0x1eb0, Value::Bool(false)),
                (0x1eb2, Value::Bool(false)),
                (
                    0x1f3d,
                    Value::Integer(i64::from(round.round_winner == Some(0))),
                ),
                (
                    0x1f3e,
                    Value::Integer(i64::from(round.round_winner == Some(1))),
                ),
                (0x1f42, Value::Bool(true)),
                (0x1f43, Value::Bool(true)),
                (0x1f80, Value::Bool(true)),
                (0x1f8d, submap),
            ]),
            [],
        )
        .map_err(invalid)?;
    }
    if final_result && previous_audio.is_some_and(|old| old.winner.is_none()) {
        let graph = configured_graph(graphs, &config.result_intro_graph)?;
        out.mount(
            RESULT_INTRO,
            graph,
            None,
            Variables::from([
                (
                    0x0918,
                    Value::Map(
                        [
                            (Value::Integer(0x1330), wins[0].clone()),
                            (Value::Integer(0x1331), wins[1].clone()),
                        ]
                        .into_iter()
                        .collect(),
                    ),
                ),
                (
                    0x35d8,
                    Value::Map(
                        [(Value::Integer(0x1c8e), Value::Float(2.5))]
                            .into_iter()
                            .collect(),
                    ),
                ),
            ]),
            [(
                config.result_intro_state,
                StatePayload::from([
                    ("fresh".into(), Value::Bool(true)),
                    ("count".into(), Value::Integer(1)),
                ]),
            )],
        )
        .map_err(invalid)?;
    } else if final_result && previous.is_some_and(|snapshot| snapshot.has_instance(RESULT_INTRO)) {
        // The result layer owns its animation past this transition.
        let graph = configured_graph(graphs, &config.result_intro_graph)?;
        let vars = previous
            .and_then(|snapshot| snapshot.instance_variables(RESULT_INTRO))
            .cloned()
            .unwrap_or_default();
        out.mount(
            RESULT_INTRO,
            graph,
            None,
            vars,
            [(
                config.result_intro_state,
                StatePayload::from([
                    ("fresh".into(), Value::Bool(true)),
                    ("count".into(), Value::Integer(1)),
                ]),
            )],
        )
        .map_err(invalid)?;
    }
    Ok(out)
}

pub(crate) fn setup_music(
    graphs: &GraphStore,
    snapshot: &mut ProjectedSnapshot,
    previous: &ProjectedSnapshot,
    current: &super::super::AudioTimeline,
    old: &super::super::AudioTimeline,
    remaining: f64,
    old_clock: f64,
) -> io::Result<Option<(u16, u16)>> {
    if current.stage == 0
        || current.stage != old.stage
        || current.phase != MatchPhase::Setup
        || old.phase != MatchPhase::Setup
    {
        return Ok(None);
    }
    let start = old_clock > 10.0 && remaining <= 10.0 && remaining > 5.0;
    if start || previous.has_instance(CHECKPOINT_SOUND_INSTANCE) {
        let action = mount_sound_effect(
            graphs,
            snapshot,
            control_bindings().intermission_setup_music,
        )?;
        return Ok(start.then_some(action));
    }
    Ok(None)
}

pub(crate) fn actions(
    old: &super::super::AudioTimeline,
    current: &super::super::AudioTimeline,
    old_view: &ProjectedSnapshot,
    round: &ControlRound,
    remaining: f64,
    old_clock: f64,
) -> Vec<(u16, u16)> {
    let config = control_bindings();
    let mut actions = Vec::new();
    if old.phase == current.phase {
        match current.phase {
            MatchPhase::Assemble if old_clock > 10.0 && remaining <= 10.0 && remaining > 0.0 => {
                actions.push(config.assembly_music)
            }
            MatchPhase::Setup if remaining.ceil() < old_clock.ceil() => {
                if let Some(&position) = (remaining.ceil() as usize)
                    .checked_sub(1)
                    .and_then(|index| config.setup_countdown.get(index))
                {
                    actions.push(position);
                }
            }
            MatchPhase::Unlock if old_clock > 3.0 && remaining <= 3.0 && remaining > 0.0 => {
                actions.push(config.unlock_sound)
            }
            MatchPhase::Active => {
                let old_owner = old_view
                    .instance_variables(1)
                    .and_then(|vars| vars.get(&0x1332));
                if round.owner >= 0 && old_owner != Some(&Value::Integer(i64::from(round.owner))) {
                    actions.push(config.capture_sound);
                }
            }
            _ => {}
        }
    }
    if old.phase == MatchPhase::Active
        && (current.phase == MatchPhase::Active || current.round_winner.is_some())
    {
        if let Ok(owner) = usize::try_from(round.owner) {
            let old_score = old_view
                .instance_variables(1)
                .and_then(|vars| vars.get(&(0x1330 + owner as u32)));
            if matches!(old_score, Some(Value::Float(score)) if round.scores[owner].floor() > score.floor() && round.scores[owner] >= 75.0)
            {
                actions.push(config.score_sounds[owner]);
            }
        }
    }
    if old.phase == MatchPhase::Active && current.round_winner.is_some() && current.winner.is_none()
    {
        actions.extend(config.round_result_actions);
    }
    actions.into_iter().map(|position| (1, position)).collect()
}
