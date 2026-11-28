use std::{collections::BTreeMap, fs, io, path::Path, sync::Arc};

use serde::Deserialize;

use super::{HEAD_BELOW_EYE, TargetPose};
use crate::{
    accounts::parse_guid,
    static_ray::{SurfaceHit, Vec3, capsule_entry_hit, norm, segment_separation},
};

/// Active articulated posture selector for heroes with transformable hurtboxes
/// (e.g. Bastion Sentry rear weakspot core, Wrecking Ball roll sphere).
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) enum HurtPosture {
    #[default]
    Standard,
    Sentry,
    Ball,
}

/// Live skeletal posture inputs used to articulate multi-region hurtboxes.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct SkeletalStance {
    pub yaw: i32,
    pub speed: f64,
    pub crouched: bool,
    pub head_protected: bool,
    pub posture: HurtPosture,
}

/// Sampled movement pose plus an optional combat profile for that posture.
#[derive(Clone)]
pub(crate) struct HurtPose {
    movement: TargetPose,
    calibrated: Option<HurtProfile>,
    sentry: Option<HurtProfile>,
    ball: Option<HurtProfile>,
    stance: Option<SkeletalStance>,
}

impl From<TargetPose> for HurtPose {
    fn from(movement: TargetPose) -> Self {
        Self {
            movement,
            calibrated: None,
            sentry: None,
            ball: None,
            stance: None,
        }
    }
}

impl HurtPose {
    pub(crate) fn with_stance(mut self, stance: SkeletalStance) -> Self {
        self.stance = Some(stance);
        self
    }

    pub(crate) fn feet(&self) -> Vec3 {
        self.movement.feet
    }

    #[cfg(test)]
    pub(crate) fn head_probe_height(&self) -> Option<f64> {
        let offset = match self.active_profile() {
            Some(profile) => {
                let head = profile.head?;
                head.lower.midpoint(head.upper)
            }
            None => self.movement.eye,
        };
        Some(self.movement.feet[1] + offset)
    }

    fn active_profile(&self) -> Option<&HurtProfile> {
        match self.stance.map(|s| s.posture).unwrap_or_default() {
            HurtPosture::Sentry if self.sentry.is_some() => self.sentry.as_ref(),
            HurtPosture::Ball if self.ball.is_some() => self.ball.as_ref(),
            _ => self.calibrated.as_ref(),
        }
    }

