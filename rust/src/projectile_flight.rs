//! Server-side projectile flight and splash for build 104319.
//!
//! Uses the swept target capsules and map ray from the match. Damage and
//! network delivery are separate so a predicted alias never hits twice.
//! The reference is ow174/game/combat.py.

use serde_json::Value as Json;

use crate::{
    collision_index::IndexError,
    game_clock::TICK_PERIOD,
    game_hitscan::{HitDefinition, Impact, TargetPose},
    game_projectiles::authored_number,
    script_expr::EvalError,
    script_graph::Node,
    script_wire::Value,
    static_ray::{Vec3, along, capsule_entry_distance, norm, segment_separation},
};

const DEFAULT_LIFETIME: f64 = 5.0;
const EPSILON: f64 = 1e-9;
const IMPACT_RAY_OFFSET: f64 = 1e-4;

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileBallistics {
    pub speed: f64,
    pub lifetime: f64,
    pub definition: Option<u64>,
    pub direct: Option<HitDefinition>,
    pub blast: Option<Blast>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Blast {
    pub hit: HitDefinition,
    pub rings: Vec<(f64, f64)>,
}

impl ProjectileBallistics {
    pub fn from_authored(
        node: &Node,
        evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
    ) -> Result<Option<Self>, EvalError> {
        Self::from_fields(&node.fields, evaluate)
    }

    pub(crate) fn from_fields(
        fields: &Json,
        mut evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
    ) -> Result<Option<Self>, EvalError> {
        let field = |path| crate::script_graph::field_at(fields, path);
        let speed = authored_number(field("m_projectileSpeed"), 0.0, &mut evaluate)?;
        if !speed.is_finite() || speed <= 0.0 {
            return Ok(None);
        }
        let mut lifetime = authored_number(
            field("m_projectileLifetime"),
            DEFAULT_LIFETIME,
            &mut evaluate,
        )?;
        if lifetime == 0.0 {
            lifetime = DEFAULT_LIFETIME;
        }
        let reach = authored_number(field("m_FEC435C6"), 0.0, &mut evaluate)?;
        if reach != 0.0 {
            lifetime = lifetime.min(reach / speed);
        }
        if !lifetime.is_finite() || lifetime <= 0.0 {
            return Ok(None);
        }
        let definition = match field("m_projectileEntity.m_entityDef") {
            Some(config) => match evaluate(config)? {
                Value::Asset(definition) => Some(definition),
                _ => None,
            },
            None => None,
        };
        let direct = HitDefinition::from_authored(
            field("m_6396149F").unwrap_or(&Json::Null),
            &mut evaluate,
        )?;
        let config = field("m_D2D11CE9").unwrap_or(&Json::Null);
        let blast = if let Some(hit) = HitDefinition::from_authored(config, &mut evaluate)? {
            let mut rings = Vec::new();
            if let Some(rows) = config.get("m_0D30E40C").and_then(Json::as_array) {
                for row in rows.iter().filter(|row| row.is_object()) {
                    let radius = authored_number(row.get("m_radius"), 0.0, &mut evaluate)?;
                    let scalar = authored_number(row.get("m_B399CACB"), 0.0, &mut evaluate)?;
                    if radius.is_finite() && scalar.is_finite() {
                        rings.push((radius, scalar));
                    }
                }
            }
            rings.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.total_cmp(&b.1)));
            Some(Blast { hit, rings })
        } else {
            None
        };
        Ok(Some(Self {
            speed,
            lifetime,
            definition,
            direct,
            blast,
        }))
    }
}

impl Blast {
    fn multiplier(&self, distance: f64) -> f64 {
        let Some(&(mut previous, mut scalar)) = self.rings.first() else {
            return 0.0;
        };
        if distance <= previous {
            return scalar;
        }
        for &(radius, next) in &self.rings[1..] {
            if distance <= radius {
                let share = (distance - previous) / (radius - previous).max(EPSILON);
                return scalar + (next - scalar) * share;
            }
            previous = radius;
            scalar = next;
        }
        0.0
    }
}

