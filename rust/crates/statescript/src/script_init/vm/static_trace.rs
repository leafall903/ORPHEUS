//! The static-ray hook launch (`STU_4BBC5D09`).
//!
//! The trace flies along the owner's aim at the authored speed, publishes
//! its endpoint as the presentation rope tip (`m_F2F8606D`) while the flight
//! runs, and on arrival either reports the raw face hit, or — when the face
//! hides a standable lip inside the mover's own capsule reach — a validated
//! wall-to-walkable transition: the rope anchor on the lip line plus a
//! standing feet destination a footprint behind it (the reference's `ledge`
//! tuple, grapple.py T-0013). The wall branch's MoveTo state reads the
//! movement destination from `m_5ED803CF` (exported selector
//! `I1221 ? I1220 : I431`, byte-decoded from the prepared pack), so the two
//! roles are separate wire values: the anchor travels in `m_58B4C4D7`, the
//! movement target in `m_5ED803CF`; a centre miss re-publishes the launch
//! ray's answer at arrival, as an unchanged static result must. Every
//! dimension the scan measures comes from the owner's own mover settings —
//! capsule radius and segment, step-down band, walkable slope cosine —
//! never a hero table, a graph ID or a new constant.

use crate::{
    script_expr::{self, SpatialRayHit, lvalue},
    script_graph::Node,
    script_wire::Value,
};

use super::super::{
    StateKind,
    machine::{Event, StateKey, VarKey},
    shapes::field,
    types::InitFault,
    world::{HookPullGeometry, Site, World},
};
use super::Vm;

/// One validated edge transition found by the promotion scan: the rope
/// anchor on the lip line, the standing feet destination on the validated
/// top, and the face normal behind the transition.
#[derive(Clone, Copy, Debug, PartialEq)]
struct HookPromotion {
    anchor: [f64; 3],
    feet: [f64; 3],
    face: [f64; 3],
}

/// The support grid's probe offsets (contour, inward) in capsule radii:
/// the centre, its four neighbours, and the diagonals (grapple.py `_GRID`).
const GRID: [(f64, f64); 9] = [
    (0.0, 0.0),
    (-1.0, 0.0),
    (1.0, 0.0),
    (0.0, -1.0),
    (0.0, 1.0),
    (-1.0, -1.0),
    (-1.0, 1.0),
    (1.0, -1.0),
    (1.0, 1.0),
];

type Vec3 = [f64; 3];

fn flat_len([x, _, z]: Vec3) -> f64 {
    (x * x + z * z).sqrt()
}

fn gap(a: Vec3, b: Vec3) -> f64 {
    let delta = add(a, scaled(b, -1.0));
    (delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2]).sqrt()
}

fn add(a: Vec3, b: Vec3) -> Vec3 {
    std::array::from_fn(|i| a[i] + b[i])
}