    fn for_each_capsule(&self, mut visit: impl FnMut(Capsule, bool)) {
        let [x, y, z] = self.movement.feet;
        let (sin, cos) = self
            .stance
            .map(|stance| {
                let rad = f64::from(stance.yaw) * std::f64::consts::TAU / 65_536.0;
                rad.sin_cos()
            })
            .unwrap_or((0.0, 1.0));
        let world_legacy = |shape: LocalCapsule, head| {
            let fwd = shape.forward_offset;
            let cx = x + sin * fwd;
            let cz = z + cos * fwd;
            (
                Capsule {
                    ends: [[cx, y + shape.lower, cz], [cx, y + shape.upper, cz]],
                    radius: shape.radius,
                },
                head,
            )
        };
        if let Some(profile) = self.active_profile() {
            let head_protected = self.stance.is_some_and(|s| s.head_protected);
            if let Some(engine_caps) = &profile.capsules {
                let run_lean = self.stance.map_or(0.0, |s| {
                    if s.crouched {
                        0.0
                    } else {
                        (s.speed / 8.0).clamp(0.0, 1.0) * 0.06
                    }
                });
                let to_world = |p: [f64; 3]| {
                    let lz = p[2] + if p[1] > 0.9 { run_lean } else { 0.0 };
                    [
                        x + cos * p[0] + sin * lz,
                        y + p[1],
                        z - sin * p[0] + cos * lz,
                    ]
                };
                for cap in engine_caps.iter() {
                    let is_crit = cap.critical && !head_protected;
                    visit(
                        Capsule {
                            ends: [to_world(cap.a), to_world(cap.b)],
                            radius: cap.radius,
                        },
                        is_crit,
                    );
                }
                return;
            }
            let (body_cap, _) = world_legacy(profile.body, false);
            visit(body_cap, false);
            if !head_protected {
                if let Some(shape) = profile.head {
                    let (head_cap, _) = world_legacy(shape, true);
                    visit(head_cap, true);
                }
            }
            return;
        }
        if let Some(stance) = self.stance {
            let h = self.movement.height.max(0.8);
            let r = self.movement.radius.max(0.25);
            let lean = if stance.crouched {
                0.09
            } else {
                (stance.speed / 8.0).clamp(0.0, 1.0) * 0.08
            };
            let legs_r = (r * 0.62).max(0.18);
            let legs_top = (h * 0.46).max(legs_r + 0.05);
            visit(
                Capsule {
                    ends: [[x, y + legs_r, z], [x, y + legs_top, z]],
                    radius: legs_r,
                },
                false,
            );
            let torso_r = (r * 0.94).max(0.24);
            let torso_low = (h * 0.46).max(torso_r);
            let torso_high = (h * 0.78).max(torso_low + 0.05);
            visit(
                Capsule {
                    ends: [
                        [
                            x + sin * (lean * 0.4),
                            y + torso_low,
                            z + cos * (lean * 0.4),
                        ],
                        [x + sin * lean, y + torso_high, z + cos * lean],
                    ],
                    radius: torso_r,
                },
                false,
            );
            if !stance.head_protected {
                let head_r = (r * 0.42).clamp(0.18, 0.28);
                let head_y = (self.movement.eye + 0.04).clamp(torso_high, h - head_r * 0.5);
                let head_fwd = lean * 1.35;
                let center = [x + sin * head_fwd, y + head_y, z + cos * head_fwd];
                visit(
                    Capsule {
                        ends: [center, center],
                        radius: head_r,
                    },
                    true,
                );
            }
            return;
        }
        visit(
            Capsule {
                ends: self.movement.segment(),
                radius: self.movement.radius,
            },
            false,
        );
    }

    pub(crate) fn contact(
        &self,
        origin: Vec3,
        direction: Vec3,
        reach: f64,
        padding: f64,
    ) -> Option<HurtContact> {
        let mut best_body: Option<HurtContact> = None;
        let mut best_head: Option<HurtContact> = None;
        self.for_each_capsule(|capsule, head| {
            let [a, b] = capsule.ends;
            if let Some(hit) =
                capsule_entry_hit(origin, direction, reach, a, b, capsule.radius + padding)
            {
                let candidate = HurtContact { capsule, hit, head };
                let slot = if head { &mut best_head } else { &mut best_body };
                if slot
                    .as_ref()
                    .is_none_or(|prev| candidate.hit.distance < prev.hit.distance)
                {
                    *slot = Some(candidate);
                }
            }
        });
        let mut contact = match (best_head, best_body) {
            (Some(head), Some(body)) => {
                // Only allow a slightly recessed head capsule to win over a wider torso
                // capsule when the ray enters at genuine head height above the neck plane,
                // preventing chest shots from piercing into lower neck/head hemispheres.
                let impact_y = origin[1] + direction[1] * head.hit.distance;
                let head_low_y = head.capsule.ends[0][1].min(head.capsule.ends[1][1]);
                let head_high_y = head.capsule.ends[0][1].max(head.capsule.ends[1][1]);
                // Point-sphere heads (e.g. Training Bot `lower == upper`) place
                // the face/visor below sphere center; allow upper ~75% of the head
                // sphere to beat a wider torso cylinder while keeping chest shots body.
                let neck_drop = if (head_high_y - head_low_y).abs() <= 1e-4 {
                    0.55
                } else {
                    0.25
                };
                let neck_plane = head_low_y - head.capsule.radius * neck_drop;
                let front_margin = if impact_y >= neck_plane {
                    (body.capsule.radius - head.capsule.radius).max(0.0) + 0.12
                } else {
                    0.0
                };
                if head.hit.distance <= body.hit.distance + front_margin {
                    head
                } else {
                    body
                }
            }
            (Some(head), None) => head,
            (None, Some(body)) => body,
            (None, None) => return None,
        };
        if self.active_profile().is_none() {
            // Compatibility for bodies that have no separately measured combat profile.
            let impact_y = origin[1] + direction[1] * contact.hit.distance;
            contact.head = impact_y >= self.movement.feet[1] + self.movement.eye - HEAD_BELOW_EYE;
        }
        Some(contact)
    }

