//! Evaluate radial combat impulses independently of health loss.

use serde_json::Value as Json;

use crate::{
    script_expr::{EvalError, authored_number},
    script_wire::Value,
};

#[derive(Clone, Debug, PartialEq)]
pub struct Knockback {
    horizontal: f32,
    upward: f32,
    rings: Vec<(f32, [f32; 2])>,
}

impl Knockback {
    pub fn from_authored(
        modify_health: &Json,
        evaluate: &mut impl FnMut(&Json) -> Result<Value, EvalError>,
    ) -> Option<Self> {
        let horizontal =
            authored_number(modify_health.get("m_E33EFEE1"), 0.0, evaluate).ok()? as f32;
        let base_upward =
            authored_number(modify_health.get("m_10399AE7"), 0.0, evaluate).ok()? as f32;
        let vertical_override = modify_health
            .get("m_583C208B")
            .filter(|v| !v.is_null())
            .and_then(|v| authored_number(Some(v), 0.0, evaluate).ok())
            .map(|v| v as f32)
            .filter(|v| v.is_finite() && *v > 0.0);
        // Retail `STUStatescriptModifyHealth` uses `m_583C208B` for vertical pop-up velocity
        // (e.g. Wrecking Ball Fireball `5.0`, Piledriver `11.0`, Minefield `5.0`) while
        // `m_10399AE7` holds either an upward scale (`1`) or projectile upward impulse.
        let upward = match vertical_override {
            Some(v) if base_upward == 0.0 || (base_upward - 1.0).abs() < 1e-4 => v,
            _ => base_upward,
        };
        if [horizontal, upward] == [0.0; 2] || ![horizontal, upward].into_iter().all(f32::is_finite)
        {
            return None;
        }
        let mut rings = Vec::new();
        let rows = modify_health
            .get("m_knockbackRings")
            .filter(|rows| !rows.is_null());
        if let Some(rows) = rows {
            for row in rows.as_array()? {
                if row["$"].as_str() != Some("STUStatescriptModifyHealthKnockbackRing") {
                    return None;
                }
                let radius = authored_number(row.get("m_radius"), 0.0, evaluate).ok()? as f32;
                let horizontal = authored_number(row.get("m_579D7CFF"), 0.0, evaluate).ok()? as f32;
                let upward = authored_number(row.get("m_10399AE7"), 0.0, evaluate).ok()? as f32;
                if !radius.is_finite()
                    || radius < 0.0
                    || !horizontal.is_finite()
                    || !upward.is_finite()
                {
                    return None;
                }
                rings.push((radius, [horizontal, upward]));
            }
        }
        rings.sort_by(|a, b| a.0.total_cmp(&b.0));
        Some(Self {
            horizontal,
            upward,
            rings,
        })
    }

    pub fn delta(
        &self,
        point: [f64; 3],
        feet: [f64; 3],
        distance: f64,
        in_damage_range: bool,
        target_scale: f32,
    ) -> Option<[f64; 3]> {
        self.radial_delta(
            [feet[0] - point[0], feet[2] - point[2]],
            distance,
            in_damage_range,
            target_scale,
        )
    }

    pub fn radial_delta(
        &self,
        [x, z]: [f64; 2],
        distance: f64,
        in_damage_range: bool,
        target_scale: f32,
    ) -> Option<[f64; 3]> {
        if target_scale <= 0.0 || !distance.is_finite() {
            return None;
        }
        let scale = self.scale_at(distance, in_damage_range)?;
        let horizontal = f64::from(self.horizontal * scale[0] * target_scale);
        let upward = f64::from(self.upward * scale[1] * target_scale);
        let planar = x.hypot(z);
        let delta = if planar > 0.0 {
            [x / planar * horizontal, upward, z / planar * horizontal]
        } else {
            [0.0, upward, 0.0]
        };
        (delta != [0.0; 3] && delta.iter().all(|axis| (*axis as f32).is_finite())).then_some(delta)
    }

    fn scale_at(&self, distance: f64, in_damage_range: bool) -> Option<[f32; 2]> {
        let distance = distance.max(0.0) as f32;
        let Some(&(mut previous, mut scale)) = self.rings.first() else {
            return in_damage_range.then_some([1.0; 2]);
        };
        if distance < previous {
            return Some(scale);
        }
        for &(radius, next) in &self.rings[1..] {
            if distance < radius {
                let share = (distance - previous) / (radius - previous).max(0.0001);
                return Some(std::array::from_fn(|axis| {
                    scale[axis] + (next[axis] - scale[axis]) * share
                }));
            }
            previous = radius;
            scale = next;
        }
        None
    }
}
