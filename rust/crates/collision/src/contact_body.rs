//! Upright character capsule with ordered static pairs and bounded contacts.
//!
//! Ports `CharacterBody` from `ow174/game/contacts.py`. The immutable
//! world is shared. Candidate lifetimes and warm caches are per body.

use std::{collections::HashSet, fmt, sync::Arc};

use crate::{
    collision_grid::ColumnRange,
    collision_scene::BreakableState,
    contact_cache::{CapsulePose, LOOSE, ProxyContact, encloses},
    contact_gjk::{PointSetSize, SimplexCache, gjk, sweep::MeshProbe},
    contact_hull::HullContactError,
    contact_math::{TINY, Vec3, add, dot, maxps, minps, scale, sub},
    contact_mesh::ContactBounds,
    contact_round::{Capsule, ContactPoint, RoundShape, capsule_capsule},
    contact_solver::{Constraint, ContactPlane, project_target},
    contact_triangle::{SKIN, TOUCH, closest_parameters},
    contact_world::{ContactWorld, PROXY_MARGIN, StaticGeometry, StaticShape, WorldHit, overlaps},
    static_ray::{capsule_entry_hit, norm, segment_separation},
};

const MAX_ROUNDS: u32 = 20;
const FIT_SHRINK: f32 = 0.02;
const AXIS_CONTACT_NORMAL: Vec3 = [-1.0, 0.0, 0.0];

pub struct BodyMove<'a> {
    pub position: Vec3,
    pub planes: &'a [ContactPlane],
    pub touching: bool,
    /// An admitted actor changed the requested motion through a cast or constraint.
    pub actor_contact: bool,
}

#[derive(Debug, Clone, Copy)]
pub struct SweepClip {
    pub proxy_id: u32,
    pub category: u16,
    pub fraction: f32,
}

pub struct CharacterBody {
    world: Arc<ContactWorld>,
    breakables: Option<Arc<BreakableState>>,
    pose: CapsulePose,
    category: u16,
    fat: ContactBounds,
    proxy_moved: bool,
    contacts: Vec<(u32, ProxyContact)>,
    pairs: HashSet<u32>,
    budget: u32,
    constraints: Vec<Constraint>,
    planes: Vec<ContactPlane>,
}

impl fmt::Debug for CharacterBody {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CharacterBody")
            .field("position", &self.pose.position)
            .field("category", &self.category)
            .finish_non_exhaustive()
    }
}

impl CharacterBody {
    pub fn new(world: Arc<ContactWorld>, pose: CapsulePose, category: u16) -> Self {
        Self {
            world,
            breakables: None,
            pose,
            category,
            fat: expanded(pose.bounds(), PROXY_MARGIN),
            proxy_moved: true,
            contacts: Vec::new(),
            pairs: HashSet::new(),
            budget: MAX_ROUNDS,
            constraints: Vec::new(),
            planes: Vec::new(),
        }
    }

    pub fn set_breakables(&mut self, breakables: Arc<BreakableState>) {
        self.breakables = Some(breakables);
    }

    pub fn position(&self) -> Vec3 {
        self.pose.position
    }

    pub fn capsule_pose(&self) -> CapsulePose {
        self.pose
    }

    pub fn radius(&self) -> f32 {
        self.pose.radius
    }

    pub fn world(&self) -> &ContactWorld {
        &self.world
    }

    /// Create the same capsule at a new origin with fresh contact state.
    pub fn fresh_at(&self, position: Vec3) -> Self {
        let mut body = Self::new(
            Arc::clone(&self.world),
            CapsulePose {
                position,
                ..self.pose
            },
            self.category,
        );
        body.breakables = self.breakables.clone();
        body
    }

    /// Relocate to a clear capsule origin without sweeping the intervening path.
    /// Tangency is admissible; invalid or overlapping destinations do not mutate
    /// the body or its warm contact state. Actor admission belongs to the caller.
    pub fn relocate_if_clear(
        &mut self,
        position: Vec3,
        actors: &[CapsulePose],
    ) -> Result<bool, HullContactError> {
        if position == self.pose.position {
            return Ok(false);
        }
        let candidate = CapsulePose {
            position,
            ..self.pose
        };
        if !valid_placement(candidate) || actors.iter().any(|&actor| !valid_placement(actor)) {
            return Ok(false);
        }
        let capsule = candidate.world_capsule();
        let bounds = candidate.bounds();
        for id in self
            .world
            .query_with_breakables(bounds, self.breakables.as_deref())
        {
            let shape = &self.world.shapes()[id as usize];
            if shape.geometry().mask() & self.category != 0
                && penetrates_shape(shape, capsule, self.category)?
            {
                return Ok(false);
            }
        }
        let low = bounds.low.map(f64::from);
        let high = bounds.high.map(f64::from);
        for actor in actors {
            if overlaps(low, high, actor.bounds())
                && capsule_capsule(capsule, actor.world_capsule())
                    .points()
                    .iter()
                    .any(|point| point.separation < 0.0)
            {
                return Ok(false);
            }
        }
        *self = self.fresh_at(position);
        Ok(true)
    }

