//! Build 104319 encounter cues and enUS sample counts at 48 kHz.

use crate::{
    script_expr::native_shape,
    script_graph::{Node, asset_guid},
};
use serde_json::Value;
use std::hash::{DefaultHasher, Hash, Hasher};

pub(crate) const ENCOUNTER_VOICE_SET: u64 = 0x07a0_0000_0000_013b;

const STIMULUS: u64 = 0x0ee0_0000_0000_0000;
const VOICE_LINE: u64 = 0x0760_0000_0000_0000;
const BOSS_CUE: u64 = STIMULUS | 0x06bd;
pub(crate) const VICTORY_CUE: u64 = STIMULUS | 0x0cb7;
type Lines = &'static [(u16, u32)];
const NARRATION: &[(u16, Lines)] = &[
    (0x06b5, &[(0x8cb1, 160173)]),
    (0x06b6, &[(0x89c6, 283783)]),
    (0x06b7, &[(0x8cce, 296084)]),
    (0x06c4, &[(0x8cdc, 264390)]),
    (0x08e1, &[(0xa466, 296000)]),
    (
        0x06d6,
        &[(0x8cb2, 280352), (0x8cb3, 250539), (0xa45e, 323008)],
    ),
    (
        0x06da,
        &[(0x8cc2, 525857), (0x8cc3, 384966), (0xa45f, 409344)],
    ),
    (
        0x06dd,
        &[(0x8cd3, 422035), (0x8cd5, 411218), (0xa460, 368128)],
    ),
    (
        0x06e0,
        &[
            (0x8cdd, 327646),
            (0x8cde, 318741),
            (0x8cdf, 428763),
            (0x8cf0, 186821),
        ],
    ),
    (0x08e4, &[(0xa400, 323072), (0xa401, 311360)]),
];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct IntroCue {
    pub stimulus: u64,
    pub narrator: bool,
    pub delay_ms: u16,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct VoiceRequest {
    pub anchor: u32,
    pub speaker: u32,
    pub stimulus: u64,
    pub line: u64,
}

pub(crate) fn intro_cue(node: &Node) -> Option<IntroCue> {
    let (stimulus, narrator, delay_ms) = match node.pos {
        61..=70 => (
            STIMULUS | u64::from(NARRATION[usize::from(node.pos - 61)].0),
            true,
            0,
        ),
        77 => (BOSS_CUE, false, 200),
        _ => return None,
    };
    matches_voice(node, stimulus, narrator, delay_ms).then_some(IntroCue {
        stimulus,
        narrator,
        delay_ms,
    })
}

pub(crate) fn victory_cue(node: &Node) -> bool {
    matches_voice(node, VICTORY_CUE, false, 0)
        && node
            .field("m_8C8C5285")
            .and_then(|actor| crate::script_expr::lvalue(actor).ok().flatten())
            == Some(crate::script_expr::Variable {
                scope: crate::script_expr::Scope::Entity,
                id: 8602,
            })
}

pub(crate) fn victory_line(hero: Option<u64>) -> (u64, u64) {
    let (line, samples): (u16, u32) = match hero {
        Some(0x02e0_0000_0000_0004) => (0x8ce1, 630603),
        Some(0x02e0_0000_0000_0040) => (0x8ce2, 632515),
        Some(0x02e0_0000_0000_0002) => (0x8ce3, 589970),
        Some(0x02e0_0000_0000_0065) => (0x8ce4, 810400),
        Some(0x02e0_0000_0000_0016) => (0xa3ee, 603456),
        _ => (0x8cf1, 393805),
    };
    (line_guid(line), u64::from(samples.div_ceil(48)))
}

pub(crate) fn gate_cue(node: &Node) -> Option<u64> {
    let stimulus = STIMULUS
        | match node.pos {
            50 => 0x06c7,
            51 => 0x06b8,
            52 => 0x06b9,
            _ => return None,
        };
    matches_voice(node, stimulus, true, 0).then_some(stimulus)
}

fn matches_voice(node: &Node, stimulus: u64, narrator: bool, delay_ms: u16) -> bool {
    let field = |name| node.fields.get(name).unwrap_or(&Value::Null);
    let delay = field("m_B0263D2D");
    let gap = field("m_E0E02BD4");
    let config = field("m_stimulus");
    let target = if narrator {
        "STU_A3DEF368"
    } else {
        "STU_B5A0CAF0"
    };
    let delay_matches = if delay_ms == 0 {
        delay.is_null()
    } else {
        native_shape(delay, "STUConfigVarFloat", Some("m_value"))
            && delay
                .get("m_value")
                .and_then(Value::as_f64)
                .is_some_and(|value| value as f32 == f32::from(delay_ms) / 1000.0)
    };
    node.remote == -1
        && native_shape(field("m_targets"), target, None)
        && delay_matches
        && native_shape(gap, "STUConfigVarFloat", Some("m_value"))
        && gap.get("m_value").and_then(Value::as_f64) == Some(0.0)
        && config.get("$").and_then(Value::as_str) == Some("STUConfigVarVoiceStimulus")
        && config.get("m_resourceKey").is_none_or(Value::is_null)
        && config.get("m_voiceStimulus").and_then(asset_guid) == Some(stimulus)
        && field("m_FC2F7AAA").is_null()
        && field("m_onFinishedPlug")
            .get("links")
            .and_then(Value::as_array)
            .is_some_and(Vec::is_empty)
}

pub(crate) fn gate_lines(stimulus: u64, attacker_hero: Option<u64>) -> Lines {
    match stimulus.checked_sub(STIMULUS) {
        Some(0x06c7) if attacker_hero == Some(0x02e0_0000_0000_0176) => {
            &[(0x8c71, 420716), (0x8ce8, 283980)]
        }
        Some(0x06c7) => &[(0x8c71, 420716)],
        Some(0x06b8) => &[
            (0x885f, 194670),
            (0x8860, 159974),
            (0x8870, 187612),
            (0x8871, 181610),
            (0x8c80, 232663),
        ],
        Some(0x06b9) => &[
            (0x89c2, 152851),
            (0x8c81, 299349),
            (0x8c82, 208786),
            (0x8ce9, 224529),
        ],
        _ => &[],
    }
}

pub(crate) fn select_line(lines: Lines, anchor: u32, variation: &mut u64) -> Option<(u64, u64)> {
    let (line, samples) = match lines {
        [] => return None,
        [line] => *line,
        lines => {
            let mut hash = DefaultHasher::new();
            (anchor, *variation).hash(&mut hash);
            *variation = variation.wrapping_add(1);
            lines[(hash.finish() % lines.len() as u64) as usize]
        }
    };
    Some((line_guid(line), u64::from(samples.div_ceil(48))))
}

pub(crate) fn intro_lines(cue: IntroCue, hero: u64, skin: Option<u64>) -> Lines {
    if cue.narrator {
        return NARRATION
            .iter()
            .find(|(stimulus, _)| STIMULUS | u64::from(*stimulus) == cue.stimulus)
            .map_or(&[], |(_, lines)| lines);
    }
    let (encounter_skin, lines): (u16, &[_]) = match hero {
        0x02e0_0000_0000_0002 => (0x1beb, &[(0x8a2b, 172357)]),
        0x02e0_0000_0000_0004 => (0x196a, &[(0x8986, 114448)]),
        0x02e0_0000_0000_0040 => (0x195c, &[(0x8a2c, 126194)]),
        0x02e0_0000_0000_0065 => (0x1962, &[(0x8b14, 238877)]),
        0x02e0_0000_0000_0016 => (0x16df, &[(0xa311, 150624), (0xa312, 102246)]),
        _ => return &[],
    };
    if skin.is_none_or(|skin| skin == 0x0a50_0000_0000_0000 | u64::from(encounter_skin)) {
        lines
    } else {
        &[]
    }
}

pub(crate) fn line_guid(index: u16) -> u64 {
    VOICE_LINE | u64::from(index)
}

#[derive(Clone, Copy)]
pub(crate) enum BossSpeech {
    Defeat(&'static [DefeatLine]),
    Spawn(Lines),
    Body(Lines),
    Barrier {
        node: u16,
        line: u64,
        samples: u32,
        opponents: bool,
    },
}

impl BossSpeech {
    pub(crate) fn mode(self) -> bool {
        matches!(self, Self::Defeat(_) | Self::Spawn(_))
    }
    pub(crate) fn needs_draw(self) -> bool {
        match self {
            Self::Defeat(_) => true,
            Self::Spawn(rows) | Self::Body(rows) => rows.len() > 1,
            Self::Barrier { .. } => false,
        }
    }
}

pub(crate) fn speech_allowed(
    kind: BossSpeech,
    mode_set: Option<u64>,
    body_set: Option<u64>,
) -> Option<bool> {
    if kind.mode() {
        mode_set.map(|definition| definition == ENCOUNTER_VOICE_SET)
    } else {
        Some(body_set.and_then(body_lines).is_some())
    }
}

pub(crate) fn speech_submission(
    site: crate::script_init::Site,
    kind: BossSpeech,
    stimulus: u64,
    killer: Option<u64>,
    targets: &[u32],
    draw: f64,
) -> Result<(u32, Option<VoiceRequest>), &'static str> {
    use crate::world::GAME_MODE_ENTITY;
    let mut emit = true;
    let uniform = |rows: Lines| {
        let selected = match rows {
            [line] => Some(line),
            rows if (0.0..1.0).contains(&draw) => rows.get((draw * rows.len() as f64) as usize),
            _ => None,
        };
        selected
            .map(|(line, _)| line_guid(*line))
            .ok_or("voice random choice")
    };
    let line = match kind {
        BossSpeech::Defeat(rows) => {
            if site.graph != 0x0ccd
                || !matches!(site.node, Some(6..=15))
                || targets != [GAME_MODE_ENTITY]
            {
                return Err("boss defeat voice targets");
            }
            defeat_choice(rows, killer, draw)
                .ok_or("voice random choice")?
                .0
        }
        BossSpeech::Spawn(rows) => {
            if site.graph != 0x0ce0
                || !matches!(site.node, Some(61..=70))
                || targets != [GAME_MODE_ENTITY]
            {
                return Err("boss spawn voice targets");
            }
            uniform(rows)?
        }
        BossSpeech::Body(rows) => {
            if (site.graph, site.node) != (0x0ce0, Some(77)) || targets != [site.entity] {
                return Err("boss spawn voice targets");
            }
            uniform(rows)?
        }
        BossSpeech::Barrier {
            node,
            line,
            opponents,
            ..
        } => {
            if (site.graph, site.node) != (0x156b, Some(node)) || targets != [site.entity] {
                return Err("Summoner barrier voice targets");
            }
            emit = opponents;
            line
        }
    };
    Ok((
        line as u32,
        emit.then_some(VoiceRequest {
            anchor: site.entity,
            speaker: if kind.mode() {
                GAME_MODE_ENTITY
            } else {
                site.entity
            },
            stimulus,
            line,
        }),
    ))
}

pub(crate) fn body_voice_set(hero: u64, skin: Option<u64>) -> Option<u64> {
    let (default, theme, encounter) = match hero {
        0x02e0_0000_0000_0002 => (1, 0x1beb, 1),
        0x02e0_0000_0000_0004 => (0x17, 0x196a, 0x12c),
        0x02e0_0000_0000_0040 => (0x53, 0x195c, 0x149),
        0x02e0_0000_0000_0065 => (0x55, 0x1962, 0x145),
        0x02e0_0000_0000_0016 => (0x3f, 0x16df, 0x17d),
        _ => return None,
    };
    let voice = match skin {
        None => default,
        Some(skin) if skin == 0x0a50_0000_0000_0000 | theme => encounter,
        _ => return None,
    };
    Some(0x07a0_0000_0000_0000 | voice)
}

fn body_lines(definition: u64) -> Option<Lines> {
    Some(match definition {
        0x07a0_0000_0000_0001 => &[(0x8a2b, 172357)],
        0x07a0_0000_0000_0017 | 0x07a0_0000_0000_012c => &[(0x8986, 114448)],
        0x07a0_0000_0000_0053 | 0x07a0_0000_0000_0149 => &[(0x8a2c, 126194)],
        0x07a0_0000_0000_0055 | 0x07a0_0000_0000_0145 => &[(0x8b14, 238877)],
        0x07a0_0000_0000_003f | 0x07a0_0000_0000_017d => &[(0xa311, 150624), (0xa312, 102246)],
        _ => return None,
    })
}

pub(crate) fn boss_speech(hero: u64, skin: Option<u64>, stimulus: u64) -> Option<BossSpeech> {
    if let Some((_, rows)) = DEFEAT_LINES.iter().find(|(cue, _)| *cue == stimulus) {
        return Some(BossSpeech::Defeat(rows));
    }
    if let Some((_, rows)) = NARRATION
        .iter()
        .find(|(cue, _)| STIMULUS | u64::from(*cue) == stimulus)
    {
        return Some(BossSpeech::Spawn(rows));
    }
    if stimulus == BOSS_CUE {
        return body_voice_set(hero, skin)
            .and_then(body_lines)
            .map(BossSpeech::Body);
    }
    if hero == 0x02e0_0000_0000_0016 {
        match stimulus {
            0x0ee0_0000_0000_0a5a => {
                return Some(BossSpeech::Barrier {
                    node: 65,
                    line: line_guid(0xb5b0),
                    samples: 120440,
                    opponents: true,
                });
            }
            0x0ee0_0000_0000_0a5b => {
                return Some(BossSpeech::Barrier {
                    node: 66,
                    line: line_guid(0xb577),
                    samples: 120496,
                    opponents: false,
                });
            }
            _ => (),
        }
    }
    None
}

pub(crate) fn defeat_choice(
    rows: &[DefeatLine],
    hero: Option<u64>,
    draw: f64,
) -> Option<(u64, u32)> {
    if !(0.0..1.0).contains(&draw) {
        return None;
    }
    let eligible = || {
        rows.iter()
            .filter(|row| row.killer.is_none() || row.killer == hero)
    };
    let total = eligible().map(|row| row.weight).sum::<f64>();
    let mut target = draw * total;
    let mut last = None;
    for row in eligible() {
        last = Some((row.line, row.samples));
        if target < row.weight {
            return last;
        }
        target -= row.weight;
    }
    last
}

pub(crate) fn voice_draw(anchor: u32, variation: &mut u64) -> f64 {
    let mut hash = DefaultHasher::new();
    (anchor, *variation).hash(&mut hash);
    *variation = variation.wrapping_add(1);
    (hash.finish() >> 11) as f64 / 9_007_199_254_740_992.0
}

pub(crate) fn boss_duration(handle: u32, stimulus: u64, mode: bool) -> Option<f64> {
    let duration = |samples: u32| f64::from(samples) / 48000.0;
    if !mode {
        if let Some(BossSpeech::Barrier { line, samples, .. }) =
            boss_speech(0x02e0_0000_0000_0016, None, stimulus)
        {
            return (handle == line as u32).then(|| duration(samples));
        }
        if stimulus == BOSS_CUE {
            return [1, 0x17, 0x53, 0x55, 0x3f]
                .iter()
                .filter_map(|index| body_lines(0x07a0_0000_0000_0000 | index))
                .flatten()
                .find(|(line, _)| u32::from(*line) == handle)
                .map(|(_, samples)| duration(*samples));
        }
    } else {
        if let Some((_, rows)) = DEFEAT_LINES.iter().find(|(cue, _)| *cue == stimulus) {
            return rows
                .iter()
                .find(|row| row.line as u32 == handle)
                .map(|row| duration(row.samples));
        }
        if let Some((_, rows)) = NARRATION
            .iter()
            .find(|(cue, _)| STIMULUS | u64::from(*cue) == stimulus)
        {
            return rows
                .iter()
                .find(|(line, _)| u32::from(*line) == handle)
                .map(|(_, samples)| duration(*samples));
        }
    }
    None
}

// Defeat policy and enUS duration facts from pinned Python e1128509. No audio media.
#[derive(Clone, Copy, Debug)]
pub(crate) struct DefeatLine {
    pub line: u64,
    pub samples: u32,
    pub killer: Option<u64>,
    pub weight: f64,
}
const DEFEAT_LINES: &[(u64, &[DefeatLine])] = &[
    (
        0x0ee00000000006d9,
        &[
            DefeatLine {
                line: 0x0760000000008cbc,
                samples: 136427,
                killer: None,
                weight: 0.5,
            },
            DefeatLine {
                line: 0x0760000000008cbd,
                samples: 327448,
                killer: Some(0x02e000000000013b),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008cbe,
                samples: 291962,
                killer: Some(0x02e000000000006e),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008cbf,
                samples: 243415,
                killer: Some(0x02e0000000000042),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008cc0,
                samples: 219273,
                killer: Some(0x02e0000000000005),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a3fc,
                samples: 238656,
                killer: Some(0x02e0000000000006),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a3fd,
                samples: 292160,
                killer: Some(0x02e0000000000029),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a3fe,
                samples: 258432,
                killer: Some(0x02e000000000000a),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a3ff,
                samples: 232896,
                killer: Some(0x02e0000000000020),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a61c,
                samples: 341760,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a623,
                samples: 248384,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b91a,
                samples: 283214,
                killer: Some(0x02e0000000000195),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b91b,
                samples: 303541,
                killer: Some(0x02e0000000000003),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e19f,
                samples: 302199,
                killer: Some(0x02e0000000000200),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a0,
                samples: 251453,
                killer: Some(0x02e0000000000221),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1b3,
                samples: 285934,
                killer: Some(0x02e000000000021d),
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee00000000006dc,
        &[
            DefeatLine {
                line: 0x0760000000008cc9,
                samples: 395585,
                killer: Some(0x02e0000000000042),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008cca,
                samples: 376787,
                killer: Some(0x02e000000000006e),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008ccb,
                samples: 397103,
                killer: Some(0x02e000000000013b),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008ccc,
                samples: 306340,
                killer: Some(0x02e0000000000005),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008ccd,
                samples: 427708,
                killer: None,
                weight: 0.5,
            },
            DefeatLine {
                line: 0x076000000000a430,
                samples: 256064,
                killer: Some(0x02e0000000000006),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a431,
                samples: 307712,
                killer: Some(0x02e0000000000029),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a432,
                samples: 304128,
                killer: Some(0x02e0000000000020),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a433,
                samples: 310592,
                killer: Some(0x02e000000000000a),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a61b,
                samples: 412608,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a622,
                samples: 381376,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b91c,
                samples: 368558,
                killer: Some(0x02e0000000000003),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b91d,
                samples: 275826,
                killer: Some(0x02e0000000000195),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a1,
                samples: 329537,
                killer: Some(0x02e0000000000221),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a2,
                samples: 371491,
                killer: Some(0x02e0000000000200),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1b4,
                samples: 302590,
                killer: Some(0x02e000000000021d),
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee00000000006df,
        &[
            DefeatLine {
                line: 0x0760000000008cd7,
                samples: 338134,
                killer: Some(0x02e0000000000005),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008cd8,
                samples: 297238,
                killer: Some(0x02e000000000006e),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008cd9,
                samples: 259641,
                killer: Some(0x02e000000000013b),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x0760000000008cda,
                samples: 366662,
                killer: Some(0x02e0000000000042),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a434,
                samples: 427904,
                killer: Some(0x02e0000000000006),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a435,
                samples: 352512,
                killer: Some(0x02e0000000000020),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a436,
                samples: 332480,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a437,
                samples: 286663,
                killer: Some(0x02e000000000000a),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a463,
                samples: 270656,
                killer: Some(0x02e0000000000029),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a61a,
                samples: 438848,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a621,
                samples: 464832,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b91e,
                samples: 332775,
                killer: Some(0x02e0000000000003),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b91f,
                samples: 292200,
                killer: Some(0x02e0000000000195),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a3,
                samples: 324936,
                killer: Some(0x02e0000000000221),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a4,
                samples: 271888,
                killer: Some(0x02e0000000000200),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1b5,
                samples: 310529,
                killer: Some(0x02e000000000021d),
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee00000000008e3,
        &[
            DefeatLine {
                line: 0x076000000000a40b,
                samples: 203520,
                killer: Some(0x02e000000000006e),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a40c,
                samples: 357696,
                killer: Some(0x02e0000000000042),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a40e,
                samples: 305600,
                killer: Some(0x02e000000000000a),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a40f,
                samples: 320576,
                killer: Some(0x02e0000000000029),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a410,
                samples: 279872,
                killer: Some(0x02e0000000000006),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a413,
                samples: 309760,
                killer: Some(0x02e0000000000020),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a414,
                samples: 329024,
                killer: Some(0x02e000000000013b),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a415,
                samples: 244864,
                killer: Some(0x02e0000000000005),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a41b,
                samples: 119360,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a61e,
                samples: 312512,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a61f,
                samples: 302336,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b922,
                samples: 404261,
                killer: Some(0x02e0000000000003),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b923,
                samples: 335051,
                killer: Some(0x02e0000000000195),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a7,
                samples: 350750,
                killer: Some(0x02e0000000000221),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a8,
                samples: 242888,
                killer: Some(0x02e0000000000200),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1b7,
                samples: 297941,
                killer: Some(0x02e000000000021d),
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee00000000008e9,
        &[
            DefeatLine {
                line: 0x076000000000a438,
                samples: 287360,
                killer: Some(0x02e000000000000a),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a439,
                samples: 398784,
                killer: Some(0x02e0000000000042),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a43a,
                samples: 290880,
                killer: Some(0x02e0000000000029),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a43b,
                samples: 244928,
                killer: Some(0x02e000000000006e),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a43c,
                samples: 287744,
                killer: Some(0x02e0000000000005),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a43d,
                samples: 376320,
                killer: Some(0x02e000000000013b),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a43e,
                samples: 339264,
                killer: Some(0x02e0000000000006),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a43f,
                samples: 317696,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a440,
                samples: 350592,
                killer: Some(0x02e0000000000020),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a61d,
                samples: 315456,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a620,
                samples: 292032,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b920,
                samples: 366401,
                killer: Some(0x02e0000000000003),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b921,
                samples: 349309,
                killer: Some(0x02e0000000000195),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a5,
                samples: 318758,
                killer: Some(0x02e0000000000221),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1a6,
                samples: 291649,
                killer: Some(0x02e0000000000200),
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000e1b6,
                samples: 315323,
                killer: Some(0x02e000000000021d),
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee000000000091b,
        &[
            DefeatLine {
                line: 0x076000000000a5ed,
                samples: 158912,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5ee,
                samples: 142144,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5ef,
                samples: 134080,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5f0,
                samples: 131008,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5f1,
                samples: 114624,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000b949,
                samples: 132134,
                killer: None,
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee000000000091d,
        &[
            DefeatLine {
                line: 0x076000000000a5f7,
                samples: 174912,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5f8,
                samples: 140352,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5f9,
                samples: 128704,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5fa,
                samples: 141248,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a5fb,
                samples: 163392,
                killer: None,
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee000000000091e,
        &[
            DefeatLine {
                line: 0x076000000000a601,
                samples: 166272,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a602,
                samples: 173760,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a603,
                samples: 177728,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a604,
                samples: 143424,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a605,
                samples: 134336,
                killer: None,
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee0000000000921,
        &[
            DefeatLine {
                line: 0x076000000000a60b,
                samples: 155200,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a60c,
                samples: 127168,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a60d,
                samples: 109760,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a60e,
                samples: 132160,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a60f,
                samples: 108608,
                killer: None,
                weight: 1.0,
            },
        ],
    ),
    (
        0x0ee0000000000923,
        &[
            DefeatLine {
                line: 0x076000000000a615,
                samples: 175296,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a616,
                samples: 139584,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a617,
                samples: 112704,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a618,
                samples: 132032,
                killer: None,
                weight: 1.0,
            },
            DefeatLine {
                line: 0x076000000000a619,
                samples: 129344,
                killer: None,
                weight: 1.0,
            },
        ],
    ),
];

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn shared_boss_speech_contract() {
        let contract: Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/junkenstein-boss-speech-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "junkenstein.boss-speech.v1");
        assert_eq!(contract["target_build"], 104319);
        let owner = contract["input"]["owner"].as_u64().unwrap() as u32;
        let cases = contract["input"]["cases"].as_array().unwrap();
        let results = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), results.len());
        for (case, expected) in cases.iter().zip(results) {
            assert_eq!(case["name"], expected["name"]);
            let guid = |key: &str| {
                case[key]
                    .as_str()
                    .map(|v| u64::from_str_radix(v.trim_start_matches("0x"), 16).unwrap())
            };
            let hex = |value: u64| format!("0x{value:016X}");
            let hero = guid("hero").unwrap();
            let skin = guid("skin");
            let stimulus = guid("stimulus").unwrap();
            let body_set = body_voice_set(hero, skin);
            let mut actual = json!({"body_set":body_set.map(hex),"loaded":false});
            if let Some(kind) = boss_speech(hero, skin, stimulus) {
                let allowed = speech_allowed(kind, guid("mode_set"), body_set).unwrap();
                actual["loaded"] = json!(true);
                actual["playable"] = json!(true);
                actual["mode"] = json!(kind.mode());
                actual["allowed"] = json!(allowed);
                if allowed {
                    let targets: Vec<u32> = case["targets"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|v| v.as_u64().unwrap() as u32)
                        .collect();
                    let site = crate::script_init::Site {
                        entity: owner,
                        instance: 0,
                        graph: case["graph"].as_u64().unwrap() as u32,
                        node: Some(case["node"].as_u64().unwrap() as u16),
                    };
                    match speech_submission(
                        site,
                        kind,
                        stimulus,
                        guid("killer"),
                        &targets,
                        f64::from_bits(
                            u64::from_str_radix(case["draw_bits"].as_str().unwrap(), 16).unwrap(),
                        ),
                    ) {
                        Err(error) => actual["error"] = json!(error),
                        Ok((handle, notice)) => {
                            actual["handle"] = json!(handle);
                            actual["notices"] = json!(notice.into_iter().map(|v|json!({"anchor":v.anchor,"speaker":v.speaker,"stimulus":hex(v.stimulus),"line":hex(v.line)})).collect::<Vec<_>>());
                            actual["duration_bits"] = json!(format!(
                                "{:016x}",
                                boss_duration(handle, stimulus, kind.mode())
                                    .unwrap()
                                    .to_bits()
                            ));
                            actual["part"] = json!(0.0);
                        }
                    }
                }
            }
            assert_eq!(actual, expected["output"], "{}", case["name"]);
        }
    }
}
