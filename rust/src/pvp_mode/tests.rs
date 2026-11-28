use serde_json::Value;

use super::*;
use crate::room_primitives::{
    ActorSample, BarrierEntry, BarrierKind, DynamicBarrierRegistry, MAP_ENTITY_BIT, ObbTrigger,
    ObbZone, SPECTATOR_TEAM, SplineNode, SplineSpeedCurve, SplineTrack,
};

#[test]
fn custom_start_waits_for_loaded_playing_slots_or_the_host() {
    let now = Duration::from_secs(3600);
    let mut immediate = CustomMatchStart::new(START_IMMEDIATELY);
    immediate.advance(now, [0, 0], &[1, 0]);
    assert_eq!(immediate.presentation(now), (MatchPhase::Waiting, 0));
    immediate.advance(now, [1, 0], &[2, 1]);
    assert_eq!(immediate.presentation(now), (MatchPhase::Assemble, 30));
    assert!(!immediate.request_start());
    assert_eq!(
        immediate.presentation(now + ASSEMBLY_TIME),
        (MatchPhase::Active, 0)
    );

    let mut full = CustomMatchStart::new(START_WHEN_FULL);
    full.advance(now, [2, 0], &[1, 1]);
    assert_eq!(full.presentation(now), (MatchPhase::Waiting, 0));
    full.advance(now, [1, 1], &[1, 1]);
    assert_eq!(full.presentation(now), (MatchPhase::Assemble, 30));
    let mut solo = CustomMatchStart::new(START_WHEN_FULL);
    solo.advance(now, [1, 0], &[1, 0]);
    assert_eq!(solo.presentation(now), (MatchPhase::Assemble, 30));

    for rule in [START_WHEN_FULL, START_MANUALLY] {
        let mut manual = CustomMatchStart::new(rule);
        manual.advance(now, [1, 0], &[1, 1]);
        assert_eq!(manual.presentation(now), (MatchPhase::Waiting, 0));
        assert!(manual.request_start());
        assert!(!manual.request_start());
        assert_eq!(manual.presentation(now), (MatchPhase::Waiting, 0));
        manual.advance(now, [1, 0], &[1, 1]);
        assert_eq!(
            manual.presentation(now + Duration::from_secs(1)),
            (MatchPhase::Assemble, 29)
        );
    }
    let mut manual = CustomMatchStart::new(START_MANUALLY);
    manual.advance(now, [1, 1], &[1, 1]);
    assert_eq!(manual.presentation(now), (MatchPhase::Waiting, 0));
}

fn parse_hex_u64(s: &str) -> u64 {
    u64::from_str_radix(s.trim_start_matches("0x"), 16).unwrap()
}

fn parse_hex_u32(s: &str) -> u32 {
    u32::from_str_radix(s.trim_start_matches("0x"), 16).unwrap()
}

fn f32_3(v: &Value) -> [f64; 3] {
    let a = v.as_array().unwrap();
    [
        a[0].as_f64().unwrap(),
        a[1].as_f64().unwrap(),
        a[2].as_f64().unwrap(),
    ]
}

fn f64_10(v: &Value) -> [f64; 10] {
    let a = v.as_array().unwrap();
    [
        a[0].as_f64().unwrap(),
        a[1].as_f64().unwrap(),
        a[2].as_f64().unwrap(),
        a[3].as_f64().unwrap(),
        a[4].as_f64().unwrap(),
        a[5].as_f64().unwrap(),
        a[6].as_f64().unwrap(),
        a[7].as_f64().unwrap(),
        a[8].as_f64().unwrap(),
        a[9].as_f64().unwrap(),
    ]
}

