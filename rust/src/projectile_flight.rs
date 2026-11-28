//! Server-side projectile flight and splash for build 104319.
//!
//! Uses the swept target capsules and map ray from the match. Damage and
//! network delivery are separate so a predicted alias never hits twice.
//! The reference is ow174/game/combat.py.

use serde_json::Value as Json;

pub use crate::script_init::Knockback;

#[cfg(test)]
#[path = "projectile_flight/knockback.rs"]
mod knockback_tests;

use crate::{
    collision_index::IndexError,
    game_clock::TICK_PERIOD,
    game_hitscan::{HitDefinition, HitTargetPolicy, HurtPose, Impact, ImpactGeometry, TargetPose},
    game_projectiles::authored_number,
    script_expr::EvalError,
    script_graph::Node,
    script_wire::Value,
    static_ray::{MIN_SURFACE_DISTANCE, SurfaceHit, Vec3, along, norm},
};

const DEFAULT_LIFETIME: f64 = 5.0;
const EPSILON: f64 = 1e-9;
const DISTANCE_ONLY_RAY_OFFSET: f64 = 1e-4;
const SURFACE_RAY_OFFSET: f64 = 2.0 * MIN_SURFACE_DISTANCE;
const REBOUND_SEPARATION: f64 = 0.001;

#[derive(Clone, Copy)]
struct WallContact {
    distance: f64,
    normal: Option<Vec3>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectileBallistics {
    pub speed: f64,
    pub lifetime: f64,
    pub max_distance: Option<f64>,
    pub radius: f64,
    pub(crate) terrain_radius: f32,
    pub definition: Option<u64>,
    pub direct: Option<HitDefinition>,
    pub blast: Option<Blast>,
    pub(crate) terrain_rebound: Option<TerrainRebound>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Blast {
    pub hit: HitDefinition,
    pub rings: Vec<(f64, f64)>,
    pub self_damage_scale: f64,
    pub self_knockback_scale: f32,
    pub knockback: Option<Knockback>,
    pub detonate_on_expiry: bool,
}

impl ProjectileBallistics {
    pub fn from_authored(
        node: &Node,
        evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
    ) -> Result<Option<Self>, EvalError> {
        Self::from_fields_with_critical(&node.fields, 1.0, evaluate)
    }

    pub(crate) fn from_authored_with_critical(
        node: &Node,
        default_critical: f64,
        evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
    ) -> Result<Option<Self>, EvalError> {
        Self::from_fields_with_critical(&node.fields, default_critical, evaluate)
    }

    pub(crate) fn from_fields(
        fields: &Json,
        evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
    ) -> Result<Option<Self>, EvalError> {
        Self::from_fields_with_critical(fields, 1.0, evaluate)
    }

    fn from_fields_with_critical(
        fields: &Json,
        default_critical: f64,
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
        if !lifetime.is_finite() || lifetime <= 0.0 || !reach.is_finite() || reach < 0.0 {
            return Ok(None);
        }
        let definition = match field("m_projectileEntity.m_entityDef") {
            Some(config) => match evaluate(config)? {
                Value::Asset(0)
                    if config["$"] == "STU_8556841E"
                        && config.get("m_entityDef") == Some(&Json::Null)
                        && config.get("m_resourceKey") == Some(&Json::Null)
                        && crate::script_expr::native_fields(
                            config,
                            &["m_entityDef", "m_resourceKey"],
                        ) =>
                {
                    None
                }
                Value::Asset(0) => {
                    return Err(EvalError::MalformedConfig("projectile entity definition"));
                }
                Value::Asset(definition) => Some(definition),
                _ => None,
            },
            None => None,
        };
        let radius = authored_number(field("m_6396149F.m_57DAE1B5"), 0.0, &mut evaluate)?;
        let ally_radius = authored_number(field("m_6396149F.m_71A4B759"), 0.0, &mut evaluate)?;
        let radius = if radius.is_finite() && radius > 0.0 {
            radius
        } else if ally_radius.is_finite() && ally_radius > 0.0 {
            ally_radius
        } else {
            0.0
        };
        let terrain_radius = field("m_8236947A")
            .filter(|config| config["$"] == "STU_20C861FB")
            .and_then(|config| config.get("m_9655ED90"));
        let terrain_radius = authored_number(terrain_radius, 0.0, &mut evaluate)? as f32;
        if !terrain_radius.is_finite() || terrain_radius < 0.0 {
            return Err(EvalError::MalformedConfig("projectile terrain radius"));
        }
        let direct = HitDefinition::from_authored_with_critical(
            field("m_6396149F").unwrap_or(&Json::Null),
            default_critical,
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
            let scale = authored_number(config.get("m_8A3E3883"), 0.0, &mut evaluate)?;
            let self_knockback = &config["m_49F60FB8"];
            let self_knockback_enabled = self_knockback.as_bool() == Some(true)
                || self_knockback.as_u64() == Some(1)
                || (!self_knockback.is_null()
                    && self_knockback.is_object()
                    && evaluate(self_knockback)
                        .ok()
                        .is_some_and(|v| crate::script_expr::truthy(&v)));
            let self_knockback_scale = if self_knockback_enabled {
                authored_number(config.get("m_2FE97560"), 1.0, &mut evaluate).unwrap_or(1.0) as f32
            } else {
                0.0
            };
            Some(Blast {
                hit,
                rings,
                self_damage_scale: if scale.is_finite() {
                    scale.max(0.0)
                } else {
                    0.0
                },
                self_knockback_scale: if self_knockback_scale.is_finite() {
                    self_knockback_scale.max(0.0)
                } else {
                    0.0
                },
                knockback: Knockback::from_authored(&config["m_modifyHealth"], &mut evaluate),
                detonate_on_expiry: config
                    .get("m_EEB99E41")
                    .filter(|value| !value.is_null())
                    .and_then(|value| evaluate(value).ok())
                    .is_some_and(|value| crate::script_expr::truthy(&value)),
            })
        } else {
            None
        };
        Ok(Some(Self {
            speed,
            lifetime,
            max_distance: (reach > 0.0).then_some(reach),
            radius,
            terrain_radius,
            definition,
            direct,
            blast,
            terrain_rebound: TerrainRebound::from_authored(field("m_E365DB20"), &mut evaluate)?,
        }))
    }
}

/// Static terrain response declared by the shared volley configuration.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct TerrainRebound {
    limit: u32,
    normal_retention: f64,
    tangent_retention: f64,
    tangent_loss: f64,
}

impl TerrainRebound {
    fn from_authored(
        config: Option<&Json>,
        evaluate: &mut impl FnMut(&Json) -> Result<Value, EvalError>,
    ) -> Result<Option<Self>, EvalError> {
        let Some(config) = config.filter(|config| config["$"] == "STU_3FE1A93B") else {
            return Ok(None);
        };
        let Some(enabled) = config.get("m_D9EFD182").filter(|config| !config.is_null()) else {
            return Ok(None);
        };
        if !crate::script_expr::truthy(&evaluate(enabled)?) {
            return Ok(None);
        }
        let (Some(limit), Some(normal)) = (
            config.get("m_360294F0").filter(|config| !config.is_null()),
            config.get("m_0D20DAFD").filter(|config| !config.is_null()),
        ) else {
            // The native defaults for these operands are not established.
            return Ok(None);
        };
        let Value::Integer(limit) = evaluate(limit)? else {
            return Err(EvalError::MalformedConfig("projectile rebound count"));
        };
        let limit = u32::try_from(limit)
            .map_err(|_| EvalError::MalformedConfig("projectile rebound count"))?;
        let normal_retention = authored_number(Some(normal), -1.0, evaluate)?;
        let tangent_retention = authored_number(config.get("m_C29D35E8"), 1.0, evaluate)?;
        let tangent_loss = authored_number(config.get("m_CE33458D"), 0.0, evaluate)? * 0.016;
        if !(0.0..=1.0).contains(&normal_retention)
            || !(0.0..=1.0).contains(&tangent_retention)
            || !tangent_loss.is_finite()
            || tangent_loss < 0.0
        {
            return Err(EvalError::MalformedConfig(
                "projectile rebound coefficients",
            ));
        }
        Ok(Some(Self {
            limit,
            normal_retention,
            tangent_retention,
            tangent_loss,
        }))
    }