    /// Query the same upright capsule and mask without changing its contact state.
    pub fn sweep(&self, start: Vec3, end: Vec3) -> Result<Option<WorldHit>, PointSetSize> {
        self.world.scene_sweep_with_breakables(
            MeshProbe {
                points: &[[0.0, self.pose.radius, 0.0], [0.0, self.pose.top, 0.0]],
                radius: self.pose.radius,
                start,
                end,
                category: self.category,
            },
            self.breakables.as_deref(),
        )
    }

    pub fn teleport(&mut self, position: Vec3) {
        if self.pose.position != position {
            self.pose.position = position;
            self.move_proxy(self.pose.bounds());
        }
    }

    pub fn try_resize(&mut self, radius: f32, top: f32) -> Result<bool, HullContactError> {
        if !self.fits(radius, top)? {
            return Ok(false);
        }
        self.pose = CapsulePose {
            radius,
            top,
            ..self.pose
        };
        self.move_proxy(self.pose.bounds());
        self.update_pairs();
        Ok(true)
    }

    pub fn fits(&self, radius: f32, top: f32) -> Result<bool, HullContactError> {
        if radius
            .partial_cmp(&SKIN)
            .is_none_or(std::cmp::Ordering::is_lt)
        {
            return Ok(false);
        }
        let reach = maxps(TOUCH, radius - FIT_SHRINK);
        let capsule = CapsulePose {
            radius,
            top,
            ..self.pose
        }
        .world_capsule();
        let bounds = segment_bounds(capsule.a, capsule.b, reach);
        for id in self
            .world
            .query_with_breakables(bounds, self.breakables.as_deref())
        {
            let shape = &self.world.shapes()[id as usize];
            if shape.geometry().mask() & self.category != 0
                && overlaps_segment(shape, [capsule.a, capsule.b], reach, self.category)?
            {
                return Ok(false);
            }
        }
        Ok(true)
    }