#[test]
fn contract_custom_mode_start_v1() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/custom-mode-start-v1.json"
    )))
    .unwrap();
    let inp = &fixture["input"];
    let exp = &fixture["expected"];

    let setting_id = inp["setting_id"].as_u64().unwrap() as u32;
    let setting_type = inp["setting_value_type"].as_u64().unwrap() as u32;
    let full = inp["choices"]["full"].as_u64().unwrap() as u32;
    let immediate = inp["choices"]["immediate"].as_u64().unwrap() as u32;
    let manual = inp["choices"]["manual"].as_u64().unwrap() as u32;

    let mut rules = CustomModeRules::default();
    assert_eq!(rules.start_mode(), exp["default"].as_u64().unwrap() as u32);
    for choice in [full, immediate, manual] {
        rules.mode_overrides = vec![SettingRow {
            id: setting_id,
            value_type: setting_type,
            raw: choice,
        }];
        assert_eq!(rules.start_mode(), choice);
    }
    rules.map_overrides = vec![
        SettingRow {
            id: setting_id,
            value_type: setting_type,
            raw: 100,
        },
        SettingRow {
            id: setting_id,
            value_type: setting_type,
            raw: immediate,
        },
    ];
    assert_eq!(rules.start_mode(), immediate);

    let p = &inp["payload_speed_rule"];
    let ptype = p["type"].as_u64().unwrap() as u32;
    let map_raw = p["map"].as_u64().unwrap() as u32;
    let mode_raw = p["mode"].as_u64().unwrap() as u32;
    let invalid_raw = p["invalid"].as_u64().unwrap() as u32;
    let speeds = exp["payload_speed_scales"].as_array().unwrap();
    let (want_map, want_mode, want_def) = (
        speeds[0].as_f64().unwrap(),
        speeds[1].as_f64().unwrap(),
        speeds[2].as_f64().unwrap(),
    );

    for rule_id in [
        p["id"].as_u64().unwrap() as u32,
        inp["capture_speed_id"].as_u64().unwrap() as u32,
    ] {
        let mut r = CustomModeRules::default();
        assert_eq!(r.objective_rate(rule_id), want_def);
        r.mode_overrides = vec![SettingRow {
            id: rule_id,
            value_type: ptype,
            raw: mode_raw,
        }];
        r.map_overrides = vec![SettingRow {
            id: rule_id,
            value_type: ptype,
            raw: invalid_raw,
        }];
        assert_eq!(r.objective_rate(rule_id), want_mode);
        r.map_overrides.push(SettingRow {
            id: rule_id,
            value_type: ptype,
            raw: map_raw,
        });
        assert_eq!(r.objective_rate(rule_id), want_map);
    }

    let cstw = &inp["control_score_to_win"];
    let c_rules = CustomModeRules {
        map_overrides: vec![SettingRow {
            id: cstw["id"].as_u64().unwrap() as u32,
            value_type: cstw["type"].as_u64().unwrap() as u32,
            raw: cstw["rounds"].as_u64().unwrap() as u32,
        }],
        mode_overrides: Vec::new(),
    };
    assert_eq!(
        c_rules.control_settings().points_to_win,
        Some(cstw["rounds"].as_u64().unwrap() as u32)
    );

    assert_eq!(
        CustomModeRules::practice_start_automatically(),
        exp["practice_start_automatically"].as_bool().unwrap()
    );
    assert_eq!(
        CustomModeRules::spectators_fill_playing_slots(),
        exp["spectators_fill_playing_slots"].as_bool().unwrap()
    );
    let allowed: Vec<u32> = exp["manual_override_while_waiting"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap() as u32)
        .collect();
    for choice in [full, immediate, manual] {
        assert_eq!(
            CustomModeRules::can_manual_start(Some(choice), false),
            allowed.contains(&choice)
        );
        assert!(!CustomModeRules::can_manual_start(Some(choice), true));
    }
}

#[test]
fn contract_escort_objective_placeables_v1() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/escort-objective-placeables-v1.json"
    )))
    .unwrap();
    let inp = &fixture["input"];
    let exp = &fixture["expected"];
    let escort_mode = parse_hex_u64(inp["escort_mode"].as_str().unwrap());
    let other_mode = parse_hex_u64(inp["other_mode"].as_str().unwrap());
    let cat = &inp["catalog"];

    let to_u32_vec = |arr: &Value| -> Vec<u32> {
        arr.as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_u64().unwrap() as u32)
            .collect()
    };

    let mut by_mode = BTreeMap::new();
    for (k, v) in cat["placeables_by_mode"].as_object().unwrap() {
        by_mode.insert(parse_hex_u64(k), to_u32_vec(v));
    }
    let full_cat = ModePlaceablesCatalog {
        base_placeables: to_u32_vec(&cat["placeables"]),
        placeables_by_mode: by_mode.clone(),
        mirrored: to_u32_vec(&cat["mirrored"]).into_iter().collect(),
        objective_placeables: cat["objectives"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|o| matches!(o["kind"].as_str(), Some("payload" | "checkpoint")))
            .map(|o| o["placeable"].as_u64().unwrap() as u32)
            .collect(),
        health_pack_placeables: cat["health_packs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|h| h["placeable"].as_u64().unwrap() as u32)
            .collect(),
        scene_placeables: to_u32_vec(&cat["escort_scene_placeables"]),
    };
    assert_eq!(
        full_cat.resolve(escort_mode, true),
        to_u32_vec(&exp["escort_placeables"])
    );
    assert_eq!(
        full_cat.resolve(other_mode, false),
        to_u32_vec(&exp["other_placeables"])
    );

    let sc = &inp["spawn_candidates"];
    let initial: Vec<(u8, Option<u32>)> = sc["initial"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            (
                row[0].as_u64().unwrap() as u8,
                row[1].as_u64().map(|v| v as u32),
            )
        })
        .collect();
    let later: Vec<(u8, Option<u32>)> = sc["later"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            (
                row[0].as_u64().unwrap() as u8,
                row[1].as_u64().map(|v| v as u32),
            )
        })
        .collect();
    let got_init = active_spawn_candidates(&initial);
    assert_eq!(
        got_init[0],
        to_u32_vec(&exp["spawn_candidates"]["initial"][0])
    );
    assert_eq!(
        got_init[1],
        to_u32_vec(&exp["spawn_candidates"]["initial"][1])
    );

    let _later_res = active_spawn_candidates(&later);
    assert_eq!(exp["spawn_candidates"]["variable"], "0x8006");
}