    fn reflect(self, incident: Vec3, normal: Vec3) -> Vec3 {
        let approach = incident[0] * normal[0] + incident[1] * normal[1] + incident[2] * normal[2];
        if approach >= 0.0 {
            return incident;
        }
        let tangent = along(incident, normal, -approach);
        let speed = norm(tangent);
        let retained = if speed > 0.0 {
            self.tangent_retention * (1.0 - self.tangent_loss / speed).max(0.0)
        } else {
            0.0
        };
        along(
            tangent.map(|axis| axis * retained),
            normal,
            -approach * self.normal_retention,
        )
    }
}

impl Blast {
    fn affects_shooter(&self) -> bool {
        (self.knockback.is_some() && self.self_knockback_scale > 0.0)
            || (self.hit.amount > 0.0 && self.self_damage_scale > 0.0)
    }

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

#[derive(Clone, Copy, Debug)]
struct StuckState {
    anchor: Option<(u32, Vec3)>,
    splash_origin: Vec3,
    remaining: f64,
}

pub struct ProjectileFlight {
    ballistics: ProjectileBallistics,
    position: Vec3,
    direction: Vec3,
    remaining: f64,
    travelled: f64,
    distance_travelled: f64,
    last_tick: Option<u32>,
    ended: bool,
    expiry_blast: bool,
    gravity: f32,
    velocity: Vec3,
    terrain_rebounds: u32,
    post_bounce_fuse: Option<f64>,
    sticky_delay: Option<f64>,
    stuck: Option<StuckState>,
    direct_effect: Option<u64>,
    blast_effect: Option<u64>,
    piercing: bool,
    hit_actors: Vec<u32>,
    piercing_impacts: Vec<Impact>,
    target_policy: HitTargetPolicy,
}

#[derive(Debug, PartialEq)]
pub struct FlightEnd {
    pub point: Vec3,
    /// The directly contacted actor, if the flight ended against one.
    pub contact: Option<u32>,
    pub travelled: f64,
    pub impacts: Vec<Impact>,
    pub expired: bool,
    pub impact: bool,
}

enum Segment {
    Complete,
    Rebounded(f64),
    Ended(FlightEnd),
}

impl ProjectileFlight {
    pub(crate) fn affects_shooter(&self) -> bool {
        self.ballistics
            .blast
            .as_ref()
            .is_some_and(Blast::affects_shooter)
    }

    pub(crate) fn target_policy(&self) -> HitTargetPolicy {
        self.target_policy
    }

    pub(crate) fn with_target_policy(mut self, policy: HitTargetPolicy) -> Self {
        self.target_policy = policy;
        self
    }

    pub(crate) fn with_piercing(mut self, piercing: bool) -> Self {
        self.piercing = piercing;
        self
    }

    pub(crate) fn with_post_bounce_fuse(mut self, fuse: Option<f64>) -> Self {
        self.post_bounce_fuse = fuse.filter(|f| f.is_finite() && *f > 0.0);
        if self.post_bounce_fuse.is_some() && self.ballistics.blast.is_some() {
            self.expiry_blast = true;
        }
        self
    }

    pub(crate) fn with_sticky_delay(mut self, delay: Option<f64>) -> Self {
        self.sticky_delay = delay.filter(|d| d.is_finite() && *d > 0.0);
        self
    }

    pub(crate) fn with_effects(
        mut self,
        direct_effect: Option<u64>,
        blast_effect: Option<u64>,
    ) -> Self {
        self.direct_effect = direct_effect;
        self.blast_effect = blast_effect;
        self
    }

    pub(crate) fn position(&self) -> Vec3 {
        self.position
    }

    pub(crate) fn velocity(&self) -> Vec3 {
        self.velocity
    }

    pub(crate) fn is_piercing(&self) -> bool {
        self.piercing
    }

    pub(crate) fn barrier_impact_damage(&self) -> f64 {
        let direct = self
            .ballistics
            .direct
            .map_or(0.0, |h| h.damage_at(self.distance_travelled, false));
        let blast = self
            .ballistics
            .blast
            .as_ref()
            .map_or(0.0, |b| b.hit.damage_at(0.0, false));
        direct.max(blast)
    }

    pub(crate) fn direct_effect(&self) -> Option<u64> {
        self.direct_effect
    }

    pub(crate) fn blast_effect(&self) -> Option<u64> {
        self.blast_effect
    }

    pub(crate) fn drain_piercing_impacts(&mut self) -> Vec<Impact> {
        std::mem::take(&mut self.piercing_impacts)
    }

    pub(crate) fn blast_radius(&self) -> f64 {
        self.ballistics
            .blast
            .as_ref()
            .and_then(|blast| blast.rings.last().map(|&(radius, _)| radius))
            .filter(|radius| radius.is_finite() && *radius > 0.0)
            .unwrap_or(0.0)
    }

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
            || ballistics
                .max_distance
                .is_some_and(|range| !range.is_finite() || range <= 0.0)
            || !position.into_iter().chain(direction).all(f64::is_finite)
            || (direction.iter().map(|value| value * value).sum::<f64>() - 1.0).abs() > 1e-6
        {
            return None;
        }
        let expiry_blast = ballistics
            .blast
            .as_ref()
            .is_some_and(|blast| blast.detonate_on_expiry);
        Some(Self {
            remaining: ballistics.lifetime,
            velocity: direction.map(|axis| axis * ballistics.speed),
            ballistics,
            position,
            direction,
            travelled: 0.0,
            distance_travelled: 0.0,
            last_tick: fired_at,
            ended: false,
            expiry_blast,
            gravity: 0.0,
            terrain_rebounds: 0,
            post_bounce_fuse: None,
            sticky_delay: None,
            stuck: None,
            direct_effect: None,
            blast_effect: None,
            piercing: false,
            hit_actors: Vec::new(),
            piercing_impacts: Vec::new(),
            target_policy: HitTargetPolicy::default(),
        })
    }

    pub(crate) fn with_gravity(mut self, gravity: f32) -> Self {
        self.gravity = gravity;
        self.velocity = launch_velocity(self.direction, self.ballistics.speed);
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
        self.advance_with_wall(tick, targets, None, |origin, direction, reach, _| {
            Ok(wall(origin, direction, reach)?.map(|distance| WallContact {
                distance,
                normal: None,
            }))
        })
    }

    pub(crate) fn advance_combat<T: Clone + Into<HurtPose>>(
        &mut self,
        tick: u32,
        targets: &[(u32, T)],
        shooter: Option<(u32, HurtPose)>,
        mut wall: impl FnMut(Vec3, Vec3, f64, f32) -> Result<Option<SurfaceHit>, IndexError>,
    ) -> Result<Option<FlightEnd>, IndexError> {
        self.advance_with_wall(
            tick,
            targets,
            shooter,
            |origin, direction, reach, radius| {
                Ok(
                    wall(origin, direction, reach, radius)?.map(|hit| WallContact {
                        distance: hit.distance,
                        normal: Some(hit.normal),
                    }),
                )
            },
        )
    }

