use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
};

use serde_json::Value as Json;

use super::lifecycle::{DEADEYE_GRAPH, DEADEYE_TARGET_EFFECT};
use crate::{
    collision_scene::CollisionScene,
    commands::Command,
    game_combat::{RoomScriptRequests, eye_height},
    game_content::{PRACTICE_RANGE_MODE, TUTORIAL_MODE, is_junkenstein_mode},
    game_heroes::CASSIDY,
    game_hitscan::{TargetPose, aim_direction, direction_for_angles},
    game_instance::TeamMemberCounts,
    game_replication::OwnerRoomActivity,
    junkenstein_messages::{self, DefenderSighting, SIGHTING, SIGHTING_TAGS, SightingSource},
    mover::{VelocityChange, sample_movement_flags},
    scene_assets::MOVER_CATEGORY,
    script_expr::{EntityRelation, OnFire, RoomEntityFacts, native_shape, valid_entity},
    script_graph::GraphStore,
    script_init::{
        HealthTargetFacts, MAX_INIT_WORK, MovementSample, RoomMessage, ScriptedMotion, Site,
        TargetTagEffect, TargetTagEffects, World,
    },
    script_wire::{Value, Variables},
    static_ray::{Vec3, capsule_entry_distance, norm},
    vitals::{PoolKind, Vitals},
    world::{GAME_MODE_ENTITY, Movement},
};

#[derive(Clone, Default)]
pub(crate) struct DeadeyeScene {
    pub health: Option<std::collections::BTreeMap<u32, f64>>,
    pub targets: Vec<DeadeyeTarget>,
    pub collision: Option<Arc<CollisionScene>>,
    pub view_subjects: BTreeMap<u32, ViewSubject>,
}

#[derive(Clone)]
pub(crate) struct ViewSubject {
    pub position: Vec3,
    pub forward: Vec3,
    pub tags: crate::script_init::TargetTagSnapshot,
}

#[derive(Clone)]
pub(crate) struct DeadeyeTarget {
    pub velocity: Option<[f64; 3]>,
    pub script_state: crate::script_entity_state::EntityScriptState,
    pub entity: u32,
    pub pose: TargetPose,
    pub health: f64,
}

impl DeadeyeScene {
    pub(super) fn shot_direction(&self, origin: Vec3, entity: u32) -> Option<Vec3> {
        let target = self
            .targets
            .iter()
            .find(|target| target.entity == entity)?
            .pose;
        let delta = target_delta(origin, target);
        let distance = norm(delta);
        (distance > 0.0 && self.aim_point_visible(origin, target))
            .then(|| delta.map(|axis| axis / distance))
    }

    pub(super) fn aim_point_visible(&self, origin: Vec3, target: TargetPose) -> bool {
        let Some(collision) = &self.collision else {
            return true;
        };
        let [bottom, top] = target.segment();
        // Admission must use the same ray as the emitted auto-aim shot. The
        // nearest capsule point can be hidden below a balcony while the actual
        // aim point is exposed; it is not a valid proxy for this shot's path.
        let delta = target_delta(origin, target);
        let length = norm(delta);
        if length <= 1e-9 {
            return true;
        }
        let direction = delta.map(|axis| axis / length);
        let Some(reach) =
            capsule_entry_distance(origin, direction, length, bottom, top, target.radius)
        else {
            return false;
        };
        reach <= 1e-9
            || collision
                .cast_ray(origin, direction, reach, MOVER_CATEGORY)
                .is_ok_and(|hit| hit.is_none_or(|hit| hit.distance + 1e-9 >= reach))
    }

    pub(super) fn select(
        &self,
        origin: Vec3,
        forward: Vec3,
        radius: f64,
        angle: f64,
        visible: bool,
    ) -> Vec<u32> {
        let threshold = (angle * 0.5).to_radians().cos();
        let mut matches = Vec::new();
        for &DeadeyeTarget {
            entity,
            pose: target,
            ..
        } in &self.targets
        {
            let delta = target_delta(origin, target);
            let distance = norm(delta);
            let projection: f64 = delta.iter().zip(forward).map(|(a, b)| a * b).sum();
            if distance == 0.0 || distance > radius || projection < threshold * distance {
                continue;
            }
            if visible && !self.aim_point_visible(origin, target) {
                continue;
            }
            matches.push((distance, entity));
        }
        matches.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        matches.into_iter().map(|(_, entity)| entity).collect()
    }
}