    pub(crate) fn nearest_surface(&self, point: Vec3, origin: Vec3) -> Option<Surface> {
        let mut best: Option<(f64, Capsule)> = None;
        self.for_each_capsule(|capsule, _| {
            let [a, b] = capsule.ends;
            let distance = (norm(segment_separation(point, a, b)) - capsule.radius).max(0.0);
            if best.as_ref().is_none_or(|(prev, _)| distance < *prev) {
                best = Some((distance, capsule));
            }
        });
        let (distance, capsule) = best?;
        let [a, b] = capsule.ends;
        let from_axis = segment_separation(origin, a, b);
        let axis_distance = norm(from_axis);
        Some(Surface {
            distance,
            axis_distance,
            from_axis,
            sight_reach: axis_distance - capsule.radius,
        })
    }
}

pub(crate) struct Surface {
    pub distance: f64,
    axis_distance: f64,
    from_axis: Vec3,
    sight_reach: f64,
}

impl Surface {
    pub(crate) fn visible<E>(
        &self,
        cast: impl FnOnce(Vec3, f64) -> Result<Option<f64>, E>,
    ) -> Result<bool, E> {
        const EPSILON: f64 = 1e-9;
        let reach = self.sight_reach;
        if reach <= EPSILON {
            return Ok(true);
        }
        let toward = self.from_axis.map(|axis| -axis / self.axis_distance);
        Ok(cast(toward, reach)?.is_none_or(|hit| hit + EPSILON >= reach))
    }
}

pub(crate) struct HurtContact {
    capsule: Capsule,
    pub(crate) hit: SurfaceHit,
    pub(crate) head: bool,
}

impl HurtContact {
    pub(crate) fn point_on_body(&self, sweep_centre: Vec3) -> Vec3 {
        let [a, b] = self.capsule.ends;
        let separation = segment_separation(sweep_centre, a, b);
        std::array::from_fn(|axis| {
            sweep_centre[axis] - separation[axis] + self.hit.normal[axis] * self.capsule.radius
        })
    }
}

struct Capsule {
    ends: [Vec3; 2],
    radius: f64,
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct LocalCapsule {
    /// Endpoints of a vertical capsule axis, in metres above the feet.
    lower: f64,
    upper: f64,
    radius: f64,
    /// Optional local forward offset along actor facing yaw (e.g. D.Va MEKA cockpit,
    /// Bastion Sentry rear core with negative offset, or forward-leaning Tank heads).
    #[serde(default)]
    forward_offset: f64,
}

impl LocalCapsule {
    fn valid(self) -> bool {
        self.lower.is_finite()
            && self.upper.is_finite()
            && self.lower <= self.upper
            && self.radius.is_finite()
            && self.radius > 0.0
            && self.forward_offset.is_finite()
    }
}

#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EngineCapsule {
    a: [f64; 3],
    b: [f64; 3],
    radius: f64,
    #[serde(default)]
    critical: bool,
}

impl EngineCapsule {
    fn valid(self) -> bool {
        self.a.into_iter().all(f64::is_finite)
            && self.b.into_iter().all(f64::is_finite)
            && self.radius.is_finite()
            && self.radius > 0.0
    }
}

