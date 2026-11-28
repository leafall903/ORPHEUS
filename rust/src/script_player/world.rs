use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    sync::Arc,
};

use serde_json::Value as Json;

use crate::{
    collision_scene::CollisionScene,
    commands::Command,
    game_combat::{RoomScriptRequests, eye_height},
    game_content::{TUTORIAL_MODE, is_junkenstein_mode},
    game_hitscan::{TargetPose, aim_direction, direction_for_angles},
    game_instance::TeamMemberCounts,
    game_replication::OwnerRoomActivity,
    mover::{VelocityChange, sample_movement_flags},
    pve_encounter::messages::{DefenderSighting, SIGHTING, SIGHTING_TAGS, SightingSource},
    script_expr::{EntityRelation, OnFire, RoomEntityFacts, native_shape, valid_entity},
    script_graph::GraphStore,
    script_init::{
        HealthTargetFacts, HookPullGeometry, MAX_INIT_WORK, MovementSample, RoomMessage,
        ScriptedMotion, Site, TargetTagEffect, TargetTagEffects, World,
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
                .cast_ray(
                    origin,
                    direction,
                    reach,
                    crate::scene_assets::weapon_terrain_category(collision),
                )
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

pub(super) struct CommandView {
    frame: u32,
    eye: Vec3,
    direction: Vec3,
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
    pub(super) body_definition: u64,
    pub(super) anim_timing: Option<Arc<crate::anim_timing::AnimationTiming>>,
    pub(super) vitals: Vitals,
    pub(super) pool_types_known: bool,
    pub(super) target_tags: Option<BTreeSet<u64>>,
    pub(super) command_views: VecDeque<CommandView>,
    pub(super) wheel_cosmetics: crate::match_scene::WheelCosmetics,
    pub(super) model_hardpoints: Option<Arc<BTreeSet<u64>>>,
    pub(super) target_tag_effects: TargetTagEffects,
    pub(super) controller_on_fire: bool,
    pub(super) defenders: Arc<[u32]>,
    pub(super) sightings: Vec<DefenderSighting>,
    pub(super) sighting_source: Option<SightingSource>,
    pub(super) death_source: Option<crate::game_combat::DeathSourceFacts>,
    pub(super) cinematic_aim: indexmap::IndexMap<Site, [i32; 2]>,
    pub(super) facing_claims: indexmap::IndexMap<Site, crate::script_init::AiFacing>,
    pub(super) deadeye_scene: DeadeyeScene,
    pub(super) velocity_changes: Vec<(u32, crate::mover::VelocityChange)>,
    pub(super) scripted_motion: Option<(u32, ScriptedMotion)>,
    pub(super) collision_category: u16,
    pub(super) mover_settings: crate::mover_settings::MoverSettings,
    pub(super) voice_variant_counter: usize,
    pub(super) voice_cooldowns: BTreeMap<u64, u32>,
    pub(super) pet_positions: BTreeMap<u32, ([f64; 3], f64)>,
    pub(super) spawned_possessables: BTreeMap<u32, u64>,
    pub(super) pending_body_swap: Option<u64>,
    pub(super) combat_mods: BTreeMap<Site, crate::script_init::CombatModEntry>,
    pub(super) deflect: BTreeMap<Site, crate::script_init::DeflectState>,
    pub(super) recall_history: VecDeque<(u32, [f64; 3], f64)>,
    pub(super) active_auras: BTreeMap<Site, (Vec<u32>, u64, Option<f64>)>,
    pub(super) ball_roll_speed: Option<f64>,
}

impl World for BodyWorld {
    fn spray_placement(
        &self,
        reach: f64,
        command_frame: Option<u32>,
    ) -> Option<Option<crate::script_init::SprayPlacement>> {
        let view = command_frame.and_then(|frame| {
            self.command_views
                .iter()
                .rev()
                .find(|view| view.frame == frame)
        });
        let (eye, direction) = match view {
            Some(view) => (view.eye, view.direction),
            None => (
                self.eye_position?,
                direction_for_angles(self.movement.yaw, self.movement.pitch),
            ),
        };
        self.spatial_frame
            .as_deref()?
            .spray_placement(eye, direction, reach)
    }

    fn equipped_cosmetic(
        &self,
        entity: u32,
        slot: usize,
        cosmetic: crate::script_expr::EquippedCosmetic,
    ) -> Option<u64> {
        use crate::script_expr::EquippedCosmetic;
        if entity != self.entity && entity != self.controller {
            return None;
        }
        let slots = match cosmetic {
            EquippedCosmetic::Spray => &self.wheel_cosmetics.sprays,
            EquippedCosmetic::EmoteAlias => &self.wheel_cosmetics.emote_aliases,
            EquippedCosmetic::EmoteBlendTree => &self.wheel_cosmetics.emote_blend_trees,
        };
        slots.get(slot).copied()
    }

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
        self.is_player_site(site)
            .then(|| self.actor_voice_available(site))
    }

    fn voice_manager(&mut self, site: Site) -> Option<bool> {
        self.voice_context(site)
    }

    fn voice_clock(&mut self, site: Site, now_ms: u64) -> Option<f64> {
        self.voice_context(site)
            .filter(|&ok| ok)
            .map(|_| now_ms as f64 / 1000.0)
    }

    fn voice_stimulus(
        &mut self,
        site: Site,
        guid: u64,
    ) -> Option<Option<crate::script_voice::Stimulus>> {
        if !self.actor_voice_available(site) {
            return None;
        }
        let playable = self
            .spatial_frame
            .as_deref()
            .is_some_and(|frame| frame.actor_voice_has_stimulus(site.entity, guid));
        Some(playable.then_some(crate::script_voice::Stimulus {
            guid,
            playable: true,
            mode: false,
        }))
    }

    fn voice_allow(&mut self, site: Site, stimulus: crate::script_voice::Stimulus) -> Option<bool> {
        self.actor_voice_available(site)
            .then_some(stimulus.playable)
    }

    fn voice_submit(
        &mut self,
        site: Site,
        stimulus: crate::script_voice::Stimulus,
        targets: &[u32],
        _actor: Option<&Value>,
    ) -> Result<Option<u32>, &'static str> {
        if !self.actor_voice_available(site) {
            return Ok(None);
        }
        if !targets.is_empty() && !targets.contains(&site.entity) {
            return Ok(Some(0));
        }
        let pick = self.voice_variant_counter;
        self.voice_variant_counter = self.voice_variant_counter.wrapping_add(1);
        let Some((cue, listeners)) = self.spatial_frame.as_deref().and_then(|frame| {
            frame.actor_voice_request(
                site.entity,
                crate::game_combat::ActorVoiceQuery::Stimulus(stimulus.guid),
                &mut |count| pick % count,
            )
        }) else {
            return Ok(Some(0));
        };
        Ok(Some(u32::from(self.queue_room_cue(site, &listeners, cue))))
    }

    fn voice_duration(&mut self, site: Site, handle: u32, guid: u64, _mode: bool) -> Option<f64> {
        if !self.actor_voice_available(site) || handle == 0 {
            return None;
        }
        Some(
            self.spatial_frame
                .as_deref()
                .and_then(|frame| frame.voice_line_duration(guid))
                .unwrap_or(1.0),
        )
    }

    fn voice_part(&mut self, site: Site, _number: u16) -> Option<f64> {
        self.actor_voice_available(site).then_some(0.0)
    }

    fn team_members(&self, team: u8) -> Option<u32> {
        self.team_member_counts?.count(team)
    }

    fn route_game_message(
        &mut self,
        site: Site,
        message: u64,
        parameters: &crate::script_wire::Variables,
    ) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        if let Some(frame) = self.spatial_frame.as_deref() {
            // The VM's return value describes routing support. A full room
            // outbox rejects this broadcast without invalidating the script.
            let _ = self.script_requests.queue_broadcast_others(
                frame,
                self.entity,
                RoomMessage {
                    id: message,
                    parameters: parameters.clone(),
                },
            );
        }
        true
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

    fn apply_velocity_ops(
        &mut self,
        site: Site,
        frame: u32,
        ops: &[crate::script_init::VelocityOp],
    ) -> bool {
        use crate::script_init::VelocityOp;
        if !self.is_player_site(site) || self.vitals.death().is_some() {
            return false;
        }
        let mut applied = false;
        for &op in ops {
            let change = match op {
                VelocityOp::AddVector(delta) => VelocityChange::Impulse(delta),
                VelocityOp::SetVector(target) => VelocityChange::SetVelocity(target),
                VelocityOp::SetHorizontalVector(target) => {
                    VelocityChange::SetHorizontalVelocity(target)
                }
                VelocityOp::SetSpeed { axis, value } => {
                    VelocityChange::SetAxisSpeed { axis, value }
                }
                VelocityOp::CapSpeed { axis, max } => VelocityChange::CapAxisSpeed { axis, max },
                VelocityOp::ScaleSpeed { axis, factor } => {
                    VelocityChange::ScaleAxisSpeed { axis, factor }
                }
                VelocityOp::ClampSpeed { axis, min, max } => {
                    VelocityChange::ClampAxisSpeed { axis, min, max }
                }
            };
            applied |= self.queue_velocity_change(frame, change);
        }
        applied
    }

    fn queue_blink(&mut self, site: Site, frame: u32, distance: f64) -> bool {
        if !self.is_player_site(site)
            || self.vitals.death().is_some()
            || !distance.is_finite()
            || distance <= 0.0
        {
            return false;
        }
        let yaw = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
        let (sin, cos) = yaw.sin_cos();
        let forward = [sin, 0.0, cos];
        let right = [cos, 0.0, -sin];
        let [t_right, t_forward] = self.throttles.map(f64::from);
        let dir = if t_right != 0.0 || t_forward != 0.0 {
            let raw = [
                forward[0] * t_forward + right[0] * t_right,
                0.0,
                forward[2] * t_forward + right[2] * t_right,
            ];
            let len = raw[0].hypot(raw[2]);
            if len > 1e-6 {
                [raw[0] / len, 0.0, raw[2] / len]
            } else {
                forward
            }
        } else {
            forward
        };
        let delta = [dir[0] * distance, 0.0, dir[2] * distance];
        let Some(displacement) = crate::script_init::SweptDisplacement::new(delta) else {
            return false;
        };
        self.queue_scripted_motion(site, frame, ScriptedMotion::Sweep(displacement)) == Some(true)
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

    fn entity_alive(&self, site: Site, entity: u32) -> bool {
        if !self.is_player_site(site) || self.vitals.death().is_some() {
            return false;
        }
        if self.pet_positions.contains_key(&entity) {
            return true;
        }
        if self.deadeye_scene.collision.is_some() || !self.deadeye_scene.targets.is_empty() {
            return self
                .deadeye_scene
                .targets
                .iter()
                .any(|target| target.entity == entity);
        }
        self.spatial_frame
            .as_deref()
            .is_some_and(|frame| frame.actor_alive(entity))
    }

    fn cone_targets(
        &self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        angle: f64,
        visible: bool,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        self.cone_targets_with_cylinder(site, origin, offset, radius, angle, 0.0, visible, filter)
    }

    fn cone_targets_with_cylinder(
        &self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        angle: f64,
        cylinder_radius: f64,
        visible: bool,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        if !self.is_player_site(site) || !radius.is_finite() || radius < 0.0 {
            return None;
        }
        if self.vitals.death().is_some() {
            return Some(Vec::new());
        }
        let origin_pos = if origin == self.entity && offset == [0.0; 3] {
            let [x, y, z] = self.position;
            let crouched = self.movement.flags & crate::mover_posture::CROUCHED != 0;
            self.eye_position
                .unwrap_or([x, y + eye_height(self.hero, crouched), z])
        } else if origin == 0 {
            offset
        } else {
            let base = if origin == self.entity {
                self.position
            } else {
                self.spatial_frame
                    .as_deref()
                    .and_then(|frame| frame.actor_pose(origin).map(|(pos, _)| pos))?
            };
            [
                base[0] + offset[0],
                base[1] + offset[1],
                base[2] + offset[2],
            ]
        };
        let forward = aim_direction(&Command {
            yaw: self.movement.yaw,
            pitch: self.movement.pitch,
            ..Command::default()
        });
        if self.deadeye_scene.collision.is_some() || !self.deadeye_scene.targets.is_empty() {
            return Some(
                self.deadeye_scene
                    .select(origin_pos, forward, radius, angle, visible),
            );
        }
        let relation = match filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0) {
            1 => 1,
            2 => 2,
            _ if filter.get("m_89831602").and_then(Json::as_u64) == Some(1) => 2,
            _ => 0,
        };
        self.spatial_frame.as_deref()?.cone_targets(
            site.entity,
            origin_pos,
            forward,
            radius,
            angle,
            cylinder_radius,
            visible,
            relation,
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

    fn voice_category_service_available(&self, site: Site) -> Option<bool> {
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
            self.cinematic_aim
                .insert(site, [self.movement.yaw, self.movement.pitch]);
        } else {
            self.cinematic_aim.shift_remove(&site);
        }
        true
    }

    fn set_captured_aim_lock(&mut self, site: Site, degrees: Option<[f64; 2]>) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        if let Some(degrees) = degrees {
            let Some(aim) = crate::linked_aim::unparented_lock(degrees) else {
                return false;
            };
            self.cinematic_aim.insert(site, aim);
        } else {
            self.cinematic_aim.shift_remove(&site);
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
        if let Some(source) = self
            .death_source
            .as_ref()
            .filter(|source| source.target.target == entity)
        {
            return source.target.script_state.read(id);
        }
        if entity == self.controller && self.spatial_frame.is_some() {
            return Some(Value::Null);
        }
        if entity != GAME_MODE_ENTITY {
            if let Some(target) = self
                .deadeye_scene
                .targets
                .iter()
                .find(|target| target.entity == entity)
            {
                return target.script_state.read(id);
            }
            if let Some(value) = self
                .spatial_frame
                .as_deref()
                .and_then(|frame| frame.actor_variable(entity, id))
            {
                return Some(value);
            }
            if self.spawned_possessables.contains_key(&entity)
                || self.pet_positions.contains_key(&entity)
                || self
                    .spatial_frame
                    .as_deref()
                    .is_some_and(|frame| frame.actor_pose(entity).is_some())
            {
                if matches!(id, 478 | 8193) {
                    return Some(Value::Float(250.0));
                }
                return Some(Value::Null);
            }
            return None;
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
        if let Some(source) = self
            .death_source
            .as_ref()
            .filter(|source| source.target.target == entity)
        {
            return VelocityQuery::Present(source.velocity);
        }
        if let Some(velocity) = self
            .spatial_frame
            .as_deref()
            .and_then(|frame| frame.actor_velocity(entity))
        {
            return VelocityQuery::Present(velocity);
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
        if valid_entity(entity)
            && (entity == self.entity
                || entity == self.controller
                || self.pet_positions.contains_key(&entity)
                || self.spatial_frame.as_deref().is_some_and(|frame| {
                    frame
                        .candidates()
                        .iter()
                        .any(|candidate| candidate.entity == entity)
                }))
        {
            return Some(true);
        }
        if self.spatial_frame.is_some() {
            return Some(false);
        }
        None
    }

    fn entity_team(&self, site: Site, entity: u32) -> Option<i8> {
        if !self.is_player_site(site) {
            return None;
        }
        if entity == self.entity {
            let team = crate::match_scene::team_from_spawn_filter(self.body_filter)?;
            return i8::try_from(team).ok();
        }
        self.spatial_frame
            .as_deref()?
            .candidates()
            .iter()
            .find(|candidate| candidate.entity == entity)
            .and_then(|candidate| candidate.team)
            .and_then(|team| i8::try_from(team).ok())
    }

    fn queue_health_change(&mut self, site: Site, target: u32, delta: f64) -> bool {
        if !self.is_player_site(site) || !delta.is_finite() || delta == 0.0 {
            return false;
        }
        if self
            .spatial_frame
            .as_deref()
            .is_some_and(|frame| self.script_requests.queue_health(frame, target, delta))
        {
            return true;
        }
        if target == self.entity && delta > 0.0 && self.vitals.death().is_none() {
            self.vitals.heal(delta);
            return true;
        }
        false
    }

    fn queue_modify_health(
        &mut self,
        site: Site,
        target: u32,
        delta: f64,
        impulse: Option<[f64; 3]>,
    ) -> bool {
        self.queue_modify_health_with_meta(
            site,
            target,
            delta,
            impulse,
            None,
            crate::script_init::HitTags::EMPTY,
        )
    }

    fn queue_modify_health_with_meta(
        &mut self,
        site: Site,
        target: u32,
        delta: f64,
        impulse: Option<[f64; 3]>,
        loadout: Option<u64>,
        tags: crate::script_init::HitTags,
    ) -> bool {
        if !self.is_player_site(site) || !delta.is_finite() {
            return false;
        }
        if delta < 0.0 || impulse.is_some() {
            let damage = (-delta).max(0.0);
            let frame_num = self.movement.frame.unwrap_or(0);
            if let Some(frame) = self.spatial_frame.as_deref() {
                return self.script_requests.queue_contact_damage_with_meta(
                    frame,
                    frame_num,
                    damage,
                    loadout,
                    tags,
                    &[(target, impulse)],
                );
            }
            return false;
        }
        if delta > 0.0 {
            return self.queue_health_change(site, target, delta);
        }
        false
    }

    fn queue_health_changes(&mut self, site: Site, changes: &[(u32, f64)]) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        self.spatial_frame
            .as_deref()
            .is_some_and(|frame| self.script_requests.queue_health_changes(frame, changes))
    }

    fn ground_wave_targets(
        &self,
        site: Site,
        wave: &crate::script_init::GroundWave,
        reach: f64,
    ) -> Option<Vec<u32>> {
        if !self.is_player_site(site) || self.vitals.death().is_some() {
            return None;
        }
        self.spatial_frame
            .as_deref()?
            .ground_wave_targets(site.entity, wave, reach)
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
        source_frame: u32,
        damage: f64,
        changes: &[(u32, Option<Vec3>)],
    ) -> bool {
        self.is_player_site(site)
            && self.spatial_frame.as_deref().is_some_and(|frame| {
                self.script_requests
                    .queue_contact_damage(frame, source_frame, damage, changes)
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

    fn queue_equipped_voice(
        &mut self,
        site: Site,
        slot: usize,
        choose: &mut dyn FnMut(usize) -> usize,
    ) -> bool {
        let Some(&effect) = self.wheel_cosmetics.voice_effects.get(slot) else {
            return false;
        };
        let Some((cue, listeners)) = self.spatial_frame.as_deref().and_then(|frame| {
            frame.actor_voice_request(
                site.entity,
                crate::game_combat::ActorVoiceQuery::EquippedEffect(effect),
                choose,
            )
        }) else {
            return false;
        };
        self.queue_room_cue(site, &listeners, cue)
    }

    fn actor_voice_available(&self, site: Site) -> bool {
        self.is_player_site(site)
            && self
                .spatial_frame
                .as_deref()
                .is_some_and(|frame| frame.actor_voice_available(site.entity))
    }

    fn animation_duration(
        &mut self,
        site: Site,
        blend_tree: u64,
        alias: u64,
        category: u64,
    ) -> Option<f64> {
        self.is_player_site(site).then_some(())?;
        self.anim_timing
            .as_deref()?
            .duration(self.body_definition, blend_tree, alias, category)
    }

    fn queue_actor_voice(
        &mut self,
        site: Site,
        criterion: u64,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        self.queue_actor_voice_with_audience(site, criterion, None, choose);
    }

    fn queue_actor_voice_with_audience(
        &mut self,
        site: Site,
        criterion: u64,
        audience: Option<&[u32]>,
        choose: &mut dyn FnMut(usize) -> usize,
    ) {
        let frame = self.movement.frame.unwrap_or(0);
        if frame > 0
            && self
                .voice_cooldowns
                .get(&criterion)
                .is_some_and(|&last| frame.saturating_sub(last) < 90)
        {
            return;
        }
        if let Some((cue, listeners)) = self.spatial_frame.as_deref().and_then(|frame| {
            frame.actor_voice_request_with_audience(
                site.entity,
                crate::game_combat::ActorVoiceQuery::Criterion(criterion),
                audience,
                choose,
            )
        }) {
            if frame > 0 {
                self.voice_cooldowns.insert(criterion, frame);
            }
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
        let frame = self.spatial_frame.as_deref()?;
        if query.sight.is_none() && query.eye_heading.is_none() {
            if let Some(empty) = frame.empty_view_targets(query) {
                return Some(empty);
            }
            let fallback = crate::script_init::ViewSightProfile {
                static_mask: frame.terrain_category(),
                block_actor_flags: 0,
                samples: vec![0.5],
            };
            return frame.view_targets(&crate::script_init::ViewTargetQuery {
                sight: Some(&fallback),
                ..*query
            });
        }
        frame.view_targets(query)
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

    fn ability_ground_ray(
        &self,
        site: Site,
        frame: u32,
        origin: [f64; 3],
        direction: [f64; 3],
        length: f64,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        self.spatial_frame
            .as_deref()?
            .profile_ray_cast(site, frame, origin, direction, length, 0x1fdf)
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

    fn profiled_static_ray(
        &self,
        site: Site,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
        category: u16,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        if !self.is_player_site(site) || self.vitals.death().is_some() {
            return None;
        }
        self.spatial_frame.as_deref()?.profiled_static_ray(
            origin,
            direction,
            max_distance,
            category,
        )
    }

    fn owner_collision_category(&self, site: Site) -> Option<u16> {
        self.is_player_site(site).then_some(self.collision_category)
    }

    /// The standing-capsule dimensions, step band and walkable slope of the
    /// owner's own mover (the reference promotion reads exactly
    /// `mover.data.capsule(False)`, `step_down` and `slope_cos`), and only
    /// while the body lives: a dead body pulls nothing.
    fn hook_pull_geometry(&self, site: Site) -> Option<HookPullGeometry> {
        (self.is_player_site(site) && self.vitals.death().is_none()).then(|| {
            let radius = self.mover_settings.stand_radius.max(0.02);
            HookPullGeometry {
                radius,
                segment: (self.mover_settings.stand_height
                    - self.mover_settings.stand_pogo
                    - 2.0 * radius)
                    .max(0.1 * radius),
                step_down: self.mover_settings.step_down,
                slope_cos: self.mover_settings.max_slope.to_radians().cos(),
            }
        })
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
        if entity == self.entity || self.spawned_possessables.contains_key(&entity) {
            Some(self.position)
        } else {
            self.pet_positions.get(&entity).map(|(pos, _)| *pos)
        }
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
                .or_else(|| {
                    self.spatial_frame
                        .as_deref()
                        .and_then(|frame| frame.actor_pose(entity).map(|(pos, _)| pos))
                })
        })
    }

    fn emit_contact_effect(
        &mut self,
        site: Site,
        source: u32,
        _resource: u64,
        targets: &[u32],
    ) -> bool {
        self.is_player_site(site)
            && source == self.entity
            && targets
                .iter()
                .all(|&target| self.authored_position(target).is_some())
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
        if entity == self.entity || self.spawned_possessables.contains_key(&entity) {
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
        if entity == self.entity || self.spawned_possessables.contains_key(&entity) {
            let yaw = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
            let (sin, cos) = yaw.sin_cos();
            Some([sin, 0.0, cos])
        } else {
            self.deadeye_scene
                .view_subjects
                .get(&entity)
                .map(|subject| subject.forward)
                .or_else(|| {
                    self.spatial_frame
                        .as_deref()
                        .and_then(|frame| frame.actor_pose(entity))
                        .map(|(_, [yaw, _])| {
                            let rad = f64::from(yaw) * std::f64::consts::TAU / 65_536.0;
                            let (sin, cos) = rad.sin_cos();
                            [sin, 0.0, cos]
                        })
                })
        }
    }

    fn pitch(&self, entity: u32) -> Option<f64> {
        // Only the body's own mover pose is live in this replica; the
        // authored operand reads it through GetViewPitch (the hook line and
        // wheel aim accessors), where the 104319 reference converts the raw
        // pose pitch to degrees.
        (entity == self.entity).then(|| f64::from(self.movement.pitch) * 360.0 / 65_536.0)
    }

    fn send_message(
        &mut self,
        site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        if is_junkenstein_mode(self.mode)
            && (site.graph, site.node) == (0x01ca, Some(127))
            && message == SIGHTING
            && self.defenders.contains(&target)
            && self.sightings.len() < MAX_INIT_WORK
        {
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
            return true;
        }
        // Generic cross-entity message delivery in live rooms (Whip Shot, Chain Hook,
        // Concussion Mine, Halt, Translocator, Resurrect, Ice Wall, Teleporter, etc.).
        if (target == GAME_MODE_ENTITY || target == self.controller) && self.spatial_frame.is_some()
        {
            return true;
        }
        if valid_entity(target) {
            if let Some(frame) = self.spatial_frame.as_deref() {
                let _ = self.script_requests.queue_message(
                    frame,
                    target,
                    crate::script_init::RoomMessage {
                        id: message,
                        parameters: parameters.clone(),
                    },
                );
                return true;
            }
        }
        false
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
        if !self.is_player_site(site) {
            return None;
        }
        let known_target = target == self.entity
            || self.spatial_frame.is_some()
            || self.pet_positions.contains_key(&target);
        if !known_target {
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
        if let Some(health) = self
            .spatial_frame
            .as_deref()
            .and_then(|frame| frame.actor_health(entity, None, false))
        {
            return HealthQuery::Present(health);
        }
        if let Some(&(_, health)) = self.pet_positions.get(&entity) {
            return HealthQuery::Present(health);
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
            .or_else(|| {
                self.spatial_frame
                    .as_deref()
                    .map(|frame| frame.actor_health(entity, None, false).unwrap_or(0.0))
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
        if !self.is_player_site(site) {
            return None;
        }
        if let Some(health) = self
            .spatial_frame
            .as_deref()
            .and_then(|frame| frame.actor_health(entity, kind, maximum))
        {
            return Some(health);
        }
        if let Some(&(_, health)) = self.pet_positions.get(&entity) {
            return Some(health);
        }
        if kind.is_none()
            && !maximum
            && (self.deadeye_scene.collision.is_some() || !self.deadeye_scene.targets.is_empty())
        {
            return Some(
                self.deadeye_scene
                    .targets
                    .iter()
                    .find(|target| target.entity == entity)
                    .map_or(0.0, |target| target.health),
            );
        }
        if let Some(frame) = self.spatial_frame.as_deref() {
            return Some(frame.actor_health(entity, kind, maximum).unwrap_or(0.0));
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

    fn recharge_shields(&mut self, site: Site, amount: f64) -> bool {
        if !self.is_player_site(site) || !amount.is_finite() || amount < 0.0 {
            return false;
        }
        if amount > 0.0 {
            self.vitals.recharge_shields(amount);
        }
        true
    }

    fn shockwave_targets(&mut self, site: Site, reach: f64) -> Option<Vec<u32>> {
        if !self.is_player_site(site) {
            return None;
        }
        let frame = self.spatial_frame.as_deref()?;
        let radius = if reach.is_finite() && reach > 0.0 {
            reach
        } else {
            8.0
        };
        let origin = self.position;
        let my_team = u8::try_from((self.body_filter >> 23) & 31)
            .ok()
            .filter(|&bits| bits > 0)
            .map(|bits| bits.trailing_zeros() as u8);
        let mut out = Vec::new();
        for c in frame.candidates() {
            if c.entity == self.entity {
                continue;
            }
            if my_team.is_some() && c.team == my_team {
                continue;
            }
            let dx = c.position[0] - origin[0];
            let dy = c.position[1] - origin[1];
            let dz = c.position[2] - origin[2];
            if dx.hypot(dz) <= radius && dy.abs() <= 4.0 {
                out.push(c.entity);
            }
        }
        Some(out)
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
            if let Some(source) = self
                .death_source
                .as_ref()
                .filter(|source| source.target.target == entity)
            {
                return source.target.tags.presence(tag);
            }
            if let Some(present) = self
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
            {
                return Some(present);
            }
            if let Some(entities) = self.room_entities() {
                if let Some(facts) = entities.get(&entity) {
                    return facts.tags.presence(tag);
                }
            }
            if self.spawned_possessables.contains_key(&entity)
                || self.pet_positions.contains_key(&entity)
                || entity >= 0xc008_0000
            {
                return Some(false);
            }
            return None;
        }
        if let Some(present) = self.target_tag_effects.presence(tag) {
            return Some(present);
        }
        Some(self.target_tags.as_ref()?.contains(&tag))
    }

    fn entity_definition(&self, site: Site, entity: u32) -> Option<u64> {
        if !self.is_player_site(site) {
            return None;
        }
        if entity == self.entity {
            return Some(self.body_definition);
        }
        if let Some(&def) = self.spawned_possessables.get(&entity) {
            return Some(def);
        }
        self.spatial_frame
            .as_deref()
            .is_some_and(|frame| frame.actor_pose(entity).is_some())
            .then_some(0)
    }

    fn targets_with_tag(&self, site: Site, tag: u64, excluded: u64) -> Option<Vec<u32>> {
        if !self.is_player_site(site) {
            return None;
        }
        let Some(entities) = self.room_entities() else {
            return Some(Vec::new());
        };
        let matches = entities
            .iter()
            .filter_map(|(&entity, facts)| {
                (facts.tags.presence(tag) == Some(true)
                    && facts.tags.presence(excluded) != Some(true))
                .then_some(entity)
            })
            .collect();
        Some(matches)
    }

    fn select_landing(&mut self, site: Site) -> crate::script_init::LandingSelection {
        use crate::script_init::LandingSelection;
        if !self.is_player_site(site) {
            return LandingSelection::Unhandled;
        }
        let Some(frame) = self.spatial_frame.as_deref() else {
            return LandingSelection::Point(self.position);
        };
        let origin = self.eye_position.unwrap_or([
            self.position[0],
            self.position[1] + 1.5,
            self.position[2],
        ]);
        let raw_dir = direction_for_angles(self.movement.yaw, self.movement.pitch);
        let dir = if raw_dir[1] > -0.08 {
            let hlen = raw_dir[0].hypot(raw_dir[2]).max(1e-4);
            [raw_dir[0] / hlen * 0.995, -0.095, raw_dir[2] / hlen * 0.995]
        } else {
            raw_dir
        };
        let reach = if self.movement.air_ticks == 0 {
            8.5
        } else {
            22.0
        };
        let vm_frame = self.movement.frame.unwrap_or(0);
        let probe = match frame.ray_cast(site, vm_frame, origin, dir, reach).flatten() {
            Some(hit) => [
                hit.point[0] + hit.normal[0] * 0.35,
                hit.point[1] + hit.normal[1] * 0.35,
                hit.point[2] + hit.normal[2] * 0.35,
            ],
            None => crate::static_ray::along(origin, dir, reach),
        };
        // Snap onto walkable ground directly beneath the aimed probe point.
        let down_origin = [probe[0], probe[1] + 1.5, probe[2]];
        if let Some(floor) = frame
            .ray_cast(site, vm_frame, down_origin, [0.0, -1.0, 0.0], 25.0)
            .flatten()
        {
            return LandingSelection::Point(floor.point);
        }
        LandingSelection::Point(probe)
    }

    fn tracked_targets(
        &mut self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        self.swept_tracked_targets(site, origin, offset, None, false, radius, filter)
    }

    fn swept_tracked_targets(
        &mut self,
        site: Site,
        origin: u32,
        start_offset: [f64; 3],
        end_offset: Option<[f64; 3]>,
        local_space: bool,
        radius: f64,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        if !self.is_player_site(site) || !radius.is_finite() || radius <= 0.0 {
            return None;
        }
        let (base_pos, yaw_raw) = if origin == 0 {
            ([0.0; 3], 0)
        } else if origin == self.entity {
            (self.position, self.movement.yaw)
        } else if let Some((pos, angles)) = self
            .spatial_frame
            .as_deref()
            .and_then(|frame| frame.actor_pose(origin))
        {
            (pos, angles[0])
        } else {
            (self.authored_position(origin)?, 0)
        };
        let frame = self.spatial_frame.as_deref()?;
        let to_world = |off: [f64; 3]| -> [f64; 3] {
            if origin == 0 {
                return off;
            }
            if local_space {
                let rad = f64::from(yaw_raw) * (std::f64::consts::TAU / 65536.0);
                let (sin, cos) = rad.sin_cos();
                [
                    base_pos[0] + off[0] * cos + off[2] * sin,
                    base_pos[1] + off[1],
                    base_pos[2] - off[0] * sin + off[2] * cos,
                ]
            } else {
                [
                    base_pos[0] + off[0],
                    base_pos[1] + off[1],
                    base_pos[2] + off[2],
                ]
            }
        };
        let start = to_world(start_offset);
        let end = end_offset.map(to_world);
        let own_team = crate::match_scene::team_from_spawn_filter(self.body_filter);
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let relation = match (team_mode, hostile_flag) {
            (1, 0) => 1u64,
            (2, _) | (_, 1) => 2u64,
            _ => 0u64,
        };
        let mut matches: Vec<(f64, u32)> = frame
            .candidates()
            .iter()
            .filter(|c| match relation {
                1 => own_team.is_some() && c.team == own_team,
                2 => c.entity != self.entity && (own_team.is_none() || c.team != own_team),
                _ => c.entity != self.entity,
            })
            .filter_map(|c| {
                // Test against the target's body capsule [feet+r .. feet+h-r] + radius
                // (not just the ground feet point) so chest-height melee sweeps hit reliably.
                let cap_r = 0.45;
                let cap_a = [c.position[0], c.position[1] + cap_r, c.position[2]];
                let cap_b = [c.position[0], c.position[1] + 1.45, c.position[2]];
                let hit_radius = radius + cap_r;
                let best_dist = if let Some(seg_end) = end {
                    let mut best = f64::INFINITY;
                    for step in 0..=4 {
                        let t = f64::from(step) * 0.25;
                        let p = [
                            start[0] + (seg_end[0] - start[0]) * t,
                            start[1] + (seg_end[1] - start[1]) * t,
                            start[2] + (seg_end[2] - start[2]) * t,
                        ];
                        let d = crate::static_ray::segment_distance(p, cap_a, cap_b);
                        if d < best {
                            best = d;
                        }
                    }
                    best
                } else {
                    crate::static_ray::segment_distance(start, cap_a, cap_b)
                };
                (best_dist <= hit_radius).then_some((best_dist, c.entity))
            })
            .collect();
        matches.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
        Some(matches.into_iter().map(|(_, e)| e).collect())
    }

    fn generic_beam_contact(
        &self,
        site: Site,
        origin: Option<[f64; 3]>,
        direction: Option<[f64; 3]>,
        length: Option<f64>,
        radius: f64,
        filter: &Json,
    ) -> Option<crate::script_init::GenericBeamContact> {
        if !self.is_player_site(site) {
            return None;
        }
        let Some(frame) = self.spatial_frame.as_deref() else {
            return Some(crate::script_init::GenericBeamContact::default());
        };
        let Some(origin) = origin.or(self.eye_position) else {
            return Some(crate::script_init::GenericBeamContact::default());
        };
        let direction = direction
            .and_then(|d| {
                let len = d[0].hypot(d[1]).hypot(d[2]);
                (len > 1e-6).then(|| d.map(|a| a / len))
            })
            .unwrap_or_else(|| direction_for_angles(self.movement.yaw, self.movement.pitch));
        let reach = length.filter(|l| l.is_finite() && *l > 0.0).unwrap_or(15.0);
        let radius = if radius.is_finite() && radius > 0.0 {
            radius
        } else {
            0.0
        };
        let own_team = crate::match_scene::team_from_spawn_filter(self.body_filter);
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let filter_mask = filter.get("m_4EA686DB").and_then(Json::as_u64).unwrap_or(0);
        let candidates: Vec<u32> = frame
            .candidates()
            .iter()
            .filter(|c| c.entity != self.entity)
            .filter(|c| match (team_mode, hostile_flag) {
                (1, 0) => own_team.is_some() && c.team == own_team,
                (2, _) | (_, 1) => own_team.is_none() || c.team != own_team,
                // Retail damage beams (Zarya 0241, Symmetra 157C, Echo 1FBD) set
                // `m_4EA686DB = 20` (0x14) with `(team_mode=0, hostile_flag=0)`:
                // exclude same-team allies so teammates do not occlude enemy hits.
                (0, 0) if filter_mask == 20 => own_team.is_none() || c.team != own_team,
                _ => true,
            })
            .map(|c| c.entity)
            .collect();
        let vm_frame = self.movement.frame.unwrap_or(0);
        let actor_hit = frame
            .actor_beam_cast(
                site,
                vm_frame,
                origin,
                direction,
                reach,
                radius,
                &candidates,
            )
            .flatten();
        let world_hit = frame
            .ray_cast(site, vm_frame, origin, direction, reach)
            .flatten();
        match (actor_hit, world_hit) {
            (Some(actor), Some(world)) if actor.distance <= world.distance => {
                Some(crate::script_init::GenericBeamContact {
                    entity: Some(actor.entity),
                    hit: true,
                    point: Some(crate::static_ray::along(origin, direction, actor.distance)),
                    normal: Some(actor.normal),
                    distance: Some(actor.distance),
                })
            }
            (Some(actor), None) => Some(crate::script_init::GenericBeamContact {
                entity: Some(actor.entity),
                hit: true,
                point: Some(crate::static_ray::along(origin, direction, actor.distance)),
                normal: Some(actor.normal),
                distance: Some(actor.distance),
            }),
            (_, Some(world)) => Some(crate::script_init::GenericBeamContact {
                entity: None,
                hit: true,
                point: Some(world.point),
                normal: Some(world.normal),
                distance: Some(world.distance),
            }),
            (None, None) => Some(crate::script_init::GenericBeamContact {
                entity: None,
                hit: false,
                point: Some(crate::static_ray::along(origin, direction, reach)),
                normal: None,
                distance: Some(reach),
            }),
        }
    }

    fn summoner_beam_contact(&self, site: Site) -> Option<crate::script_init::BeamContact> {
        if !self.is_player_site(site) {
            return None;
        }
        let (Some(frame), Some(origin)) = (self.spatial_frame.as_deref(), self.eye_position) else {
            return Some(crate::script_init::BeamContact::default());
        };
        let direction = direction_for_angles(self.movement.yaw, self.movement.pitch);
        let candidates: Vec<u32> = frame
            .candidates()
            .iter()
            .map(|c| c.entity)
            .filter(|&e| e != self.entity)
            .collect();
        let vm_frame = self.movement.frame.unwrap_or(0);
        if let Some(Some(hit)) =
            frame.actor_ray_cast(site, vm_frame, origin, direction, 12.0, &candidates)
        {
            return Some(crate::script_init::BeamContact {
                entity: Some(hit.entity),
                hit: true,
            });
        }
        Some(crate::script_init::BeamContact::default())
    }

    fn spawn_pet(&mut self, site: Site, spawn: crate::script_init::PetSpawn) -> Option<u32> {
        if !self.is_player_site(site) || self.vitals.death().is_some() {
            return None;
        }
        let pet_id = crate::game_combat::next_pet_wire_id();
        if spawn.possessable && self.graphs.entity_body(spawn.definition).is_some() {
            self.spawned_possessables.insert(pet_id, spawn.definition);
            return Some(pet_id);
        }
        let mut pos = spawn.position.unwrap_or(self.position);
        // World-placed pets whose `m_position` resolves to the caster's own feet (e.g.
        // Mei Ice Wall `0x05FE:17`, Torb Turret, barriers with `m_position = self`) should
        // land at the aimed ground point in front of the caster instead of wrapping the caster.
        let rules = crate::hero_ability_rules::HeroAbilityRules::defaults();
        let at_self = (pos[0] - self.position[0]).hypot(pos[2] - self.position[2]) < 0.35;
        if spawn.attach.is_none() && at_self && spawn.definition == rules.ice_wall.entity {
            let aimed = match self.select_landing(site) {
                crate::script_init::LandingSelection::Point(pt) => pt,
                crate::script_init::LandingSelection::Unhandled
                | crate::script_init::LandingSelection::Invalid => {
                    let rad = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
                    let (sin_y, cos_y) = rad.sin_cos();
                    [
                        self.position[0] + sin_y * 6.0,
                        self.position[1],
                        self.position[2] + cos_y * 6.0,
                    ]
                }
            };
            pos = aimed;
        }
        let authored_pools = self.graphs.entity_body(spawn.definition).and_then(|body| {
            crate::game_pools::authored_pools(&self.graphs, body)
                .ok()
                .flatten()
        });
        let max_health = authored_pools
            .map(|p| p.total())
            .filter(|h| *h > 0.0)
            .or_else(|| self.authored_pet_max_health(site.graph));
        let local_offset = spawn.attach.map(|_| {
            if let Some(world_pos) = spawn.position {
                let dx = world_pos[0] - self.position[0];
                let dy = (world_pos[1] - self.position[1]).clamp(0.0, 2.2);
                let dz = world_pos[2] - self.position[2];
                let rad = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
                let (sin, cos) = rad.sin_cos();
                let local_x = (dx * cos - dz * sin).clamp(-2.0, 2.0);
                let mut local_z = (dx * sin + dz * cos).clamp(-2.0, 2.0);
                let y = if spawn.bone_attached {
                    if local_z < 0.85 {
                        local_z = 1.35;
                    }
                    dy.clamp(1.05, 1.45)
                } else {
                    dy
                };
                [local_x, y, local_z]
            } else if spawn.bone_attached {
                [0.0, 1.15, 1.35]
            } else {
                [0.0, 0.0, 0.0]
            }
        });
        let cached_hp = max_health.unwrap_or_else(|| self.vitals.maximum().max(200.0));
        self.pet_positions.insert(pet_id, (pos, cached_hp));
        if let Some(frame) = self.spatial_frame.as_deref() {
            let _ = self.script_requests.queue_spawn_pet(
                frame,
                self.entity,
                pet_id,
                spawn.definition,
                spawn.attach,
                pos,
                self.movement.yaw,
                max_health,
                local_offset,
            );
        }
        Some(pet_id)
    }

    fn despawn_pet(&mut self, site: Site, entity: u32) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        self.pet_positions.remove(&entity);
        self.spawned_possessables.remove(&entity);
        if let Some(frame) = self.spatial_frame.as_deref() {
            let _ = self
                .script_requests
                .queue_despawn_pet(frame, self.entity, entity);
        }
        true
    }

    fn assign_team(&mut self, site: Site, entity: u32, _team: i8) -> bool {
        self.is_player_site(site) && valid_entity(entity)
    }

    fn request_possess_entity(&mut self, site: Site, target: u32) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        if let Some(&definition) = self.spawned_possessables.get(&target) {
            if definition != self.body_definition && self.graphs.entity_body(definition).is_some() {
                self.pending_body_swap = Some(definition);
                return true;
            }
        }
        false
    }

    fn duplicate_target_entity(&mut self, site: Site, target: u32) -> bool {
        if !self.is_player_site(site) || !valid_entity(target) || target == self.entity {
            return false;
        }
        let Some(frame) = self.spatial_frame.as_deref() else {
            return false;
        };
        self.script_requests
            .queue_start_duplicate(frame, self.entity, target, self.body_definition)
    }

    fn set_combat_mod(
        &mut self,
        site: Site,
        entry: Option<crate::script_init::CombatModEntry>,
    ) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        match entry {
            Some(entry) => {
                self.combat_mods.insert(site, entry);
            }
            None => {
                self.combat_mods.remove(&site);
            }
        }
        true
    }

    fn set_deflect_state(
        &mut self,
        site: Site,
        state: Option<crate::script_init::DeflectState>,
    ) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        match state {
            Some(state) => {
                self.deflect.insert(site, state);
            }
            None => {
                self.deflect.remove(&site);
            }
        }
        true
    }

    fn set_target_aura(
        &mut self,
        site: Site,
        targets: &[u32],
        effect: u64,
        param: Option<f64>,
        elapsed_ms: u64,
    ) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        let rules = crate::hero_ability_rules::HeroAbilityRules::defaults();
        if targets.is_empty() {
            self.active_auras.remove(&site);
            if effect == rules.sombra_hack.effect {
                if let Some(frame) = self.spatial_frame.as_deref() {
                    let _ = self.script_requests.queue_hack_health_pack(
                        frame,
                        self.entity,
                        self.position,
                    );
                }
            }
            return true;
        }
        let previously_latched = self
            .active_auras
            .get(&site)
            .map(|(prev, _, _)| prev.clone())
            .unwrap_or_default();
        self.active_auras
            .insert(site, (targets.to_vec(), effect, param));
        let Some(frame) = self.spatial_frame.as_deref() else {
            return true;
        };
        let dt = (elapsed_ms as f64 / 1000.0).min(0.05);
        let own_team = crate::match_scene::team_from_spawn_filter(self.body_filter);
        let mut hacked_enemy = false;
        for &target in targets {
            if !valid_entity(target) {
                continue;
            }
            let is_ally = target == self.entity
                || frame
                    .candidates()
                    .iter()
                    .find(|c| c.entity == target)
                    .is_some_and(|c| own_team.is_some() && c.team == own_team);
            if is_ally {
                if dt > 0.0 && rules.is_heal_aura_effect(effect) {
                    if let Some(rate) = param.filter(|r| r.is_finite() && *r > 1.0 && *r <= 300.0) {
                        let _ = self.script_requests.queue_health(frame, target, rate * dt);
                    }
                }
            } else if effect == rules.sombra_hack.effect && !previously_latched.contains(&target) {
                // Authored Sombra Hack interrupt & silence on latch.
                let _ = self.script_requests.queue_stun(
                    frame,
                    target,
                    rules.sombra_hack.interrupt_ticks,
                );
                hacked_enemy = true;
            }
        }
        if effect == rules.sombra_hack.effect && !hacked_enemy && previously_latched.is_empty() {
            let _ = self
                .script_requests
                .queue_hack_health_pack(frame, self.entity, self.position);
        }
        true
    }

    fn record_recall_sample(&mut self, site: Site, window_secs: f64) -> bool {
        if !self.is_player_site(site) || self.vitals.death().is_some() {
            return false;
        }
        let frame = self.movement.frame.unwrap_or(0);
        let window_frames = ((window_secs * 62.5).ceil() as u32).clamp(30, 625);
        if let Some(last) = self.recall_history.back_mut() {
            if last.0 == frame {
                last.1 = self.position;
                last.2 = self.vitals.total();
                return true;
            }
        }
        self.recall_history
            .push_back((frame, self.position, self.vitals.total()));
        while let Some(&(oldest_frame, _, _)) = self.recall_history.front() {
            if frame.saturating_sub(oldest_frame) > window_frames && self.recall_history.len() > 1 {
                self.recall_history.pop_front();
            } else {
                break;
            }
        }
        true
    }

    fn execute_recall(&mut self, site: Site, frame: u32) -> Option<[f64; 3]> {
        if !self.is_player_site(site) || self.vitals.death().is_some() {
            return None;
        }
        let (_, target_pos, target_hp) = self.recall_history.front().copied().unwrap_or((
            frame,
            self.position,
            self.vitals.total(),
        ));
        self.recall_history.clear();
        if let Some(reloc) = crate::script_init::Relocation::new(target_pos) {
            self.scripted_motion = Some((frame, ScriptedMotion::Teleport(reloc)));
        }
        let current_hp = self.vitals.total();
        if target_hp > current_hp {
            self.vitals.heal(target_hp - current_hp);
        }
        Some(target_pos)
    }

    fn set_ball_roll_state(&mut self, site: Site, active: bool, max_speed: f64) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        self.ball_roll_speed = active.then(|| {
            if max_speed.is_finite() && max_speed > 0.0 {
                max_speed
            } else {
                10.0
            }
        });
        true
    }

    fn apply_physics_explosion(
        &mut self,
        site: Site,
        center: [f64; 3],
        radius: f64,
        impulse: f64,
        direction: Option<[f64; 3]>,
    ) -> bool {
        if !self.is_player_site(site) || !radius.is_finite() || radius <= 0.0 {
            return false;
        }
        if !impulse.is_finite() || impulse.abs() <= 1e-4 {
            return true;
        }
        let Some(frame) = self.spatial_frame.as_deref() else {
            return true;
        };
        let own_team = {
            let bits = (self.body_filter >> 23) & 31;
            (bits != 0).then(|| bits.trailing_zeros() as u8)
        };
        let mut pushes = Vec::new();
        for c in frame.candidates() {
            if c.entity == self.entity {
                continue;
            }
            if own_team.is_some() && c.team == own_team {
                continue;
            }
            let dx = c.position[0] - center[0];
            let dy = (c.position[1] + 0.9) - center[1];
            let dz = c.position[2] - center[2];
            let dist = dx.hypot(dy).hypot(dz);
            if dist > radius {
                continue;
            }
            let falloff = (1.0 - dist / radius).clamp(0.25, 1.0);
            let mag = (impulse * falloff).clamp(-22.0, 22.0);
            let dir = if let Some(d) = direction {
                let len = d[0].hypot(d[1]).hypot(d[2]).max(1e-5);
                [d[0] / len, d[1] / len, d[2] / len]
            } else if dist > 1e-4 {
                let uy = if impulse > 0.0 {
                    (dy / dist).max(0.25)
                } else {
                    dy / dist
                };
                [dx / dist, uy, dz / dist]
            } else {
                [0.0, 1.0, 0.0]
            };
            pushes.push((c.entity, Some([dir[0] * mag, dir[1] * mag, dir[2] * mag])));
        }
        if !pushes.is_empty() {
            let _ = self.script_requests.queue_contact_damage(
                frame,
                self.movement.frame.unwrap_or(0),
                0.0,
                &pushes,
            );
        }
        true
    }

    fn play_script(
        &mut self,
        site: Site,
        target: u32,
        request: crate::script_init::PlayScript,
    ) -> bool {
        if !self.is_player_site(site) {
            return false;
        }
        if target != self.entity && valid_entity(target) {
            // Doomfist Rocket Punch victim script (`0x0D07`, invoked by `0x0D06:16`):
            // settles initial punch impact (50..100), knockback along `0x6F`, brief stun,
            // and wall-slam bonus damage (50..150) when a wall sits behind the target.
            if request.graph == 0x0d07 {
                let charge_frac = request
                    .parameters
                    .iter()
                    .find(|&&(k, _)| (k & 0xffff_ffff) == 0x2b)
                    .map(|(_, v)| crate::script_expr::to_float(v))
                    .filter(|v| v.is_finite())
                    .unwrap_or(0.0)
                    .clamp(0.0, 1.0);
                let raw_dir = match request
                    .parameters
                    .iter()
                    .find(|&&(k, _)| (k & 0xffff_ffff) == 0x6f)
                    .map(|(_, v)| v)
                {
                    Some(&Value::Vector(v))
                        if v.iter().all(|c| c.is_finite()) && v[0].hypot(v[2]) > 1e-4 =>
                    {
                        v
                    }
                    _ => self.forward(self.entity).unwrap_or([0.0, 0.0, 1.0]),
                };
                let hlen = raw_dir[0].hypot(raw_dir[2]).max(1e-4);
                let dir = [raw_dir[0] / hlen, 0.0, raw_dir[2] / hlen];
                let frame_num = self.movement.frame.unwrap_or(0);
                let mut total_damage = 50.0 + 50.0 * charge_frac;
                let mut stun_ticks = 12u32;
                if let Some(tpos) = self.position(target) {
                    let wall_range = 4.0 + 6.5 * charge_frac;
                    let wall_hit = self
                        .spatial_ray_cast(
                            site,
                            frame_num,
                            [tpos[0], tpos[1] + 0.9, tpos[2]],
                            dir,
                            wall_range,
                        )
                        .flatten()
                        .is_some_and(|hit| hit.normal[1].abs() < 0.55);
                    if wall_hit {
                        total_damage += 50.0 + 100.0 * charge_frac;
                        stun_ticks = 28;
                    }
                }
                let push_speed = 14.0 + 12.0 * charge_frac;
                let impulse = Some([dir[0] * push_speed, 2.5, dir[2] * push_speed]);
                let mut tags = crate::script_init::HitTags::EMPTY;
                let _ = tags.push(0x0d80_0000_0000_245a);
                let _ = tags.push(0x0d80_0000_0000_4d2b);
                if let Some(frame) = self.spatial_frame.as_deref() {
                    let _ = self.script_requests.queue_stun(frame, target, stun_ticks);
                    let _ = self.script_requests.queue_contact_damage_with_meta(
                        frame,
                        frame_num,
                        total_damage,
                        Some(0x0b90_0000_0000_0107),
                        tags,
                        &[(target, impulse)],
                    );
                }
                return true;
            }
            if let Some(ticks) =
                crate::game_combat::extract_play_script_stun_ticks(&self.graphs, &request)
            {
                if let Some(frame) = self.spatial_frame.as_deref() {
                    let _ = self.script_requests.queue_stun(frame, target, ticks);
                }
            }
        }
        self.graphs.is_client_only_graph(request.graph)
            || self.graphs.graph(request.graph).is_some()
    }

    fn admits_generic_player_runtime(&self, site: Site) -> bool {
        self.is_player_site(site)
    }

    fn accepts_client_self_message(&self, site: Site, topic: u64) -> bool {
        self.is_player_site(site) && !self.graphs.has_server_message_receiver(topic)
    }

    fn witch_staff_targets(&self, site: Site, radius: f64) -> Option<Vec<u32>> {
        if !self.is_player_site(site) {
            return None;
        }
        let Some(frame) = self.spatial_frame.as_deref() else {
            return Some(Vec::new());
        };
        let own_team = crate::match_scene::team_from_spawn_filter(self.body_filter);
        let max_sq = radius * radius;
        let targets = frame
            .candidates()
            .iter()
            .filter(|c| c.entity != self.entity && own_team.is_some() && c.team == own_team)
            .filter(|c| {
                let dx = c.position[0] - self.position[0];
                let dy = c.position[1] - self.position[1];
                let dz = c.position[2] - self.position[2];
                dx * dx + dy * dy + dz * dz <= max_sq
            })
            .map(|c| c.entity)
            .collect();
        Some(targets)
    }

    fn set_witch_staff_effect(&mut self, site: Site, _effect: Option<(u32, f64)>) -> bool {
        self.is_player_site(site)
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
                .or_else(|| {
                    self.death_source
                        .as_ref()
                        .filter(|source| source.target.target == entity)
                        .and_then(|source| source.target.filter_flags)
                })
                .or_else(|| self.spatial_frame.as_deref()?.actor_filter_flags(entity))
        }
    }

    fn scene_filter_policy(
        &self,
        site: Site,
        filter: &Json,
    ) -> Option<crate::script_expr::SceneFilterPolicy> {
        use crate::script_expr::{FilterPredicate, FilterRelation, SceneFilterPolicy};
        if self.is_player_site(site) {
            let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
            let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
            let relation = if hostile_flag & 1 != 0 || team_mode == 2 {
                Some(FilterRelation::Hostile)
            } else if hostile_flag & 4 != 0 {
                Some(FilterRelation::FriendlyOther)
            } else if hostile_flag & 2 != 0 || team_mode == 1 {
                Some(FilterRelation::Friendly)
            } else {
                Some(FilterRelation::Any)
            };
            if let Some(relation) = relation {
                let predicate = if filter.get("m_condition").is_none_or(Json::is_null) {
                    FilterPredicate::None
                } else {
                    FilterPredicate::Target
                };
                if (relation == FilterRelation::Any
                    && (predicate != FilterPredicate::None || self.spatial_frame.is_some()))
                    || (relation != FilterRelation::Any && self.spatial_frame.is_some())
                {
                    return Some(SceneFilterPolicy {
                        relation,
                        predicate,
                    });
                }
            }
        }
        let source = self.death_source.as_ref()?;
        self.settled_scene_filter_policy(site, filter, &source.target)
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

    fn entities_hostile(
        &self,
        site: Site,
        source: u32,
        target: u32,
        _option: bool,
    ) -> Option<bool> {
        if !self.is_player_site(site) {
            return None;
        }
        if !valid_entity(source) || !valid_entity(target) || source == target {
            return Some(false);
        }
        let left = self.entity_filter_flags(site, source);
        let right = self.entity_filter_flags(site, target);
        if let (Some(left), Some(right)) = (left, right) {
            if let Some(hostile) = crate::pve_encounter::host::ordinary_hostility(left, right) {
                return Some(hostile);
            }
        }
        let team_of = |ent: u32| -> Option<u8> {
            if ent == self.entity {
                return crate::match_scene::team_from_spawn_filter(self.body_filter);
            }
            self.spatial_frame
                .as_deref()?
                .candidates()
                .iter()
                .find(|c| c.entity == ent)
                .and_then(|c| c.team)
        };
        if let (Some(a), Some(b)) = (team_of(source), team_of(target)) {
            return Some(a != b);
        }
        self.spatial_frame.is_some().then_some(false)
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
        crate::pve_encounter::host::ordinary_hostility(left, right)
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
            // Human bodies and live room actors have no separate AI control entity.
            EntityRelation::AiControlEntity
                if entity == self.entity || self.spatial_frame.is_some() =>
            {
                Some(0)
            }
            _ => None,
        }
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        if self.is_player_site(site)
            && config
                .get("m_entity")
                .is_some_and(|entity| native_shape(entity, "STU_B5A0CAF0", None))
        {
            let yaw = f64::from(self.movement.yaw) * std::f64::consts::TAU / 65_536.0;
            let (sin, cos) = yaw.sin_cos();
            let vector = match config.get("$").and_then(Json::as_str) {
                Some("STU_021D01CD") | Some("STU_24087C9E") => Some([sin, 0.0, cos]),
                Some("STU_66F3B89E") => Some([cos, 0.0, -sin]),
                Some("STU_EE66D6C3") => Some([0.0, 1.0, 0.0]),
                Some("STU_FC99C77E") => Some(self.eye_position.unwrap_or(self.position)),
                _ => None,
            };
            if let Some(vector) = vector {
                return Some(Value::Vector(vector.map(|v| f64::from(v as f32))));
            }
        }

        if self.is_player_site(site) {
            if crate::pve_encounter::messages::sighting_selector(config) {
                if !self.defenders.is_empty() {
                    return Some(crate::pve_encounter::messages::recipients(&self.defenders));
                }
                if let Some(frame) = self.spatial_frame.as_deref() {
                    let bits = (self.body_filter >> 23) & 31;
                    let own_team = (bits != 0).then(|| bits.trailing_zeros() as u8);
                    let allies: Vec<Value> = frame
                        .candidates()
                        .iter()
                        .filter(|c| {
                            c.entity != self.entity && own_team.is_some() && c.team == own_team
                        })
                        .map(|c| Value::Entity(c.entity))
                        .collect();
                    return Some(Value::Array(allies));
                }
            }
            if crate::pve_encounter::messages::self_team_query(config) {
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
            "STU_D3685C04"
                if self.is_player_site(site) && native_shape(config, "STU_D3685C04", None) =>
            {
                Some(Value::Bool(false))
            }
            "STU_ED45C5E8"
                if self.is_player_site(site)
                    && config
                        .get("m_B2ABF4FF")
                        .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None)) =>
            {
                let max_range = config
                    .pointer("/m_2BA6230F/m_value")
                    .and_then(Json::as_f64)
                    .unwrap_or(0.0);
                let allies: Vec<Value> = if let Some(frame) = self.spatial_frame.as_deref() {
                    let bits = (self.body_filter >> 23) & 31;
                    let own_team = (bits != 0).then(|| bits.trailing_zeros() as u8);
                    frame
                        .candidates()
                        .iter()
                        .filter(|c| {
                            if c.entity == self.entity || own_team.is_none() || c.team != own_team {
                                return false;
                            }
                            if max_range > 0.0 {
                                let dx = c.position[0] - self.position[0];
                                let dy = c.position[1] - self.position[1];
                                let dz = c.position[2] - self.position[2];
                                dx.hypot(dy).hypot(dz) <= max_range
                            } else {
                                true
                            }
                        })
                        .map(|c| Value::Entity(c.entity))
                        .collect()
                } else {
                    Vec::new()
                };
                Some(Value::Array(allies))
            }
            "STU_65ABFCCC"
                if self.is_player_site(site)
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
                    && self.mode != 0
                    && crate::script_expr::is_zeroed_owner_selector(config) =>
            {
                Some(Value::Null)
            }
            _ => None,
        }
    }
}

impl BodyWorld {
    pub(super) fn record_command_view(&mut self, frame: u32) {
        const HISTORY_FRAMES: usize = 64;
        if let Some(eye) = self.eye_position {
            if self.command_views.len() == HISTORY_FRAMES {
                self.command_views.pop_front();
            }
            self.command_views.push_back(CommandView {
                frame,
                eye,
                direction: direction_for_angles(self.movement.yaw, self.movement.pitch),
            });
        }
    }

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
        self.hero == crate::game_heroes::CASSIDY
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

    fn authored_pet_max_health(&self, graph_index: u32) -> Option<f64> {
        let graph = self.graphs.graph(graph_index)?;
        // Retail barrier graphs (0x0095 Rein, 0x0EE3 Brig, 0x1B55 Sigma) declare their
        // authored barrier max health via a literal assignment into var[587].
        // Note: `STU_12F5D52D` uses `m_143D4C5B` in retail 1.74 data.
        for node in &graph.nodes {
            if let Some(entries) = node
                .fields
                .get("m_143D4C5B")
                .or_else(|| node.fields.get("m_4E87690F"))
                .and_then(Json::as_array)
            {
                for entry in entries {
                    if entry
                        .pointer("/m_out_Var/m_identifier")
                        .and_then(Json::as_u64)
                        == Some(587)
                    {
                        if let Some(v) = entry
                            .pointer("/m_value/m_value")
                            .and_then(Json::as_f64)
                            .filter(|v| v.is_finite() && *v > 1.0)
                        {
                            return Some(v);
                        }
                    }
                }
            }
            if node.class == "STUStatescriptActionSetVar"
                && node
                    .fields
                    .pointer("/m_out_Var/m_identifier")
                    .and_then(Json::as_u64)
                    == Some(587)
            {
                if let Some(v) = node
                    .fields
                    .pointer("/m_value/m_value")
                    .and_then(Json::as_f64)
                    .filter(|v| v.is_finite() && *v > 1.0)
                {
                    return Some(v);
                }
            }
        }
        None
    }
}