#[test]
fn contract_escort_round_v1() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/escort-round-v1.json"
    )))
    .unwrap();
    let inp = &fixture["input"];
    let exp = &fixture["expected"];

    let goals: Vec<u32> = inp["checkpoints"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap() as u32)
        .collect();
    let stage_seconds: Vec<f64> = inp["stage_seconds"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_f64().unwrap())
        .collect();
    let assembly_ends = inp["assembly_ends"].as_f64().unwrap();

    let mut nodes = Vec::new();
    for i in 0..=3 {
        let mut n = SplineNode::simple([(i as f64) * 14.0, 0.0, 0.0]);
        if i < 3 {
            n.curve = Some([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [14.0, 0.0, 0.0]]);
        }
        if i > 0 {
            n.checkpoint = Some(goals[i - 1]);
        }
        nodes.push(n);
    }
    let track = SplineTrack::from_nodes(nodes);

    let mut round = EscortRound::new(
        track,
        SplineSpeedCurve::default(),
        DynamicBarrierRegistry::default(),
        stage_seconds.clone(),
        false,
    );
    round.start_setup(assembly_ends);

    for (step, want) in inp["steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["steps"].as_array().unwrap())
    {
        let now = step["now"].as_f64().unwrap();
        let actors: Vec<ActorSample> = step["players"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| {
                ActorSample::living(
                    p[0].as_u64().unwrap() as u32,
                    p[1].as_u64().unwrap() as u8,
                    f32_3(&p[2]),
                )
            })
            .collect();
        round.advance(now, &actors);
        assert_eq!(round.phase.name(), want["phase"].as_str().unwrap());
        assert_eq!(round.stage, want["stage"].as_u64().unwrap() as usize);
        assert!((round.mover.distance - want["distance"].as_f64().unwrap()).abs() < 1e-4);
        assert_eq!(
            round.mover.contested(),
            want["contested"].as_bool().unwrap()
        );
        assert_eq!(round.winner.map(u64::from), want["winner"].as_u64());
    }

    // Verify clock samples
    for (sample, want) in inp["clock_samples"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["clock_samples"].as_array().unwrap())
    {
        let got = hud_clock_seconds(sample.as_f64().unwrap());
        assert!((got - want.as_f64().unwrap()).abs() < 1e-6);
    }

    // Verify scripted route + gate presentation
    let scripted = &inp["scripted_route"];
    let mut s_nodes = Vec::new();
    for (i, n) in scripted["nodes"].as_array().unwrap().iter().enumerate() {
        let mut sn = SplineNode::simple([(i as f64) * 7.0, 0.0, 0.0]);
        if i + 1 < scripted["nodes"].as_array().unwrap().len() {
            sn.curve = Some([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [7.0, 0.0, 0.0]]);
        }
        sn.wait_seconds = n["wait_seconds"].as_f64().unwrap_or(0.0);
        sn.open_gate = n["open_gate"].as_u64().map(|v| v as u32);
        sn.close_gate = n["close_gate"].as_u64().map(|v| v as u32);
        sn.gate_impact = n["gate_impact"].as_bool().unwrap_or(false);
        sn.auto_drive_to = n["auto_drive_to"].as_u64().map(|v| v as usize);
        sn.checkpoint = n["checkpoint"].as_u64().map(|v| v as u32);
        s_nodes.push(sn);
    }
    let mut barriers = Vec::new();
    for g in scripted["gates"].as_array().unwrap() {
        let mut b = BarrierEntry::new(
            g["placeable"].as_u64().unwrap() as u32,
            BarrierKind::CheckpointGate,
            vec![(1, 1)],
        );
        b.gate_tag = g["gate_tag"].as_u64().map(|v| v as u32);
        b.open_seconds = g["open_seconds"].as_f64().unwrap_or(2.0);
        b.close_seconds = g["close_seconds"].as_f64().unwrap_or(2.0);
        b.close_delay = g["close_delay"].as_f64().unwrap_or(0.0);
        barriers.push(b);
    }
    let mut s_round = EscortRound::new(
        SplineTrack::from_nodes(s_nodes),
        SplineSpeedCurve::default(),
        DynamicBarrierRegistry::new(barriers),
        stage_seconds,
        false,
    );
    s_round.start_setup(assembly_ends);
    let start = s_round.deadline;
    s_round.advance(start, &[]);
    for want in exp["scripted_route"].as_array().unwrap() {
        let now = start + want["time"].as_f64().unwrap();
        let pos = s_round.mover.pose().position;
        let actors = match want["players"].as_str().unwrap() {
            "attack" => vec![ActorSample::living(101, 1, pos)],
            "defense" => vec![ActorSample::living(102, 0, pos)],
            "both" => vec![
                ActorSample::living(101, 1, pos),
                ActorSample::living(102, 0, pos),
            ],
            _ => Vec::new(),
        };
        s_round.advance(now, &actors);
        assert!((s_round.mover.distance - want["distance"].as_f64().unwrap()).abs() < 1e-4);
        assert_eq!(s_round.stage, want["stage"].as_u64().unwrap() as usize);
        assert_eq!(
            s_round.mover.effective_pushers().len(),
            want["pushers"].as_u64().unwrap() as usize
        );
        assert_eq!(
            s_round.mover.effective_direction() as i64,
            want["direction"].as_i64().unwrap()
        );
    }
}