    fn advance_with_wall<T: Clone + Into<HurtPose>>(
        &mut self,
        tick: u32,
        targets: &[(u32, T)],
        shooter: Option<(u32, HurtPose)>,
        mut wall: impl FnMut(Vec3, Vec3, f64, f32) -> Result<Option<WallContact>, IndexError>,
    ) -> Result<Option<FlightEnd>, IndexError> {
        if self.ended {
            return Ok(None);
        }
        let elapsed = self.last_tick.map_or(1, |last| tick.saturating_sub(last));
        if elapsed == 0 {
            return Ok(None);
        }
        let seconds = TICK_PERIOD.as_secs_f64();
        self.last_tick = Some(tick);
        if let Some(mut stuck) = self.stuck {
            let dt = f64::from(elapsed) * seconds;
            if let Some((anchor_entity, offset)) = stuck.anchor {
                if let Some((_, pose)) = targets.iter().find(|(e, _)| *e == anchor_entity) {
                    let feet = pose.clone().into().feet();
                    self.position = [
                        feet[0] + offset[0],
                        feet[1] + offset[1],
                        feet[2] + offset[2],
                    ];
                    stuck.splash_origin = self.position;
                }
            }
            self.travelled += dt;
            stuck.remaining -= dt;
            if stuck.remaining <= 0.0 {
                self.stuck = None;
                let impacts = self.splash(
                    stuck.splash_origin,
                    self.position,
                    targets,
                    shooter,
                    &mut wall,
                )?;
                return Ok(Some(self.finish(
                    impacts,
                    false,
                    stuck.anchor.map(|(entity, _)| entity),
                )));
            }
            self.stuck = Some(stuck);
            return Ok(None);
        }
        let kinetic = self.gravity != 0.0 || self.ballistics.terrain_rebound.is_some();
        let steps = if kinetic { elapsed } else { 1 };
        let step_time = if kinetic {
            seconds
        } else {
            f64::from(elapsed) * seconds
        };
        for _ in 0..steps {
            let mut duration = step_time.min(self.remaining);
            while duration > 0.0 {
                let mut velocity = self.velocity;
                if self.gravity != 0.0 {
                    velocity = gravity_step(velocity, self.gravity, duration);
                }
                let speed = if kinetic {
                    norm(velocity)
                } else {
                    self.ballistics.speed
                };
                if kinetic && speed > EPSILON {
                    self.direction = velocity.map(|axis| axis / speed);
                }
                match self.advance_segment(duration, speed, targets, shooter.clone(), &mut wall)? {
                    Segment::Complete => {
                        if self.stuck.is_some() {
                            return Ok(None);
                        }
                        self.velocity = velocity;
                        break;
                    }
                    Segment::Rebounded(spent) => duration -= spent,
                    Segment::Ended(end) => return Ok(Some(end)),
                }
            }
        }
        Ok(None)
    }

    fn advance_segment<T: Clone + Into<HurtPose>>(
        &mut self,
        duration: f64,
        speed: f64,
        targets: &[(u32, T)],
        shooter: Option<(u32, HurtPose)>,
        mut wall: impl FnMut(Vec3, Vec3, f64, f32) -> Result<Option<WallContact>, IndexError>,
    ) -> Result<Segment, IndexError> {
        let step = speed * duration;
        let range_left = self
            .ballistics
            .max_distance
            .map(|range| (range - self.distance_travelled).max(0.0));
        let reach = range_left.map_or(step, |range| step.min(range));
        let mut closest = None;
        if !self.piercing {
            for (entity, target) in targets {
                if let Some(contact) = target.clone().into().contact(
                    self.position,
                    self.direction,
                    reach,
                    self.ballistics.radius,
                ) {
                    let distance = contact.hit.distance;
                    if closest.as_ref().is_none_or(|(best, _)| distance < *best) {
                        closest = Some((distance, Some((*entity, contact))));
                    }
                }
            }
        }
        let wall_hit = wall(
            self.position,
            self.direction,
            reach,
            self.ballistics.terrain_radius,
        )?
        .filter(|hit| {
            (0.0..=reach).contains(&hit.distance)
                && closest
                    .as_ref()
                    .is_none_or(|(best, _)| hit.distance < *best)
        });
        if self.piercing {
            let actor_limit = wall_hit.map_or(reach, |hit| hit.distance);
            if let Some(direct) = self.ballistics.direct.filter(|hit| hit.amount > 0.0) {
                for &(entity, ref target) in targets {
                    if self.hit_actors.contains(&entity) {
                        continue;
                    }
                    if let Some(contact) = target.clone().into().contact(
                        self.position,
                        self.direction,
                        actor_limit,
                        self.ballistics.radius,
                    ) {
                        let distance = contact.hit.distance;
                        let point = along(self.position, self.direction, distance);
                        let critical = contact.head && direct.critical > 1.0;
                        self.hit_actors.push(entity);
                        self.piercing_impacts.push(Impact {
                            tags: direct.tags,
                            entity,
                            distance,
                            damage: direct.damage_at(self.distance_travelled + distance, critical),
                            critical,
                            loadout: direct.loadout,
                            impulse: None,
                            geometry: Some(ImpactGeometry::Direct {
                                origin: self.position,
                                point: contact.point_on_body(point),
                                direction: self.direction,
                                normal: contact.hit.normal,
                            }),
                        });
                    }
                }
            }
        }
        if let Some(hit) = wall_hit {
            closest = Some((hit.distance, None));
        }
        let Some((distance, target)) = closest else {
            self.position = along(self.position, self.direction, reach);
            let spent = if reach < step {
                reach / speed
            } else {
                duration
            };
            self.travelled += spent;
            self.distance_travelled += reach;
            self.remaining -= spent;
            if self.remaining <= 0.0 || range_left == Some(reach) {
                let impacts = if self.expiry_blast {
                    self.splash(self.position, self.position, targets, shooter, &mut wall)?
                } else {
                    Vec::new()
                };
                return Ok(Segment::Ended(self.finish(impacts, true, None)));
            }
            return Ok(Segment::Complete);
        };
        let point = along(self.position, self.direction, distance);
        let spent = if speed > EPSILON {
            distance / speed
        } else {
            0.0
        };
        let rebound = self.ballistics.terrain_rebound.filter(|response| {
            spent > 0.0
                && spent < self.remaining
                && range_left.is_none_or(|range| distance < range)
                && self.terrain_rebounds < response.limit
        });
        if let (Some(normal), Some(rebound)) = (wall_hit.and_then(|hit| hit.normal), rebound) {
            let incident = if self.gravity == 0.0 {
                self.velocity
            } else {
                gravity_step(self.velocity, self.gravity, spent)
            };
            self.velocity = rebound.reflect(incident, normal);
            let separation = if self.ballistics.terrain_radius > 0.0 {
                f64::from(self.ballistics.terrain_radius) + REBOUND_SEPARATION
            } else {
                REBOUND_SEPARATION
            };
            self.position = along(point, normal, separation);
            self.terrain_rebounds += 1;
            self.distance_travelled += distance;
            self.travelled += spent;
            self.remaining -= spent;
            if self.terrain_rebounds == 1 {
                if let Some(fuse) = self.post_bounce_fuse {
                    self.remaining = self.remaining.min(fuse);
                    self.expiry_blast = true;
                }
            }
            return Ok(Segment::Rebounded(spent.min(duration)));
        }
        let mut impacts = Vec::new();
        if let (Some((entity, contact)), Some(direct)) = (
            target.as_ref(),
            self.ballistics.direct.filter(|hit| hit.amount > 0.0),
        ) {
            let critical = contact.head && direct.critical > 1.0;
            impacts.push(Impact {
                tags: direct.tags,
                entity: *entity,
                distance,
                damage: direct.damage_at(self.distance_travelled + distance, critical),
                critical,
                loadout: direct.loadout,
                impulse: None,
                geometry: Some(ImpactGeometry::Direct {
                    origin: self.position,
                    point: contact.point_on_body(point),
                    direction: self.direction,
                    normal: contact.hit.normal,
                }),
            });
        }
        if let Some(delay) = self
            .sticky_delay
            .filter(|_| self.ballistics.blast.is_some())
        {
            // Sticky projectile (e.g. Echo Sticky Bombs `m_12654E6E`): apply direct
            // impact immediately, adhere to the contacted target or world surface,
            // and detonate splash after the authored fuse delay.
            self.piercing_impacts.extend(impacts);
            let anchor = target.as_ref().and_then(|(entity, _)| {
                targets
                    .iter()
                    .find(|(e, _)| *e == *entity)
                    .map(|(_, pose)| {
                        let feet = pose.clone().into().feet();
                        (
                            *entity,
                            [point[0] - feet[0], point[1] - feet[1], point[2] - feet[2]],
                        )
                    })
            });
            let splash_origin = match wall_hit {
                Some(WallContact {
                    normal: Some(normal),
                    ..
                }) => along(point, normal, SURFACE_RAY_OFFSET),
                Some(_) => along(
                    self.position,
                    self.direction,
                    (distance - DISTANCE_ONLY_RAY_OFFSET).max(0.0),
                ),
                None => point,
            };
            self.position = point;
            self.velocity = [0.0; 3];
            self.distance_travelled += distance;
            self.travelled += spent;
            self.stuck = Some(StuckState {
                anchor,
                splash_origin,
                remaining: delay,
            });
            return Ok(Segment::Complete);
        }
        let origin = match wall_hit {
            // Offset perpendicular to the hit surface. At a glancing angle an
            // offset along the shot can leave the blast origin inside the ray cutoff.
            Some(WallContact {
                normal: Some(normal),
                ..
            }) => along(point, normal, SURFACE_RAY_OFFSET),
            Some(_) => along(
                self.position,
                self.direction,
                (distance - DISTANCE_ONLY_RAY_OFFSET).max(0.0),
            ),
            None => point,
        };
        impacts.extend(self.splash(point, origin, targets, shooter, &mut wall)?);
        self.position = point;
        self.distance_travelled += distance;
        self.travelled += spent;
        Ok(Segment::Ended(self.finish(
            impacts,
            false,
            target.map(|(entity, _)| entity),
        )))
    }