pub struct ProjectileFlight {
    ballistics: ProjectileBallistics,
    position: Vec3,
    direction: Vec3,
    remaining: f64,
    travelled: f64,
    last_tick: Option<u32>,
    ended: bool,
    direct_falloff: bool,
    expiry_blast: bool,
    gravity: Option<(f32, Vec3)>,
}

#[derive(Debug, PartialEq)]
pub struct FlightEnd {
    pub point: Vec3,
    pub travelled: f64,
    pub impacts: Vec<Impact>,
    pub expired: bool,
}

impl ProjectileFlight {
    pub fn new(
        ballistics: ProjectileBallistics,
        position: Vec3,
        direction: Vec3,
        fired_at: Option<u32>,
    ) -> Option<Self> {
        if !ballistics.speed.is_finite()
            || ballistics.speed <= 0.0
            || !ballistics.lifetime.is_finite()
            || ballistics.lifetime <= 0.0
            || !(ballistics.speed * ballistics.lifetime).is_finite()
            || !position.into_iter().chain(direction).all(f64::is_finite)
            || (direction.iter().map(|value| value * value).sum::<f64>() - 1.0).abs() > 1e-6
        {
            return None;
        }
        Some(Self {
            remaining: ballistics.lifetime,
            ballistics,
            position,
            direction,
            travelled: 0.0,
            last_tick: fired_at,
            ended: false,
            direct_falloff: false,
            expiry_blast: false,
            gravity: None,
        })
    }

    pub(crate) fn with_expiry_blast(mut self, enabled: bool) -> Self {
        self.expiry_blast = enabled;
        self
    }

    pub(crate) fn with_direct_falloff(mut self) -> Self {
        self.direct_falloff = true;
        self
    }

    pub(crate) fn with_gravity(mut self, gravity: f32) -> Self {
        self.gravity = Some((
            gravity,
            launch_velocity(self.direction, self.ballistics.speed),
        ));
        self
    }

    /// Advance the elapsed portion of a tick, even when it ends inside the
    /// tick due to range or lifetime. Static geometry wins only a closer hit.
    pub fn advance(
        &mut self,
        tick: u32,
        targets: &[(u32, TargetPose)],
        mut wall: impl FnMut(Vec3, Vec3, f64) -> Result<Option<f64>, IndexError>,
    ) -> Result<Option<FlightEnd>, IndexError> {
        if self.ended {
            return Ok(None);
        }
        let elapsed = self.last_tick.map_or(1, |last| tick.saturating_sub(last));
        if elapsed == 0 {
            return Ok(None);
        }
        let seconds = TICK_PERIOD.as_secs_f64();
        let mut end = None;
        if self.gravity.is_some() {
            for _ in 0..elapsed {
                let duration = seconds.min(self.remaining);
                let (gravity, velocity) = self.gravity.as_mut().expect("gravity flight");
                *velocity = gravity_step(*velocity, *gravity, duration);
                let speed = norm(*velocity);
                if speed > EPSILON {
                    self.direction = velocity.map(|axis| axis / speed);
                }
                end = self.advance_segment(duration, speed, targets, &mut wall)?;
                if end.is_some() {
                    break;
                }
            }
        } else {
            let duration = (f64::from(elapsed) * seconds).min(self.remaining);
            end = self.advance_segment(duration, self.ballistics.speed, targets, &mut wall)?;
        }
        self.last_tick = Some(tick);
        Ok(end)
    }