    pub fn move_capsule(
        &mut self,
        displacement: Vec3,
        dt: f32,
    ) -> Result<BodyMove<'_>, HullContactError> {
        self.move_capsule_against(displacement, dt, &[])
    }

    /// Move against the map and an ordered snapshot of eligible actor capsules.
    /// The caller excludes this body and supplies valid, finite capsule poses.
    pub fn move_capsule_against(
        &mut self,
        displacement: Vec3,
        dt: f32,
        actors: &[CapsulePose],
    ) -> Result<BodyMove<'_>, HullContactError> {
        self.move_capsule_inner::<false>(displacement, dt, actors, &mut None)
    }

    /// Observe the first clipped solver sweep of this move.
    pub fn move_capsule_traced(
        &mut self,
        displacement: Vec3,
        dt: f32,
        clip: &mut Option<SweepClip>,
    ) -> Result<BodyMove<'_>, HullContactError> {
        self.move_capsule_against_traced(displacement, dt, &[], clip)
    }

    /// As `move_capsule_against`, retaining static-proxy trace evidence.
    /// A nearer actor clip has no static proxy ID and leaves `clip` empty.
    pub fn move_capsule_against_traced(
        &mut self,
        displacement: Vec3,
        dt: f32,
        actors: &[CapsulePose],
        clip: &mut Option<SweepClip>,
    ) -> Result<BodyMove<'_>, HullContactError> {
        *clip = None;
        self.move_capsule_inner::<true>(displacement, dt, actors, clip)
    }

    fn move_capsule_inner<const TRACE: bool>(
        &mut self,
        displacement: Vec3,
        dt: f32,
        actors: &[CapsulePose],
        clip: &mut Option<SweepClip>,
    ) -> Result<BodyMove<'_>, HullContactError> {
        let rounds = self.budget;
        let mut current = self.pose.position;
        let target = add(add(scale([0.0; 3], dt), current), displacement);
        let mut passes_total = 0;
        let mut actor_contact = false;
        for _ in 0..rounds {
            self.update_pairs();
            self.constraints.clear();
            for (_, contact) in &mut self.contacts {
                let sign = contact.normal_sign();
                let manifolds = contact.manifolds(self.pose)?;
                for (normal, points) in manifolds.iter() {
                    let normal = if sign > 0.0 {
                        normal
                    } else {
                        normal.map(|axis| -axis)
                    };
                    self.constraints
                        .push(contact_constraint(current, normal, points));
                }
            }
            let static_constraints = self.constraints.len();
            if !actors.is_empty() {
                let bounds = expanded(self.pose.bounds(), TOUCH);
                let low = bounds.low.map(f64::from);
                let high = bounds.high.map(f64::from);
                for actor in actors {
                    if !overlaps(low, high, actor.bounds()) {
                        continue;
                    }
                    let (a, b, radius) = actor_exclusion(self.pose, *actor);
                    let separation = segment_separation(current.map(f64::from), a, b);
                    let distance = norm(separation);
                    if distance <= radius + f64::from(TOUCH) {
                        let normal = if distance == 0.0 {
                            AXIS_CONTACT_NORMAL
                        } else {
                            separation.map(|axis| (axis / distance) as f32)
                        };
                        // Cancel the static solver's penetration allowance for actors.
                        // Leave rounding clearance so a projected tangent can slide.
                        let position_dot = dot(normal, current);
                        let rounding = f32::EPSILON * position_dot.abs().max(1.0);
                        let offset = position_dot + ((radius - distance) as f32 + TOUCH + rounding);
                        self.constraints
                            .push(ContactPlane { normal, offset }.into());
                    }
                }
            }
            let (projected, passes) = project_target(target, &mut self.constraints);
            actor_contact |= self.constraints[static_constraints..]
                .iter()
                .any(|constraint| constraint.impulse > 0.0);
            passes_total += passes;
            let (moved, actor_clipped) = self.set_position::<TRACE>(projected, actors, clip)?;
            actor_contact |= actor_clipped;
            let step = sub(moved, current);
            current = moved;
            if dot(step, step) < TOUCH * TOUCH {
                break;
            }
        }
        self.budget = if passes_total > (rounds * 100).min(500) {
            (self.budget / 2).max(1)
        } else {
            (self.budget * 2).min(MAX_ROUNDS)
        };
        self.planes.clear();
        let mut touching = false;
        for constraint in &self.constraints {
            if constraint.impulse > 0.0 {
                touching = true;
                if constraint.impulse < f32::MAX {
                    self.planes.push(constraint.plane);
                }
            }
        }
        Ok(BodyMove {
            position: current,
            planes: &self.planes,
            touching,
            actor_contact,
        })
    }

    fn move_proxy(&mut self, bounds: ContactBounds) {
        if encloses(self.fat, bounds) && encloses(expanded(bounds, LOOSE), self.fat) {
            return;
        }
        self.fat = expanded(bounds, PROXY_MARGIN);
        self.proxy_moved = true;
    }

    fn update_pairs(&mut self) {
        if let Some(breakables) = self.breakables.as_deref() {
            if breakables.broken_count() > 0 {
                self.contacts.retain(|(id, _)| {
                    let keep = self.world.shapes()[*id as usize]
                        .breakable_placement()
                        .is_none_or(|placement| !breakables.is_broken(placement));
                    if !keep {
                        self.pairs.remove(id);
                    }
                    keep
                });
            }
        }
        if !std::mem::take(&mut self.proxy_moved) {
            return;
        }
        let before = self.contacts.len();
        for id in self
            .world
            .query_with_breakables(self.fat, self.breakables.as_deref())
        {
            let shape = &self.world.shapes()[id as usize];
            if shape.geometry().mask() & self.category != 0 && self.pairs.insert(id) {
                self.contacts
                    .push((id, ProxyContact::new(shape, self.category)));
            }
        }
        let new = self.contacts.len() - before;
        if new != 0 {
            self.contacts[before..].reverse();
            self.contacts.rotate_right(new);
        }
    }

    fn set_position<const TRACE: bool>(
        &mut self,
        target: Vec3,
        actors: &[CapsulePose],
        clip: &mut Option<SweepClip>,
    ) -> Result<(Vec3, bool), HullContactError> {
        let swept = self.pose.swept_bounds(target);
        self.move_proxy(swept);
        self.update_pairs();
        let translation = sub(target, self.pose.position);
        let low = self.fat.low.map(f64::from);
        let high = self.fat.high.map(f64::from);
        self.contacts.retain(|(id, _)| {
            let bounds = self.world.shapes()[*id as usize].fat_bounds();
            let keep = bounds.is_some_and(|bounds| overlaps(low, high, bounds));
            if !keep {
                self.pairs.remove(id);
            }
            keep
        });
        let mut fraction = 1.0;
        let mut proxy_id = None;
        let mut actor_clipped = false;
        if translation != [0.0; 3] {
            for (id, contact) in &mut self.contacts {
                if let Some(candidate) = contact.cast(self.pose, swept, translation)? {
                    fraction = minps(fraction, candidate);
                    if TRACE && candidate == fraction {
                        proxy_id = Some(*id);
                    }
                }
            }
            if !actors.is_empty() {
                let low = swept.low.map(f64::from);
                let high = swept.high.map(f64::from);
                let origin = self.pose.position.map(f64::from);
                let delta = translation.map(f64::from);
                let length = norm(delta);
                let direction = delta.map(|axis| axis / length);
                for actor in actors {
                    if !overlaps(low, high, actor.bounds()) {
                        continue;
                    }
                    let (a, b, radius) = actor_exclusion(self.pose, *actor);
                    if let Some(hit) = capsule_entry_hit(origin, direction, length, a, b, radius)
                        .filter(|hit| {
                            if hit.distance != 0.0 {
                                return true;
                            }
                            let outward = if norm(segment_separation(origin, a, b)) == 0.0 {
                                // Use the same separating plane as the contact projection.
                                AXIS_CONTACT_NORMAL
                            } else {
                                hit.normal.map(|axis| axis as f32)
                            };
                            dot(outward, translation) < 0.0
                        })
                    {
                        let candidate = (hit.distance / length) as f32;
                        if candidate < fraction {
                            actor_clipped = true;
                            if TRACE {
                                proxy_id = None;
                            }
                        }
                        fraction = minps(fraction, candidate);
                    }
                }
            }
        }
        if TRACE && clip.is_none() && fraction < 1.0 {
            *clip = proxy_id.map(|proxy_id| SweepClip {
                proxy_id,
                category: self.category,
                fraction,
            });
        }
        let moved = add(scale(translation, fraction), self.pose.position);
        if moved.into_iter().all(f32::is_finite) {
            self.teleport(moved);
        }
        Ok((self.pose.position, actor_clipped))
    }
}