    fn splash<T: Clone + Into<HurtPose>>(
        &self,
        point: Vec3,
        origin: Vec3,
        targets: &[(u32, T)],
        shooter: Option<(u32, HurtPose)>,
        mut wall: impl FnMut(Vec3, Vec3, f64, f32) -> Result<Option<WallContact>, IndexError>,
    ) -> Result<Vec<Impact>, IndexError> {
        let mut impacts = Vec::new();
        if let Some(blast) = self
            .ballistics
            .blast
            .as_ref()
            .filter(|blast| blast.hit.amount > 0.0 || blast.knockback.is_some())
        {
            let targets = targets
                .iter()
                .map(|(entity, pose)| (*entity, pose.clone().into(), 1.0, 1.0));
            let shooter = shooter
                .filter(|_| blast.affects_shooter())
                .map(|(entity, pose)| {
                    (
                        entity,
                        pose,
                        blast.self_damage_scale,
                        blast.self_knockback_scale,
                    )
                });
            for (entity, pose, damage_scale, knockback_scale) in targets.chain(shooter) {
                let Some(surface) = pose.nearest_surface(point, origin) else {
                    continue;
                };
                let falloff = blast.multiplier(surface.distance);
                let damage = blast.hit.amount * falloff * damage_scale;
                let damage = if damage.is_finite() {
                    damage.max(0.0)
                } else {
                    0.0
                };
                let impulse = blast.knockback.as_ref().and_then(|knockback| {
                    knockback.delta(
                        point,
                        pose.feet(),
                        surface.distance,
                        falloff > 0.0,
                        knockback_scale,
                    )
                });
                if damage == 0.0 && impulse.is_none() {
                    continue;
                }
                if !surface.visible(|direction, reach| {
                    wall(origin, direction, reach, 0.0).map(|hit| hit.map(|hit| hit.distance))
                })? {
                    continue;
                }
                impacts.push(Impact {
                    tags: blast.hit.tags,
                    entity,
                    distance: surface.distance,
                    damage,
                    critical: false,
                    loadout: blast.hit.loadout,
                    impulse,
                    geometry: Some(ImpactGeometry::Blast { origin: point }),
                });
            }
        }
        Ok(impacts)
    }