#[test]
fn contract_assault_round_v1() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/assault-round-v1.json"
    )))
    .unwrap();
    let inp = &fixture["input"];
    let exp = &fixture["expected"];
    let segs = fixture["presentation"]["capture_segments"]
        .as_u64()
        .unwrap() as usize;
    let waypoint = fixture["presentation"]["waypoint_type"].as_str().unwrap();
    let waypoint = u64::from_str_radix(waypoint.trim_start_matches("0x"), 16).unwrap();
    let mut wire = crate::bits::BitWriter::new();
    crate::script_wire::write_value(
        &mut wire,
        &crate::script_wire::Value::Asset(waypoint),
        crate::script_wire::ValueEncoding::Compact,
    )
    .unwrap();
    let mut read = crate::bits::BitReader::new(wire.as_bytes());
    assert_eq!(read.bits(4).unwrap(), 9);
    assert_eq!(read.bits(1).unwrap(), 0);
    let bytes: Vec<u8> =
        serde_json::from_value(fixture["presentation"]["waypoint_mask_bytes"].clone()).unwrap();
    assert_eq!(read.raw_bits(bytes.len() * 8).unwrap(), bytes);

    let specs: Vec<CapturePointSpec> = inp["points"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| CapturePointSpec {
            placeable: p["placeable"].as_u64().unwrap() as u32,
            position: f32_3(&p["position"]),
            zone: ObbZone::new(vec![ObbTrigger::from_raw(f64_10(&p["box"]))]),
            round_seconds: p["round_seconds"].as_f64().unwrap(),
            capture_seconds: p["capture_seconds"].as_f64().unwrap(),
            segments: segs,
        })
        .collect();

    let actor_map = inp["occupants"].as_object().unwrap();
    let build_actors = |names: &Value| -> Vec<ActorSample> {
        names
            .as_array()
            .unwrap()
            .iter()
            .map(|n| {
                let row = &actor_map[n.as_str().unwrap()];
                ActorSample::living(
                    row[0].as_u64().unwrap() as u32,
                    row[1].as_u64().unwrap() as u8,
                    f32_3(&row[2]),
                )
            })
            .collect()
    };

    let mut round = AssaultRound::new(specs.clone(), 1.0);
    round.start_setup(inp["assembly_ends"].as_f64().unwrap());
    for (step, want) in inp["capture_steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["capture_steps"].as_array().unwrap())
    {
        let now = step[0].as_f64().unwrap();
        let actors = build_actors(&step[1]);
        round.advance(now, &actors);
        assert_eq!(round.phase.name(), want[0].as_str().unwrap());
        assert_eq!(round.stage, want[1].as_u64().unwrap() as usize);
        assert!((round.current().progress - want[2].as_f64().unwrap()).abs() < 1e-4);
        assert_eq!(round.current().contested, want[3].as_bool().unwrap());
        assert!((round.deadline - want[4].as_f64().unwrap()).abs() < 1e-4);
        assert_eq!(round.winner.map(u64::from), want[5].as_u64());
    }

    let mut m_round = AssaultRound::new(specs.clone(), 1.0);
    m_round.start_setup(inp["assembly_ends"].as_f64().unwrap());
    for (step, want) in inp["milestone_steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["milestone_steps"].as_array().unwrap())
    {
        m_round.advance(step[0].as_f64().unwrap(), &build_actors(&step[1]));
        let cur = m_round.current();
        assert_eq!(m_round.stage, want[0].as_u64().unwrap() as usize);
        assert!((cur.progress - want[1].as_f64().unwrap()).abs() < 1e-4);
        assert!((cur.peak - want[2].as_f64().unwrap()).abs() < 1e-4);
        assert_eq!(cur.captured_segments(), want[3].as_u64().unwrap() as usize);
    }

    let mut rate_round = AssaultRound::new(specs.clone(), 1.0);
    rate_round.start_setup(inp["assembly_ends"].as_f64().unwrap());
    for (step, wanted) in inp["rate_steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["rate_progress"].as_array().unwrap())
    {
        rate_round.advance(step[0].as_f64().unwrap(), &build_actors(&step[1]));
        assert!((rate_round.current().progress - wanted.as_f64().unwrap()).abs() < 1e-6);
    }

    let mut o_round = AssaultRound::new(specs, 1.0);
    o_round.start_setup(inp["assembly_ends"].as_f64().unwrap());
    o_round.advance(75.0, &[]);
    for (step, want) in inp["overtime_steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["overtime_steps"].as_array().unwrap())
    {
        o_round.advance(step[0].as_f64().unwrap(), &build_actors(&step[1]));
        assert_eq!(o_round.overtime, want[0].as_bool().unwrap());
        assert_eq!(o_round.winner.map(u64::from), want[1].as_u64());
    }
}

#[test]
fn contract_objective_overtime() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/objective-overtime-v1.json"
    )))
    .unwrap();
    let input = &fixture["input"];
    let expected = &fixture["expected"];
    let deadline = input["deadline"].as_f64().unwrap();
    let timer_window = Some((deadline, deadline));
    for (touch, enters) in input["touches"]
        .as_array()
        .unwrap()
        .iter()
        .zip(expected["timed_enters"].as_array().unwrap())
    {
        let mut fuse = OvertimeFuse::default();
        fuse.advance(touch.as_f64().unwrap(), true, None);
        fuse.advance(deadline, false, timer_window);
        assert_eq!(fuse.started.is_some(), enters.as_bool().unwrap());
    }
    let mut late = OvertimeFuse::default();
    late.advance(deadline + 0.01, true, timer_window);
    assert!(late.started.is_none());

    let mut fuse = OvertimeFuse::default();
    for (at, grace) in input["decay_samples"]
        .as_array()
        .unwrap()
        .iter()
        .zip(expected["delay_then_held_fuse"].as_array().unwrap())
    {
        let at = at.as_f64().unwrap();
        fuse.advance(at, true, timer_window);
        assert!((fuse.remaining(at) - grace.as_f64().unwrap()).abs() < 1e-6);
    }
    fuse.advance(145.5, false, timer_window);
    assert!((fuse.remaining(145.5) - 1.0).abs() < 1e-6);
    let mut refreshed = fuse.clone();
    refreshed.advance(146.25, true, timer_window);
    assert!((refreshed.remaining(146.25) - 1.5).abs() < 1e-6);
    fuse.advance(146.5, true, timer_window);
    assert_eq!(fuse.remaining(146.5), 0.0);
}