// For two upright capsules the forbidden origins form one upright capsule
// with the summed radii and the interval of vertical axis differences.
fn actor_exclusion(moving: CapsulePose, actor: CapsulePose) -> ([f64; 3], [f64; 3], f64) {
    let [x, y, z] = actor.position.map(f64::from);
    let low = y + f64::from(actor.radius.min(actor.top)) - f64::from(moving.radius.max(moving.top));
    let high =
        y + f64::from(actor.radius.max(actor.top)) - f64::from(moving.radius.min(moving.top));
    (
        [x, low, z],
        [x, high, z],
        f64::from(moving.radius) + f64::from(actor.radius),
    )
}

/// Horizontal center-to-center distance at contact, keeping both capsules at
/// their current heights. Between the rounded ends this shrinks to zero.
pub fn planar_contact_radius(moving: CapsulePose, actor: CapsulePose) -> f64 {
    let (low, high, radius) = actor_exclusion(moving, actor);
    let y = f64::from(moving.position[1]);
    let gap = (low[1] - y).max(y - high[1]).max(0.0);
    if gap >= radius {
        return 0.0;
    }
    ((radius - gap) * (radius + gap)).sqrt()
}

fn contact_constraint(current: Vec3, normal: Vec3, points: &[ContactPoint]) -> Constraint {
    let deepest = points[1..]
        .iter()
        .fold(points[0].separation, |depth, point| {
            minps(depth, point.separation)
        });
    let offset = dot(normal, current) - deepest;
    ContactPlane { normal, offset }.into()
}

fn expanded(bounds: ContactBounds, margin: f32) -> ContactBounds {
    ContactBounds {
        low: sub(bounds.low, [margin; 3]),
        high: add(bounds.high, [margin; 3]),
    }
}

fn segment_bounds(a: Vec3, b: Vec3, reach: f32) -> ContactBounds {
    ContactBounds {
        low: std::array::from_fn(|k| minps(a[k], b[k]) - reach),
        high: std::array::from_fn(|k| maxps(a[k], b[k]) + reach),
    }
}

fn valid_placement(pose: CapsulePose) -> bool {
    if !pose
        .position
        .into_iter()
        .chain([pose.radius, pose.top])
        .all(f32::is_finite)
        || pose.radius <= 0.0
        || pose.top < pose.radius
    {
        return false;
    }
    let ContactBounds { low, high } = pose.bounds();
    low.into_iter().chain(high).all(f32::is_finite)
        && ColumnRange::new(
            [f64::from(low[0]), f64::from(low[2])],
            [f64::from(high[0]), f64::from(high[2])],
        )
        .is_some()
}

fn penetrates_shape(
    shape: &StaticShape,
    capsule: Capsule,
    category: u16,
) -> Result<bool, HullContactError> {
    if let StaticGeometry::Round {
        shape: RoundShape::Capsule(other),
        ..
    } = shape.geometry()
    {
        let [a, b] = shape.frame().map_or([capsule.a, capsule.b], |frame| {
            [capsule.a, capsule.b].map(|point| frame.local(point))
        });
        // The shared manifold also handles collapsed axes and exposes signed
        // separation, so exact tangency does not become a rejected placement.
        return Ok(capsule_capsule(
            Capsule {
                a,
                b,
                radius: capsule.radius,
            },
            *other,
        )
        .points()
        .iter()
        .any(|point| point.separation < 0.0));
    }
    overlaps_segment(shape, [capsule.a, capsule.b], capsule.radius, category)
}