#[derive(Clone, Debug)]
struct HurtProfile {
    body: LocalCapsule,
    head: Option<LocalCapsule>,
    capsules: Option<Arc<[EngineCapsule]>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawHurtProfile {
    body: LocalCapsule,
    head: Option<LocalCapsule>,
    #[serde(default)]
    capsules: Vec<EngineCapsule>,
}

impl RawHurtProfile {
    fn into_profile(self) -> Option<HurtProfile> {
        if !self.body.valid()
            || self.head.is_some_and(|head| !head.valid())
            || self.capsules.iter().any(|cap| !cap.valid())
        {
            return None;
        }
        let capsules = (!self.capsules.is_empty()).then(|| Arc::from(self.capsules));
        Some(HurtProfile {
            body: self.body,
            head: self.head,
            capsules,
        })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawBodyProfiles {
    body: LocalCapsule,
    head: Option<LocalCapsule>,
    #[serde(default)]
    capsules: Vec<EngineCapsule>,
    crouched: Option<RawHurtProfile>,
    #[serde(default)]
    sentry: Option<RawHurtProfile>,
    #[serde(default)]
    ball: Option<RawHurtProfile>,
}

struct BodyProfiles {
    standing: HurtProfile,
    crouched: Option<HurtProfile>,
    sentry: Option<HurtProfile>,
    ball: Option<HurtProfile>,
}

#[derive(Default)]
pub(crate) struct HurtVolumeCatalog {
    by_body: BTreeMap<u64, BodyProfiles>,
}

impl HurtVolumeCatalog {
    pub(crate) fn load(path: &Path) -> io::Result<Self> {
        match fs::read_to_string(path) {
            Ok(text) => Self::from_json(&text),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error),
        }
    }

    pub(crate) fn from_json(text: &str) -> io::Result<Self> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Data {
            version: u32,
            bodies: BTreeMap<String, RawBodyProfiles>,
        }

        let invalid = |message: &str| io::Error::new(io::ErrorKind::InvalidData, message);
        let data: Data = serde_json::from_str(text)
            .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?;
        if data.version != 1 {
            return Err(invalid("unsupported combat hurt volume catalog version"));
        }
        let mut by_body = BTreeMap::new();
        for (key, mut raw) in data.bodies {
            let body = parse_guid(&key)
                .filter(|body| *body != 0)
                .ok_or_else(|| invalid("invalid combat hurt volume body GUID"))?;
            // Normalize Wrecking Ball (`0x200B`) bind-pose pilot head capsule from mech origin
            // up to the cockpit mount so top-of-mech shots crit and belly shots do not.
            if body == 0x0400_0000_0000_200b {
                if let Some(head) = &mut raw.head {
                    if head.upper < 0.8 {
                        head.lower += 1.32;
                        head.upper += 1.32;
                        head.forward_offset += 0.22;
                    }
                }
                for cap in &mut raw.capsules {
                    if cap.critical && cap.a[1].max(cap.b[1]) < 0.8 {
                        cap.a[1] += 1.32;
                        cap.b[1] += 1.32;
                        cap.a[2] += 0.22;
                        cap.b[2] += 0.22;
                    }
                }
            }
            // Tighten Practice Range friendly bot (`0x143E`, `0x146E`) cylinder radius from
            // 0.64 m to visual torso width (0.42 m) so enemy bolts impact cleanly on the chest.
            if matches!(body, 0x0400_0000_0000_143e | 0x0400_0000_0000_146e)
                && raw.body.radius > 0.45
            {
                raw.body.radius = 0.42;
            }
            let standing = RawHurtProfile {
                body: raw.body,
                head: raw.head,
                capsules: raw.capsules,
            }
            .into_profile()
            .ok_or_else(|| invalid("invalid combat hurt volume capsule"))?;
            let crouched = match raw.crouched {
                Some(pose) => Some(
                    pose.into_profile()
                        .ok_or_else(|| invalid("invalid combat hurt volume capsule"))?,
                ),
                None => None,
            };
            let sentry = match raw.sentry {
                Some(pose) => Some(
                    pose.into_profile()
                        .ok_or_else(|| invalid("invalid combat hurt volume capsule"))?,
                ),
                None => None,
            };
            let ball = match raw.ball {
                Some(pose) => Some(
                    pose.into_profile()
                        .ok_or_else(|| invalid("invalid combat hurt volume capsule"))?,
                ),
                None => None,
            };
            let profile = BodyProfiles {
                standing,
                crouched,
                sentry,
                ball,
            };
            if by_body.insert(body, profile).is_some() {
                return Err(invalid("duplicate combat hurt volume body GUID"));
            }
        }
        Ok(Self { by_body })
    }