#[test]
fn contract_hybrid_round_v1() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/hybrid-round-v1.json"
    )))
    .unwrap();
    let inp = &fixture["input"];
    let exp = &fixture["expected"];
    let pt = &inp["point"];
    let cart = &inp["cart"];
    let cps = inp["checkpoints"].as_array().unwrap();

    let cap = CapturePointStage::new(
        CapturePointSpec {
            placeable: pt["placement"].as_u64().unwrap() as u32,
            position: f32_3(&pt["position"]),
            zone: ObbZone::new(vec![ObbTrigger::from_raw([
                0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 3.0, 2.0, 3.0,
            ])]),
            round_seconds: pt["seconds"].as_f64().unwrap(),
            capture_seconds: pt["capture_seconds"].as_f64().unwrap(),
            segments: 3,
        },
        1.0,
    );
    let mut n0 = SplineNode::simple(f32_3(&cart["position"]));
    n0.curve = Some([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [14.0, 0.0, 0.0]]);
    let mut n1 = SplineNode::simple([cps[0][1].as_f64().unwrap(), 0.0, 0.0]);
    n1.curve = Some([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [14.0, 0.0, 0.0]]);
    n1.checkpoint = Some(cps[0][0].as_u64().unwrap() as u32);
    let mut n2 = SplineNode::simple([cps[1][1].as_f64().unwrap(), 0.0, 0.0]);
    n2.checkpoint = Some(cps[1][0].as_u64().unwrap() as u32);
    let escort = EscortRound::new(
        SplineTrack::from_nodes(vec![n0, n1, n2]),
        SplineSpeedCurve::default(),
        DynamicBarrierRegistry::new(vec![{
            let mut b = BarrierEntry::new(
                inp["gate"].as_u64().unwrap() as u32,
                BarrierKind::CheckpointGate,
                vec![(1, 1)],
            );
            b.payload_unlock = true;
            b
        }]),
        vec![cps[0][2].as_f64().unwrap(), cps[1][2].as_f64().unwrap()],
        false,
    );

    let mut hybrid = HybridRound::new(
        cap,
        escort,
        cart["unlock_delay"].as_f64().unwrap(),
        cart["unlock_seconds"].as_f64().unwrap(),
    );
    hybrid.start_setup(inp["assembly_ends"].as_f64().unwrap());
    let mut timeout = hybrid.clone();
    for (step, want) in inp["capture_timeout_steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["capture_timeout"].as_array().unwrap())
    {
        let point = f32_3(&pt["position"]);
        let contest = [
            ActorSample::living(100, 1, point),
            ActorSample::living(101, 0, point),
        ];
        let actors = if step[1] == "contested" {
            &contest[..]
        } else {
            &[]
        };
        timeout.advance(step[0].as_f64().unwrap(), actors);
        assert_eq!(timeout.escort.overtime, want[0].as_bool().unwrap());
        assert_eq!(timeout.escort.winner.map(u64::from), want[1].as_u64());
        assert!(!timeout.escort.unlocked_objective());
    }

    for (step, want) in inp["steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["steps"].as_array().unwrap())
    {
        let now = step[0].as_f64().unwrap();
        let p_pos = f32_3(&pt["position"]);
        let c_pos = hybrid.escort.mover.pose().position;
        let actors = match step[1].as_str().unwrap() {
            "contested" => vec![
                ActorSample::living(100, 1, p_pos),
                ActorSample::living(101, 0, p_pos),
            ],
            "point" => vec![ActorSample::living(100, 1, p_pos)],
            "cart" => vec![ActorSample::living(100, 1, c_pos)],
            _ => Vec::new(),
        };
        hybrid.advance(now, &actors);
        assert_eq!(hybrid.escort.phase.name(), want[0].as_str().unwrap());
        assert_eq!(hybrid.room_stage(), want[1].as_u64().unwrap() as usize);
        assert!((hybrid.escort.deadline - want[2].as_f64().unwrap()).abs() < 1e-4);
        assert!((hybrid.escort.mover.distance - want[3].as_f64().unwrap()).abs() < 1e-3);
        let gate = &hybrid.escort.barriers.entries[0];
        assert_eq!(
            hybrid
                .escort
                .barriers
                .door_open(gate.entity, hybrid.room_stage(), true),
            want[4].as_bool().unwrap()
        );
        assert_eq!(hybrid.payload_active(), want[5].as_bool().unwrap());
        assert_eq!(hybrid.escort.winner.map(u64::from), want[6].as_u64());
    }
}

#[test]
fn contract_control_round_v1() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/control-round-v1.json"
    )))
    .unwrap();
    let inp = &fixture["input"];
    let exp = &fixture["expected"];
    let half = f32_3(&inp["area_half_extents"]);
    let room_boxes: Vec<ObbTrigger> = inp["room"]["boxes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|b| ObbTrigger::from_raw(f64_10(b)))
        .collect();

    let stages: Vec<ControlStageSpec> = inp["points"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(i, p)| {
            let pos = f32_3(p);
            ControlStageSpec {
                point: CapturePointSpec {
                    placeable: (20 + i) as u32,
                    position: pos,
                    zone: ObbZone::new(vec![ObbTrigger::from_raw([
                        0.0, 0.0, 0.0, 1.0, pos[0], pos[1], pos[2], half[0], half[1], half[2],
                    ])]),
                    round_seconds: 0.0,
                    capture_seconds: 8.0,
                    segments: 3,
                },
                healing_rooms: if i == 1 {
                    [Some(ObbZone::new(room_boxes.clone())), None]
                } else {
                    [None, None]
                },
            }
        })
        .collect();

    let mut ctrl = ControlRound::new(
        stages,
        vec![0, 1, 2],
        2,
        1.0,
        1.0,
        None,
        DynamicBarrierRegistry::default(),
    );
    ctrl.start_setup(inp["setup_starts"].as_f64().unwrap());

    for (step, want) in inp["opening"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["opening"].as_array().unwrap())
    {
        let now = step[0].as_f64().unwrap();
        let pt = ctrl.stages[ctrl.stage()].point.position;
        let actors: Vec<ActorSample> = step[1]
            .as_array()
            .unwrap()
            .iter()
            .enumerate()
            .map(|(i, t)| ActorSample::living((i + 1) as u32, t.as_u64().unwrap() as u8, pt))
            .collect();
        ctrl.advance(now, &actors);
        assert_eq!(ctrl.phase.name(), want[0].as_str().unwrap());
        assert_eq!(ctrl.owner as i64, want[1].as_i64().unwrap());
        assert!((ctrl.progress - want[2].as_f64().unwrap()).abs() < 1e-3);
        assert!((ctrl.scores[0] - want[3].as_f64().unwrap()).abs() < 1e-2);
        assert!((ctrl.scores[1] - want[4].as_f64().unwrap()).abs() < 1e-2);
        assert_eq!(ctrl.contested, want[5].as_bool().unwrap());
        assert_eq!(ctrl.overtime, want[6].as_bool().unwrap());
    }

    let order = control_stage_order(3, inp["score_to_win"]["setting"].as_u64().unwrap() as u32);
    assert_eq!(order.len(), 5);
    assert_eq!(
        order[..3].iter().copied().collect::<BTreeSet<_>>(),
        BTreeSet::from([0, 1, 2])
    );
    assert!(order.windows(2).all(|rounds| rounds[0] != rounds[1]));

    let mut neutral = ctrl.clone();
    neutral.next_round(0.0);
    neutral.advance(60.0, &[]);
    let point = neutral.stages[neutral.stage()].point.position;
    for (step, expected) in inp["neutral_retake"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["neutral_retake"].as_array().unwrap())
    {
        let actors: Vec<_> = step[1]
            .as_array()
            .unwrap()
            .iter()
            .enumerate()
            .map(|(id, team)| {
                ActorSample::living(id as u32 + 1, team.as_u64().unwrap() as u8, point)
            })
            .collect();
        neutral.advance(step[0].as_f64().unwrap(), &actors);
        assert_eq!(i64::from(neutral.capturer), expected[0].as_i64().unwrap());
        assert!((neutral.progress - expected[1].as_f64().unwrap()).abs() < 1e-6);
    }

    let overtime: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/objective-overtime-v1.json"
    )))
    .unwrap();
    for (score, enters) in overtime["input"]["control_touch_scores"]
        .as_array()
        .unwrap()
        .iter()
        .zip(overtime["expected"]["control_enters"].as_array().unwrap())
    {
        let mut round = ctrl.clone();
        round.next_round(0.0);
        round.advance(60.0, &[]);
        round.owner = 0;
        round.score_scale = 1.0;
        let point = round.stages[round.stage()].point.position;
        let contest = [
            ActorSample::living(101, 0, point),
            ActorSample::living(102, 1, point),
        ];
        let touch = 60.0 + score.as_f64().unwrap() * 1.2;
        round.advance(touch, &contest);
        round.advance(179.0, &[]);
        assert_eq!(
            round.overtime,
            enters.as_bool().unwrap(),
            "touch at {score}%"
        );
    }

    let mut fuse = ctrl.clone();
    fuse.next_round(0.0);
    fuse.advance(60.0, &[]);
    fuse.owner = 0;
    fuse.scores[0] = 99.0;
    let point = fuse.stages[fuse.stage()].point.position;
    let contested = [
        ActorSample::living(101, 0, point),
        ActorSample::living(102, 1, point),
    ];
    let start = 118.8;
    fuse.last_update = start;
    fuse.advance(start, &contested);
    assert_eq!(fuse.scores[0], 99.99);
    for (time, expected) in inp["overtime_hold"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["overtime_grace"].as_array().unwrap())
    {
        let now = time.as_f64().unwrap();
        fuse.advance(now, &contested);
        assert!(fuse.overtime);
        assert!((fuse.fuse.remaining(now) - expected.as_f64().unwrap()).abs() < 1e-6);
    }
    fuse.advance(150.29, &[]);
    assert!(fuse.overtime);
    fuse.advance(150.31, &[]);
    assert_eq!(fuse.round_winner, Some(0));

    // Verify control room panel fitting
    let rp = &inp["room_panel"];
    let rpe = &exp["room_panel"];
    let (pos, scale) = fit_control_room_panel(
        f32_3(&rp["position"]),
        f32_3(&rp["scale"]),
        f32_3(&rp["rim_position"]),
        [0.0, 0.0, 0.0, 1.0],
        f32_3(&rp["rim_scale"]),
        [2.7946, 2.8287],
        4.0,
    );
    let want_pos = f32_3(&rpe["position"]);
    let want_scale = f32_3(&rpe["scale"]);
    for i in 0..3 {
        assert!((f64::from(pos[i]) - want_pos[i]).abs() < 1e-4);
        assert!((f64::from(scale[i]) - want_scale[i]).abs() < 1e-4);
    }

    // Verify custom rules for control
    let cr = &inp["custom_rules"];
    let map_ov: Vec<SettingRow> = cr["map"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| SettingRow {
            raw: r[0].as_u64().unwrap() as u32,
            id: r[1].as_u64().unwrap() as u32,
            value_type: r[2].as_u64().unwrap() as u32,
        })
        .collect();
    let mode_ov: Vec<SettingRow> = cr["mode"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| SettingRow {
            raw: r[0].as_u64().unwrap() as u32,
            id: r[1].as_u64().unwrap() as u32,
            value_type: r[2].as_u64().unwrap() as u32,
        })
        .collect();
    let settings = CustomModeRules {
        map_overrides: map_ov,
        mode_overrides: mode_ov,
    }
    .control_settings();
    assert_eq!(
        settings.capture_scale(),
        exp["custom_rules"]["capture_scale"].as_f64().unwrap()
    );
    assert_eq!(
        settings.score_scale(),
        exp["custom_rules"]["score_scale"].as_f64().unwrap()
    );
    assert_eq!(
        settings.fixed_stage,
        Some(exp["custom_rules"]["fixed_stage"].as_u64().unwrap() as usize)
    );
}

