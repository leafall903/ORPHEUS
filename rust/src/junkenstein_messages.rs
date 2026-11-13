//! Authored defender messages in the 104319 encounter.

use serde_json::Value as Json;

use crate::{
    script_expr::native_shape,
    script_init::Site,
    script_wire::{Value, Variables},
    world::GAME_MODE_ENTITY,
};

pub(crate) const FLASHBANG_HIT: u64 = 0x0240_0000_0000_0622;
pub(crate) const SIGHTING: u64 = 0x0240_0000_0000_00bd;
pub(crate) const BOSS_INTRO_STARTED: u64 = 0x0240_0000_0000_06b0;
pub(crate) const BOSS_INTRO_ENDED: u64 = 0x0240_0000_0000_06ac;
pub(crate) const BOSS_PAUSE: u64 = 0x0240_0000_0000_06af;
pub(crate) const BOSS_CAMERA: u64 = 0x0240_0000_0000_06ab;
pub(crate) const VICTORY_VIEW: u64 = 0x0240_0000_0000_06bb;
pub(crate) const RESET_HIGHLIGHTS: u64 = 0x0240_0000_0000_06de;
pub(crate) const POST_MATCH_PHASE: u64 = 0x0240_0000_0000_0695;
pub(crate) const POST_MATCH_SIGNAL: u64 = 0x0240_0000_0000_08d6;
pub(crate) const POST_MATCH_COMPLETE: u64 = 0x0240_0000_0000_0bed;
pub(crate) const ENCOUNTER_RESULT: u64 = 0x0240_0000_0000_06be;
pub(crate) const RESULT_SUMMARY: u64 = 0x0240_0000_0000_06e2;
pub(crate) const SIGHTING_TAGS: [u64; 3] = [
    0x0d60_0000_0000_0326,
    0x0d60_0000_0000_051e,
    0x0d60_0000_0000_0038,
];

pub(crate) struct DefenderSighting {
    pub target: u32,
    pub source: SightingSource,
    pub parameters: Variables,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct DefenderMessage {
    pub target: Option<u32>,
    pub source: u32,
    pub message: u64,
    pub parameters: Variables,
}

impl DefenderMessage {
    pub fn accepts_body(&self, body: u32) -> bool {
        self.target.is_none_or(|target| target == body)
    }

    pub fn flashbang(site: Site, target: u32, parameters: &Variables) -> Option<Self> {
        ((site.graph, site.node) == (0x224e, Some(22))
            && parameters.len() == 1
            && parameters.contains_key(&0x2b))
        .then(|| Self {
            target: Some(target),
            source: site.entity,
            message: FLASHBANG_HIT,
            parameters: parameters.clone(),
        })
    }

    pub fn victory_view(site: Site, message: u64, parameters: &Variables) -> Option<Self> {
        if site.entity != GAME_MODE_ENTITY || site.graph != 0x0cec || message != VICTORY_VIEW {
            return None;
        }
        let valid = match site.node {
            Some(2) => {
                parameters.len() == 2
                    && parameters.get(&0x390) == Some(&Value::Bool(true))
                    && parameters.contains_key(&8)
            }
            Some(3) => parameters.len() == 1 && parameters.get(&0x390) == Some(&Value::Bool(false)),
            _ => false,
        };
        valid.then(|| Self {
            target: None,
            source: site.entity,
            message,
            parameters: parameters.clone(),
        })
    }

    pub fn authored(site: Site, message: u64) -> Option<Self> {
        let valid = match (site.graph, site.node, message) {
            (0x0ce0, Some(2), BOSS_INTRO_ENDED) | (0x0ce0, Some(52..=54), RESET_HIGHLIGHTS) => {
                site.entity != GAME_MODE_ENTITY
            }
            (0x0c8c, Some(125), RESET_HIGHLIGHTS) => site.entity == GAME_MODE_ENTITY,
            _ => false,
        };
        valid.then_some(Self {
            target: None,
            source: site.entity,
            message,
            parameters: Variables::new(),
        })
    }
}

#[derive(Clone, Copy)]
pub(crate) struct SightingSource {
    pub entity: u32,
    pub position: [f64; 3],
    pub tags: [Option<bool>; SIGHTING_TAGS.len()],
    pub filter_flags: Option<u64>,
}

pub(crate) fn sighting_selector(config: &Json) -> bool {
    native_shape(
        config.get("m_B2ABF4FF").unwrap_or(&Json::Null),
        "STU_B5A0CAF0",
        None,
    ) && config.get("$").and_then(Json::as_str) == Some("STU_ED45C5E8")
        && config.pointer("/m_2BA6230F/$").and_then(Json::as_str) == Some("STUConfigVarFloat")
        && config.pointer("/m_2BA6230F/m_value").and_then(Json::as_f64) == Some(0.0)
        && ["m_5912C8DF", "m_5A72EEF5", "m_C9E6F46B"].iter().all(|key| {
            config.get(key).is_some_and(|flag| {
                native_shape(flag, "STUConfigVarBool", Some("m_value"))
                    && matches!(flag.get("m_value"), Some(value) if value.as_u64() == Some(0) || value.as_bool() == Some(false))
            })
        })
}

pub(crate) fn self_team_query(config: &Json) -> bool {
    native_shape(config, "STU_65ABFCCC", Some("m_target"))
        && config
            .get("m_target")
            .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None))
}

pub(crate) fn recipients(ids: &[u32]) -> Value {
    Value::Array(ids.iter().copied().map(Value::Entity).collect())
}