fn overlaps_segment(
    shape: &StaticShape,
    segment: [Vec3; 2],
    reach: f32,
    category: u16,
) -> Result<bool, HullContactError> {
    let [a, b] = shape
        .frame()
        .map_or(segment, |frame| segment.map(|point| frame.local(point)));
    let near = |vertices: &[Vec3]| {
        gjk(vertices, &[a, b], SimplexCache::EMPTY, None).map(|distance| distance.distance < reach)
    };
    let overlaps = match shape.geometry() {
        StaticGeometry::Mesh(mesh) => {
            let bounds = segment_bounds(a, b, reach);
            for id in mesh.query(bounds.low, bounds.high, category) {
                let triangle = mesh.triangle(id).expect("candidate from same mesh");
                if near(triangle.corners())? {
                    return Ok(true);
                }
            }
            false
        }
        StaticGeometry::Hull(hull) => near(&hull.vertices)?,
        StaticGeometry::Round {
            shape: RoundShape::Capsule(capsule),
            ..
        } => {
            let total = capsule.radius + reach;
            let (first, second) = closest_parameters(capsule.a, capsule.b, a, b);
            let d = sub(second, first);
            dot(d, d) < total * total
        }
        StaticGeometry::Round {
            shape: RoundShape::Sphere(sphere),
            ..
        } => {
            let axis = sub(b, a);
            let along = dot(sub(sphere.center, a), axis);
            let length = dot(axis, axis);
            let point =
                if along <= 0.0 || length.partial_cmp(&TINY) != Some(std::cmp::Ordering::Greater) {
                    a
                } else if length <= along {
                    b
                } else {
                    add(scale(axis, along / length), a)
                };
            let d = sub(sphere.center, point);
            let total = reach + sphere.radius;
            dot(d, d) < total * total
        }
    };
    Ok(overlaps)
}

#[cfg(test)]
mod tests {
    use sha2::{Digest, Sha256};

    use super::*;
    use crate::{
        collision_cache::{Mesh, Triangle},
        contact_mesh::ContactMesh,
        contact_solver::clip_velocity,
    };

    fn room() -> Arc<ContactWorld> {
        let mesh = Mesh {
            vertices: vec![
                [-10.0, 0.0, -10.0],
                [-10.0, 0.0, 10.0],
                [10.0, 0.0, -10.0],
                [10.0, 0.0, 10.0],
                [-1.8, 0.0, -10.0],
                [-1.8, 4.0, -10.0],
                [-1.8, 0.0, 10.0],
                [-1.8, 4.0, 10.0],
                [-1.8, 0.0, 2.0],
                [-1.8, 4.0, 2.0],
                [10.0, 0.0, 2.0],
                [10.0, 4.0, 2.0],
                [3.0, 1.3, -2.0],
                [5.0, 1.3, -2.0],
                [3.0, 1.3, 2.0],
                [5.0, 1.3, 2.0],
            ],
            triangles: (0..16)
                .step_by(4)
                .flat_map(|first| {
                    [
                        Triangle {
                            corners: [first, first + 1, first + 2],
                            wings: [-1, first + 3, -1],
                            mask: 0x18,
                            surface: 0,
                        },
                        Triangle {
                            corners: [first + 3, first + 2, first + 1],
                            wings: [-1, first, -1],
                            mask: 0x18,
                            surface: 0,
                        },
                    ]
                })
                .collect(),
        };
        let geometry =
            StaticGeometry::Mesh(Arc::new(ContactMesh::new(mesh, [1.0; 3], 0x18).unwrap()));
        Arc::new(ContactWorld::new(vec![StaticShape::new(geometry, None).unwrap()]).unwrap())
    }

    fn standing(position: Vec3) -> CapsulePose {
        CapsulePose {
            position,
            radius: 0.5,
            top: 1.5,
        }
    }