#[test]
fn contract_match_audio_v1() {
    let fixture: Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/match-audio-v1.json"
    )))
    .unwrap();
    let inp = &fixture["input"];
    let exp = &fixture["expected"];

    let parse_phase = |s: &str| match s {
        "WAITING" => MatchPhase::Waiting,
        "ASSEMBLE" => MatchPhase::Assemble,
        "SETUP" => MatchPhase::Setup,
        "ACTIVE" => MatchPhase::Active,
        "UNLOCK" => MatchPhase::Unlock,
        "COMPLETE" => MatchPhase::Complete,
        "ROUND_END" => MatchPhase::RoundEnd,
        _ => panic!("unknown phase {s}"),
    };

    for (team, key) in [(0u8, "escort_defense"), (1u8, "escort_attack")] {
        let mut prev: Option<AudioTimeline> = None;
        for (row, want) in inp["escort"]
            .as_array()
            .unwrap()
            .iter()
            .zip(exp[key].as_array().unwrap())
        {
            let cur = AudioTimeline {
                phase: parse_phase(row["phase"].as_str().unwrap()),
                seconds: row["seconds"].as_i64().unwrap(),
                stage: row["stage"].as_u64().unwrap_or(0) as usize,
                overtime: row["overtime"].as_bool().unwrap_or(false),
                winner: row["winner"].as_u64().map(|v| v as u8),
                near_finish: row["near_finish"].as_bool().unwrap_or(false),
                control_wins: None,
                round_winner: None,
            };
            let cues: Vec<String> = match_speech(prev.as_ref(), &cur, MODE_ESCORT, team, false)
                .into_iter()
                .map(|c| format!("{:04X}", c.line))
                .collect();
            let want_cues: Vec<String> = want
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_string())
                .collect();
            assert_eq!(cues, want_cues, "team {team} row {row}");
            prev = Some(cur);
        }
    }

    for (row, want) in inp["mode_deltas"]
        .as_array()
        .unwrap()
        .iter()
        .zip(exp["mode_actions"].as_array().unwrap())
    {
        let phase = parse_phase(row[0].as_str().unwrap());
        let got = mode_action(
            ModeActionSnapshot {
                phase,
                stage: row[3].as_u64().unwrap() as usize,
                seconds: row[1].as_f64().unwrap(),
                overtime: row[5].as_bool().unwrap(),
            },
            ModeActionSnapshot {
                phase,
                stage: row[4].as_u64().unwrap() as usize,
                seconds: row[2].as_f64().unwrap(),
                overtime: row[6].as_bool().unwrap(),
            },
        );
        assert_eq!(got.map(u64::from), want.as_u64());
    }

    // Suppress unused helper warnings when all checks pass
    let _ = (parse_hex_u32, MAP_ENTITY_BIT, SPECTATOR_TEAM);
}