    fn finish(&mut self, impacts: Vec<Impact>, expired: bool, contact: Option<u32>) -> FlightEnd {
        self.ended = true;
        FlightEnd {
            point: self.position,
            contact,
            travelled: self.travelled,
            impacts,
            expired,
            impact: !expired || self.expiry_blast,
        }
    }
}

/// A direct single shot uses the sampled command pose with any authored lift bias.
pub(crate) fn posed_projectile_direction(
    node: &Node,
    direction: Vec3,
    mut evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
) -> Option<Vec3> {
    let launch = node.field("m_5EC1EB38").filter(|config| !config.is_null());
    let kind = launch.and_then(|config| config["$"].as_str());
    if launch.is_some() && !matches!(kind, Some("STU_239B571E" | "STU_613BDDB0" | "STU_1145C42E")) {
        return None;
    }
    if let Some(aim) = node.field("m_aimID").filter(|config| !config.is_null()) {
        // Retail 1.74 weapons use aim channels 0..=3 (0/1 primary/alt, 2 thrown abilities
        // such as Biotic Grenade/Graviton/Barrier/Lamp, 3 secondary ability aim).
        if !matches!(evaluate(aim).ok()?, Value::Integer(0..=3)) {
            return None;
        }
    }
    direct_projectile_direction(launch.unwrap_or(&Json::Null), direction, evaluate)
}

pub(crate) fn direct_projectile_direction(
    launch: &Json,
    direction: Vec3,
    mut evaluate: impl FnMut(&Json) -> Result<Value, EvalError>,
) -> Option<Vec3> {
    let base = if let Some(axis_cfg) = launch.get("m_E2FD6F04").filter(|v| !v.is_null()) {
        match evaluate(axis_cfg).ok()? {
            Value::Vector(axis) if axis.iter().all(|c| c.is_finite()) => {
                lifted_direction(axis, 0.0)?
            }
            _ => direction,
        }
    } else {
        direction
    };
    let lift = &launch["m_22CE7F30"];
    if lift.is_null() {
        return Some(base);
    }
    lifted_direction(base, crate::script_expr::to_float(&evaluate(lift).ok()?))
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
) -> Result<Option<f32>, EvalError> {
    let motions = &fields["m_projectileMotions"];
    if motions.is_null() {
        return Ok(None);
    }
    let motions = motions
        .as_array()
        .ok_or(EvalError::MalformedConfig("projectile motion list"))?;
    let mut gravity = 0.0f32;
    for motion in motions {
        if motion["$"] == "STU_7A5DFFF8" {
            // Non-gravity speed/acceleration curve; gravity remains unaffected.
            continue;
        }
        if motion["$"] != "STU_E1A5B579" {
            return Err(EvalError::UnsupportedConfig(
                "projectile motion class".into(),
            ));
        }
        let disable = &motion["m_D0C4CF49"];
        if !disable.is_null() && crate::script_expr::truthy(&evaluate(disable)?) {
            continue;
        }
        let acceleration = match evaluate(&motion["m_599AB8D7"])? {
            Value::Float(value) => value as f32,
            Value::Integer(value) => value as f32,
            _ => {
                return Err(EvalError::MalformedConfig(
                    "projectile gravity acceleration",
                ));
            }
        };
        gravity += acceleration;
        if acceleration < 0.0 || !gravity.is_finite() {
            return Err(EvalError::MalformedConfig(
                "projectile gravity acceleration",
            ));
        }
    }
    Ok((gravity > 0.0).then_some(gravity))
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
            max_distance: None,
            terrain_rebound: None,
            terrain_radius: 0.0,
            radius: 0.0,
            definition: Some(0x0400_0000_0000_072c),
            direct: Some(hit),
            blast: Some(Blast {
                hit: HitDefinition {
                    tags: crate::game_hitscan::DamageTags::EMPTY,
                    amount: 80.0,
                    ..hit
                },
                rings: vec![(1.0, 1.0), (3.0, 0.5)],
                self_damage_scale: 0.0,
                self_knockback_scale: 0.0,
                knockback: None,
                detonate_on_expiry: false,
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
    fn direct_projectile_uses_authored_falloff_over_the_entire_flight() {
        #[derive(serde::Deserialize)]
        struct Case {
            contact_distance: f64,
            damage: f64,
            without_falloff: bool,
        }
        #[derive(serde::Deserialize)]
        struct Contract {
            fields: Json,
            cases: Vec<Case>,
        }
        let contract: Contract = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-direct-falloff-v1.json"
        ))
        .unwrap();
        for case in contract.cases {
            let mut fields = contract.fields.clone();
            if case.without_falloff {
                fields["m_6396149F"]
                    .as_object_mut()
                    .unwrap()
                    .remove("m_157E5BC5");
            }
            let profile = ProjectileBallistics::from_fields(&fields, |value| {
                crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
            })
            .unwrap()
            .unwrap();
            let mut flight =
                ProjectileFlight::new(profile, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], Some(0)).unwrap();
            let targets = [(1, target(0.0, case.contact_distance + 0.5))];
            assert!(
                flight
                    .advance(1, &targets, |_, _, _| Ok(None))
                    .unwrap()
                    .is_none()
            );
            let segment_origin = flight.position;
            let end = flight
                .advance(20, &targets, |_, _, _| Ok(None))
                .unwrap()
                .unwrap();
            assert!((end.point[2] - case.contact_distance).abs() < EPSILON);
            assert_eq!(end.contact, Some(1));
            let [impact] = end.impacts.as_slice() else {
                panic!("expected one direct projectile impact");
            };
            assert!((impact.damage - case.damage).abs() < EPSILON);
            assert_eq!(
                impact.geometry,
                Some(ImpactGeometry::Direct {
                    origin: segment_origin,
                    point: end.point,
                    direction: [0.0, 0.0, 1.0],
                    normal: [0.0, 0.0, -1.0],
                })
            );
        }
    }