pub(super) fn target_delta(origin: Vec3, target: TargetPose) -> Vec3 {
    let [x, y, z] = target.feet;
    let [_, top] = target.segment();
    [
        x - origin[0],
        (y + target.eye).min(top[1]) - origin[1],
        z - origin[2],
    ]
}

pub(super) struct BodyWorld {
    pub(super) graphs: Arc<GraphStore>,
    pub(super) spatial_frame: Option<Arc<crate::game_combat::RoomSpatialFrame>>,
    pub(super) team_member_counts: Option<TeamMemberCounts>,
    pub(super) room_activity: Option<OwnerRoomActivity>,
    pub(super) script_requests: RoomScriptRequests,
    pub(super) controller: u32,
    pub(super) entity: u32,
    pub(super) body_filter: u64,
    pub(super) position: [f64; 3],
    pub(super) eye_position: Option<[f64; 3]>,
    pub(super) movement: Movement,
    pub(super) throttles: [i8; 2],
    pub(super) mode: u64,
    pub(super) hero: u64,
    pub(super) vitals: Vitals,
    pub(super) pool_types_known: bool,
    pub(super) target_tags: Option<BTreeSet<u64>>,
    pub(super) model_hardpoints: Option<Arc<BTreeSet<u64>>>,
    pub(super) target_tag_effects: TargetTagEffects,
    pub(super) controller_on_fire: bool,
    pub(super) defenders: Arc<[u32]>,
    pub(super) sightings: Vec<DefenderSighting>,
    pub(super) sighting_source: Option<SightingSource>,
    pub(super) cinematic_aim: Option<(Site, [i32; 2])>,
    pub(super) facing_claims: indexmap::IndexMap<Site, crate::script_init::AiFacing>,
    pub(super) deadeye_scene: DeadeyeScene,
    pub(super) velocity_changes: Vec<(u32, crate::mover::VelocityChange)>,
    pub(super) scripted_motion: Option<(u32, ScriptedMotion)>,
}

impl World for BodyWorld {
    fn local_command_present(&self, site: Site) -> Option<bool> {
        self.is_player_site(site).then_some(false)
    }

    fn local_combat_context_available(&self, site: Site) -> Option<bool> {
        // The headless owner body has no client-local combat accumulator.
        self.is_player_site(site).then_some(false)
    }

    fn has_dynamic_map_rigid_bodies(&self, site: Site) -> Option<bool> {
        // Native rooms load only static map collision, including during startup.
        self.is_player_site(site).then_some(false)
    }

    fn voice_context(&mut self, site: Site) -> Option<bool> {
        // This body has no native voice manager. Authored room cues use
        // the separate room request path.
        self.is_player_site(site).then_some(false)
    }

    fn team_members(&self, team: u8) -> Option<u32> {
        self.team_member_counts?.count(team)
    }

    fn engine_setting(&self, _site: Site, key: u32) -> crate::script_expr::EngineSetting {
        crate::body_ruleset::engine_setting(&self.graphs, self.mode, key)
    }