#[test]
fn authored_escort_catalog_drives_shared_route_and_spawn_interfaces() {
    use crate::game_content::MapCatalog;
    use crate::room_primitives::{DoorPose, MAP_ENTITY_BIT};

    let case: serde_json::Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/fixtures/behavior/escort-authored-route-v1.json"
    )))
    .unwrap();
    let catalog = MapCatalog::from_json(
        &serde_json::json!({
            "0x0800000000003333": case["input"]["map"]
        })
        .to_string(),
    )
    .unwrap();
    let expected = &case["expected"];
    let mut map = catalog
        .game_map(0x0800_0000_0000_3333, MODE_ESCORT, "Escort", false, &[6, 6])
        .unwrap();
    let values = |key: &str| expected[key].clone();
    assert_eq!(serde_json::json!(map.placeables), values("placeables"));
    let numbers = |key: &str| {
        expected[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .collect::<Vec<_>>()
    };
    assert_eq!(
        map.spawn_for_stage(1, 0, 1).unwrap().0.as_slice(),
        numbers("later_spawn")
    );
    assert_eq!(
        map.spawn_for_stage(0, 0, 1).unwrap().0.as_slice(),
        numbers("fallback_spawn")
    );
    map.payload_route.as_mut().unwrap().speed_scale = 2.0;
    let Some(PvpObjectiveRuntime::Escort(mut round)) = PvpObjectiveRuntime::from_game_map(&map)
    else {
        panic!("route did not mount")
    };
    assert_eq!(round.stage_seconds, numbers("stage_seconds"));
    assert_eq!(round.pose().position.as_slice(), numbers("start_pose"));
    let offsets: [[f64; 3]; 3] =
        serde_json::from_value(values("first_segment_control_offsets")).unwrap();
    assert_eq!(round.mover.track.nodes[0].curve, Some(offsets));
    for ((distance, placement), row) in round
        .checkpoints
        .iter()
        .zip(expected["checkpoints"].as_array().unwrap())
    {
        assert!((distance - row[0].as_f64().unwrap()).abs() < 1e-8);
        assert_eq!(u64::from(*placement), row[1].as_u64().unwrap());
    }
    round.phase = MatchPhase::Active;
    round.deadline = 60.0;
    round.advance(
        8.0,
        &[ActorSample {
            entity: 7,
            team: 1,
            position: [0.0, 0.5, 0.0],
            alive: true,
        }],
    );
    assert_eq!(round.stage, 1);
    let gate = MAP_ENTITY_BIT | 40;
    assert_eq!(round.door_state(gate, 8.5, None).pose, DoorPose::Closed);
    let command = round.barriers.gate_commands[&gate];
    assert_eq!(
        command.starts_at,
        expected["gate_open_starts"].as_f64().unwrap()
    );
    assert_eq!(
        command.ends_at,
        expected["gate_open_ends"].as_f64().unwrap()
    );
    round.advance(18.0, &[]);
    assert!((round.mover.distance - expected["auto_drive_ends_at"].as_f64().unwrap()).abs() < 1e-8);
    assert_eq!(round.mover.route_rollback_floor, round.mover.distance);
    assert_eq!(round.mover.speed, 0.0);

    let skirmish = catalog
        .game_map(
            map.map_guid,
            0x0230_0000_0000_0010,
            "Skirmish",
            false,
            &[6, 6],
        )
        .unwrap();
    assert!(skirmish.payload_route.is_none());
    assert!(!skirmish.placeables.contains(&20));
}