    pub(crate) fn pose(&self, body: u64, movement: TargetPose, crouched: bool) -> HurtPose {
        let profiles = self.by_body.get(&body);
        let calibrated = profiles.and_then(|p| {
            if crouched {
                p.crouched.clone()
            } else {
                Some(p.standing.clone())
            }
        });
        let sentry = profiles.and_then(|p| p.sentry.clone());
        let ball = profiles.and_then(|p| p.ball.clone());
        HurtPose {
            movement,
            calibrated,
            sentry,
            ball,
            stance: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::Value;

    use super::*;
    use crate::{
        game_hitscan::{DamageTags, HitDefinition, HitscanShot},
        projectile_flight::{ProjectileBallistics, ProjectileFlight},
        scene_assets::PreparedScene,
    };

    #[test]
    fn crouched_hurt_pose_uses_matching_calibration_or_mover_shape() {
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/combat-hurt-volumes-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"];
        let crouched = &fixture["crouched"];
        let key = input["body"].as_str().unwrap();
        let body = parse_guid(key).unwrap();
        let pose = TargetPose {
            feet: serde_json::from_value(input["movement"]["feet"].clone()).unwrap(),
            height: crouched["height"].as_f64().unwrap(),
            radius: input["movement"]["radius"].as_f64().unwrap(),
            eye: crouched["eye"].as_f64().unwrap(),
        };
        let mut scene = PreparedScene::fixture_for_bots(Vec::new());
        let direction = [0.0, 0.0, 1.0];
        let standing_ray = serde_json::from_value(crouched["standing_head_ray"].clone()).unwrap();
        let crouched_ray = serde_json::from_value(crouched["crouched_head_ray"].clone()).unwrap();
        let mut data = input["catalog"].clone();
        for custom in [false, true] {
            if custom {
                data["bodies"][key]["crouched"] = crouched["profile"].clone();
            }
            let catalog = HurtVolumeCatalog::from_json(&data.to_string()).unwrap();
            scene.fixture_set_hurt_volumes(catalog);
            let (_, queries) = scene.bot_simulation();
            let sampled = queries.hurt_pose(body, pose);
            assert_eq!(sampled.calibrated.is_some(), custom);
            assert!(
                sampled
                    .contact(standing_ray, direction, 10.0, 0.0)
                    .is_none()
            );
            assert!(
                sampled
                    .contact(crouched_ray, direction, 10.0, 0.0)
                    .unwrap()
                    .head
            );
        }
    }

    #[test]
    fn calibrated_combat_volumes_distinguish_head_body_and_empty_space() {
        let contract: Value = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/combat-hurt-volumes-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["case_id"], "combat-hurt-volumes.synthetic.v1");
        let input = &contract["input"];
        let body = parse_guid(input["body"].as_str().unwrap()).unwrap();
        let mover = &input["movement"];
        let movement = TargetPose {
            feet: serde_json::from_value(mover["feet"].clone()).unwrap(),
            height: mover["height"].as_f64().unwrap(),
            radius: mover["radius"].as_f64().unwrap(),
            eye: mover["eye"].as_f64().unwrap(),
        };
        let profile = HurtVolumeCatalog::from_json(&input["catalog"].to_string()).unwrap();
        let target = profile.pose(body, movement, false);
        let compatibility = profile.pose(body + 1, movement, false);
        let direction = [0.0, 0.0, 1.0];
        let hit = HitDefinition {
            tags: DamageTags::EMPTY,
            amount: 20.0,
            loadout: None,
            critical: 2.0,
            falloff: None,
        };
        for case in contract["cases"].as_array().unwrap() {
            let origin = serde_json::from_value(case["origin"].clone()).unwrap();
            let critical = case["critical"].as_bool();
            let shot = HitscanShot::with_direction(origin, direction, 10.0, hit);
            let resolve = |target: HurtPose| {
                shot.resolve_combat([(1, target)], |_, _, _| Ok(None))
                    .unwrap()
            };
            assert_eq!(
                resolve(target.clone()).map(|impact| (impact.critical, impact.damage)),
                critical.map(|head| (head, if head { 40.0 } else { 20.0 })),
                "{}",
                case["name"]
            );
            match case["name"].as_str().unwrap() {
                "head-above-mover" | "side-outside-mover" => {
                    assert!(resolve(compatibility.clone()).is_none());
                }
                "chest-below-head" => assert!(resolve(compatibility.clone()).unwrap().critical),
                _ => {}
            }
            assert!(
                shot.resolve_combat([(1, target.clone())], |_, _, _| Ok(Some(1.0)))
                    .unwrap()
                    .is_none()
            );
            for profile in input["projectile_profiles"].as_array().unwrap() {
                let multiplier = profile["critical_multiplier"].as_f64().unwrap();
                let expected = critical.map(|head| {
                    let critical = head && multiplier > 1.0;
                    (critical, if critical { 20.0 * multiplier } else { 20.0 })
                });
                let mut flight = ProjectileFlight::new(
                    ProjectileBallistics {
                        speed: 600.0,
                        lifetime: 0.5,
                        max_distance: None,
                        terrain_rebound: None,
                        terrain_radius: 0.0,
                        radius: 0.0,
                        definition: None,
                        direct: Some(HitDefinition {
                            critical: multiplier,
                            ..hit
                        }),
                        blast: None,
                    },
                    origin,
                    direction,
                    None,
                )
                .unwrap();
                let impact = flight
                    .advance_combat(1, &[(1, target.clone())], None, |_, _, _, _| Ok(None))
                    .unwrap()
                    .map(|end| (end.impacts[0].critical, end.impacts[0].damage));
                assert_eq!(impact, expected, "{} ({multiplier})", case["name"]);
            }
        }
    }

    #[test]
    #[ignore = "requires a calibrated 104319 Practice Range pack and private ray replay"]
    fn prepared_live_rays_hit_the_calibrated_bot_regions() {
        use std::{path::PathBuf, time::Duration};

        use crate::{
            commands::Command,
            game_combat::{PosedHitscan, ShotContext},
            game_combat_messages::CombatNotice,
            game_content::GameMap,
            game_instance::RETAIL_QUANTA,
            scene_assets::SceneAssets,
        };

        #[derive(Deserialize)]
        struct Replay {
            target: u32,
            samples: Vec<Ray>,
        }

        #[derive(Deserialize)]
        struct Ray {
            frame: u32,
            origin: Vec3,
            direction: Vec3,
            critical: Option<bool>,
        }

        let root = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let replay = PathBuf::from(std::env::var_os("ORPHEUS_HURT_REPLAY").unwrap());
        let replay: Replay = serde_json::from_slice(&fs::read(replay).unwrap()).unwrap();
        assert!(!replay.samples.is_empty());
        let mut scene = SceneAssets::load(&root)
            .unwrap()
            .prepare(GameMap::practice_range())
            .unwrap();
        let (room, queries) = scene.bot_simulation();
        let target = room
            .bodies()
            .unwrap()
            .find(|bot| bot.entity == replay.target)
            .unwrap();
        assert!(
            queries
                .hurt_pose(target.spawn.body, queries.target(target))
                .calibrated
                .is_some()
        );
        for sample in replay.samples {
            let result = room
                .hitscan(
                    &queries,
                    PosedHitscan {
                        source: 0xa000_0101,
                        origin: sample.origin,
                        direction: sample.direction,
                        command: Command {
                            frame: sample.frame,
                            ..Command::default()
                        },
                        shot_frame: sample.frame,
                        reach: 30.0,
                        hit: HitDefinition {
                            tags: DamageTags::EMPTY,
                            amount: 2.0,
                            loadout: None,
                            critical: 2.0,
                            falloff: None,
                        },
                    },
                    ShotContext {
                        team: 0,
                        quanta: RETAIL_QUANTA,
                        tick: sample.frame,
                        now: Duration::ZERO,
                    },
                    |entity| entity == replay.target,
                )
                .unwrap();
            let observed = result.map(|hit| match hit.notices[0] {
                Some(CombatNotice::Hit {
                    target, critical, ..
                }) => {
                    assert_eq!(target, replay.target);
                    critical
                }
                _ => panic!("missing ordinary damage notice"),
            });
            assert_eq!(observed, sample.critical, "frame {}", sample.frame);
        }
    }

    #[test]
    fn engine_multi_capsule_3d_rotation_and_sentry_ball_postures() {
        let data = serde_json::json!({
            "version": 1,
            "bodies": {
                "0x04000000000001FD": {
                    "body": {"lower": 0.4, "upper": 1.4, "radius": 0.4},
                    "head": {"lower": 1.85, "upper": 1.85, "radius": 0.2},
                    "capsules": [
                        {"a": [0.0, 0.5, 0.0], "b": [0.0, 1.4, 0.0], "radius": 0.35},
                        {"a": [0.5, 1.2, 0.0], "b": [0.5, 0.8, 0.0], "radius": 0.12},
                        {"a": [0.0, 1.85, 0.0], "b": [0.0, 1.85, 0.0], "radius": 0.2, "critical": true}
                    ],
                    "sentry": {
                        "body": {"lower": 0.38, "upper": 1.05, "radius": 0.56},
                        "head": {"lower": 0.92, "upper": 0.96, "radius": 0.22, "forward_offset": -0.48},
                        "capsules": [
                            {"a": [0.0, 0.38, 0.0], "b": [0.0, 1.05, 0.05], "radius": 0.56},
                            {"a": [0.0, 0.92, -0.44], "b": [0.0, 0.96, -0.52], "radius": 0.22, "critical": true}
                        ]
                    },
                    "ball": {
                        "body": {"lower": 0.78, "upper": 0.78, "radius": 0.78},
                        "head": null,
                        "capsules": [
                            {"a": [0.0, 0.78, 0.0], "b": [0.0, 0.78, 0.0], "radius": 0.78}
                        ]
                    }
                }
            }
        });
        let catalog = HurtVolumeCatalog::from_json(&data.to_string()).unwrap();
        let movement = TargetPose {
            feet: [0.0, 0.0, 0.0],
            height: 2.0,
            radius: 0.5,
            eye: 1.7,
        };
        // Standard posture: arm capsule at local +X=0.5m is hittable from front, head is critical.
        let standard = catalog.pose(0x0400_0000_0000_01FD, movement, false);
        let arm_hit = standard
            .contact([0.5, 1.0, -3.0], [0.0, 0.0, 1.0], 10.0, 0.0)
            .unwrap();
        assert!(!arm_hit.head);
        let head_hit = standard
            .contact([0.0, 1.85, -3.0], [0.0, 0.0, 1.0], 10.0, 0.0)
            .unwrap();
        assert!(head_hit.head);

        // Sentry posture: shot from behind (-Z) hits rear glowing core critically; front head height misses.
        let sentry = standard.clone().with_stance(SkeletalStance {
            yaw: 0,
            speed: 0.0,
            crouched: true,
            head_protected: false,
            posture: HurtPosture::Sentry,
        });
        assert!(
            sentry
                .contact([0.0, 1.85, -3.0], [0.0, 0.0, 1.0], 10.0, 0.0)
                .is_none()
        );
        let rear_crit = sentry
            .contact([0.0, 0.94, -3.0], [0.0, 0.0, 1.0], 10.0, 0.0)
            .unwrap();
        assert!(rear_crit.head);

        // Ball posture: pure sphere with zero critical hits.
        let ball = standard.with_stance(SkeletalStance {
            yaw: 0,
            speed: 10.0,
            crouched: false,
            head_protected: true,
            posture: HurtPosture::Ball,
        });
        let ball_hit = ball
            .contact([0.0, 0.78, -3.0], [0.0, 0.0, 1.0], 10.0, 0.0)
            .unwrap();
        assert!(!ball_hit.head);
    }

    #[test]
    fn chest_shot_below_neck_plane_stays_body_while_wrecking_ball_pilot_crit_elevates() {
        let data = serde_json::json!({
            "version": 1,
            "bodies": {
                "0x0400000000001403": {
                    "body": {"lower": 0.45, "upper": 1.55, "radius": 0.56},
                    "head": {"lower": 1.62, "upper": 1.82, "radius": 0.30},
                    "capsules": [
                        {"a": [0.0, 0.45, 0.0], "b": [0.0, 1.55, 0.0], "radius": 0.56},
                        {"a": [0.0, 1.62, 0.0], "b": [0.0, 1.82, 0.0], "radius": 0.30, "critical": true}
                    ]
                },
                "0x040000000000200B": {
                    "body": {"lower": 0.85, "upper": 1.35, "radius": 0.92},
                    "head": {"lower": 0.31, "upper": 0.39, "radius": 0.24},
                    "capsules": [
                        {"a": [0.0, 0.85, 0.0], "b": [0.0, 1.35, 0.0], "radius": 0.92},
                        {"a": [0.0, 0.31, 0.02], "b": [0.0, 0.39, 0.02], "radius": 0.24, "critical": true}
                    ]
                }
            }
        });
        let catalog = HurtVolumeCatalog::from_json(&data.to_string()).unwrap();
        let bot_pose = catalog.pose(
            0x0400_0000_0000_1403,
            TargetPose {
                feet: [0.0, 0.0, 0.0],
                height: 2.0,
                radius: 0.56,
                eye: 1.7,
            },
            false,
        );
        // Chest shot at Y=1.25m must register as body hit, never stolen by head front-margin.
        let chest = bot_pose
            .contact([0.0, 1.25, -4.0], [0.0, 0.0, 1.0], 10.0, 0.0)
            .unwrap();
        assert!(!chest.head);
        // True head shot at Y=1.72m registers as critical hit.
        let head = bot_pose
            .contact([0.0, 1.72, -4.0], [0.0, 0.0, 1.0], 10.0, 0.0)
            .unwrap();
        assert!(head.head);

        // Wrecking Ball (`0x200B`) pilot crit volume is lifted out of the lower hull into the cockpit.
        let hammond = catalog
            .pose(
                0x0400_0000_0000_200B,
                TargetPose {
                    feet: [0.0, 0.0, 0.0],
                    height: 2.1,
                    radius: 0.92,
                    eye: 1.65,
                },
                false,
            )
            .with_stance(SkeletalStance {
                yaw: 0,
                speed: 0.0,
                crouched: false,
                head_protected: false,
                posture: HurtPosture::Standard,
            });
        let lower_hull = hammond
            .contact([0.0, 0.95, 4.0], [0.0, 0.0, -1.0], 10.0, 0.0)
            .unwrap();
        assert!(!lower_hull.head);
        let cockpit = hammond
            .contact([0.0, 1.68, 4.0], [0.0, 0.0, -1.0], 10.0, 0.0)
            .unwrap();
        assert!(cockpit.head);
    }

    #[test]
    fn training_bot_point_sphere_head_beats_wide_torso_at_visor_height() {
        let data = serde_json::json!({
            "version": 1,
            "bodies": {
                "0x0400000000001403": {
                    "body": {"lower": 0.62, "upper": 1.16, "radius": 0.64},
                    "head": {"lower": 1.91, "upper": 1.91, "radius": 0.365}
                }
            }
        });
        let catalog = HurtVolumeCatalog::from_json(&data.to_string()).unwrap();
        let bot = catalog.pose(
            0x0400_0000_0000_1403,
            TargetPose {
                feet: [0.0, 0.0, 10.0],
                height: 2.0,
                radius: 0.64,
                eye: 1.91,
            },
            false,
        );
        let aim_at = |target_y: f64| {
            let origin = [0.0, 1.55, 0.0];
            let raw = [0.0, target_y - origin[1], 10.0];
            let len = norm(raw);
            bot.contact(origin, raw.map(|v| v / len), 30.0, 0.0)
                .unwrap()
        };
        assert!(!aim_at(1.35).head, "chest shot must stay body");
        assert!(
            aim_at(1.75).head,
            "visor shot must register as critical headshot"
        );
        assert!(
            aim_at(1.91).head,
            "head center shot must register as critical headshot"
        );
    }
}