    fn edit_velocity(&mut self, site: Site, frame: u32, value: Value, cap: bool) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        let change = match (value, cap) {
            (Value::Vector(desired), false) => VelocityChange::Delta(std::array::from_fn(|axis| {
                f64::from(desired[axis] as f32 - self.movement.velocity[axis] as f32)
            })),
            (Value::Float(cap), true) => VelocityChange::HorizontalCap(cap),
            _ => return false,
        };
        self.queue_velocity_change(frame, change)
    }

    fn cap_horizontal_velocity(&mut self, site: Site, frame: u32, cap: f64) -> bool {
        self.is_player_site(site)
            && self.queue_velocity_change(frame, VelocityChange::HorizontalCap(cap))
    }

    fn queue_scripted_motion(
        &mut self,
        site: Site,
        frame: u32,
        motion: ScriptedMotion,
    ) -> Option<bool> {
        if !self.is_player_site(site) {
            return None;
        }
        if self.vitals.death().is_some() || self.scripted_motion.is_some() {
            return Some(false);
        }
        self.scripted_motion = Some((frame, motion));
        Some(true)
    }

    fn apply_velocity_impulse(
        &mut self,
        site: Site,
        frame: u32,
        vertical: f64,
        delta: [f64; 3],
    ) -> bool {
        self.is_player_site(site)
            && self
                .queue_velocity_change(frame, VelocityChange::VerticalImpulse { vertical, delta })
    }

    fn deadeye_target_alive(&self, site: Site, entity: u32) -> bool {
        self.has_deadeye()
            && site.entity == self.entity
            && self.vitals.death().is_none()
            && self
                .deadeye_scene
                .targets
                .iter()
                .any(|target| target.entity == entity)
    }

    fn deadeye_targets(&self, site: Site, radius: f64, angle: f64) -> Option<Vec<u32>> {
        if !self.has_deadeye() || site.entity != self.entity || site.graph != DEADEYE_GRAPH {
            return None;
        }
        if self.vitals.death().is_some() || self.graphs.graph(DEADEYE_TARGET_EFFECT).is_none() {
            return Some(Vec::new());
        }
        let [x, y, z] = self.position;
        let crouched = self.movement.flags & crate::mover_posture::CROUCHED != 0;
        let origin = [x, y + eye_height(self.hero, crouched), z];
        let forward = aim_direction(&Command {
            yaw: self.movement.yaw,
            pitch: self.movement.pitch,
            ..Command::default()
        });
        Some(
            self.deadeye_scene
                .select(origin, forward, radius, angle, site.node == Some(86)),
        )
    }

    fn player_stats_service_available(&self, site: Site) -> Option<bool> {
        // The bound body cannot update the scoreboard through StateScript.
        self.is_player_site(site).then_some(false)
    }

    fn spatial_binding_owner_available(&self, site: Site) -> Option<bool> {
        // The headless body has no local auxiliary view owner.
        self.is_player_site(site).then_some(false)
    }

    fn outline_context_available(&self, site: Site) -> Option<bool> {
        // The headless body has no local outline presentation context.
        self.is_player_site(site).then_some(false)
    }

    fn set_cinematic_aim_lock(&mut self, site: Site, active: bool) -> bool {
        if site.entity != self.entity
            || (site.graph, site.node) != (0x0ca5, Some(70))
            || !is_junkenstein_mode(self.mode)
        {
            return false;
        }
        if active {
            self.cinematic_aim = Some((site, [self.movement.yaw, self.movement.pitch]));
        } else if self.cinematic_aim.is_some_and(|(owner, _)| owner == site) {
            self.cinematic_aim = None;
        }
        true
    }

    fn set_facing_claim(
        &mut self,
        site: Site,
        record: crate::script_init::AiFacing,
        active: bool,
    ) -> Option<bool> {
        if !self.is_player_site(site) {
            return None;
        }
        if active {
            if record.target != self.entity
                || self.vitals.death().is_some()
                || crate::facing::angles(record.direction).is_none()
            {
                return Some(false);
            }
            self.facing_claims.insert(site, record);
        } else {
            self.facing_claims.shift_remove(&site);
        }
        Some(true)
    }

    fn ruleset(&mut self, id: u32) -> Option<Value> {
        crate::body_ruleset::get(
            &self.graphs,
            self.mode,
            self.hero,
            crate::body_ruleset::ActorKind::Player,
            id,
        )
    }

    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity != GAME_MODE_ENTITY {
            return self
                .deadeye_scene
                .targets
                .iter()
                .find(|target| target.entity == entity)?
                .script_state
                .read(id);
        }
        self.graphs
            .mode_variables(self.mode)
            .map(|variables| variables.get(&id).copied().map_or(Value::Null, Value::from))
    }

    fn body_has_hardpoint(&self, site: Site, entity: u32, hardpoint: u64) -> Option<bool> {
        if entity != self.entity || !self.is_player_site(site) {
            return None;
        }
        self.model_hardpoints
            .as_ref()
            .map(|hardpoints| hardpoints.contains(&hardpoint))
    }

    fn body_angle(
        &self,
        entity: u32,
        axis: crate::script_expr::BodyAngle,
    ) -> crate::script_expr::AngleQuery {
        use crate::script_expr::{AngleQuery, BodyAngle};
        if entity == 0 {
            return AngleQuery::Unbound;
        }
        if entity != self.entity {
            return AngleQuery::Unavailable;
        }
        AngleQuery::from_packed(match axis {
            BodyAngle::Yaw => self.movement.yaw,
            BodyAngle::Pitch => self.movement.pitch,
        })
    }

    fn entity_velocity(&self, entity: u32) -> crate::script_expr::VelocityQuery {
        use crate::script_expr::VelocityQuery;
        if entity == 0 {
            return VelocityQuery::Unbound;
        }
        if entity == self.entity {
            return VelocityQuery::Present(self.movement.velocity);
        }
        self.deadeye_scene
            .targets
            .iter()
            .find(|target| target.entity == entity)
            .and_then(|target| target.velocity)
            .map_or(VelocityQuery::Unavailable, VelocityQuery::Present)
    }

    fn entity_exists(&self, entity: u32) -> Option<bool> {
        if entity == GAME_MODE_ENTITY {
            return match self.graphs.mode_variables(self.mode) {
                Some(_) => Some(true),
                None if is_junkenstein_mode(self.mode) => Some(false),
                None => None,
            };
        }
        (valid_entity(entity) && (entity == self.entity || entity == self.controller))
            .then_some(true)
    }

    fn entity_team(&self, site: Site, entity: u32) -> Option<i8> {
        if !self.is_player_site(site) || entity != self.entity {
            return None;
        }
        let team = crate::match_scene::team_from_spawn_filter(self.body_filter)?;
        i8::try_from(team).ok()
    }

    fn queue_health_change(&mut self, site: Site, target: u32, delta: f64) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        self.spatial_frame
            .as_deref()
            .is_some_and(|frame| self.script_requests.queue_health(frame, target, delta))
    }

    fn queue_health_changes(&mut self, site: Site, changes: &[(u32, f64)]) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        self.spatial_frame
            .as_deref()
            .is_some_and(|frame| self.script_requests.queue_health_changes(frame, changes))
    }

    fn contact_volume_targets(
        &self,
        site: Site,
        volume: crate::script_init::ContactVolume,
    ) -> Option<Vec<crate::script_init::ContactTarget>> {
        if !self.is_player_site(site)
            || self.vitals.death().is_some()
            || self.movement.frame != Some(volume.source_frame)
        {
            return None;
        }
        let category = self.graphs.static_ray_category(volume.profile)?;
        let yaw = self.movement.yaw;
        let right = direction_for_angles(yaw + 16384, 0);
        let forward = direction_for_angles(yaw, self.movement.pitch);
        let [x, y, z] = volume.offset;
        let vertical = [0.0, y, 0.0];
        let centre = std::array::from_fn(|axis| {
            self.movement.position[axis] + x * right[axis] + vertical[axis] + z * forward[axis]
        });
        self.spatial_frame.as_deref()?.hostile_contact_targets(
            site.entity,
            centre,
            volume.sight_origin,
            volume.radius,
            category,
        )
    }

    fn queue_contact_damage(
        &mut self,
        site: Site,
        damage: f64,
        changes: &[(u32, Option<Vec3>)],
    ) -> bool {
        self.is_player_site(site)
            && self.spatial_frame.as_deref().is_some_and(|frame| {
                self.script_requests
                    .queue_contact_damage(frame, damage, changes)
            })
    }

    fn room_body_generation(&self, wire: u32) -> Option<u64> {
        self.spatial_frame.as_deref()?.body_generation(wire)
    }

    fn queue_room_message(&mut self, site: Site, target: u32, message: RoomMessage) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        self.spatial_frame
            .as_deref()
            .is_some_and(|frame| self.script_requests.queue_message(frame, target, message))
    }

    fn queue_room_cue(
        &mut self,
        site: Site,
        targets: &[u32],
        cue: crate::script_init::RoomCue,
    ) -> bool {
        self.is_player_site(site)
            && self.spatial_frame.as_deref().is_some_and(|frame| {
                self.script_requests
                    .queue_cue(frame, site.entity, targets, cue)
            })
    }

    fn actor_voice_available(&self, site: Site) -> bool {
        self.is_player_site(site)
            && self
                .spatial_frame
                .as_deref()
                .is_some_and(|frame| frame.actor_voice_available(site.entity))
    }

    fn queue_actor_voice(
        &mut self,
        site: Site,
        criterion: u64,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        if let Some((cue, listeners)) = self
            .spatial_frame
            .as_deref()
            .and_then(|frame| frame.actor_voice_request(site.entity, criterion, choose))
        {
            self.queue_room_cue(site, &listeners, cue);
        }
    }

    fn spatial_candidates(&self) -> Option<&[crate::script_expr::SpatialCandidate]> {
        Some(self.spatial_frame.as_deref()?.candidates())
    }

    fn view_targets(
        &self,
        site: Site,
        query: &crate::script_init::ViewTargetQuery<'_>,
    ) -> Option<Vec<u32>> {
        if !self.is_player_site(site) || query.source != site.entity {
            return None;
        }
        self.spatial_frame.as_deref()?.view_targets(query)
    }

    fn room_entities(&self) -> Option<&BTreeMap<u32, RoomEntityFacts>> {
        self.spatial_frame.as_deref()?.entity_facts()
    }

    fn room_actor_identity(&self, entity: u32) -> Option<u64> {
        self.spatial_frame.as_deref()?.actor_identity(entity)
    }

    fn body_active(&self, site: Site) -> Option<bool> {
        self.is_player_site(site).then_some(())?;
        self.room_activity
            .map(|activity| activity.allows_body_activity() && self.vitals.death().is_none())
    }

    fn spatial_line_clear(&self, origin: [f64; 3], target: [f64; 3]) -> Option<bool> {
        self.spatial_frame.as_deref()?.line_clear(origin, target)
    }

    fn spatial_ray_cast(
        &self,
        site: Site,
        vm_frame: u32,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        self.spatial_frame
            .as_deref()?
            .ray_cast(site, vm_frame, origin, direction, max_distance)
    }

    fn spatial_actor_ray_cast(
        &self,
        site: Site,
        vm_frame: u32,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
        targets: &[u32],
    ) -> Option<Option<crate::script_expr::SpatialActorRayHit>> {
        self.spatial_frame.as_deref()?.actor_ray_cast(
            site,
            vm_frame,
            origin,
            direction,
            max_distance,
            targets,
        )
    }

    fn known_position(&self, entity: u32) -> Option<[f64; 3]> {
        (entity == self.entity).then_some(self.position)
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.known_position(entity).or_else(|| {
            self.sighting_source
                .filter(|source| source.entity == entity)
                .map(|source| source.position)
                .or_else(|| {
                    self.deadeye_scene
                        .view_subjects
                        .get(&entity)
                        .map(|subject| subject.position)
                })
        })
    }

    fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        if let Some(position) = self.known_position(entity) {
            return Some(position);
        }
        match self.spatial_frame.as_deref() {
            Some(frame) => frame.actor_pose(entity).map(|(position, _)| position),
            None => self.position(entity),
        }
    }

    fn authored_forward(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            return Some(direction_for_angles(self.movement.yaw, self.movement.pitch));
        }
        match self.spatial_frame.as_deref() {
            Some(frame) => frame
                .actor_pose(entity)
                .map(|(_, [yaw, pitch])| direction_for_angles(yaw, pitch)),
            None => self.forward(entity),
        }
    }

    fn body_eye_position(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            return self.eye_position.filter(|_| self.vitals.death().is_none());
        }
        self.spatial_frame.as_deref()?.actor_eye_position(entity)
    }

    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            let yaw = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
            let (sin, cos) = yaw.sin_cos();
            Some([sin, 0.0, cos])
        } else {
            self.deadeye_scene
                .view_subjects
                .get(&entity)
                .map(|subject| subject.forward)
        }
    }

    fn send_message(
        &mut self,
        site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if !self.is_player_site(site)
            || !is_junkenstein_mode(self.mode)
            || (site.graph, site.node) != (0x01ca, Some(127))
            || message != SIGHTING
            || !self.defenders.contains(&target)
            || self.sightings.len() >= MAX_INIT_WORK
        {
            return false;
        }
        self.sightings.push(DefenderSighting {
            target,
            source: SightingSource {
                entity: self.entity,
                position: self.position,
                tags: SIGHTING_TAGS.map(|tag| self.has_target_tag(site, self.entity, tag)),
                filter_flags: Some(self.body_filter),
            },
            parameters: parameters.clone(),
        });
        true
    }

    fn movement_sample(&self, site: Site) -> Option<MovementSample> {
        if !self.is_player_site(site) {
            return None;
        }
        let pose = &self.movement;
        Some(MovementSample {
            flags: sample_movement_flags(pose)?,
            air_ticks: pose.air_ticks,
            pose_frame: pose.frame,
        })
    }

    fn body_scale(&mut self, site: Site, target: u32, override_value: &Value) -> Option<f64> {
        if target != self.entity || !self.is_player_site(site) {
            return None;
        }
        match override_value {
            Value::Null | Value::Integer(1) => Some(1.0),
            Value::Float(scale) if *scale == 1.0 => Some(1.0),
            _ => None,
        }
    }

    fn body_health(&self, site: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
        if !self.is_player_site(site) || (kind.is_some() && !self.pool_types_known) {
            return None;
        }
        let amount = match kind {
            None if maximum => self.vitals.maximum(),
            None => self.vitals.total(),
            Some(_) => self.vitals.amount(kind, maximum),
        };
        (amount.is_finite() && amount >= 0.0).then_some(amount)
    }

    fn total_health(&self, site: Site, entity: u32) -> crate::script_expr::HealthQuery {
        use crate::script_expr::HealthQuery;
        if !self.is_player_site(site) {
            return HealthQuery::Unavailable;
        }
        if entity == 0 {
            return HealthQuery::Unbound;
        }
        if entity == self.entity {
            return HealthQuery::Present(self.vitals.total());
        }
        self.deadeye_scene
            .health
            .as_ref()
            .and_then(|health| health.get(&entity).copied())
            .or_else(|| {
                self.deadeye_scene
                    .targets
                    .iter()
                    .find(|target| target.entity == entity)
                    .map(|target| target.health)
            })
            .map_or(HealthQuery::Unavailable, HealthQuery::Present)
    }

    fn entity_health(
        &self,
        site: Site,
        entity: u32,
        kind: Option<PoolKind>,
        maximum: bool,
    ) -> Option<f64> {
        if entity == self.entity {
            return self.body_health(site, kind, maximum);
        }
        if self.has_deadeye()
            && self.is_player_site(site)
            && site.graph == DEADEYE_TARGET_EFFECT
            && kind.is_none()
            && !maximum
        {
            return Some(
                self.deadeye_scene
                    .targets
                    .iter()
                    .find(|target| target.entity == entity)
                    .map_or(0.0, |target| target.health),
            );
        }
        None
    }

    fn add_health_pool(
        &mut self,
        site: Site,
        kind: PoolKind,
        maximum: f64,
        current: f64,
    ) -> Option<u32> {
        if !self.is_player_site(site) || !self.pool_types_known {
            return None;
        }
        self.vitals.add_pool(kind, maximum, current)
    }

    fn add_temporary_shield(
        &mut self,
        site: Site,
        identifier: u64,
        amount: f64,
        stacked: bool,
    ) -> Option<u32> {
        if !self.is_player_site(site) {
            return None;
        }
        self.vitals
            .add_temporary_shield(identifier, amount, stacked)
    }

    fn temporary_shield_current(&self, site: Site, identifier: u64) -> Option<f64> {
        (self.is_player_site(site)).then(|| self.vitals.temporary_shield_current(identifier))
    }

    fn drain_temporary_shield(&mut self, site: Site, identifier: u64, amount: f64) -> Option<f64> {
        (self.is_player_site(site)).then(|| self.vitals.drain_temporary_shield(identifier, amount))
    }

    fn health_pool_current(&self, site: Site, pool: u32) -> Option<f64> {
        if !self.is_player_site(site) {
            return None;
        }
        self.vitals.pool_current(pool)
    }

    fn remove_health_pool(&mut self, site: Site, pool: u32) -> Option<f64> {
        if !self.is_player_site(site) {
            return None;
        }
        self.vitals.remove_pool(pool)
    }

    fn has_target_tag(&self, site: Site, entity: u32, tag: u64) -> Option<bool> {
        if !self.is_player_site(site) {
            return None;
        }
        if entity != self.entity {
            return self
                .sighting_source
                .filter(|source| source.entity == entity)
                .and_then(|source| {
                    let index = SIGHTING_TAGS.iter().position(|&known| known == tag)?;
                    source.tags[index]
                })
                .or_else(|| {
                    self.deadeye_scene
                        .view_subjects
                        .get(&entity)
                        .and_then(|subject| subject.tags.presence(tag))
                })
                .or_else(|| self.room_entities()?.get(&entity)?.tags.presence(tag));
        }
        if let Some(present) = self.target_tag_effects.presence(tag) {
            return Some(present);
        }
        Some(self.target_tags.as_ref()?.contains(&tag))
    }

    fn set_target_tag_effect(&mut self, site: Site, effect: TargetTagEffect, active: bool) -> bool {
        if !self.is_player_site(site) || effect.entity != self.entity {
            return false;
        }
        self.target_tag_effects.apply(effect, active)
    }

    fn entity_filter_flags(&self, site: Site, entity: u32) -> Option<u64> {
        if !self.is_player_site(site) {
            return None;
        }
        if entity == self.entity {
            Some(self.own_filter_flags())
        } else {
            self.sighting_source
                .filter(|source| source.entity == entity)
                .and_then(|source| source.filter_flags)
                .or_else(|| self.spatial_frame.as_deref()?.actor_filter_flags(entity))
        }
    }

    fn settled_scene_filter_policy(
        &self,
        site: Site,
        filter: &Json,
        _target: &HealthTargetFacts,
    ) -> Option<crate::script_expr::SceneFilterPolicy> {
        use crate::script_expr::{FilterPredicate, FilterRelation, SceneFilterPolicy};
        (self.is_player_site(site)
            && filter["m_66747E7D"] == 2
            && filter
                .get("m_89831602")
                .is_none_or(|v| v.is_null() || v == 0)
            && filter.get("m_condition").is_none_or(Json::is_null))
        .then_some(SceneFilterPolicy {
            relation: FilterRelation::Hostile,
            predicate: FilterPredicate::None,
        })
    }

    fn settled_entities_hostile(
        &self,
        site: Site,
        source: u32,
        target: &HealthTargetFacts,
        option: bool,
    ) -> Option<bool> {
        if !self.is_player_site(site) || source != self.entity || option {
            return None;
        }
        let left = self.own_filter_flags();
        let right = target.filter_flags?;
        crate::junkenstein_host::ordinary_hostility(left, right)
    }

    fn body_on_fire(&mut self, site: Site, entity: u32) -> Option<OnFire> {
        self.related_entity(site, EntityRelation::CurrentPossessor, entity)
            .map(|_| OnFire::Active(self.controller_on_fire))
    }

    fn related_entity(&mut self, site: Site, relation: EntityRelation, entity: u32) -> Option<u32> {
        if !self.is_player_site(site)
            || !valid_entity(self.controller)
            || self.controller == self.entity
        {
            return None;
        }
        // Both relations point to the same controller for this human body.
        match relation {
            EntityRelation::OwningController | EntityRelation::CurrentPossessor
                if entity == self.entity =>
            {
                Some(self.controller)
            }
            EntityRelation::PossessedBody if entity == self.controller => Some(self.entity),
            EntityRelation::ControllerBodyField if entity == self.controller => Some(self.entity),
            EntityRelation::ControllerBodyField
                if entity == self.entity || !valid_entity(entity) =>
            {
                Some(0)
            }
            // The authenticated human body has no AI control entity in this world.
            EntityRelation::AiControlEntity if entity == self.entity => Some(0),
            _ => None,
        }
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        if self.is_player_site(site)
            && crate::script_expr::native_fields(config, &["m_entity"])
            && config
                .get("m_entity")
                .is_some_and(|entity| native_shape(entity, "STU_B5A0CAF0", None))
        {
            let yaw = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
            let (sin, cos) = yaw.sin_cos();
            let vector = match config.get("$").and_then(Json::as_str) {
                Some("STU_021D01CD") => Some([sin, 0.0, cos]),
                Some("STU_66F3B89E") => Some([cos, 0.0, -sin]),
                Some("STU_2200A66D") => Some(aim_direction(&Command {
                    yaw: self.movement.yaw,
                    pitch: self.movement.pitch,
                    ..Command::default()
                })),
                _ => None,
            };
            if let Some(vector) = vector {
                return Some(Value::Vector(vector.map(|v| f64::from(v as f32))));
            }
        }

        if self.is_player_site(site) && self.has_deadeye() {
            match (
                site.graph,
                site.node,
                config.get("$").and_then(Json::as_str),
            ) {
                (DEADEYE_GRAPH, Some(84), Some("STU_ED45C5E8"))
                    if crate::script_expr::is_zeroed_owner_selector(config) =>
                {
                    return Some(Value::Null);
                }
                (DEADEYE_TARGET_EFFECT, Some(28), Some("STU_7C005F8F"))
                    if crate::script_expr::native_fields(config, &["m_82B6345B", "m_entity"])
                        && config
                            .get("m_82B6345B")
                            .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None))
                        && config
                            .pointer("/m_entity/m_identifier")
                            .and_then(Json::as_u64)
                            == Some(536) =>
                {
                    // The prepared Junkenstein roster has no Deadeye immunity overrides.
                    return Some(Value::Bool(false));
                }
                _ => (),
            }
        }
        if self.is_player_site(site) && is_junkenstein_mode(self.mode) {
            if (site.graph, site.node) == (0x01ca, Some(126))
                && junkenstein_messages::sighting_selector(config)
            {
                return Some(junkenstein_messages::recipients(&self.defenders));
            }
            if matches!(
                (site.graph, site.node),
                (0x01ca, Some(11 | 127)) | (0x0ca5, Some(77))
            ) && junkenstein_messages::self_team_query(config)
            {
                let team = (self.body_filter >> 23) & 31;
                return Some(Value::Integer(if team == 0 {
                    -1
                } else {
                    i64::from(team.trailing_zeros())
                }));
            }
        }
        match config.get("$")?.as_str()? {
            "STU_1FF67CA6" => Some(Value::Entity(self.entity)),
            "STU_310D6529" => {
                const THROTTLE_UNIT: f32 = 1.0 / 127.0;
                let [right, forward] = self
                    .throttles
                    .map(|byte| f64::from(f32::from(byte) * THROTTLE_UNIT));
                Some(Value::Vector([right, forward, 0.0]))
            }
            "STU_37F20D24" => Some(Value::Asset(self.mode)),
            "STU_A3DEF368"
                if self.is_player_site(site)
                    && native_shape(config, "STU_A3DEF368", None)
                    && self.entity_exists(GAME_MODE_ENTITY) == Some(true) =>
            {
                Some(Value::Entity(GAME_MODE_ENTITY))
            }
            "STU_E72F6348"
                if self.is_player_site(site)
                    && self.entity_exists(GAME_MODE_ENTITY) == Some(false)
                    && native_shape(config, "STU_E72F6348", Some("m_entity"))
                    && config
                        .get("m_entity")
                        .is_some_and(|mode| native_shape(mode, "STU_A3DEF368", None)) =>
            {
                // An existence check still has an answer when the mode
                // supplies no script entity for the bare selector to return.
                Some(Value::Bool(false))
            }
            "STU_4A101787" => Some(Value::Bool(false)),
            "STU_ED45C5E8"
                if (site.graph, site.node, self.mode)
                    == (0x01ca, Some(126), PRACTICE_RANGE_MODE)
                    && self.is_player_site(site)
                    && config
                        .get("m_B2ABF4FF")
                        .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None))
                    && config.pointer("/m_2BA6230F/m_value").and_then(Json::as_f64)
                        == Some(0.0)
                    && ["m_5912C8DF", "m_5A72EEF5", "m_C9E6F46B"]
                        .iter()
                        .all(|flag| {
                            config
                                .get(*flag)
                                .and_then(|flag| flag.get("m_value"))
                                .and_then(Json::as_u64)
                                == Some(0)
                        }) =>
            {
                // The solo Practice Range has no recipients for this voice route.
                Some(Value::Array(Vec::new()))
            }
            "STU_65ABFCCC"
                if (site.graph, site.node, self.mode)
                    == (0x01ca, Some(127), PRACTICE_RANGE_MODE)
                    && self.is_player_site(site)
                    && config
                        .get("m_target")
                        .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None)) =>
            {
                Some(Value::Null)
            }
            "STU_A77AC2AC"
                if self.is_player_site(site)
                    && self.mode != 0
                    && native_shape(config, "STU_A77AC2AC", None) =>
            {
                Some(Value::Bool(self.mode == TUTORIAL_MODE))
            }
            // The reference has no model for this entity selector and
            // defaults it to None. It never reaches the 01CA site where the
            // owner VM meets it, and that graph handles the message it sends
            // to the selection, so None probably drops a local delivery. Only
            // the all-false form around the owner is answered; the original
            // selection is unverified.
            "STU_ED45C5E8"
                if self.is_player_site(site)
                    && self.mode == PRACTICE_RANGE_MODE
                    && crate::script_expr::is_zeroed_owner_selector(config) =>
            {
                Some(Value::Null)
            }
            _ => None,
        }
    }
}

impl BodyWorld {
    fn queue_velocity_change(&mut self, frame: u32, change: VelocityChange) -> bool {
        if !change.valid()
            || self.vitals.death().is_some()
            || self.velocity_changes.len() >= MAX_INIT_WORK
        {
            return false;
        }
        self.velocity_changes
            .push((frame.saturating_add(1), change));
        true
    }

    pub(super) fn has_deadeye(&self) -> bool {
        self.hero == CASSIDY && is_junkenstein_mode(self.mode)
    }

    pub(super) fn own_filter_flags(&self) -> u64 {
        let mut flags = self.body_filter;
        if is_junkenstein_mode(self.mode) {
            flags |= 1;
            if self.vitals.death().is_some() {
                flags |= 0x10;
            }
        }
        flags
    }

    fn is_player_site(&self, site: Site) -> bool {
        site.entity == self.entity && valid_entity(self.entity)
    }
}