#[test]
fn from_game_map_auto_mounts_all_four_pvp_modes() {
    use crate::game_content::{GameMap, MapObjective};
    let make_map = |objectives: Vec<MapObjective>| GameMap {
        objectives,
        ..GameMap::practice_range()
    };
    let obj = |kind: &str, pos: [f64; 3]| MapObjective {
        stage: None,
        submap_name: None,
        outlines: vec![],
        kind: kind.into(),
        name: None,
        placeable: Some(10),
        position: pos,
        rotation: None,
        objective: None,
        escort_seconds: None,
        casual_seconds: None,
        unlock_delay: 0.0,
        unlock_seconds: 0.0,
        auto_drive: false,
        speed_multiplier: None,
        round_seconds: None,
        capture_seconds: None,
        decay_seconds: None,
        capture_boxes: Vec::new(),
        outline: None,
        collision: Vec::new(),
        scale: [1.0; 3],
    };
    let assault = make_map(vec![
        obj("point", [0.0, 0.0, 0.0]),
        obj("point", [40.0, 0.0, 0.0]),
    ]);
    assert!(matches!(
        PvpObjectiveRuntime::from_game_map(&assault),
        Some(PvpObjectiveRuntime::Assault(_))
    ));
    let escort = make_map(vec![
        obj("payload", [0.0, 0.0, 0.0]),
        obj("checkpoint", [50.0, 0.0, 0.0]),
        obj("checkpoint", [100.0, 0.0, 0.0]),
    ]);
    assert!(matches!(
        PvpObjectiveRuntime::from_game_map(&escort),
        Some(PvpObjectiveRuntime::Escort(_))
    ));
    let hybrid = make_map(vec![
        obj("point", [0.0, 0.0, 0.0]),
        obj("payload", [2.0, 0.0, 0.0]),
        obj("checkpoint", [60.0, 0.0, 0.0]),
    ]);
    assert!(matches!(
        PvpObjectiveRuntime::from_game_map(&hybrid),
        Some(PvpObjectiveRuntime::Hybrid(_))
    ));
    let mut control = make_map(vec![
        obj("control point", [0.0, 0.0, 0.0]),
        obj("control point", [0.0, 100.0, 0.0]),
        obj("control point", [0.0, 200.0, 0.0]),
    ]);
    control.mode_guid = MODE_CONTROL;
    assert!(matches!(
        PvpObjectiveRuntime::from_game_map(&control),
        Some(PvpObjectiveRuntime::Control(_))
    ));
    assert!(PvpObjectiveRuntime::from_game_map(&GameMap::practice_range()).is_none());
}