    fn relocation_fixture() -> serde_json::Value {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/native-capsule-relocation-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["case_id"], "native-capsule-relocation.synthetic.v1");
        assert_eq!(fixture["evidence"], "synthetic-native");
        fixture
    }

    #[test]
    fn relocation_checks_destination_and_preserves_rejected_warm_contacts() {
        let fixture = relocation_fixture();
        let input = &fixture["input"];
        let start = serde_json::from_value(input["start"].clone()).unwrap();
        let destination: Vec3 = serde_json::from_value(input["clear_destination"].clone()).unwrap();
        let mut body = CharacterBody::new(room(), standing(start), 0x18);
        let swept = body
            .move_capsule(sub(destination, start), 0.016)
            .unwrap()
            .position;
        assert!(f64::from(swept[2]) < fixture["expected"]["maximum_swept_z"].as_f64().unwrap());
        assert!(!body.contacts.is_empty());
        let pose = body.pose;
        let fat = body.fat;
        let proxy_moved = body.proxy_moved;
        let pairs = body.pairs.clone();
        let contact_ids: Vec<_> = body.contacts.iter().map(|(id, _)| *id).collect();
        let counts = (body.constraints.len(), body.planes.len(), body.budget);
        assert!(
            !body
                .relocate_if_clear(
                    serde_json::from_value(input["ceiling_destination"].clone()).unwrap(),
                    &[],
                )
                .unwrap()
        );
        assert_eq!(body.pose, pose);
        assert_eq!(body.fat, fat);
        assert_eq!(body.proxy_moved, proxy_moved);
        assert_eq!(body.pairs, pairs);
        assert_eq!(
            body.contacts.iter().map(|(id, _)| *id).collect::<Vec<_>>(),
            contact_ids
        );
        assert_eq!(
            (body.constraints.len(), body.planes.len(), body.budget),
            counts
        );
        assert!(body.relocate_if_clear(destination, &[]).unwrap());
        assert_eq!(body.position(), destination);
        assert!(body.contacts.is_empty());
        assert!(body.pairs.is_empty());
        assert!(body.constraints.is_empty());
        assert!(body.planes.is_empty());
        assert!(body.proxy_moved);
        assert_eq!(body.budget, MAX_ROUNDS);
        assert!(!body.relocate_if_clear(destination, &[]).unwrap());
        for invalid in [
            [f32::NAN, 0.0, 0.0],
            [0.0, f32::INFINITY, 0.0],
            [1e20, 0.0, 0.0],
        ] {
            assert!(!body.relocate_if_clear(invalid, &[]).unwrap());
            assert_eq!(body.position(), destination);
        }
    }

    #[test]
    fn relocation_allows_actor_tangency_but_rejects_penetration() {
        let fixture = relocation_fixture();
        let input = &fixture["input"];
        let actor = standing(serde_json::from_value(input["actor_origin"].clone()).unwrap());
        let mut body = CharacterBody::new(
            Arc::new(ContactWorld::new(Vec::new()).unwrap()),
            standing([0.0; 3]),
            0x18,
        );
        let tangent = serde_json::from_value(input["actor_tangent_destination"].clone()).unwrap();
        assert!(body.relocate_if_clear(tangent, &[actor]).unwrap());
        assert!(
            !body
                .relocate_if_clear(
                    serde_json::from_value(input["actor_overlap_destination"].clone()).unwrap(),
                    &[actor],
                )
                .unwrap()
        );
        assert_eq!(body.position(), tangent);
        assert!(!body.relocate_if_clear(actor.position, &[actor]).unwrap());
        let separate_floor =
            serde_json::from_value(input["separate_floor_destination"].clone()).unwrap();
        assert!(body.relocate_if_clear(separate_floor, &[actor]).unwrap());
        let invalid_actor = CapsulePose {
            radius: f32::NAN,
            ..actor
        };
        assert!(!body.relocate_if_clear(tangent, &[invalid_actor]).unwrap());
        assert_eq!(body.position(), separate_floor);
    }

    #[test]
    fn relocation_uses_round_narrow_phase_for_placed_collapsed_capsules() {
        use crate::contact_math::ContactFrame;

        let fixture = relocation_fixture();
        let input = &fixture["input"];
        let radius = input["collapsed_radius"].as_f64().unwrap() as f32;
        let offset = input["static_offset_x"].as_f64().unwrap() as f32;
        let shape = StaticShape::new(
            StaticGeometry::Round {
                shape: RoundShape::Capsule(Capsule {
                    a: [0.0, radius, 0.0],
                    b: [0.0, radius, 0.0],
                    radius,
                }),
                mask: 0x18,
            },
            Some(ContactFrame {
                translation: [offset, 0.0, 0.0],
                ..ContactFrame::IDENTITY
            }),
        )
        .unwrap();
        let mut body = CharacterBody::new(
            Arc::new(ContactWorld::new(vec![shape]).unwrap()),
            CapsulePose {
                position: [offset + 2.0, 0.0, 0.0],
                radius,
                top: radius,
            },
            0x18,
        );
        for name in ["collapsed_clear_gap", "collapsed_tangent_gap"] {
            let position = [offset + input[name].as_f64().unwrap() as f32, 0.0, 0.0];
            assert!(body.relocate_if_clear(position, &[]).unwrap());
            assert_eq!(body.position(), position);
        }
        let tangent = body.position();
        assert!(
            !body
                .relocate_if_clear(
                    [
                        offset + input["collapsed_overlap_gap"].as_f64().unwrap() as f32,
                        0.0,
                        0.0
                    ],
                    &[],
                )
                .unwrap()
        );
        assert_eq!(body.position(), tangent);
    }

    #[test]
    fn empty_dynamic_snapshot_preserves_static_contact_and_trace_order() {
        let world = room();
        let start = standing([0.0, 0.1, 0.0]);
        let mut legacy = CharacterBody::new(Arc::clone(&world), start, 0x18);
        let mut dynamic = CharacterBody::new(world, start, 0x18);
        for displacement in [[-4.0, 0.0, 0.0], [0.0, 0.0, 3.0], [2.0, -1.0, 0.0]] {
            let mut legacy_clip = None;
            let mut dynamic_clip = None;
            let expected = legacy
                .move_capsule_traced(displacement, 0.016, &mut legacy_clip)
                .unwrap();
            let actual = dynamic
                .move_capsule_against_traced(displacement, 0.016, &[], &mut dynamic_clip)
                .unwrap();
            assert_eq!(actual.position, expected.position);
            assert_eq!(actual.planes, expected.planes);
            assert_eq!(actual.touching, expected.touching);
            let trace = |clip: Option<SweepClip>| {
                clip.map(|clip| (clip.proxy_id, clip.category, clip.fraction))
            };
            assert_eq!(trace(dynamic_clip), trace(legacy_clip));
        }
        assert_eq!(dynamic.capsule_pose(), legacy.capsule_pose());
    }

    #[test]
    fn dynamic_capsules_choose_nearest_impact_without_static_trace_identity() {
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let near = standing([2.0, 0.0, 0.0]);
        let far = standing([3.5, 0.0, 0.0]);
        let mut positions = Vec::new();
        for actors in [[near, far], [far, near]] {
            let mut body = CharacterBody::new(Arc::clone(&world), standing([0.0; 3]), 0x18);
            let mut clip = None;
            let movement = body
                .move_capsule_against_traced([5.0, 0.0, 0.0], 0.016, &actors, &mut clip)
                .unwrap();
            assert!((0.95..=1.02).contains(&movement.position[0]));
            assert!(clip.is_none());
            positions.push(movement.position);
        }
        assert_eq!(positions[0], positions[1]);
    }

    #[test]
    fn dynamic_capsule_stops_fast_approach_and_clips_inward_velocity() {
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let mut body = CharacterBody::new(world, standing([0.0; 3]), 0x18);
        let actor = standing([2.0, 0.0, 0.0]);
        let movement = body
            .move_capsule_against([4.0, 0.0, 0.0], 0.016, &[actor])
            .unwrap();
        assert!(
            (0.95..=1.02).contains(&movement.position[0]),
            "{movement:?}",
            movement = movement.position
        );
        assert!(movement.touching);
        let clipped = clip_velocity([4.0, 0.0, 0.0], movement.planes);
        assert!(clipped.into_iter().all(|axis| axis.abs() < 1e-4));
        assert_eq!(body.capsule_pose().radius, 0.5);
    }

    #[test]
    fn dynamic_capsule_glances_without_entering_the_actor() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/hostile-capsule-glance-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"];
        let expected = &fixture["expected"];
        let vector = |key: &str| serde_json::from_value::<Vec3>(input[key].clone()).unwrap();
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let actor = standing(vector("actor_origin"));
        let mut body = CharacterBody::new(world, standing(vector("mover_origin")), 0x18);
        let displacement = vector("displacement");
        let dt = input["step_seconds"].as_f64().unwrap() as f32;
        let minimum = expected["minimum_axis_distance"].as_f64().unwrap() as f32;
        let mut contacted = false;
        for _ in 0..input["steps"].as_u64().unwrap() {
            let movement = body
                .move_capsule_against(displacement, dt, &[actor])
                .unwrap();
            let [x, _, z] = movement.position;
            assert!(
                (x - actor.position[0]).hypot(z - actor.position[2]) >= minimum - 1e-6,
                "{:?}",
                movement.position
            );
            contacted |= movement.actor_contact;
        }
        assert_eq!(contacted, expected["contact_detected"].as_bool().unwrap());
        assert!(
            f64::from(body.position()[0]) > expected["final_x_above"].as_f64().unwrap(),
            "{:?}",
            body.position()
        );
    }

    #[test]
    fn dynamic_capsule_on_separate_floor_does_not_block() {
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let mut body = CharacterBody::new(world, standing([0.0; 3]), 0x18);
        let actor = standing([2.0, 2.5, 0.0]);
        let movement = body
            .move_capsule_against([4.0, 0.0, 0.0], 0.016, &[actor])
            .unwrap();
        assert_eq!(movement.position, [4.0, 0.0, 0.0]);
        assert!(movement.planes.is_empty());
        assert!(!movement.touching);
    }

    #[test]
    fn static_wall_and_dynamic_capsule_share_first_impact() {
        let world = room();
        let start = standing([0.0, 0.1, 0.0]);
        let mut body = CharacterBody::new(Arc::clone(&world), start, 0x18);
        let displacement = [-4.0, 0.0, 0.0];
        let wall = body.move_capsule(displacement, 0.016).unwrap().position;
        assert!((-1.4..=-1.2).contains(&wall[0]), "{wall:?}");

        let mut body = CharacterBody::new(Arc::clone(&world), start, 0x18);
        let beyond_wall = standing([-3.5, 0.1, 0.0]);
        let blocked = body
            .move_capsule_against(displacement, 0.016, &[beyond_wall])
            .unwrap();
        assert_eq!(blocked.position, wall);

        let mut body = CharacterBody::new(world, start, 0x18);
        let before_wall = standing([-1.2, 0.1, 0.0]);
        let blocked = body
            .move_capsule_against(displacement, 0.016, &[before_wall])
            .unwrap();
        assert!(
            (-0.25..=-0.15).contains(&blocked.position[0]),
            "{:?}",
            blocked.position,
        );
    }

    #[test]
    fn initially_overlapping_dynamic_capsule_allows_escape() {
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let mut body = CharacterBody::new(world, standing([0.0; 3]), 0x18);
        let actor = standing([0.75, 0.0, 0.0]);
        let movement = body
            .move_capsule_against([-1.0, 0.0, 0.0], 0.016, &[actor])
            .unwrap();
        assert_eq!(movement.position, [-1.0, 0.0, 0.0]);
        assert!(!movement.touching);
    }

    #[test]
    fn coincident_actor_capsules_escape_through_the_same_projection_and_sweep() {
        let world = Arc::new(ContactWorld::new(Vec::new()).unwrap());
        let pose = standing([0.0; 3]);
        let mut body = CharacterBody::new(world, pose, 0x18);
        let result = body.move_capsule_against([0.0; 3], 0.016, &[pose]).unwrap();
        assert!(result.actor_contact);
        assert!(result.position[0] <= -2.0 * pose.radius + TOUCH);
        assert_eq!([result.position[1], result.position[2]], [0.0, 0.0]);
    }

    #[test]
    fn relocation_compares_squared_distance_for_capsules_with_coincident_endpoints() {
        #[derive(serde::Deserialize)]
        struct Case {
            radius: f32,
            target: Vec3,
            clear: bool,
        }
        let cases: Vec<Case> = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/capsule-point-placement-v1.json"
        ))
        .unwrap();
        for case in cases {
            let center = [0.0, case.radius, 0.0];
            let obstacle = StaticShape::new(
                StaticGeometry::Round {
                    shape: RoundShape::Capsule(Capsule {
                        a: center,
                        b: center,
                        radius: case.radius,
                    }),
                    mask: 0x18,
                },
                None,
            )
            .unwrap();
            let world = Arc::new(ContactWorld::new(vec![obstacle]).unwrap());
            let start = [-5.0, 0.0, 0.0];
            let pose = CapsulePose {
                position: start,
                radius: case.radius,
                top: case.radius,
            };
            let mut body = CharacterBody::new(world, pose, 0x18);
            assert_eq!(
                body.relocate_if_clear(case.target, &[]).unwrap(),
                case.clear,
                "{:?}",
                case.target
            );
            assert_eq!(
                body.position(),
                if case.clear { case.target } else { start }
            );
        }
    }

    #[test]
    fn capsule_projects_to_floor_and_corner_and_checks_standing_clearance() {
        let pose = CapsulePose {
            position: [0.0, 0.1, 0.0],
            radius: 0.5,
            top: 1.0,
        };
        let mut body = CharacterBody::new(room(), pose, 0x18);
        let mut digest = Sha256::new();
        for input in [
            [-0.85, -0.15, 0.8],
            [-0.8, 0.0, 0.9],
            [-0.9, 0.0, 1.0],
            [0.0; 3],
            [0.5, 0.0, -0.6],
            [-0.75, 0.0, 0.8],
            [0.1, 0.25, -0.3],
            [0.1, -0.4, -0.3],
        ] {
            let result = body.move_capsule(input, 0.016).unwrap();
            for axis in result.position {
                digest.update(axis.to_le_bytes());
            }
            digest.update([u8::from(result.touching), result.planes.len() as u8]);
            for plane in result.planes {
                for axis in plane.normal.into_iter().chain([plane.offset]) {
                    digest.update(axis.to_le_bytes());
                }
            }
            for axis in clip_velocity(input, result.planes) {
                digest.update(axis.to_le_bytes());
            }
        }
        assert_eq!(
            format!("{:x}", digest.finalize()),
            "2ff511de4f4f90c94e11a261e6cf92577172eef72c9f606e99c5896336a4ccbf"
        );
        body.teleport([4.0, 0.0, 0.0]);
        assert!(!body.fits(0.5, 1.0).unwrap());
        assert!(body.try_resize(0.5, 0.55).unwrap());
        assert!(!body.try_resize(0.5, 1.0).unwrap());
        assert!(!body.fits(0.001, 1.0).unwrap());
    }
}