    fn advance_segment(
        &mut self,
        duration: f64,
        speed: f64,
        targets: &[(u32, TargetPose)],
        mut wall: impl FnMut(Vec3, Vec3, f64) -> Result<Option<f64>, IndexError>,
    ) -> Result<Option<FlightEnd>, IndexError> {
        let reach = speed * duration;
        let mut closest = None;
        for &(entity, target) in targets {
            let [a, b] = target.segment();
            if let Some(distance) =
                capsule_entry_distance(self.position, self.direction, reach, a, b, target.radius)
            {
                if closest.is_none_or(|(best, _)| distance < best) {
                    closest = Some((distance, Some(entity)));
                }
            }
        }
        if let Some(distance) = wall(self.position, self.direction, reach)?
            .filter(|distance| (0.0..=reach).contains(distance))
        {
            if closest.is_none_or(|(best, _)| distance < best) {
                closest = Some((distance, None));
            }
        }
        let Some((distance, target)) = closest else {
            self.position = along(self.position, self.direction, reach);
            self.travelled += duration;
            self.remaining -= duration;
            if self.remaining <= 0.0 {
                let impacts = if self.expiry_blast {
                    self.splash(self.position, self.position, targets, &mut wall)?
                } else {
                    Vec::new()
                };
                return Ok(Some(self.finish(impacts, true)));
            }
            return Ok(None);
        };
        let point = along(self.position, self.direction, distance);
        let mut impacts = Vec::new();
        if let (Some(entity), Some(direct)) = (
            target,
            self.ballistics.direct.filter(|hit| hit.amount > 0.0),
        ) {
            impacts.push(Impact {
                tags: direct.tags,
                entity,
                distance,
                damage: if self.direct_falloff {
                    direct.damage_at(self.travelled * self.ballistics.speed + distance, false)
                } else {
                    direct.amount
                },
                critical: false,
                loadout: direct.loadout,
            });
        }
        // Cast from the approach side when the projectile hit the map.
        let origin = if target.is_none() {
            along(
                self.position,
                self.direction,
                (distance - IMPACT_RAY_OFFSET).max(0.0),
            )
        } else {
            point
        };
        impacts.extend(self.splash(point, origin, targets, &mut wall)?);
        self.position = point;
        if speed > EPSILON {
            self.travelled += distance / speed;
        }
        Ok(Some(self.finish(impacts, false)))
    }

    fn splash(
        &self,
        point: Vec3,
        origin: Vec3,
        targets: &[(u32, TargetPose)],
        mut wall: impl FnMut(Vec3, Vec3, f64) -> Result<Option<f64>, IndexError>,
    ) -> Result<Vec<Impact>, IndexError> {
        let mut impacts = Vec::new();
        if let Some(blast) = self
            .ballistics
            .blast
            .as_ref()
            .filter(|blast| blast.hit.amount > 0.0)
        {
            for &(entity, pose) in targets {
                let [a, b] = pose.segment();
                let distance = (norm(segment_separation(point, a, b)) - pose.radius).max(0.0);
                let damage = blast.hit.amount * blast.multiplier(distance);
                if damage <= 0.0 {
                    continue;
                }
                let separation = segment_separation(origin, a, b);
                let axis_distance = norm(separation);
                let reach = axis_distance - pose.radius;
                if reach > EPSILON
                    && wall(
                        origin,
                        separation.map(|value| -value / axis_distance),
                        reach,
                    )?
                    .is_some_and(|hit| hit + EPSILON < reach)
                {
                    continue;
                }
                impacts.push(Impact {
                    tags: blast.hit.tags,
                    entity,
                    distance,
                    damage,
                    critical: false,
                    loadout: blast.hit.loadout,
                });
            }
        }
        Ok(impacts)
    }

    fn finish(&mut self, impacts: Vec<Impact>, expired: bool) -> FlightEnd {
        self.ended = true;
        FlightEnd {
            point: self.position,
            travelled: self.travelled,
            impacts,
            expired,
        }
    }
}

/// Native launch bias: float32 add to Y followed by normalization.
pub(crate) fn lifted_direction(direction: Vec3, lift: f64) -> Option<Vec3> {
    if !direction.into_iter().chain([lift]).all(f64::is_finite) {
        return None;
    }
    let [x, y, z] = direction.map(|value| value as f32);
    let y = y + lift as f32;
    let squared = (x * x + y * y) + z * z;
    if !squared.is_finite() {
        return None;
    }
    if squared < 1.175_494_35e-36 {
        return Some([-0.0, -1.0, -0.0]);
    }
    let inverse = 1.0 / f64::from(squared).sqrt();
    Some([x, y, z].map(|value| f64::from((f64::from(value) * inverse) as f32)))
}