    #[test]
    fn expanded_projectile_records_the_target_surface_separately_from_the_sweep_centre() {
        let profile = ProjectileBallistics {
            radius: 0.25,
            ..helix()
        };
        let mut flight =
            ProjectileFlight::new(profile, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], Some(10)).unwrap();
        let end = flight
            .advance(15, &[(1, target(0.0, 3.0))], |_, _, _| Ok(None))
            .unwrap()
            .unwrap();
        assert_eq!(end.point, [0.0, 1.0, 2.25]);
        assert_eq!(
            end.impacts[0].geometry,
            Some(ImpactGeometry::Direct {
                origin: [0.0, 1.0, 0.0],
                point: [0.0, 1.0, 2.5],
                direction: [0.0, 0.0, 1.0],
                normal: [0.0, 0.0, -1.0],
            })
        );
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
    fn gravity_nodes_apply_only_the_enabled_authored_accelerations() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-expiry-v1.json"
        ))
        .unwrap();
        let evaluate = |value: &Json| {
            crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
        };
        for case in fixture["gravity_cases"].as_array().unwrap() {
            let motions: Vec<_> = case["accelerations"]
                .as_array()
                .unwrap()
                .iter()
                .zip(case["disabled"].as_array().unwrap())
                .map(|(value, disabled)| {
                    serde_json::json!({
                        "$": "STU_E1A5B579",
                        "m_599AB8D7": value,
                        "m_D0C4CF49": {"$":"STUConfigVarBool","m_value":disabled},
                    })
                })
                .collect();
            let mut fields = serde_json::json!({"m_projectileMotions":motions});
            assert_eq!(
                authored_gravity(&fields, evaluate).unwrap(),
                case["net"].as_f64().map(|value| value as f32),
            );
            fields["m_projectileMotions"][0]["$"] = serde_json::json!("UnknownMotion");
            assert!(authored_gravity(&fields, evaluate).is_err());
        }
    }

    #[test]
    fn expiry_blast_uses_splash_radius_occlusion_and_once_only_retirement() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-expiry-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"];
        let expected = &fixture["expected"];
        let range = input["range"].as_f64().unwrap();
        let speed = input["speed"].as_f64().unwrap();
        let splash_damage = input["splash_damage"].as_f64().unwrap();
        for enabled in [false, true] {
            for blocked in [false, true] {
                let fields = serde_json::json!({
                    "m_projectileSpeed": speed,
                    "m_projectileLifetime": 1.0,
                    "m_FEC435C6": range,
                    "m_projectileMotions":[{"$":"STU_E1A5B579","m_599AB8D7":input["gravity"]}],
                    "m_D2D11CE9": {
                        "$": "STU_4AA6CA44",
                        "m_modifyHealth": {"m_amount": splash_damage},
                        "m_0D30E40C": [{"m_radius": input["splash_radius"], "m_B399CACB": 1.0}],
                        "m_EEB99E41": {"$": "STUConfigVarBool", "m_value": enabled},
                    },
                });
                let evaluate = |value: &Json| {
                    crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
                };
                let data = ProjectileBallistics::from_fields(&fields, evaluate)
                    .unwrap()
                    .unwrap();
                let gravity = authored_gravity(&fields, evaluate).unwrap().unwrap();
                let direction =
                    lifted_direction([0.0, 0.0, 1.0], input["lift"].as_f64().unwrap()).unwrap();
                let mut flight = ProjectileFlight::new(data, [0.0, 1.55, 0.0], direction, Some(0))
                    .unwrap()
                    .with_gravity(gravity);
                // The target is off the flight axis but inside the range-end blast.
                let visible = expected["visible_target"].as_u64().unwrap() as u32;
                let out_of_range = expected["out_of_range_target"].as_u64().unwrap() as u32;
                let targets = [
                    (visible, target(2.0, range)),
                    (out_of_range, target(5.0, range)),
                ];
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
                assert_eq!(end.impact, enabled);
                assert!(end.point[1] < 1.55);
                assert!((flight.distance_travelled - range).abs() < 1e-9);
                assert!((end.point[2] - expected["range_end_z"].as_f64().unwrap()).abs() < 1e-6);
                let hits: Vec<_> = end
                    .impacts
                    .iter()
                    .map(|hit| (hit.entity, hit.damage))
                    .collect();
                assert_eq!(
                    hits,
                    if enabled && !blocked {
                        vec![(visible, splash_damage)]
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
        assert!(!end.impact);
    }

    #[test]
    fn authored_terrain_hull_contacts_early_without_widening_blast_visibility() {
        use crate::{
            collision_cache::{CollisionCache, Mesh, Triangle},
            collision_scene::CollisionScene,
            contact_world::ContactWorld,
            mover_settings::MoverCatalog,
            scene_assets::BotQueries,
        };

        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-terrain-hull-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"];
        let expected = &fixture["expected"];
        let cache = CollisionCache {
            header: serde_json::from_str(
                r#"{"meshes":[[4,2]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
            )
            .unwrap(),
            meshes: vec![Mesh {
                vertices: serde_json::from_value(input["wall"].clone()).unwrap(),
                triangles: [[0, 1, 2], [0, 2, 3]]
                    .map(|corners| Triangle {
                        corners,
                        wings: [-1; 3],
                        mask: 0x18,
                        surface: 0,
                    })
                    .to_vec(),
            }],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        let ids = Default::default();
        let ground = CollisionScene::movement_probe(&cache, &ids).unwrap();
        let contacts = std::sync::Arc::new(ContactWorld::from_movement_cache(cache, &ids).unwrap());
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground, Some(&contacts));
        let mut fields = serde_json::json!({
            "m_projectileSpeed": input["speed"],
            "m_8236947A": {
                "$": "STU_20C861FB",
                "m_9655ED90": {"$": "STUConfigVarFloat", "m_value": input["terrain_radius"]},
            },
            "m_D2D11CE9": {
                "m_modifyHealth": {"m_amount": input["splash_damage"]},
                "m_0D30E40C": [{"m_radius": input["splash_radius"], "m_B399CACB": 1.0}],
            },
        });
        let evaluate = |value: &Json| {
            crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
        };
        let origin = serde_json::from_value(input["origin"].clone()).unwrap();
        let direction = serde_json::from_value(input["direction"].clone()).unwrap();
        let exposed = expected["exposed_target"].as_u64().unwrap() as u32;
        let occluded = expected["occluded_target"].as_u64().unwrap() as u32;
        let targets = [(exposed, target(0.25, 1.2)), (occluded, target(1.75, 0.25))];

        let profile = ProjectileBallistics::from_fields(&fields, evaluate)
            .unwrap()
            .unwrap();
        let mut flight = ProjectileFlight::new(profile, origin, direction, Some(0)).unwrap();
        let mut radii = Vec::new();
        let end = flight
            .advance_combat(1, &targets, None, |origin, direction, reach, radius| {
                radii.push(radius);
                queries.projectile_wall(origin, direction, reach, radius)
            })
            .unwrap()
            .unwrap();
        let [low, high]: [f64; 2] =
            serde_json::from_value(expected["contact_centre_x"].clone()).unwrap();
        assert!((low..=high).contains(&end.point[0]), "{end:?}");
        assert!(end.impact && !end.expired && end.contact.is_none());
        let [hit] = end.impacts.as_slice() else {
            panic!("expected one exposed actor, got {:?}", end.impacts);
        };
        assert_eq!(hit.entity, exposed);
        assert_eq!(hit.damage, input["splash_damage"].as_f64().unwrap());
        assert_eq!(radii[0], input["terrain_radius"].as_f64().unwrap() as f32);
        assert!(!radii[1..].is_empty() && radii[1..].iter().all(|&radius| radius == 0.0));

        // With the same motion and no declared terrain hull, first contact
        // with this wall occurs in the next tick at the centreline crossing.
        fields["m_8236947A"] = Json::Null;
        let profile = ProjectileBallistics::from_fields(&fields, evaluate)
            .unwrap()
            .unwrap();
        let mut flight = ProjectileFlight::new(profile, origin, direction, Some(0)).unwrap();
        let wall = |origin, direction, reach, radius| {
            queries.projectile_wall(origin, direction, reach, radius)
        };
        assert!(
            flight
                .advance_combat(1, &targets, None, wall)
                .unwrap()
                .is_none()
        );
        let end = flight
            .advance_combat(2, &targets, None, wall)
            .unwrap()
            .unwrap();
        assert_eq!(end.point[0], 1.0);
    }

    #[test]
    fn projectile_definition_does_not_override_its_authored_floor_response() {
        let floor = TriangleIndex::new([SceneTriangle {
            vertices: [[-3.0, 0.0, -3.0], [0.0, 0.0, 3.0], [3.0, 0.0, -3.0]],
            mask: 0x18,
            surface: 0,
        }])
        .unwrap();
        let sweep = |origin, direction, reach, _| {
            floor.cast_ray(origin, direction, reach, 0x18).map(|hit| {
                hit.map(|hit| SurfaceHit {
                    distance: hit.distance,
                    normal: hit.normal,
                })
            })
        };
        let targets: &[(u32, TargetPose)] = &[];
        let mut positions = Vec::new();
        for definition in [0x0400_0000_0000_2578_u64, 0x0400_0000_0000_00a3] {
            for enabled in [true, false] {
                let fields = serde_json::json!({
                    "m_projectileSpeed": 20.0,
                    "m_projectileEntity": {
                        "m_entityDef": {"$": "STU_8556841E", "m_entityDef": format!("0x{definition:016X}")}
                    },
                    "m_E365DB20": {
                        "$": "STU_3FE1A93B",
                        "m_D9EFD182": {"$":"STUConfigVarBool", "m_value": i32::from(enabled)},
                        "m_360294F0": {"$":"STUConfigVarInt", "m_value": 2},
                        "m_0D20DAFD": 0.5
                    }
                });
                let profile = ProjectileBallistics::from_fields(&fields, |value| {
                    crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
                })
                .unwrap()
                .unwrap();
                assert_eq!(profile.definition, Some(definition));
                let mut flight =
                    ProjectileFlight::new(profile, [0.0, 0.1, 0.0], [0.0, -1.0, 0.0], Some(0))
                        .unwrap();
                let impact = flight.advance_combat(1, targets, None, sweep).unwrap();
                if enabled {
                    assert!(impact.is_none());
                    assert_eq!(flight.terrain_rebounds, 1);
                    assert!(flight.position[1] > 0.0 && flight.velocity[1] > 0.0);
                    positions.push(flight.position);
                } else {
                    assert!(impact.unwrap().impact);
                    assert_eq!(flight.terrain_rebounds, 0);
                }
            }
        }
        assert_eq!(positions[0], positions[1]);
    }

    #[test]
    fn authored_rebound_preserves_sweep_order_and_remaining_time_and_range() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-terrain-rebound-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"];
        let expected = &fixture["expected"];
        let start = serde_json::from_value(input["origin"].clone()).unwrap();
        let direction = serde_json::from_value(input["direction"].clone()).unwrap();
        let front = input["front_wall"].as_f64().unwrap();
        let back = input["back_wall"].as_f64().unwrap();
        let world = TriangleIndex::new([front, back].map(|z| SceneTriangle {
            vertices: [[-6.0, -3.0, z], [6.0, -3.0, z], [0.0, 7.0, z]],
            mask: 0x18,
            surface: 0,
        }))
        .unwrap();
        let wall = |origin, direction, length, _| {
            world.cast_ray(origin, direction, length, 0x18).map(|hit| {
                hit.map(|hit| SurfaceHit {
                    distance: hit.distance,
                    normal: hit.normal,
                })
            })
        };
        let mut fields = serde_json::json!({
            "m_projectileSpeed": input["speed"],
            "m_E365DB20": {
                "$": "STU_3FE1A93B",
                "m_D9EFD182": {"$": "STUConfigVarBool", "m_value": 1},
                "m_360294F0": {"$": "STUConfigVarInt", "m_value": input["count"]},
                "m_0D20DAFD": input["normal_retention"],
                "m_C29D35E8": input["tangent_retention"],
                "m_CE33458D": input["tangent_loss"],
            },
        });
        let evaluate = |value: &Json| {
            crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
        };
        let profile = ProjectileBallistics::from_fields(&fields, evaluate)
            .unwrap()
            .unwrap();
        let no_targets: [(u32, TargetPose); 0] = [];
        let mut flight = ProjectileFlight::new(profile.clone(), start, direction, Some(0)).unwrap();
        assert!(
            flight
                .advance_combat(1, &no_targets, None, wall)
                .unwrap()
                .is_none()
        );
        for (actual, expected) in flight
            .position
            .into_iter()
            .zip(expected["after_first_tick"].as_array().unwrap())
        {
            assert!((actual - expected.as_f64().unwrap()).abs() < 1e-9);
        }
        assert!(
            flight
                .advance_combat(2, &no_targets, None, wall)
                .unwrap()
                .is_none()
        );
        let end = flight
            .advance_combat(3, &no_targets, None, wall)
            .unwrap()
            .unwrap();
        assert!(end.impact && !end.expired && end.contact.is_none());
        assert!((end.point[2] - back).abs() < 1e-9);
        assert!(
            (end.travelled - expected["elapsed_at_second_wall"].as_f64().unwrap()).abs() < 1e-9
        );
        assert!(
            flight
                .advance_combat(4, &no_targets, None, wall)
                .unwrap()
                .is_none()
        );

        let intervening_actor = [(
            42,
            TargetPose {
                radius: 0.1,
                ..target(0.25, 0.3)
            },
        )];
        let mut flight = ProjectileFlight::new(profile.clone(), start, direction, Some(0)).unwrap();
        let end = flight
            .advance_combat(1, &intervening_actor, None, wall)
            .unwrap()
            .unwrap();
        assert_eq!(end.contact, Some(42));
        assert_eq!(flight.terrain_rebounds, 0);

        let short = ProjectileBallistics {
            max_distance: expected["short_range"].as_f64(),
            ..profile
        };
        let mut flight = ProjectileFlight::new(short, start, direction, Some(0)).unwrap();
        let end = flight
            .advance_combat(1, &no_targets, None, wall)
            .unwrap()
            .unwrap();
        assert!(end.expired && !end.impact);
        assert_eq!(flight.terrain_rebounds, 1);
        assert!(
            (flight.distance_travelled - expected["short_range"].as_f64().unwrap()).abs() < 1e-9
        );

        fields["m_E365DB20"]["m_D9EFD182"]["m_value"] = serde_json::json!(0);
        let disabled = ProjectileBallistics::from_fields(&fields, evaluate)
            .unwrap()
            .unwrap();
        let mut flight = ProjectileFlight::new(disabled, start, direction, Some(0)).unwrap();
        let end = flight
            .advance_combat(1, &no_targets, None, wall)
            .unwrap()
            .unwrap();
        assert!((end.point[2] - front).abs() < 1e-9);
        assert!(end.impact && !end.expired);
        assert_eq!(flight.terrain_rebounds, 0);
    }

    #[test]
    fn glancing_impact_blocks_blast_damage_and_force_on_the_far_side() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-glancing-wall-v1.json"
        ))
        .unwrap();
        let vertices = serde_json::from_value(fixture["wall"].clone()).unwrap();
        let world = TriangleIndex::new([SceneTriangle {
            vertices,
            mask: 0x18,
            surface: 0,
        }])
        .unwrap();
        let input = &fixture["input"];
        let incidence = input["incidence"].as_f64().unwrap();
        let offset = input["start_offset"].as_f64().unwrap();
        let target_offset = input["target_offset"].as_f64().unwrap();
        let along_wall = (1.0 - incidence * incidence).sqrt();
        let impact_z = along_wall * offset / incidence;
        let fields = serde_json::json!({
            "m_projectileSpeed": 50.0,
            "m_D2D11CE9": {
                "m_modifyHealth": {
                    "m_amount": input["damage"],
                    "m_E33EFEE1": input["horizontal_impulse"],
                    "m_10399AE7": input["upward_impulse"],
                },
                "m_0D30E40C": [{"m_radius": 2.0, "m_B399CACB": 1.0}]
            }
        });
        let profile = ProjectileBallistics::from_fields(&fields, |value| {
            crate::script_expr::evaluate(value, &mut crate::script_expr::EmptyContext)
        })
        .unwrap()
        .unwrap();
        for side in [-1.0, 1.0] {
            let mut flight = ProjectileFlight::new(
                profile.clone(),
                [side * offset, 1.0, 0.0],
                [-side * incidence, 0.0, along_wall],
                Some(0),
            )
            .unwrap();
            let end = flight
                .advance_combat(
                    2,
                    &[
                        (1, target(side * target_offset, impact_z)),
                        (2, target(-side * target_offset, impact_z)),
                    ],
                    None,
                    |origin, direction, reach, _| {
                        world.cast_ray(origin, direction, reach, 0x18).map(|hit| {
                            hit.map(|hit| SurfaceHit {
                                distance: hit.distance,
                                normal: hit.normal,
                            })
                        })
                    },
                )
                .unwrap()
                .unwrap();
            assert!(end.impact && !end.expired && end.contact.is_none());
            assert!(end.point[0].abs() < 1e-12);
            assert!((end.point[2] - impact_z).abs() < 1e-12);
            let [near] = end.impacts.as_slice() else {
                panic!("expected only the exposed target, got {:?}", end.impacts);
            };
            assert_eq!(near.entity, 1);
            assert_eq!(near.damage, input["damage"].as_f64().unwrap());
            assert_eq!(
                near.geometry,
                Some(ImpactGeometry::Blast { origin: end.point })
            );
            let [x, y, z] = near.impulse.unwrap();
            assert_eq!(x, side * input["horizontal_impulse"].as_f64().unwrap());
            assert_eq!(y, input["upward_impulse"].as_f64().unwrap());
            assert!(z.abs() < 1e-12, "unexpected impulse along the wall: {z}");
        }
    }

    #[test]
    fn authored_self_splash_reuses_damage_falloff_and_occlusion_without_blocking_the_flight() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-self-splash-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"];
        let expected = &fixture["expected"];
        let mut fields = serde_json::json!({
            "m_projectileSpeed": input["projectile_speed"],
            "m_D2D11CE9": {
                "m_8A3E3883": input["self_damage_scale"],
                "m_modifyHealth": { "m_amount": input["blast_damage"] },
                "m_0D30E40C": [{ "m_radius": input["blast_radius"], "m_B399CACB": 1.0 }]
            }
        });
        let shooter = (1, target(0.0, 0.0).into());
        let targets = [(2, target(1.2, 0.75))];
        let fire = |fields: &Json, obstructed: bool| {
            let profile = ProjectileBallistics::from_fields(fields, |config| {
                Ok(config.as_f64().map_or(Value::Null, Value::Float))
            })
            .unwrap()
            .unwrap();
            let mut flight =
                ProjectileFlight::new(profile, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], None).unwrap();
            flight
                .advance_with_wall(
                    1,
                    &targets,
                    Some(shooter.clone()),
                    |_, direction, reach, _| {
                        if direction == [0.0, 0.0, 1.0] && reach >= 0.75 {
                            Ok(Some(WallContact {
                                distance: 0.75,
                                normal: None,
                            }))
                        } else if obstructed && direction[2] < -0.9 {
                            Ok(Some(WallContact {
                                distance: reach / 2.0,
                                normal: None,
                            }))
                        } else {
                            Ok(None)
                        }
                    },
                )
                .unwrap()
                .unwrap()
        };
        let hit = fire(&fields, false);
        assert_eq!(hit.point, [0.0, 1.0, 0.75]);
        assert_eq!(hit.contact, None);
        assert_eq!(
            hit.impacts
                .iter()
                .map(|hit| (hit.entity, hit.damage))
                .collect::<Vec<_>>(),
            [
                (2, expected["enemy_damage"].as_f64().unwrap()),
                (1, expected["owner_damage"].as_f64().unwrap())
            ],
        );
        let obscured = fire(&fields, true);
        assert_eq!(obscured.impacts, hit.impacts[..1]);
        fields["m_D2D11CE9"]["m_8A3E3883"] = Json::Null;
        assert_eq!(fire(&fields, false).impacts, hit.impacts[..1]);

        fields["m_D2D11CE9"]["m_modifyHealth"]["m_10399AE7"] = serde_json::json!(4.0);
        assert_eq!(fire(&fields, false).impacts.len(), 1);
        fields["m_D2D11CE9"]["m_49F60FB8"] = serde_json::json!(1);
        fields["m_D2D11CE9"]["m_2FE97560"] = serde_json::json!(0.5);
        let impulse_only = fire(&fields, false);
        assert_eq!(impulse_only.impacts[1].entity, shooter.0);
        assert_eq!(impulse_only.impacts[1].damage, 0.0);
        assert_eq!(impulse_only.impacts[1].impulse, Some([0.0, 2.0, 0.0]));
        assert_eq!(fire(&fields, true).impacts.len(), 1);
    }

    #[test]
    fn projectile_radius_expands_actor_hit_envelope_without_inflating_wall_rays() {
        let fields = serde_json::json!({
            "m_projectileSpeed": 50.0,
            "m_projectileLifetime": 2.0,
            "m_6396149F": {
                "m_57DAE1B5": 0.2,
                "m_modifyHealth": {
                    "m_amount": 40.0
                }
            }
        });
        let authored = ProjectileBallistics::from_fields(&fields, |config| {
            Ok(config.as_f64().map_or(Value::Null, Value::Float))
        })
        .unwrap()
        .unwrap();
        assert_eq!(authored.radius, 0.2);

        // Target capsule radius 0.5 at x = 0.6: zero-radius ray misses, 0.2m projectile hits.
        let targets = [(1, target(0.6, 1.5))];
        let zero_radius = ProjectileBallistics {
            radius: 0.0,
            ..authored.clone()
        };
        let mut missed =
            ProjectileFlight::new(zero_radius, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], Some(100))
                .unwrap();
        assert!(
            missed
                .advance(103, &targets, |_, _, _| Ok(None))
                .unwrap()
                .is_none()
        );

        let mut grazed =
            ProjectileFlight::new(authored, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], Some(100)).unwrap();
        let end = grazed
            .advance(103, &targets, |_, _, _| Ok(None))
            .unwrap()
            .unwrap();
        assert!(!end.expired);
        assert_eq!(end.impacts.len(), 1);
        assert_eq!((end.impacts[0].entity, end.impacts[0].damage), (1, 40.0));
    }

    #[test]
    fn piercing_projectile_hits_multiple_aligned_targets_until_wall() {
        let fields = serde_json::json!({
            "m_projectileSpeed": 25.0,
            "m_projectileLifetime": 2.0,
            "m_6396149F": {
                "m_57DAE1B5": 0.4,
                "m_0A5ED456": 1,
                "m_modifyHealth": {
                    "m_amount": 100.0
                }
            }
        });
        let authored = ProjectileBallistics::from_fields(&fields, |config| {
            Ok(config.as_f64().map_or(Value::Null, Value::Float))
        })
        .unwrap()
        .unwrap();
        let targets = [(1, target(0.0, 2.0)), (2, target(0.0, 4.0))];
        let mut flight =
            ProjectileFlight::new(authored, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], Some(100))
                .unwrap()
                .with_piercing(true);
        let mut hit_entities = Vec::new();
        let mut wall_hit = None;
        for tick in 101..=130 {
            let end = flight
                .advance(tick, &targets, |origin, dir, reach| {
                    let wall_z = 6.0;
                    if dir[2] > 0.0 && origin[2] <= wall_z && origin[2] + dir[2] * reach >= wall_z {
                        Ok(Some((wall_z - origin[2]) / dir[2]))
                    } else {
                        Ok(None)
                    }
                })
                .unwrap();
            for impact in flight.drain_piercing_impacts() {
                hit_entities.push((impact.entity, impact.damage));
            }
            if let Some(end) = end {
                wall_hit = Some(end);
                break;
            }
        }
        assert_eq!(hit_entities, vec![(1, 100.0), (2, 100.0)]);
        let end = wall_hit.expect("piercing projectile terminates at wall");
        assert!(!end.expired);
        assert!(end.impact);
        assert_eq!(end.contact, None);
    }

    #[test]
    fn hit_target_policy_distinguishes_enemy_pure_heal_and_dual_team_volleys() {
        use crate::game_hitscan::HitTargetPolicy;
        // Standard enemy-only volley (default)
        let enemy = HitTargetPolicy::from_authored(&serde_json::json!({}));
        assert!(!enemy.healing && !enemy.affects_allies && enemy.affects_enemies);
        // Pure healing volley (e.g. Baptiste RMB / Moira spray): m_93CDFD2F = 0
        let pure_heal = HitTargetPolicy::from_authored(&serde_json::json!({
            "m_modifyHealth": { "m_93CDFD2F": 0 }
        }));
        assert!(pure_heal.healing && pure_heal.affects_allies && !pure_heal.affects_enemies);
        // Dual ally-heal + enemy-damage volley (e.g. Ana Rifle / Biotic Grenade)
        let dual = HitTargetPolicy::from_authored(&serde_json::json!({
            "m_09528B11": 1,
            "m_50C84E9E": 1,
            "m_3E7A533D": 1
        }));
        assert!(!dual.healing && dual.affects_allies && dual.affects_enemies);
        // Ashe / Wrecking Ball m_09528B11=1 with m_50C84E9E=0 is NOT healing
        let non_heal = HitTargetPolicy::from_authored(&serde_json::json!({
            "m_09528B11": 1,
            "m_50C84E9E": 0,
            "m_3E7A533D": 1
        }));
        assert!(!non_heal.healing && !non_heal.affects_allies && non_heal.affects_enemies);
    }

    #[test]
    fn projectile_definition_maps_explicit_null_stu_to_none_and_rejects_malformed_zero() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/projectile-tokenless-v1.json"
        ))
        .unwrap();
        let evaluate = |config: &Json| {
            crate::script_expr::evaluate(config, &mut crate::script_expr::EmptyContext)
        };
        let ballistics = ProjectileBallistics::from_fields(&fixture["fields"], evaluate)
            .unwrap()
            .unwrap();
        assert_eq!(ballistics.definition, None);
        assert_eq!(
            ballistics.direct.unwrap().amount,
            fixture["expected_damage"].as_f64().unwrap()
        );

        let mut malformed = fixture["fields"].clone();
        malformed["m_projectileEntity"]["m_entityDef"] = serde_json::json!({"$": "Other"});
        assert!(matches!(
            ProjectileBallistics::from_fields(&malformed, |config| {
                if config.get("$").and_then(Json::as_str) == Some("Other") {
                    Ok(Value::Asset(0))
                } else {
                    evaluate(config)
                }
            }),
            Err(EvalError::MalformedConfig("projectile entity definition"))
        ));
    }
}
