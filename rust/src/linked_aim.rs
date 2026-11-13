//! Captured zero-limit aim for the room's unparented NPC motor.

/// CAC810 converts degrees in f32; its zero pitch limits clamp to +/-89 degrees.
pub(crate) fn unparented_lock(degrees: [f64; 2]) -> Option<[i32; 2]> {
    let radians = degrees.map(|value| (value as f32) * 0.017_453_292_f32);
    if !radians.iter().all(|value| value.is_finite()) {
        return None;
    }
    let yaw = radians[0] * 10_430.378_f32;
    if !yaw.is_finite() || yaw.abs() > i32::MAX as f32 / 2.0 {
        return None;
    }
    let pitch_bound = f32::from_bits(0x3fc6_d3f2); // Native 2613F94.
    let pitch = radians[1].clamp(-pitch_bound, pitch_bound) * 10_430.378_f32;
    let pack = |value: f32| (value + 0.5_f32.copysign(value)).trunc() as i32;
    // The authoritative host pose uses a signed16 heading, rather than a quaternion.
    let yaw = (pack(yaw) + 32768).rem_euclid(65536) - 32768;
    Some([yaw, pack(pitch)])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_captured_aim_numeric_contract() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-captured-aim-lock-v1.json"
        ))
        .unwrap();
        for sample in fixture["samples"].as_array().unwrap() {
            let degrees = serde_json::from_value(sample["degrees"].clone()).unwrap();
            let [yaw, pitch] = unparented_lock(degrees).unwrap();
            let bounds: [f64; 6] = serde_json::from_value(sample["bounds"].clone()).unwrap();
            let projected_yaw = f64::from(yaw) * std::f64::consts::TAU / 65536.0;
            let difference = (projected_yaw - bounds[2] + std::f64::consts::PI)
                .rem_euclid(std::f64::consts::TAU)
                - std::f64::consts::PI;
            assert!(difference.abs() < std::f64::consts::TAU / 131072.0 + 1e-6);
            let native_pitch = bounds[3] as f32 * 10_430.378_f32;
            let expected = (native_pitch + 0.5_f32.copysign(native_pitch)).trunc() as i32;
            assert_eq!(pitch, expected);
        }
        for invalid in [f64::NAN, f64::INFINITY, f64::MAX] {
            assert_eq!(unparented_lock([invalid, 0.0]), None);
            assert_eq!(unparented_lock([0.0, invalid]), None);
        }
    }
}
