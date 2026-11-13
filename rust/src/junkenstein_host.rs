//! Engine-service defaults captured for the 104319 Junkenstein scene host.
//!
//! Python comparison: e1128509 junkenstein_scene.py / script/scene.py.

use crate::{script_expr::EngineSetting, script_wire::Value};

/// Pinned scene-host policy: ordinary killed Zomnics/Zombardiers have no
/// authored kill-removal listener. Their corpses receive an eight-second grace.
pub(crate) fn common_corpse_due(hero: u64, death_frame: Option<u32>, frame: u32) -> bool {
    matches!(hero, 0x02e0_0000_0000_0175 | 0x02e0_0000_0000_0177)
        && death_frame
            .and_then(|death| frame.checked_sub(death))
            .is_some_and(|elapsed| u64::from(elapsed) * crate::script_runtime::FRAME_MS >= 8000)
}

/// C1CF60's evidenced ordinary two-team branch. Other team layouts remain
/// unavailable; the first team bit and spectator exclusion are independent.
pub(crate) fn ordinary_hostility(source: u64, target: u64) -> Option<bool> {
    let team = |flags: u64| {
        let bits = (flags >> 23) & 31;
        (bits != 0).then(|| bits.trailing_zeros())
    };
    let (left, right) = (team(source)?, team(target)?);
    if left > 1 || right > 1 {
        return None;
    }
    Some(left != right && (source | target) & (1 << 28) == 0)
}

/// The table exists on this host. Unknown keys retain the evaluator's destination.
pub(crate) fn engine_setting(key: u32) -> EngineSetting {
    match key {
        0x0fe1_e03e => EngineSetting::Present(Value::Bool(false)),
        0xe1c1_e402 => EngineSetting::Present(Value::Integer(128)),
        0x6ce2_9bab => EngineSetting::Present(Value::Float(1.0)),
        _ => EngineSetting::Missing,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn captured_engine_table_keeps_types_and_distinguishes_unknown_keys() {
        assert_eq!(
            engine_setting(0x0fe1_e03e),
            EngineSetting::Present(Value::Bool(false))
        );
        assert_eq!(
            engine_setting(0xe1c1_e402),
            EngineSetting::Present(Value::Integer(128))
        );
        assert_eq!(
            engine_setting(0x6ce2_9bab),
            EngineSetting::Present(Value::Float(1.0))
        );
        assert_eq!(engine_setting(0xd645_f578), EngineSetting::Missing);
        assert_eq!(engine_setting(0), EngineSetting::Missing);
    }
}