pub(crate) fn launch_velocity(direction: Vec3, speed: f64) -> Vec3 {
    direction.map(|axis| f64::from((axis * speed) as f32))
}

pub(crate) fn gravity_step(mut velocity: Vec3, gravity: f32, duration: f64) -> Vec3 {
    velocity[1] = f64::from(velocity[1] as f32 - gravity * duration as f32);
    velocity
}

pub(crate) fn authored_gravity(
    fields: &Json,
    mut evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
) -> Option<f32> {
    let [motion] = fields.get("m_projectileMotions")?.as_array()?.as_slice() else {
        return None;
    };
    if motion.get("$")?.as_str()? != "STU_E1A5B579"
        || !motion["m_D0C4CF49"].is_null()
        || motion["m_599AB8D7"].is_null()
    {
        return None;
    }
    let gravity = crate::script_expr::to_float(&evaluate(&motion["m_599AB8D7"]).ok()?) as f32;
    (gravity.is_finite() && gravity > 0.0).then_some(gravity)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::collision_index::{SceneTriangle, TriangleIndex};

    const LOADOUT: u64 = 0x0b90_0000_0000_008e;

    fn helix() -> ProjectileBallistics {
        let hit = HitDefinition {
            tags: crate::game_hitscan::DamageTags::EMPTY,
            amount: 40.0,
            loadout: Some(LOADOUT),
            critical: 1.0,
            falloff: None,
        };
        ProjectileBallistics {
            speed: 50.0,
            lifetime: 2.0,
            definition: Some(0x0400_0000_0000_072c),
            direct: Some(hit),
            blast: Some(Blast {
                hit: HitDefinition {
                    tags: crate::game_hitscan::DamageTags::EMPTY,
                    amount: 80.0,
                    ..hit
                },
                rings: vec![(1.0, 1.0), (3.0, 0.5)],
            }),
        }
    }

    fn target(x: f64, z: f64) -> TargetPose {
        TargetPose {
            feet: [x, 0.0, z],
            height: 2.0,
            radius: 0.5,
            eye: 1.55,
        }
    }

    #[test]
    fn helix_sweeps_missed_ticks_and_applies_direct_damage_before_splash() {
        let targets = [
            (1, target(0.0, 3.0)),
            (2, target(2.5, 2.5)),
            (3, target(4.0, 2.5)),
        ];
        let mut flight =
            ProjectileFlight::new(helix(), [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], None).unwrap();
        assert!(
            flight
                .advance(1000, &targets, |_, _, _| Ok(None))
                .unwrap()
                .is_none()
        );
        assert!(
            flight
                .advance(1000, &targets, |_, _, _| panic!("duplicate tick"))
                .unwrap()
                .is_none()
        );
        let end = flight
            .advance(1004, &targets, |_, _, _| Ok(None))
            .unwrap()
            .unwrap();
        assert_eq!(end.point, [0.0, 1.0, 2.5]);
        assert!((end.travelled - 0.05).abs() < 1e-12);
        assert_eq!(
            end.impacts
                .iter()
                .map(|hit| (hit.entity, hit.damage, hit.critical))
                .collect::<Vec<_>>(),
            [(1, 40.0, false), (1, 80.0, false), (2, 60.0, false)]
        );
        assert!(
            end.impacts
                .iter()
                .all(|impact| impact.loadout == Some(LOADOUT))
        );
        assert!(
            flight
                .advance(1005, &targets, |_, _, _| panic!("already ended"))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn expiry_blast_uses_splash_radius_occlusion_and_once_only_retirement() {
        for enabled in [false, true] {
            for blocked in [false, true] {
                let mut data = helix();
                data.speed = 30.0;
                data.lifetime = 7.0 / 30.0;
                data.direct = None;
                let blast = data.blast.as_mut().unwrap();
                blast.hit.amount = 25.0;
                blast.rings = vec![(3.0, 1.0)];
                let direction = lifted_direction([0.0, 0.0, 1.0], 0.05).unwrap();
                let mut flight = ProjectileFlight::new(data, [0.0, 1.55, 0.0], direction, Some(0))
                    .unwrap()
                    .with_gravity(20.0)
                    .with_expiry_blast(enabled);
                // The target is off the flight axis but inside the range-end blast.
                let targets = [(1, target(2.0, 7.0)), (2, target(5.0, 7.0))];
                let end = (1..=30)
                    .find_map(|tick| {
                        flight
                            .advance(tick, &targets, |_, _, reach| {
                                Ok((blocked && reach > 1.0).then_some(0.5))
                            })
                            .unwrap()
                    })
                    .expect("range expiry");
                assert!(end.expired);
                assert!(end.point[1] < 1.55);
                assert!((end.point[2] - direction[2] * 7.0).abs() < 1e-6);
                let hits: Vec<_> = end
                    .impacts
                    .iter()
                    .map(|hit| (hit.entity, hit.damage))
                    .collect();
                assert_eq!(
                    hits,
                    if enabled && !blocked {
                        vec![(1, 25.0)]
                    } else {
                        vec![]
                    }
                );
                assert!(
                    flight
                        .advance(31, &targets, |_, _, _| panic!("already retired"))
                        .unwrap()
                        .is_none()
                );
            }
        }
    }

    #[test]
    fn gravity_flight_sweeps_each_segment_across_a_tick_gap() {
        let mut flight = ProjectileFlight::new(helix(), [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], Some(10))
            .unwrap()
            .with_gravity(20.0);
        let mut sweeps = Vec::new();
        assert!(
            flight
                .advance(13, &[], |origin, direction, length| {
                    sweeps.push((origin, direction, length));
                    Ok(None)
                })
                .unwrap()
                .is_none()
        );
        assert_eq!(sweeps.len(), 3);
        assert!(
            sweeps
                .windows(2)
                .all(|pair| pair[1].0[1] < pair[0].0[1] && pair[1].1[1] < pair[0].1[1])
        );
        assert!((flight.position[1] - 0.96928).abs() < 1e-8);
        assert!((flight.position[2] - 2.4).abs() < 1e-12);
        assert!(
            flight
                .advance(13, &[], |_, _, _| panic!("duplicate tick"))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn walls_win_closer_impact_and_last_partial_tick_is_swept() {
        let world = TriangleIndex::new([SceneTriangle {
            vertices: [[-5.0, 0.0, 1.0], [5.0, 0.0, 1.0], [0.0, 5.0, 1.0]],
            mask: 0x18,
            surface: 0,
        }])
        .unwrap();
        let mut flight =
            ProjectileFlight::new(helix(), [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], Some(1000)).unwrap();
        let end = flight
            .advance(
                1002,
                &[(1, target(0.0, 3.0)), (2, target(1.0, 0.5))],
                |origin, direction, reach| {
                    world
                        .cast_ray(origin, direction, reach, 0x18)
                        .map(|hit| hit.map(|hit| hit.distance))
                },
            )
            .unwrap()
            .unwrap();
        assert_eq!(end.point, [0.0, 1.0, 1.0]);
        assert!(!end.expired);
        assert_eq!(end.impacts.len(), 1);
        assert_eq!((end.impacts[0].entity, end.impacts[0].damage), (2, 80.0));
        let short = ProjectileBallistics {
            lifetime: 0.01,
            ..helix()
        };
        let mut flight =
            ProjectileFlight::new(short.clone(), [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], None).unwrap();
        let end = flight
            .advance(1000, &[(1, target(0.0, 1.0))], |_, _, _| Ok(None))
            .unwrap()
            .unwrap();
        assert_eq!((end.point[2], end.travelled), (0.5, 0.01));
        assert_eq!(end.impacts[0].damage, 40.0);
        assert!(!end.expired);
        let mut flight =
            ProjectileFlight::new(short, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], None).unwrap();
        let end = flight
            .advance(1000, &[(1, target(1.0, 1.0))], |_, _, _| Ok(None))
            .unwrap()
            .unwrap();
        assert_eq!((end.point[2], end.travelled), (0.5, 0.01));
        assert!(end.impacts.is_empty());
        assert!(end.expired);
    }
}