fn dot(a: Vec3, b: Vec3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn scaled(a: Vec3, factor: f64) -> Vec3 {
    a.map(|axis| axis * factor)
}

/// A grid cell of the support scan in the rim-local frame.
fn grid_point(centre: Vec3, contour: Vec3, inward: Vec3, de: f64, di: f64, radius: f64) -> Vec3 {
    add(
        centre,
        add(scaled(contour, de * radius), scaled(inward, di * radius)),
    )
}

impl<'graph, 'world, 'machine, W: World> Vm<'graph, 'world, 'machine, W> {
    pub(in crate::script_init) fn begin_static_trace(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let site = self.site(key.instance, node);
        // End plugs also run when the query is unavailable. Invalidate the
        // prior attachment before any early Finish, without inventing a point.
        self.trace_write(key, node, "m_9E1A7C58", Value::Bool(false))?;
        self.trace_write(key, node, "m_1FFD7AC0", Value::Entity(0))?;
        self.trace_write(key, node, "m_9169D109", Value::Bool(false))?;
        self.trace_write(key, node, "m_86CF0002", Value::Asset(0))?;
        let Some(aim) = self.world.authored_forward(site.entity) else {
            return self.post(key.instance, Event::Finish(key.raw));
        };
        let magnitude = aim[0].hypot(aim[1]).hypot(aim[2]);
        if !magnitude.is_finite() || magnitude == 0.0 {
            return self.post(key.instance, Event::Finish(key.raw));
        }
        let direction = aim.map(|axis| axis / magnitude);
        let attachment = field(&node.fields, "m_F745334E");
        let origin = if attachment.is_null() {
            self.world.body_eye_position(site.entity)
        } else {
            match self.evaluate(key.instance, node, attachment, Some(key)) {
                Some(Value::Asset(id)) if id >> 48 == 0x0dc0 && id as u32 != 0 => {
                    self.world.body_hardpoint_position(site.entity, id)
                }
                _ => None,
            }
        };
        let Some(origin) = origin.filter(|point| point.iter().all(|v| v.is_finite())) else {
            return self.post(key.instance, Event::Finish(key.raw));
        };
        let speed = self
            .evaluate(
                key.instance,
                node,
                field(&node.fields, "m_speed"),
                Some(key),
            )
            .map(|value| script_expr::to_float(&value))
            .unwrap_or(0.0);
        let length = self
            .evaluate(
                key.instance,
                node,
                field(&node.fields, "m_2AFC083F"),
                Some(key),
            )
            .map(|value| script_expr::to_float(&value))
            .unwrap_or(0.0);
        if !(speed.is_finite() && speed > 0.0 && length.is_finite() && length > 0.0) {
            return self.post(key.instance, Event::Finish(key.raw));
        }
        let Some(category) = self.world.owner_collision_category(site) else {
            return self.post(key.instance, Event::Finish(key.raw));
        };
        let Some(hit) = self
            .world
            .profiled_static_ray(site, origin, direction, length, category)
        else {
            return self.post(key.instance, Event::Finish(key.raw));
        };
        // Only traces with an authored final-attachment output can publish
        // a distinct edge candidate. The geometry does not drive the mover.
        let geometry = lvalue(field(&node.fields, "m_58B4C4D7"))
            .ok()
            .flatten()
            .is_some()
            .then(|| self.world.hook_pull_geometry(site))
            .flatten();
        let (anchor, feet, face, promoted) = match (geometry, hit) {
            // A direct hit on a walkable surface does not need edge assist.
            // In particular, a neighbouring face sampled by the fan must
            // not replace a valid floor hit.
            (Some(geometry), hit) if hit.is_none_or(|hit| hit.normal[1] < geometry.slope_cos) => {
                match self
                    .grapple_promotion(site, category, geometry, origin, direction, length, hit)
                {
                    Some(promotion) => (
                        Some(promotion.anchor),
                        Some(promotion.feet),
                        Some(promotion.face),
                        true,
                    ),
                    None => (
                        hit.map(|hit| hit.point),
                        None,
                        hit.map(|hit| hit.normal),
                        false,
                    ),
                }
            }
            (_, hit) => (
                hit.map(|hit| hit.point),
                None,
                hit.map(|hit| hit.normal),
                false,
            ),
        };
        let distance = match (promoted, anchor, hit) {
            (true, Some(anchor), _) => length.min(gap(origin, anchor)),
            (_, _, Some(hit)) => hit.distance,
            _ => length,
        };
        let duration = (distance / speed * 1000.0).ceil();
        // Keep authored work within a finite scheduling horizon.
        if !(0.0..=f64::from(u32::MAX)).contains(&duration) {
            return self.post(key.instance, Event::Finish(key.raw));
        }
        self.state_mut(key).expect("active trace").kind = StateKind::StaticTrace {
            origin,
            direction,
            speed,
            length,
            distance,
            category,
            anchor,
            raw_hit: hit.map(|hit| hit.point),
            face,
            promoted,
            standing: feet,
        };
        self.trace_write(key, node, "m_5ED803CF", Value::Vector(origin))?;
        self.trace_write(key, node, "m_F2F8606D", Value::Vector(origin))?;
        self.trace_write(key, node, "m_64801077", Value::Vector(direction))?;
        self.register_tick(key, self.machine.now_ms);
        self.post_after(key.instance, duration as u64, Event::Timer(key.raw, 1))
    }

    pub(in crate::script_init) fn tick_static_trace(
        &mut self,
        key: StateKey,
    ) -> Result<(), InitFault> {
        let Some(state) = self.state(key) else {
            return Ok(());
        };
        let StateKind::StaticTrace {
            origin,
            direction,
            speed,
            length,
            distance,
            category,
            promoted,
            anchor,
            raw_hit: _,
            face,
            standing,
        } = state.kind
        else {
            return Ok(());
        };
        let elapsed = self.machine.now_ms.saturating_sub(state.start_ms);
        let reached = elapsed as f64 >= (distance / speed * 1000.0).ceil();
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("trace node");
        if !reached {
            let travel = elapsed as f64 * 0.001 * speed;
            let endpoint = match (promoted, anchor) {
                // A promoted rope already knows its validated final anchor
                // at launch and never revalidates it, so the presented tip
                // interpolates origin -> anchor across the same authored
                // flight instead of running along the raw ray and snapping
                // at arrival. Without a promotion there is no final anchor
                // to aim at (a miss has none, a direct hit's anchor sits on
                // the raw ray anyway), so every unpromoted flight keeps the
                // historical trajectory.
                (true, Some(anchor)) => {
                    let ratio = (travel / distance).min(1.0);
                    std::array::from_fn(|i| origin[i] + (anchor[i] - origin[i]) * ratio)
                }
                _ => std::array::from_fn(|i| origin[i] + direction[i] * travel.min(distance)),
            };
            self.trace_write(key, node, "m_F2F8606D", Value::Vector(endpoint))?;
            // The subscription from begin drives one publish per pose of the
            // flight (grapple.py driver `tick`); the posted Timer event also
            // routes here on the authored arrival instant.
            return Ok(());
        } else if speed > 0.0 {
            // A promoted rope writes its validated transition: the anchor sits
            // on the lip line and the movement target is the standing point —
            // a footprint behind the lip, because a centre on the face edge
            // would half-bury the capsule and the arriving sweep would launch
            // the body off the wall (grapple.py `request_finish` T-0013). A
            // direct hit is re-cast at arrival, as before, and a miss keeps
            // the last flight endpoint it reached.
            match (anchor, face, promoted, standing) {
                (Some(anchor), Some(face), true, Some(feet)) => {
                    self.trace_write(key, node, "m_5ED803CF", Value::Vector(feet))?;
                    self.trace_write(key, node, "m_F2F8606D", Value::Vector(anchor))?;
                    self.trace_write(key, node, "m_58B4C4D7", Value::Vector(anchor))?;
                    self.trace_write(key, node, "m_D7A2A03B", Value::Vector(face))?;
                    self.trace_write(key, node, "m_13BE2D3D", Value::Vector(face))?;
                    self.trace_write(key, node, "m_9E1A7C58", Value::Bool(true))?;
                }
                (Some(_), Some(_), false, _) => {
                    if let Some(hit) = self
                        .world
                        .profiled_static_ray(
                            self.site(key.instance, node),
                            origin,
                            direction,
                            length,
                            category,
                        )
                        .flatten()
                    {
                        self.trace_write(key, node, "m_5ED803CF", Value::Vector(hit.point))?;
                        self.trace_write(key, node, "m_F2F8606D", Value::Vector(hit.point))?;
                        self.trace_write(key, node, "m_58B4C4D7", Value::Vector(hit.point))?;
                        self.trace_write(key, node, "m_D7A2A03B", Value::Vector(hit.normal))?;
                        self.trace_write(key, node, "m_13BE2D3D", Value::Vector(hit.normal))?;
                        self.trace_write(key, node, "m_9E1A7C58", Value::Bool(true))?;
                    }
                }
                _ => {
                    let travel = elapsed as f64 * 0.001 * speed;
                    let endpoint =
                        std::array::from_fn(|i| origin[i] + direction[i] * travel.min(distance));
                    self.trace_write(key, node, "m_F2F8606D", Value::Vector(endpoint))?;
                }
            }
        }
        self.post(key.instance, Event::Finish(key.raw))
    }

    /// Write one authored output of the trace state, if it is wired.
    fn trace_write(
        &mut self,
        key: StateKey,
        node: &Node,
        port: &str,
        value: Value,
    ) -> Result<(), InitFault> {
        let Some(variable) = lvalue(field(&node.fields, port)).ok().flatten() else {
            return Ok(());
        };
        let site = match variable.scope {
            script_expr::Scope::Entity => VarKey::Entity(variable.id),
            script_expr::Scope::Instance => VarKey::Instance(key.instance, variable.id),
        };
        self.write(site, value)
    }

    fn ray(
        &self,
        site: Site,
        origin: Vec3,
        direction: Vec3,
        length: f64,
        category: u16,
    ) -> Option<SpatialRayHit> {
        self.world
            .profiled_static_ray(site, origin, direction, length, category)
            .flatten()
    }

    /// The reference `_ledge_promotion`: the centre (its own aim assist on a
    /// centre miss), the capsule-sized pitch/yaw steps and their doubles,
    /// then the k=1 diagonals. Every candidate additionally pays the lateral
    /// error against the original aim, measured componentwise at its own
    /// distance (one capsule radius in elevation, one diameter in azimuth).
    /// The nearest to the aim wins; ties keep the scan order (left first).
    #[allow(clippy::too_many_arguments)]
    fn grapple_promotion(
        &self,
        site: Site,
        category: u16,
        geometry: HookPullGeometry,
        origin: Vec3,
        direction: Vec3,
        length: f64,
        hit: Option<SpatialRayHit>,
    ) -> Option<HookPromotion> {
        let HookPullGeometry {
            radius, step_down, ..
        } = geometry;
        let reach = hit.map_or(length, |hit| gap(origin, hit.point).max(1e-3));
        let pitch = (radius / reach).atan();
        let yaw = (2.0 * radius / reach).atan();
        let elev = direction[1].atan2(flat_len(direction));
        let base_yaw = direction[0].atan2(direction[2]);
        let offsets = [
            (0.0, 0.0),
            (0.0, pitch),
            (0.0, -pitch),
            (yaw, 0.0),
            (-yaw, 0.0),
            (0.0, 2.0 * pitch),
            (0.0, -2.0 * pitch),
            (2.0 * yaw, 0.0),
            (-2.0 * yaw, 0.0),
            (yaw, pitch),
            (yaw, -pitch),
            (-yaw, pitch),
            (-yaw, -pitch),
        ];
        let mut best: Option<(f64, usize, HookPromotion)> = None;
        for (index, (d_yaw, d_pitch)) in offsets.into_iter().enumerate() {
            let promotion = if index == 0 {
                let Some(hit) = hit else {
                    // Aim assist, separate from the capsule-leniency fan:
                    // one bounded edge crossing under the ray end.
                    match self.assist_candidate(site, category, geometry, origin, direction, length)
                    {
                        Ok(promotion) => {
                            best = Some((0.0, index, promotion));
                            break;
                        }
                        Err(_) => continue,
                    }
                };
                if hit.normal[1].abs() > 0.5 {
                    continue; // only near-vertical faces can hide a lip
                }
                match self.ledge_candidate(
                    site, category, geometry, origin, hit.point, hit.normal, length, step_down,
                ) {
                    Ok(promotion) => promotion,
                    Err(_) => continue,
                }
            } else {
                if flat_len(direction) < 0.25 {
                    continue; // near-vertical rays have no meaningful yaw step
                }
                let az = base_yaw + d_yaw;
                let el = elev + d_pitch;
                let ray = [az.sin() * el.cos(), el.sin(), az.cos() * el.cos()];
                let Some(scan) = self.ray(site, origin, ray, length, category) else {
                    continue;
                };
                if scan.normal[1].abs() > 0.5 {
                    continue; // only near-vertical faces can hide a lip
                }
                match self.ledge_candidate(
                    site,
                    category,
                    geometry,
                    origin,
                    scan.point,
                    scan.normal,
                    length,
                    step_down,
                ) {
                    Ok(promotion) => promotion,
                    Err(_) => continue,
                }
            };
            // Capsule tolerance at the ACTUAL edge distance, componentwise:
            // a step twice as wide buys coverage, not a wider claim of
            // where the body would have hooked.
            let anchor = promotion.anchor;
            let line = gap(origin, anchor);
            let delta = add(anchor, scaled(origin, -1.0));
            let d_az = {
                let raw = delta[0].atan2(delta[2]) - base_yaw + std::f64::consts::PI;
                raw.rem_euclid(2.0 * std::f64::consts::PI) - std::f64::consts::PI
            };
            let d_el = delta[1].atan2(flat_len(delta)) - elev;
            if (line * d_el.tan()).abs() > radius + 1e-9
                || (line * d_az.tan()).abs() > 2.0 * radius + 1e-9
            {
                continue;
            }
            let away = scaled(delta, 1.0 / line.max(1e-9));
            let cosine = dot(away, direction).clamp(-1.0, 1.0);
            let angle = cosine.acos();
            if best
                .is_none_or(|(best_angle, best_index, _)| (angle, index) < (best_angle, best_index))
            {
                best = Some((angle, index, promotion));
            }
        }
        best.map(|(_, _, promotion)| promotion)
    }

    /// The reference `_ledge_candidate`: the hit face's plane carries a
    /// standable top within the mover's own step band, the rim is seen from
    /// the eye without an unrelated blocker, and the footprint holds on the
    /// top. A vertical wall puts the grid one footprint behind the face; a
    /// sloped face keeps its own hit point. The first spot of the standing
    /// list (queried point, then the rim axes one footprint over) that
    /// passes all checks makes the transition.
    #[allow(clippy::too_many_arguments)]
    fn ledge_candidate(
        &self,
        site: Site,
        category: u16,
        geometry: HookPullGeometry,
        origin: Vec3,
        hit: Vec3,
        face: Vec3,
        length: f64,
        step_down: f64,
    ) -> Result<HookPromotion, &'static str> {
        let radius = geometry.radius;
        let slope = geometry.slope_cos;
        let scale = flat_len(face);
        if scale < 0.5 {
            return Err("flat-top"); // a floor/ceiling answer, not a wall or a slope
        }
        let contour = [-face[2] / scale, 0.0, face[0] / scale];
        let mut uphill = [
            -face[0] * face[1],
            1.0 - face[1] * face[1],
            -face[2] * face[1],
        ];
        let norm = gap(uphill, [0.0; 3]);
        if norm < 1e-6 {
            return Err("flat-top");
        }
        uphill = scaled(uphill, 1.0 / norm);
        if uphill[1] < 0.05 {
            return Err("flat-top"); // too flat a plane to carry a rim
        }
        let sloped = face[1].abs() >= 0.05;
        let (centre, inward) = if sloped {
            (hit, [uphill[0], 0.0, uphill[2]])
        } else {
            // A full footprint BEHIND the face: the grid measures the cap
            // top itself, so the standing point is never flush with the
            // wall plane (grapple.py `_ledge_candidate`).
            let inset = 2.0 * radius / scale;
            (
                add(hit, scaled(face, -inset)),
                [-face[0] / scale, 0.0, -face[2] / scale],
            )
        };
        // One candidate, one answer: the reference `_transition` measures
        // the top and the support band from THIS ray's hit only — re-scan
        // would move the lip by the candidate's own step. A ray whose
        // bounded probes see no walkable top is rejected outright, never
        // promoted as a bare hit (grapple.py `_ledge_candidate`).
        let top_y = hit[1] + radius; // a top higher than one capsule radius is a climb
        let floor_y = hit[1] - step_down; // lower than the step band is a drop
        let mut patches: Vec<(f64, usize, Vec3)> = Vec::new();
        for (order, (de, di)) in GRID.into_iter().enumerate() {
            let point = grid_point(centre, contour, inward, de, di, radius);
            let from = [point[0], top_y + 0.02, point[2]];
            let Some(probe) = self.ray(
                site,
                from,
                [0.0, -1.0, 0.0],
                top_y + 0.02 - floor_y,
                category,
            ) else {
                continue;
            };
            if probe.normal[1] <= slope {
                continue; // a hole (the face's own plane) or an unclimbable answer
            }
            // The reference support probe has no downward query at the
            // standing point — the answer itself is the centre the mover
            // stands on (the disk may overhang the rim; the ring margins
            // check it in `_standable`).
            let feet = probe.point;
            patches.push(((probe.point[1] - hit[1]).abs(), order, feet));
        }
        if patches.is_empty() {
            return Err("no-top");
        }
        // The nearest top plane to the impact wins; the grid order breaks ties.
        patches.sort_by(|a, b| {
            if a.0 < b.0 {
                std::cmp::Ordering::Less
            } else if a.0 > b.0 {
                std::cmp::Ordering::Greater
            } else {
                a.1.cmp(&b.1)
            }
        });
        let mut reason = "window";
        for (_delta, _order, feet) in patches {
            if !(floor_y <= feet[1] && feet[1] <= top_y) {
                continue; // below the step or above the lip reach: not a lip
            }
            let anchor = if sloped {
                // The plane equation carries the slope case down: a negative
                // climb is the real map's lower support.
                let climb = (feet[1] - hit[1]) / uphill[1];
                add(hit, scaled(uphill, climb))
            } else {
                [hit[0], feet[1], hit[2]]
            };
            let line = gap(origin, anchor);
            if line > length {
                reason = "range"; // beyond the authored rope: fallback, never a jump
                continue;
            }
            if !self.sight_clear(site, category, origin, anchor, line, face, hit, feet) {
                reason = if self
                    .sight_answer(site, category, origin, anchor, line)
                    .is_some()
                {
                    "sight-blocked"
                } else {
                    "sight-open"
                };
                continue;
            }
            let shift = 1.9 * radius; // probe reach (>= r) + the 0.7r ring footprint
            let spots = [
                feet,
                add(feet, scaled(contour, shift)),
                add(feet, scaled(contour, -shift)),
                add(feet, scaled(inward, shift)),
                add(feet, scaled(inward, -shift)),
            ];
            for spot in spots {
                if self.standable(
                    site,
                    category,
                    geometry,
                    spot,
                    sloped.then_some((face, hit)),
                    contour,
                    inward,
                ) {
                    return Ok(HookPromotion { anchor, feet, face });
                }
            }
            reason = "footprint";
        }
        Err(reason)
    }

    /// Aim assist for a centre miss (grapple.py `_assist_candidate`): ONE
    /// down-probe below the authored ray's end and ONE bounded face
    /// crossing. The edge sits exactly where the aim line sinks into the
    /// standable plane, within the capsule's own reach of the end — never a
    /// widened tolerance, never a longer rope, never a vertical search.
    fn assist_candidate(
        &self,
        site: Site,
        category: u16,
        geometry: HookPullGeometry,
        origin: Vec3,
        direction: Vec3,
        length: f64,
    ) -> Result<HookPromotion, &'static str> {
        let HookPullGeometry {
            radius,
            step_down,
            slope_cos,
            ..
        } = geometry;
        let end = add(origin, scaled(direction, length));
        let Some(probe) = self.ray(
            site,
            [end[0], end[1] + radius, end[2]],
            [0.0, -1.0, 0.0],
            radius + step_down,
            category,
        ) else {
            return Err("assist-miss");
        };
        if probe.normal[1] <= slope_cos {
            return Err("assist-face");
        }
        let feet = probe.point;
        let reach = 2.0 * radius;
        let crossing = (feet[1] - origin[1]) / direction[1];
        if crossing <= 0.0 {
            return Err("no-edge"); // the plane was left behind at the feet
        }
        if crossing > length {
            return Err("range"); // the edge sits beyond the authored rope
        }
        if crossing < length - reach {
            return Err("no-edge"); // the ray sank in far before the end: the
            // aim sailed over this edge — measure it, do not widen for it
        }
        let anchor = add(origin, scaled(direction, crossing));
        if self
            .ray(site, origin, direction, crossing + 0.05, category)
            .is_some_and(|sight| gap(sight.point, anchor) > 0.05)
        {
            return Err("sight-blocked"); // unrelated geometry between eye and edge
        }
        let flat = flat_len(direction);
        if flat < 1e-6 {
            return Err("no-edge"); // straight up: no horizontal frame to stand on
        }
        let contour = [-direction[2] / flat, 0.0, direction[0] / flat];
        let inward = [direction[0] / flat, 0.0, direction[2] / flat];
        let shift = 1.9 * radius;
        let spots = [
            feet,
            add(feet, scaled(contour, shift)),
            add(feet, scaled(contour, -shift)),
        ];
        for spot in spots {
            if self.standable(site, category, geometry, spot, None, contour, inward) {
                return Ok(HookPromotion {
                    anchor,
                    feet,
                    face: probe.normal,
                });
            }
        }
        Err("footprint")
    }

    /// Footprint, support and headroom for a standing point, probed in the
    /// rim-local frame — never along world axes (grapple.py `_standable`).
    /// Every ring centre must pass `_support_clear`: a support cross at the
    /// ring footprint, the lip's own plane accepted as leanable support,
    /// flat answers bound by the mover's own step band, and the headroom
    /// over the ring.
    #[allow(clippy::too_many_arguments)]
    fn standable(
        &self,
        site: Site,
        category: u16,
        geometry: HookPullGeometry,
        spot: Vec3,
        sloped: Option<(Vec3, Vec3)>,
        contour: Vec3,
        inward: Vec3,
    ) -> bool {
        let HookPullGeometry {
            radius,
            segment,
            step_down,
            slope_cos,
        } = geometry;
        let footprint = 0.7 * radius;
        let centres = [
            spot,
            add(spot, scaled(contour, footprint)),
            add(spot, scaled(contour, -footprint)),
            add(spot, scaled(inward, footprint)),
            add(spot, scaled(inward, -footprint)),
        ];
        for centre in centres {
            if !self.support_clear(
                site, category, radius, segment, slope_cos, step_down, centre, contour, inward,
                sloped, spot,
            ) {
                return false;
            }
        }
        true
    }

    /// One ring centre: the support cross along the rim-local axes and the
    /// headroom over it (grapple.py `_support_clear`). The centre itself was
    /// found by the grid or the assist probe, so its own point is on the
    /// surface found there; the cross probes at ±0.7 r along contour and
    /// inward. With a hit face, an answer coplanar with it is the rim's own
    /// plane (the slope continuing under the cap) — leanable support the
    /// mover climbs on, no walkability or height test. Without one, the
    /// answer must be a walkable plane within the mover's own step band.
    #[allow(clippy::too_many_arguments)]
    fn support_clear(
        &self,
        site: Site,
        category: u16,
        radius: f64,
        segment: f64,
        slope_cos: f64,
        step_down: f64,
        centre: Vec3,
        contour: Vec3,
        inward: Vec3,
        rim: Option<(Vec3, Vec3)>,
        feet: Vec3,
    ) -> bool {
        let footprint = 0.7 * radius;
        let (face, on_face) = rim.unwrap_or(([0.0; 3], [0.0; 3]));
        let has_face = rim.is_some();
        let coplanar =
            |point: Vec3| has_face && dot(face, add(point, scaled(on_face, -1.0))).abs() <= 0.02;
        for (de, di) in [(-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0)] {
            let axis = add(scaled(contour, de), scaled(inward, di));
            let Some(spot) = self.ray(
                site,
                add(centre, [axis[0] * footprint, radius, axis[2] * footprint]),
                [0.0, -1.0, 0.0],
                2.0 * radius,
                category,
            ) else {
                return false;
            };
            if coplanar(spot.point) {
                continue; // leaning on the lip's own face is what the mover climbs
            }
            if spot.normal[1] <= slope_cos {
                return false;
            }
            if !(feet[1] - step_down <= spot.point[1] && spot.point[1] <= feet[1] + step_down) {
                return false;
            }
        }
        let over = [centre[0], centre[1] + radius, centre[2]];
        let roof_plane =
            |point: Vec3| has_face && dot(face, add(point, scaled(on_face, -1.0))).abs() > 1e-3;
        if self
            .ray(site, over, [0.0, 1.0, 0.0], radius + segment, category)
            .is_some_and(|roof| roof_plane(roof.point))
        {
            return false; // a real roof within the lift
        }
        let _ = 2.0 * radius;
        let clearance = self.ray(site, over, [0.0, 1.0, 0.0], 2.0 * radius, category);
        let Some(clearance) = clearance else {
            return true; // open sky over the ring
        };
        !has_face || dot(face, add(clearance.point, scaled(on_face, -1.0))).abs() <= 0.05
    }

    /// The eye-to-anchor line must be free of unrelated geometry (grapple.py
    /// `_sight_clear`). An answer passes only when it lies ON one of the
    /// transition's own planes (the same 0.05 skin as the fin check): the
    /// vertical rim cross-section through the anchor, the hit face's plane,
    /// or the candidate's standing-top plane. Proximity to the anchor
    /// excuses nothing by itself. No answer at all is the open line.
    #[allow(clippy::too_many_arguments)]
    fn sight_clear(
        &self,
        site: Site,
        category: u16,
        origin: Vec3,
        anchor: Vec3,
        line: f64,
        face: Vec3,
        hit: Vec3,
        feet: Vec3,
    ) -> bool {
        let direction = scaled(add(anchor, scaled(origin, -1.0)), 1.0 / line.max(1e-9));
        let Some(sight) = self.ray(site, origin, direction, line + 0.05, category) else {
            return true;
        };
        let point = sight.point;
        let scale = flat_len(face);
        if scale >= 1e-6 {
            // vertical plane through the anchor with the face's horizontal
            // normal: the rim's own cross-section (cap front wall, the lip
            // edge itself)
            let err = ((point[0] - anchor[0]) * face[0] / scale
                + (point[2] - anchor[2]) * face[2] / scale)
                .abs();
            if err <= 0.05 {
                return true;
            }
        }
        if dot(face, add(point, scaled(hit, -1.0))).abs() <= 0.05
            || (point[1] - feet[1]).abs() <= 0.05
        {
            return true; // the hit face or the top plane
        }
        false
    }

    fn sight_answer(
        &self,
        site: Site,
        category: u16,
        origin: Vec3,
        anchor: Vec3,
        line: f64,
    ) -> Option<Vec3> {
        let direction = scaled(add(anchor, scaled(origin, -1.0)), 1.0 / line.max(1e-9));
        self.ray(site, origin, direction, line + 0.05, category)
            .filter(|sight| gap(sight.point, anchor) > 0.05)
            .map(|sight| sight.point)
    }
}
