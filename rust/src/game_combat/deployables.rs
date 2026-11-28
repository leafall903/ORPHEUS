//! Generic room-authoritative pets (`STUStatescriptStatePet`) and projectile
//! impact deployables (`ImpactSpawn` / `m_49CBA3BC`) driven directly by retail
//! 1.74 entity definitions and StateScript graphs.

use std::{
    sync::{
        Arc,
        atomic::{AtomicU32, Ordering},
    },
    time::Duration,
};

use bevy_ecs::entity::Entity;
use indexmap::IndexMap;
use serde_json::Value as Json;

use super::{RoomScriptRequest, RoomScriptRequests, RoomSimulation, RoomSpatialFrame, Stun, Vec3};
use crate::{
    game_body::yaw_rotation,
    game_projectiles::ImpactSpawn,
    match_scene::bot_filter_component,
    script_expr::{self, EmptyContext, EntityRelation, SpatialCandidate, valid_entity},
    script_graph::{GraphStore, asset_guid, graph_asset_index},
    script_init::{
        Component, MAX_INIT_WORK, PlayScript, ReportedShot, RoomMessage, Root, Site,
        World as ScriptWorld,
    },
    script_wire::{Value, Variables},
    vitals::{Death, Vitals},
    world::{Create, EntityUpdate, GAME_MODE_ENTITY, Movement, Operation},
};

const FIRST_PET_WIRE: u32 = 0xc008_0000;
const PET_WIRE_SPAN: u32 = 0x0008_0000;
const FIRST_DEPLOYABLE_WIRE: u32 = 0xc010_0000;
const DEPLOYABLE_WIRE_SPAN: u32 = 0x0008_0000;
const MAX_DEPLOYABLES_PER_OWNER: usize = 16;
const SCRIPTLESS_DEPLOYABLE_TICKS: u32 = 62 * 20; // 20s default lifetime when no script expires it
const SCRIPTED_DEPLOYABLE_MAX_TICKS: u32 = 62 * 600; // 10m safety cap so permanent deployables stay active until destroyed/replaced
const SHARED_STUN_GRAPH: u32 = 0x224e;
const STUN_DURATION_VAR: u64 = 0x0d80_0000_0000_00d7;

static NEXT_PET_WIRE: AtomicU32 = AtomicU32::new(0);
static NEXT_DEPLOYABLE_WIRE: AtomicU32 = AtomicU32::new(0);

pub(crate) fn next_pet_wire_id() -> u32 {
    let seq = NEXT_PET_WIRE.fetch_add(1, Ordering::Relaxed) % PET_WIRE_SPAN;
    FIRST_PET_WIRE | seq
}

fn next_deployable_wire_id() -> u32 {
    let seq = NEXT_DEPLOYABLE_WIRE.fetch_add(1, Ordering::Relaxed) % DEPLOYABLE_WIRE_SPAN;
    FIRST_DEPLOYABLE_WIRE | seq
}

/// Extract an authored stun duration in simulation ticks from a `PlayScript`
/// invocation targeting the retail shared stun control graph (`0x224E`).
pub(crate) fn extract_play_script_stun_ticks(
    graphs: &GraphStore,
    request: &PlayScript,
) -> Option<u32> {
    if request.graph != SHARED_STUN_GRAPH {
        return None;
    }
    let override_seconds = request
        .overrides
        .iter()
        .find(|(id, _)| *id == STUN_DURATION_VAR)
        .and_then(|(_, val)| match *val {
            Value::Float(v) => Some(v),
            Value::Integer(v) => Some(v as f64),
            _ => None,
        });
    let seconds = override_seconds.or_else(|| {
        let entries = graphs
            .graph(SHARED_STUN_GRAPH)?
            .fields
            .get("m_publicSchema")?
            .get("m_entries")?
            .as_array()?;
        let row = entries
            .iter()
            .find(|row| row.get("m_0D09D2D9").and_then(asset_guid) == Some(STUN_DURATION_VAR))?;
        let val = script_expr::evaluate(row.get("m_value")?, &mut EmptyContext).ok()?;
        match val {
            Value::Float(v) => Some(v),
            Value::Integer(v) => Some(v as f64),
            _ => None,
        }
    })?;
    Stun::from_seconds(seconds).map(|stun| stun.ticks)
}

pub(super) struct RoomPet {
    pub(super) owner: u32,
    pub(super) ecs_entity: Entity,
    pub(super) team: u8,
    pub(super) definition: u64,
    pub(super) attach: Option<u32>,
    pub(super) local_offset: Option<Vec3>,
    pub(super) position: Vec3,
    pub(super) yaw: i32,
    pub(super) vitals: Option<Vitals>,
    pub(super) wire_vitals: bool,
    pub(super) published_movement: bool,
    /// Solid world obstacle (e.g. Mei Ice Wall pillar) that blocks movement & shots for both teams.
    pub(super) solid_wall: bool,
    /// Optional parent pet wire ID for multi-pillar structures (e.g. Mei Ice Wall flanks).
    pub(super) parent_pet: Option<u32>,
    /// Autonomous combat turret / companion (Torbjörn Turret / Ashe B.O.B.) fire cooldown in ticks.
    pub(super) ai_fire_cooldown: u16,
    /// Opening charge ticks remaining for high-HP autonomous companions (B.O.B.).
    pub(super) charge_ticks: u16,
    pub(super) component: Option<Component>,
    pub(super) world: Option<DeployableWorld>,
}

fn resolve_entity_vitals(
    graphs: Option<&GraphStore>,
    definition: u64,
    max_health: Option<f64>,
) -> (Option<Vitals>, bool) {
    if let Some(pools) = graphs
        .and_then(|g| {
            g.entity_body(definition)
                .or_else(|| g.bodies().get(&definition))
        })
        .and_then(|body| {
            crate::game_pools::authored_pools(graphs?, body)
                .ok()
                .flatten()
        })
        .filter(|pools| pools.total() > 0.0)
    {
        return (Some(pools.vitals()), true);
    }
    (
        max_health
            .filter(|hp| hp.is_finite() && *hp > 0.0)
            .map(|hp| Vitals::new(hp, 0.0, 0.0)),
        false,
    )
}

#[allow(clippy::too_many_arguments)]
fn start_entity_vm(
    graphs: Arc<GraphStore>,
    mode: u64,
    entity: u32,
    owner: u32,
    team: u8,
    definition: u64,
    position: Vec3,
    tick: u32,
    spatial_frame: Option<Arc<RoomSpatialFrame>>,
    extra_graph: Option<u64>,
) -> (Option<Component>, Option<DeployableWorld>) {
    let mut graph_ids = Vec::new();
    if let Some(body) = graphs
        .entity_body(definition)
        .or_else(|| graphs.bodies().get(&definition))
    {
        for binding in &body.graphs {
            if let Some(gid) = binding.graph {
                if graphs.graph(gid).is_some() && !graph_ids.contains(&gid) {
                    graph_ids.push(gid);
                }
            }
        }
    }
    if let Some(extra) = extra_graph.and_then(graph_asset_index) {
        if graphs.graph(extra).is_some() && !graph_ids.contains(&extra) {
            graph_ids.push(extra);
        }
    }
    if graph_ids.is_empty() {
        return (None, None);
    }
    let roots: Vec<Root> = graph_ids
        .into_iter()
        .enumerate()
        .map(|(idx, gid)| Root::new((idx + 1) as u16, gid))
        .collect();
    let mut world = DeployableWorld {
        graphs: Arc::clone(&graphs),
        mode,
        entity,
        owner,
        team,
        definition,
        position,
        tick,
        spatial_frame,
        script_requests: RoomScriptRequests::default(),
    };
    match Component::start_with_public_schema(
        graphs,
        entity,
        Variables::new(),
        roots,
        tick,
        &mut world,
        MAX_INIT_WORK,
    ) {
        Ok(started) => (Some(started), Some(world)),
        Err(_) => (None, None),
    }
}

/// CASC-verified (`0x0400` entity blueprint) support for Mirror components:
/// `(has_target_tag_filter_26, has_health_51)`.
///
/// Only entities verified to carry these components in retail 1.74 CASC may
/// receive them on `Create` so we never trigger a Mirror deserialization crash
/// while ensuring all barriers, turrets, and companions receive friendly blue
/// team coloring (component 26) and health bars (component 51).
fn casc_verified_entity_components(definition: u64, authored_body_vitals: bool) -> (bool, bool) {
    if authored_body_vitals {
        return (true, true);
    }
    match definition {
        // Barriers & destructible turrets/companions verified with STUTargetTagComponent + STUHealthComponent:
        0x0400_0000_0000_011a // Reinhardt Barrier Field
        | 0x0400_0000_0000_2284 // Brigitte Barrier Shield
        | 0x0400_0000_0000_2d25 // Sigma Experimental Barrier
        | 0x0400_0000_0000_26b0 // Symmetra Photon Barrier
        | 0x0400_0000_0000_1941 // Barrier / Shield Pet
        | 0x0400_0000_0000_03da // Torbjörn Turret
        | 0x0400_0000_0000_13f4 // Torbjörn Turret
        | 0x0400_0000_0000_0223 // Symmetra Pet / Teleporter
        | 0x0400_0000_0000_2533 // Ashe Dynamite
        => (true, true),
        // Entities verified with STUTargetTagComponent (26) only (no component 51):
        0x0400_0000_0000_090b // Mei Ice Wall
        | 0x0400_0000_0000_1846 // Orisa Barrier
        | 0x0400_0000_0000_2902 // Torbjörn Thrown Turret
        | 0x0400_0000_0000_2b61 // Ashe B.O.B.
        | 0x0400_0000_0000_3d10 // Companion Pet
        => (true, false),
        _ => (false, false),
    }
}

#[allow(clippy::too_many_arguments)]
fn entity_create_update(
    wire: u32,
    team: u8,
    definition: u64,
    attach: Option<u32>,
    position: Vec3,
    yaw: i32,
    local_offset: Option<Vec3>,
    vitals: Option<&Vitals>,
    authored_body_vitals: bool,
) -> Option<EntityUpdate> {
    if definition >> 48 != 0x0400 || !position.into_iter().all(f64::is_finite) {
        return None;
    }
    let mut components = Vec::new();
    let (has_filter_26, has_health_51) =
        casc_verified_entity_components(definition, authored_body_vitals);
    if has_filter_26 {
        components.push(bot_filter_component(team, false).ok()?);
    }
    if has_health_51 {
        if let Some(vitals) = vitals {
            components.push(vitals.component());
        }
    }
    let mut create = Create::new(definition, components);
    if let Some(parent) = attach {
        create.movement_parent = Some(parent);
        let offset = local_offset
            .filter(|v| v.iter().all(|a| a.is_finite()))
            .unwrap_or([0.0; 3]);
        // Attached pets (e.g. Reinhardt/Brigitte barriers) use local transforms
        // relative to `movement_parent`: setting world `yaw` here double-rotates
        // the shield (180° backwards when facing South).
        create.position = offset.map(|v| v as f32);
        create.rotation = [0.0, 0.0, 0.0, 1.0];
        create.movement = Some(Movement {
            flags: 0,
            position: offset,
            yaw: 0,
            ..Movement::default()
        });
    } else {
        create.position = position.map(|v| v as f32);
        create.rotation = yaw_rotation(f64::from(yaw));
    }
    Some(EntityUpdate {
        entity: wire,
        operation: Some(Operation::Create(Box::new(create))),
        ..EntityUpdate::default()
    })
}

fn take_entity_health(wire: u32, vitals: &mut Vitals, wire_vitals: bool) -> Option<EntityUpdate> {
    if !wire_vitals {
        vitals.clear_changed();
        return None;
    }
    let health = vitals.take_cumulative_update()?;
    Some(EntityUpdate {
        entity: wire,
        operation: Some(Operation::Update(vec![health])),
        ..EntityUpdate::default()
    })
}

pub(super) struct DeployableWorld {
    graphs: Arc<GraphStore>,
    mode: u64,
    entity: u32,
    owner: u32,
    team: u8,
    definition: u64,
    position: Vec3,
    tick: u32,
    spatial_frame: Option<Arc<RoomSpatialFrame>>,
    script_requests: RoomScriptRequests,
}

impl ScriptWorld for DeployableWorld {
    fn admits_generic_player_runtime(&self, site: Site) -> bool {
        site.entity == self.entity
    }

    fn accepts_client_self_message(&self, site: Site, topic: u64) -> bool {
        site.entity == self.entity && !self.graphs.has_server_message_receiver(topic)
    }

    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity == GAME_MODE_ENTITY {
            return self
                .graphs
                .mode_variables(self.mode)
                .map(|variables| variables.get(&id).copied().map_or(Value::Null, Value::from));
        }
        None
    }

    fn known_position(&self, entity: u32) -> Option<[f64; 3]> {
        (entity == self.entity).then_some(self.position)
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            return Some(self.position);
        }
        self.spatial_frame
            .as_deref()?
            .actor_pose(entity)
            .map(|(pos, _)| pos)
    }

    fn entity_exists(&self, entity: u32) -> Option<bool> {
        if entity == self.entity {
            return Some(true);
        }
        if entity == GAME_MODE_ENTITY {
            return Some(self.graphs.mode_variables(self.mode).is_some());
        }
        if valid_entity(entity)
            && self
                .spatial_frame
                .as_deref()
                .is_some_and(|frame| frame.candidates().iter().any(|c| c.entity == entity))
        {
            return Some(true);
        }
        None
    }

    fn entity_alive(&self, site: Site, entity: u32) -> bool {
        if site.entity != self.entity {
            return false;
        }
        entity == self.entity
            || self
                .spatial_frame
                .as_deref()
                .is_some_and(|frame| frame.candidates().iter().any(|c| c.entity == entity))
    }

    fn entity_team(&self, site: Site, entity: u32) -> Option<i8> {
        if site.entity != self.entity {
            return None;
        }
        if entity == self.entity {
            return i8::try_from(self.team).ok();
        }
        self.spatial_frame
            .as_deref()?
            .candidates()
            .iter()
            .find(|c| c.entity == entity)
            .and_then(|c| c.team)
            .and_then(|team| i8::try_from(team).ok())
    }

    fn entity_definition(&self, site: Site, entity: u32) -> Option<u64> {
        (site.entity == self.entity && entity == self.entity).then_some(self.definition)
    }

    fn related_entity(&mut self, site: Site, relation: EntityRelation, entity: u32) -> Option<u32> {
        if site.entity != self.entity || entity != self.entity {
            return None;
        }
        match relation {
            EntityRelation::CreatorBody | EntityRelation::OwningController => Some(self.owner),
            _ => None,
        }
    }

    fn spatial_candidates(&self) -> Option<&[SpatialCandidate]> {
        self.spatial_frame
            .as_deref()
            .map(RoomSpatialFrame::candidates)
    }

    fn spatial_line_clear(&self, origin: [f64; 3], target: [f64; 3]) -> Option<bool> {
        self.spatial_frame.as_deref()?.line_clear(origin, target)
    }

    fn scene_filter_policy(
        &self,
        site: Site,
        filter: &Json,
    ) -> Option<crate::script_expr::SceneFilterPolicy> {
        use crate::script_expr::{FilterPredicate, FilterRelation, SceneFilterPolicy};
        if site.entity != self.entity || self.spatial_frame.is_none() {
            return None;
        }
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let relation = match (team_mode, hostile_flag) {
            (1, 0) => Some(FilterRelation::Friendly),
            (2, _) | (_, 1) => Some(FilterRelation::Hostile),
            (0, 0) => Some(FilterRelation::Any),
            _ => None,
        }?;
        let predicate = if filter.get("m_condition").is_none_or(Json::is_null) {
            FilterPredicate::None
        } else {
            FilterPredicate::Target
        };
        Some(SceneFilterPolicy {
            relation,
            predicate,
        })
    }

    fn tracked_targets(
        &mut self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        if site.entity != self.entity || !radius.is_finite() || radius < 0.0 {
            return None;
        }
        let frame = self.spatial_frame.as_deref()?;
        let base = if origin == 0 {
            [0.0; 3]
        } else if origin == self.entity {
            self.position
        } else {
            frame
                .actor_pose(origin)
                .map(|(p, _)| p)
                .unwrap_or(self.position)
        };
        let center = [
            base[0] + offset[0],
            base[1] + offset[1],
            base[2] + offset[2],
        ];
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let relation = match (team_mode, hostile_flag) {
            (1, 0) => 1u64,
            (2, _) | (_, 1) => 2u64,
            _ => 0u64,
        };
        let mut hits = Vec::new();
        for candidate in frame.candidates() {
            let team_ok = match relation {
                1 => candidate.team == Some(self.team),
                2 => candidate.team != Some(self.team),
                _ => true,
            };
            if !team_ok {
                continue;
            }
            let cap_r = 0.45;
            let cap_a = [
                candidate.position[0],
                candidate.position[1] + cap_r,
                candidate.position[2],
            ];
            let cap_b = [
                candidate.position[0],
                candidate.position[1] + 1.45,
                candidate.position[2],
            ];
            if crate::static_ray::segment_distance(center, cap_a, cap_b) <= radius + cap_r {
                hits.push(candidate.entity);
            }
        }
        Some(hits)
    }

    fn queue_health_change(&mut self, site: Site, target: u32, delta: f64) -> bool {
        self.queue_modify_health(site, target, delta, None)
    }

    fn queue_health_changes(&mut self, site: Site, changes: &[(u32, f64)]) -> bool {
        if site.entity != self.entity {
            return false;
        }
        let mut any = false;
        for &(target, delta) in changes {
            any |= self.queue_modify_health(site, target, delta, None);
        }
        any
    }

    fn queue_modify_health(
        &mut self,
        site: Site,
        target: u32,
        delta: f64,
        impulse: Option<[f64; 3]>,
    ) -> bool {
        if site.entity != self.entity || !delta.is_finite() {
            return false;
        }
        let Some(frame) = self.spatial_frame.as_deref() else {
            return false;
        };
        if delta < 0.0 || (delta == 0.0 && impulse.is_some()) {
            self.script_requests.queue_contact_damage(
                frame,
                self.tick,
                (-delta).max(0.0),
                &[(target, impulse)],
            )
        } else if delta > 0.0 {
            self.script_requests.queue_health(frame, target, delta)
        } else {
            false
        }
    }

    fn queue_room_message(&mut self, site: Site, target: u32, message: RoomMessage) -> bool {
        site.entity == self.entity
            && self
                .spatial_frame
                .as_deref()
                .is_some_and(|frame| self.script_requests.queue_message(frame, target, message))
    }

    fn play_script(&mut self, site: Site, target: u32, request: PlayScript) -> bool {
        if site.entity != self.entity {
            return false;
        }
        if target != self.entity && valid_entity(target) {
            if let Some(ticks) = extract_play_script_stun_ticks(&self.graphs, &request) {
                if let Some(frame) = self.spatial_frame.as_deref() {
                    let _ = self.script_requests.queue_stun(frame, target, ticks);
                }
            }
        }
        self.graphs.is_client_only_graph(request.graph)
            || self.graphs.graph(request.graph).is_some()
    }

    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            if let Some(frame) = self.spatial_frame.as_deref() {
                if let Some(target) = frame.candidates().iter().find(|c| {
                    c.entity != self.entity && c.entity != self.owner && c.team != Some(self.team)
                }) {
                    let dx = target.position[0] - self.position[0];
                    let dy = (target.position[1] + 1.0) - (self.position[1] + 0.8);
                    let dz = target.position[2] - self.position[2];
                    let len = dx.hypot(dy).hypot(dz);
                    if len > 1e-4 {
                        return Some([dx / len, dy / len, dz / len]);
                    }
                }
            }
            return Some([0.0, 0.0, 1.0]);
        }
        let (_, [yaw, _]) = self.spatial_frame.as_deref()?.actor_pose(entity)?;
        let rad = f64::from(yaw) * std::f64::consts::TAU / 65_536.0;
        let (sin, cos) = rad.sin_cos();
        Some([sin, 0.0, cos])
    }

    fn authored_forward(&self, entity: u32) -> Option<[f64; 3]> {
        self.forward(entity)
    }

    fn body_eye_position(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.entity {
            return Some([self.position[0], self.position[1] + 0.8, self.position[2]]);
        }
        self.spatial_frame.as_deref()?.actor_eye_position(entity)
    }

    fn body_health(
        &self,
        site: Site,
        pool: Option<crate::vitals::PoolKind>,
        maximum: bool,
    ) -> Option<f64> {
        self.spatial_frame
            .as_deref()?
            .actor_health(site.entity, pool, maximum)
    }

    fn spatial_ray_cast(
        &self,
        site: Site,
        frame: u32,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
    ) -> Option<Option<crate::script_expr::SpatialRayHit>> {
        self.spatial_frame
            .as_deref()?
            .ray_cast(site, frame, origin, direction, max_distance)
    }

    fn cone_targets(
        &self,
        site: Site,
        origin_entity: u32,
        offset: [f64; 3],
        range: f64,
        half_angle_deg: f64,
        visible: bool,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        if site.entity != self.entity || !range.is_finite() || range <= 0.0 {
            return None;
        }
        let frame = self.spatial_frame.as_deref()?;
        let base = if origin_entity == 0 {
            [0.0; 3]
        } else if origin_entity == self.entity {
            self.position
        } else {
            frame
                .actor_pose(origin_entity)
                .map(|(p, _)| p)
                .unwrap_or(self.position)
        };
        let origin = [
            base[0] + offset[0],
            base[1] + offset[1],
            base[2] + offset[2],
        ];
        let direction = self.forward(self.entity).unwrap_or([0.0, 0.0, 1.0]);
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let min_dot = half_angle_deg.clamp(0.0, 180.0).to_radians().cos();
        let mut hits = Vec::new();
        for c in frame.candidates() {
            if c.entity == self.entity {
                continue;
            }
            let team_ok = match (team_mode, hostile_flag) {
                (1, 0) => c.team == Some(self.team),
                (2, _) | (_, 1) => c.team != Some(self.team),
                _ => true,
            };
            if !team_ok {
                continue;
            }
            let dx = c.position[0] - origin[0];
            let dy = (c.position[1] + 1.0) - origin[1];
            let dz = c.position[2] - origin[2];
            let dist = dx.hypot(dy).hypot(dz);
            if dist <= range && dist > 1e-5 {
                let dir = [dx / dist, dy / dist, dz / dist];
                let dot = dir[0] * direction[0] + dir[1] * direction[1] + dir[2] * direction[2];
                if dot >= min_dot {
                    if visible {
                        if let Some(Some(hit)) = frame.ray_cast(site, self.tick, origin, dir, dist)
                        {
                            if hit.distance + 0.35 < dist {
                                continue;
                            }
                        }
                    }
                    hits.push((dist, c.entity));
                }
            }
        }
        hits.sort_by(|a, b| a.0.total_cmp(&b.0));
        Some(hits.into_iter().map(|(_, e)| e).collect())
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
        if site.entity != self.entity {
            return None;
        }
        let Some(frame) = self.spatial_frame.as_deref() else {
            return Some(crate::script_init::GenericBeamContact::default());
        };
        let origin = origin.unwrap_or([self.position[0], self.position[1] + 0.8, self.position[2]]);
        let direction = direction
            .or_else(|| self.forward(self.entity))
            .unwrap_or([0.0, 0.0, 1.0]);
        let reach = length.filter(|l| l.is_finite() && *l > 0.0).unwrap_or(15.0);
        let radius = if radius.is_finite() && radius > 0.0 {
            radius
        } else {
            0.0
        };
        let team_mode = filter.get("m_66747E7D").and_then(Json::as_u64).unwrap_or(0);
        let hostile_flag = filter.get("m_89831602").and_then(Json::as_u64).unwrap_or(0);
        let candidates: Vec<u32> = frame
            .candidates()
            .iter()
            .filter(|c| c.entity != self.entity && c.entity != self.owner)
            .filter(|c| match (team_mode, hostile_flag) {
                (1, 0) => c.team == Some(self.team),
                (2, _) | (_, 1) => c.team != Some(self.team),
                _ => c.team != Some(self.team),
            })
            .map(|c| c.entity)
            .collect();
        let actor_hit = frame
            .actor_beam_cast(
                site,
                self.tick,
                origin,
                direction,
                reach,
                radius,
                &candidates,
            )
            .flatten();
        if let Some(hit) = actor_hit {
            let point = [
                origin[0] + direction[0] * hit.distance,
                origin[1] + direction[1] * hit.distance,
                origin[2] + direction[2] * hit.distance,
            ];
            Some(crate::script_init::GenericBeamContact {
                entity: Some(hit.entity),
                hit: true,
                point: Some(point),
                normal: Some(hit.normal),
                distance: Some(hit.distance),
            })
        } else {
            Some(crate::script_init::GenericBeamContact::default())
        }
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        if site.entity != self.entity {
            return None;
        }
        match config.get("$")?.as_str()? {
            "STU_1FF67CA6" => Some(Value::Entity(self.entity)),
            "STU_37F20D24" => Some(Value::Asset(self.mode)),
            "STU_A3DEF368" if self.graphs.mode_variables(self.mode).is_some() => {
                Some(Value::Entity(GAME_MODE_ENTITY))
            }
            "STU_4A101787" | "STU_A77AC2AC" | "STU_D3EB8D87" => Some(Value::Bool(false)),
            _ => None,
        }
    }
}

pub(super) struct RoomDeployable {
    pub(super) owner: u32,
    pub(super) ecs_entity: Entity,
    pub(super) team: u8,
    pub(super) definition: u64,
    pub(super) position: Vec3,
    pub(super) yaw: i32,
    pub(super) vitals: Option<Vitals>,
    wire_vitals: bool,
    spawned_tick: u32,
    max_ticks: u32,
    pub(super) component: Option<Component>,
    pub(super) world: Option<DeployableWorld>,
}

impl RoomDeployable {
    fn is_expired(&self, tick: u32) -> bool {
        if self.vitals.as_ref().is_some_and(|v| v.death().is_some()) {
            return true;
        }
        if tick.saturating_sub(self.spawned_tick) >= self.max_ticks {
            return true;
        }
        if let Some(component) = &self.component {
            match component.snapshot() {
                None => return true,
                Some(snapshot) => {
                    let any_active = snapshot
                        .instances
                        .values()
                        .any(|inst| inst.states.values().any(|state| state.active));
                    if !any_active && tick > self.spawned_tick.saturating_add(4) {
                        return true;
                    }
                }
            }
        }
        false
    }
}

impl RoomSimulation {
    pub(super) fn initial_deployable_creates(&self) -> impl Iterator<Item = EntityUpdate> + '_ {
        let pets = self.room_pets.iter().filter_map(|(&wire, pet)| {
            if pet
                .vitals
                .as_ref()
                .is_some_and(|vitals| vitals.death().is_some())
            {
                return None;
            }
            entity_create_update(
                wire,
                pet.team,
                pet.definition,
                pet.attach,
                pet.position,
                pet.yaw,
                pet.local_offset,
                pet.vitals.as_ref(),
                pet.wire_vitals,
            )
        });
        let deployables = self
            .room_deployables
            .iter()
            .filter_map(|(&wire, deployable)| {
                if deployable
                    .vitals
                    .as_ref()
                    .is_some_and(|vitals| vitals.death().is_some())
                {
                    return None;
                }
                entity_create_update(
                    wire,
                    deployable.team,
                    deployable.definition,
                    None,
                    deployable.position,
                    deployable.yaw,
                    None,
                    deployable.vitals.as_ref(),
                    deployable.wire_vitals,
                )
            });
        pets.chain(deployables)
    }

    pub(crate) fn published_pet_movements(&self) -> impl Iterator<Item = EntityUpdate> + '_ {
        self.room_pets
            .iter()
            .filter(|(_, pet)| pet.published_movement && pet.attach.is_none())
            .map(|(&entity, pet)| EntityUpdate {
                entity,
                movement: Some(Movement {
                    position: pet.position,
                    yaw: pet.yaw,
                    ..Movement::default()
                }),
                ..EntityUpdate::default()
            })
    }

    fn deployable_graphs(&self) -> Option<(Arc<GraphStore>, u64)> {
        self.script_graphs
            .as_ref()
            .map(|(g, m)| (Arc::clone(g), *m))
            .or_else(|| {
                self.stock_bots
                    .as_ref()
                    .map(|c| (Arc::clone(&c.graphs), c.mode))
            })
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn spawn_room_pet(
        &mut self,
        owner: u32,
        team: u8,
        pet_id: u32,
        definition: u64,
        attach: Option<u32>,
        position: Vec3,
        yaw: i32,
        max_health: Option<f64>,
        local_offset: Option<Vec3>,
        tick: u32,
    ) {
        if self.room_pets.contains_key(&pet_id) {
            return;
        }
        let graphs_and_mode = self.deployable_graphs();
        let ice_wall = self.ability_rules.ice_wall.clone();
        let is_ice_wall_root = definition == ice_wall.entity && attach.is_none();
        let (vitals, authored_body_vitals) = if is_ice_wall_root {
            (Some(Vitals::new(ice_wall.pillar_health, 0.0, 0.0)), false)
        } else {
            resolve_entity_vitals(
                graphs_and_mode.as_ref().map(|(g, _)| g.as_ref()),
                definition,
                max_health,
            )
        };
        // Brigitte's personal barrier (`0x2284`) spawns without `m_888BCD8B`, so
        // attach it cleanly to Brigitte's front left arm on the wire.
        const BRIG_SHIELD_DEF: u64 = 0x0400_0000_0000_2284;
        let (attach, local_offset) = if definition == BRIG_SHIELD_DEF && attach.is_none() {
            (Some(owner), Some([0.0, 1.0, 0.95]))
        } else {
            (attach, local_offset)
        };
        let Some(update) = entity_create_update(
            pet_id,
            team,
            definition,
            attach,
            position,
            yaw,
            local_offset,
            vitals.as_ref(),
            authored_body_vitals,
        ) else {
            return;
        };
        let (component, world_opt) = if let Some((graphs, mode)) = graphs_and_mode {
            start_entity_vm(
                graphs,
                mode,
                pet_id,
                owner,
                team,
                definition,
                position,
                tick,
                self.spatial_frame.as_ref().map(|(_, f)| Arc::clone(f)),
                None,
            )
        } else {
            (None, None)
        };
        let ecs_entity = self.world.spawn_empty().id();
        let max_hp = vitals.as_ref().map_or(0.0, Vitals::maximum);
        let (_, wire_vitals) = casc_verified_entity_components(definition, authored_body_vitals);
        let solid_wall = is_ice_wall_root;
        let charge_ticks = if attach.is_none() && max_hp >= 800.0 {
            186 // ~3.0s opening charge for B.O.B. companions
        } else {
            0
        };
        self.room_pets.insert(
            pet_id,
            RoomPet {
                owner,
                ecs_entity,
                team,
                definition,
                attach,
                local_offset,
                position,
                yaw,
                vitals,
                wire_vitals,
                published_movement: false,
                solid_wall,
                parent_pet: None,
                ai_fire_cooldown: 16,
                charge_ticks,
                component,
                world: world_opt,
            },
        );
        self.created.push(update);
        // Mei Ice Wall: automatically spawn flanking destructible solid pillars perpendicular to yaw.
        if is_ice_wall_root {
            let rad = f64::from(yaw) * std::f64::consts::TAU / 65_536.0;
            let (sin_y, cos_y) = rad.sin_cos();
            let right = [cos_y, 0.0, -sin_y];
            for &idx in &ice_wall.flank_offsets {
                let offset = f64::from(idx) * ice_wall.pillar_spacing;
                let pillar_pos = [
                    position[0] + right[0] * offset,
                    position[1],
                    position[2] + right[2] * offset,
                ];
                let pillar_id = next_pet_wire_id();
                let pillar_vitals = Vitals::new(ice_wall.pillar_health, 0.0, 0.0);
                if let Some(pillar_update) = entity_create_update(
                    pillar_id,
                    team,
                    definition,
                    None,
                    pillar_pos,
                    yaw,
                    None,
                    Some(&pillar_vitals),
                    false,
                ) {
                    let pillar_ecs = self.world.spawn_empty().id();
                    self.room_pets.insert(
                        pillar_id,
                        RoomPet {
                            owner,
                            ecs_entity: pillar_ecs,
                            team,
                            definition,
                            attach: None,
                            local_offset: None,
                            position: pillar_pos,
                            yaw,
                            vitals: Some(pillar_vitals),
                            wire_vitals: false,
                            published_movement: false,
                            solid_wall: true,
                            parent_pet: Some(pet_id),
                            ai_fire_cooldown: 0,
                            charge_ticks: 0,
                            component: None,
                            world: None,
                        },
                    );
                    self.created.push(pillar_update);
                }
            }
        }
    }

    /// Spawn a 5-pillar solid destructible Ice Wall perpendicular to `yaw`,
    /// where each pillar has independent HP and blocks movement + rays for both teams.
    #[cfg(test)]
    pub(super) fn spawn_ice_wall_pillars(
        &mut self,
        owner: u32,
        team: u8,
        definition: u64,
        center: Vec3,
        yaw: i32,
        tick: u32,
    ) -> Vec<u32> {
        let ice_wall = self.ability_rules.ice_wall.clone();
        let rad = f64::from(yaw) * std::f64::consts::TAU / 65_536.0;
        let (sin_y, cos_y) = rad.sin_cos();
        let right = [cos_y, 0.0, -sin_y];
        let mut ids = Vec::with_capacity(ice_wall.flank_offsets.len() + 1);
        for idx in -2i32..=2i32 {
            let offset = f64::from(idx) * ice_wall.pillar_spacing;
            let pos = [
                center[0] + right[0] * offset,
                center[1],
                center[2] + right[2] * offset,
            ];
            let id = next_pet_wire_id();
            self.spawn_room_pet(
                owner,
                team,
                id,
                definition,
                None,
                pos,
                yaw,
                Some(ice_wall.pillar_health),
                None,
                tick,
            );
            if let Some(pet) = self.room_pets.get_mut(&id) {
                pet.solid_wall = true;
            }
            ids.push(id);
        }
        ids
    }

    /// Return solid blocking capsules for all living wall pillars so players
    /// cannot clip through them and can stand on top of them.
    pub(super) fn solid_wall_capsules(&self) -> Vec<(u32, crate::contact_cache::CapsulePose)> {
        self.room_pets
            .iter()
            .filter(|(_, pet)| {
                pet.solid_wall && pet.vitals.as_ref().is_none_or(|v| v.death().is_none())
            })
            .map(|(&wire, pet)| {
                (
                    wire,
                    crate::contact_cache::CapsulePose {
                        position: pet.position.map(|v| v as f32),
                        radius: 0.78,
                        top: 2.6,
                    },
                )
            })
            .collect()
    }

    pub(super) fn despawn_room_pet(&mut self, pet_id: u32) {
        if let Some(pet) = self.room_pets.shift_remove(&pet_id) {
            let _ = self.world.despawn(pet.ecs_entity);
            Arc::make_mut(&mut self.retired_entity_ids).insert(pet_id);
            self.retired_enemies.push(pet_id);
        }
        // Clean up any flanking sub-pillars linked to this parent pet (e.g. Mei Ice Wall).
        let children: Vec<u32> = self
            .room_pets
            .iter()
            .filter(|(_, p)| p.parent_pet == Some(pet_id))
            .map(|(&id, _)| id)
            .collect();
        for child_id in children {
            if let Some(child) = self.room_pets.shift_remove(&child_id) {
                let _ = self.world.despawn(child.ecs_entity);
                Arc::make_mut(&mut self.retired_entity_ids).insert(child_id);
                self.retired_enemies.push(child_id);
            }
        }
    }

    pub(super) fn retire_owner_pets_and_deployables(&mut self, owner: u32) {
        let dead_pets: Vec<u32> = self
            .room_pets
            .iter()
            .filter(|(_, pet)| pet.owner == owner)
            .map(|(&id, _)| id)
            .collect();
        for pet_id in dead_pets {
            self.despawn_room_pet(pet_id);
        }
        let dead_deployables: Vec<u32> = self
            .room_deployables
            .iter()
            .filter(|(_, d)| d.owner == owner)
            .map(|(&id, _)| id)
            .collect();
        for id in dead_deployables {
            if let Some(dep) = self.room_deployables.shift_remove(&id) {
                let _ = self.world.despawn(dep.ecs_entity);
                Arc::make_mut(&mut self.retired_entity_ids).insert(id);
                self.retired_enemies.push(id);
            }
        }
    }

    pub(super) fn spawn_impact_deployable(
        &mut self,
        owner: u32,
        team: u8,
        spawn: ImpactSpawn,
        position: Vec3,
        yaw: i32,
        tick: u32,
    ) {
        // Bound active deployables per owner by retiring the oldest if needed.
        let owner_ids: Vec<u32> = self
            .room_deployables
            .iter()
            .filter(|(_, d)| d.owner == owner)
            .map(|(&id, _)| id)
            .collect();
        if owner_ids.len() >= MAX_DEPLOYABLES_PER_OWNER {
            if let Some(&oldest) = owner_ids.first() {
                if let Some(dep) = self.room_deployables.shift_remove(&oldest) {
                    let _ = self.world.despawn(dep.ecs_entity);
                    self.retired_enemies.push(oldest);
                }
            }
        }
        let entity = next_deployable_wire_id();
        let graphs_and_mode = self.deployable_graphs();
        let (vitals, authored_body_vitals) = resolve_entity_vitals(
            graphs_and_mode.as_ref().map(|(g, _)| g.as_ref()),
            spawn.definition,
            None,
        );
        let Some(update) = entity_create_update(
            entity,
            team,
            spawn.definition,
            None,
            position,
            yaw,
            None,
            vitals.as_ref(),
            authored_body_vitals,
        ) else {
            return;
        };
        let (component, world_opt) = if let Some((graphs, mode)) = graphs_and_mode {
            start_entity_vm(
                graphs,
                mode,
                entity,
                owner,
                team,
                spawn.definition,
                position,
                tick,
                self.spatial_frame.as_ref().map(|(_, f)| Arc::clone(f)),
                spawn.on_spawn_graph,
            )
        } else {
            (None, None)
        };
        let max_ticks = if component.is_some() {
            SCRIPTED_DEPLOYABLE_MAX_TICKS
        } else {
            SCRIPTLESS_DEPLOYABLE_TICKS
        };
        let ecs_entity = self.world.spawn_empty().id();
        self.room_deployables.insert(
            entity,
            RoomDeployable {
                owner,
                ecs_entity,
                team,
                definition: spawn.definition,
                position,
                yaw,
                vitals,
                wire_vitals: casc_verified_entity_components(
                    spawn.definition,
                    authored_body_vitals,
                )
                .1,
                spawned_tick: tick,
                max_ticks,
                component,
                world: world_opt,
            },
        );
        self.created.push(update);
    }

    pub(super) fn transfer_owner_pets_and_deployables(&mut self, old_owner: u32, new_owner: u32) {
        for pet in self.room_pets.values_mut() {
            if pet.owner == old_owner {
                pet.owner = new_owner;
            }
            if pet.attach == Some(old_owner) {
                pet.attach = Some(new_owner);
            }
        }
        for dep in self.room_deployables.values_mut() {
            if dep.owner == old_owner {
                dep.owner = new_owner;
            }
        }
    }

    pub(super) fn probe_barrier_ray(
        &self,
        attacker_team: Option<u8>,
        ffa: bool,
        origin: Vec3,
        direction: Vec3,
        max_dist: f64,
    ) -> Option<(u32, bool, f64, Vec3)> {
        Self::probe_barrier_ray_in(
            &self.room_pets,
            &self.room_deployables,
            attacker_team,
            ffa,
            origin,
            direction,
            max_dist,
        )
    }

    pub(super) fn probe_barrier_ray_in(
        room_pets: &IndexMap<u32, RoomPet>,
        room_deployables: &IndexMap<u32, RoomDeployable>,
        attacker_team: Option<u8>,
        ffa: bool,
        origin: Vec3,
        direction: Vec3,
        max_dist: f64,
    ) -> Option<(u32, bool, f64, Vec3)> {
        if max_dist <= 0.0 || !max_dist.is_finite() {
            return None;
        }
        let mut best: Option<(u32, bool, f64, Vec3)> = None;
        for (&wire, pet) in room_pets {
            let Some(vitals) = pet.vitals.as_ref() else {
                continue;
            };
            if vitals.death().is_some() {
                continue;
            }
            if !pet.solid_wall && !ffa && attacker_team == Some(pet.team) {
                continue;
            }
            let hit = if pet.attach.is_some() && vitals.maximum() >= 200.0 {
                // Oriented barrier panel in front of the owner (e.g. Reinhardt / Brigitte shields).
                let yaw = f64::from(pet.yaw) * std::f64::consts::TAU / 65_536.0;
                let (sin_y, cos_y) = yaw.sin_cos();
                let fwd = [sin_y, 0.0, cos_y];
                let right = [cos_y, 0.0, -sin_y];
                let offset = pet.local_offset.unwrap_or([0.0, 1.15, 1.1]);
                let fwd_dist = offset[2].max(0.85);
                let center = [
                    pet.position[0] + right[0] * offset[0] + fwd[0] * fwd_dist,
                    pet.position[1] + offset[1],
                    pet.position[2] + right[2] * offset[0] + fwd[2] * fwd_dist,
                ];
                let (half_w, half_h) = if vitals.maximum() >= 800.0 {
                    (2.8, 1.65)
                } else {
                    (0.95, 1.15)
                };
                let denom = direction[0] * fwd[0] + direction[2] * fwd[2];
                if denom.abs() > 1e-5 {
                    let t = ((center[0] - origin[0]) * fwd[0] + (center[2] - origin[2]) * fwd[2])
                        / denom;
                    if t >= 0.0 && t <= max_dist {
                        let p = [
                            origin[0] + direction[0] * t,
                            origin[1] + direction[1] * t,
                            origin[2] + direction[2] * t,
                        ];
                        let dx = p[0] - center[0];
                        let dy = p[1] - center[1];
                        let dz = p[2] - center[2];
                        let along_r = dx * right[0] + dz * right[2];
                        if along_r.abs() <= half_w && dy.abs() <= half_h {
                            let normal = if denom < 0.0 {
                                fwd
                            } else {
                                [-fwd[0], 0.0, -fwd[2]]
                            };
                            Some((t, normal))
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                } else {
                    None
                }
            } else {
                let (radius, height): (f64, f64) = if vitals.maximum() >= 250.0 {
                    (1.9, 2.5)
                } else {
                    (0.65, 1.4)
                };
                let bottom = [
                    pet.position[0],
                    pet.position[1] + radius.min(0.6),
                    pet.position[2],
                ];
                let top = [pet.position[0], pet.position[1] + height, pet.position[2]];
                crate::static_ray::capsule_entry_distance(
                    origin, direction, max_dist, bottom, top, radius,
                )
                .map(|dist| {
                    let hit_pt = crate::static_ray::along(origin, direction, dist);
                    let nx = hit_pt[0] - pet.position[0];
                    let nz = hit_pt[2] - pet.position[2];
                    let nlen = nx.hypot(nz).max(1e-5);
                    (dist, [nx / nlen, 0.0, nz / nlen])
                })
            };
            if let Some((dist, normal)) = hit {
                if best.is_none_or(|(_, _, prev, _)| dist < prev) {
                    best = Some((wire, true, dist, normal));
                }
            }
        }
        for (&wire, dep) in room_deployables {
            let Some(vitals) = dep.vitals.as_ref() else {
                continue;
            };
            if vitals.death().is_some() {
                continue;
            }
            if !ffa && attacker_team == Some(dep.team) {
                continue;
            }
            let (radius, height) = if vitals.maximum() >= 250.0 {
                (1.6, 2.3)
            } else {
                (0.55, 1.1)
            };
            let bottom = [dep.position[0], dep.position[1] + 0.3, dep.position[2]];
            let top = [dep.position[0], dep.position[1] + height, dep.position[2]];
            if let Some(dist) = crate::static_ray::capsule_entry_distance(
                origin, direction, max_dist, bottom, top, radius,
            ) {
                if best.is_none_or(|(_, _, prev, _)| dist < prev) {
                    let hit_pt = crate::static_ray::along(origin, direction, dist);
                    let nx = hit_pt[0] - dep.position[0];
                    let nz = hit_pt[2] - dep.position[2];
                    let nlen = nx.hypot(nz).max(1e-5);
                    best = Some((wire, false, dist, [nx / nlen, 0.0, nz / nlen]));
                }
            }
        }
        best
    }

    pub(super) fn damage_barrier_or_deployable(
        &mut self,
        wire: u32,
        is_pet: bool,
        damage: f64,
        source: u32,
        tick: u32,
        now: Duration,
    ) -> f64 {
        if damage <= 0.0 || !damage.is_finite() {
            return 0.0;
        }
        let death = Death {
            time: now.as_secs_f64(),
            frame: tick,
            killer: source,
        };
        let health = if is_pet {
            self.room_pets
                .get_mut(&wire)
                .and_then(|pet| pet.vitals.as_mut().map(|vitals| (vitals, pet.wire_vitals)))
        } else {
            self.room_deployables
                .get_mut(&wire)
                .and_then(|dep| dep.vitals.as_mut().map(|vitals| (vitals, dep.wire_vitals)))
        };
        let Some((vitals, wire_vitals)) = health else {
            return 0.0;
        };
        if vitals.death().is_some() {
            return 0.0;
        }
        let outcome = vitals.damage(damage, death);
        if outcome.dealt > 0.0 || outcome.killed {
            self.scripted_health
                .extend(take_entity_health(wire, vitals, wire_vitals));
        }
        outcome.dealt
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn intercept_barrier_ray(
        &mut self,
        attacker_team: Option<u8>,
        ffa: bool,
        origin: Vec3,
        direction: Vec3,
        max_dist: f64,
        damage: f64,
        source: u32,
        tick: u32,
        now: Duration,
    ) -> Option<(u32, f64)> {
        let (wire, is_pet, dist, _) =
            self.probe_barrier_ray(attacker_team, ffa, origin, direction, max_dist)?;
        self.damage_barrier_or_deployable(wire, is_pet, damage, source, tick, now);
        Some((wire, dist))
    }

    /// Check whether a player's shot intersects one of their own active throwable
    /// explosive deployables (e.g. Ashe Dynamite) and detonate it immediately with
    /// splash + burn DoT.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn detonate_owner_deployable_ray(
        &mut self,
        shooter: u32,
        shooter_team: u8,
        ffa: bool,
        origin: Vec3,
        direction: Vec3,
        max_dist: f64,
        tick: u32,
        now: Duration,
    ) -> Option<f64> {
        if self.room_deployables.is_empty() || max_dist <= 0.0 {
            return None;
        }
        let mut hit: Option<(u32, f64, Vec3)> = None;
        for (&wire, dep) in &self.room_deployables {
            // Only owner-shootable unarmored/low-HP throwables (not turrets/lamps with >= 100 HP).
            if dep.owner != shooter || dep.vitals.as_ref().is_some_and(|v| v.maximum() >= 100.0) {
                continue;
            }
            let center = [dep.position[0], dep.position[1] + 0.25, dep.position[2]];
            if let Some(dist) = crate::static_ray::capsule_entry_distance(
                origin, direction, max_dist, center, center, 0.45,
            ) {
                if hit.is_none_or(|(_, prev, _)| dist < prev) {
                    hit = Some((wire, dist, center));
                }
            }
        }
        let (wire, dist, center) = hit?;
        if let Some(dep) = self.room_deployables.shift_remove(&wire) {
            let _ = self.world.despawn(dep.ecs_entity);
            Arc::make_mut(&mut self.retired_entity_ids).insert(wire);
            self.retired_enemies.push(wire);
        }
        let radius = 5.0_f64;
        let targets: Vec<(u32, f64)> = self
            .spatial_frame
            .as_ref()
            .map(|(_, frame)| {
                frame
                    .candidates()
                    .iter()
                    .filter(|c| ffa || c.team != Some(shooter_team) || c.entity == shooter)
                    .filter_map(|c| {
                        let dx = c.position[0] - center[0];
                        let dy = (c.position[1] + 0.9) - center[1];
                        let dz = c.position[2] - center[2];
                        let d = dx.hypot(dy).hypot(dz);
                        (d <= radius).then_some((c.entity, d))
                    })
                    .collect()
            })
            .unwrap_or_default();
        let death = Death {
            time: now.as_secs_f64(),
            frame: tick,
            killer: shooter,
        };
        let burn_ticks: u16 = 310; // ~5.0s at 62Hz
        let burn_per_tick = 100.0 / f64::from(burn_ticks);
        for (target, d) in targets {
            let falloff = (1.0 - (d / radius) * 0.6).clamp(0.4, 1.0);
            let burst = if target == shooter {
                37.5 * falloff
            } else {
                75.0 * falloff
            };
            let _ = self.apply_health(
                target,
                crate::game_combat::HealthEffect::Damage(burst),
                death,
                now,
                None,
            );
            let entry = self.actor_statuses.entry(target).or_default();
            entry.dot_ticks = burn_ticks;
            entry.dot_per_tick = if target == shooter {
                burn_per_tick * 0.5
            } else {
                burn_per_tick
            };
            entry.dot_source = shooter;
        }
        Some(dist)
    }

    /// Immortality Field check: if a living actor stands within 6.5m of an allied
    /// grounded deployable with a retail lamp health pool (`120..=260` HP), return
    /// the 20% minimum HP clamp floor.
    pub(super) fn immortality_floor_active(&self, position: Vec3, team: u8) -> bool {
        let near = |dep_pos: Vec3| {
            let dx = dep_pos[0] - position[0];
            let dy = dep_pos[1] - position[1];
            let dz = dep_pos[2] - position[2];
            dx.hypot(dz) <= 6.5 && dy.abs() <= 4.0
        };
        self.room_deployables.values().any(|dep| {
            dep.team == team
                && dep
                    .vitals
                    .as_ref()
                    .is_some_and(|v| v.death().is_none() && (120.0..=260.0).contains(&v.maximum()))
                && near(dep.position)
        }) || self.room_pets.values().any(|pet| {
            pet.team == team
                && pet.attach.is_none()
                && pet
                    .vitals
                    .as_ref()
                    .is_some_and(|v| v.death().is_none() && (120.0..=260.0).contains(&v.maximum()))
                && near(pet.position)
        })
    }

    /// Teleporter interact (`F`): when an allied player presses Interact within 2.5m
    /// of one pad of a paired allied deployable/pet (e.g. Symmetra Teleporter),
    /// return the paired exit pad position.
    pub(super) fn teleporter_exit_for(&self, position: Vec3, team: u8) -> Option<Vec3> {
        let pads: Vec<(u32, u64, Vec3)> = self
            .room_pets
            .iter()
            .filter(|(_, p)| p.team == team && p.attach.is_none())
            .map(|(&w, p)| (w, p.definition, p.position))
            .chain(
                self.room_deployables
                    .iter()
                    .filter(|(_, d)| d.team == team)
                    .map(|(&w, d)| (w, d.definition, d.position)),
            )
            .collect();
        for &(wire, def, pos) in &pads {
            let dist = (pos[0] - position[0])
                .hypot(pos[2] - position[2])
                .hypot(pos[1] - position[1]);
            if dist <= 2.5 {
                if let Some(&(_, _, dest)) = pads
                    .iter()
                    .find(|&&(other_w, other_def, _)| other_w != wire && other_def == def)
                {
                    return Some([dest[0], dest[1] + 0.1, dest[2]]);
                }
            }
        }
        None
    }

    pub(super) fn advance_deployables(&mut self, tick: u32) {
        self.step_status_effects(tick);
        self.advance_scheduled_voices(tick);
        self.advance_progression_and_replay(tick);
        self.step_hero_mechanics(tick);
        let frame = self.spatial_frame.as_ref().map(|(_, f)| Arc::clone(f));

        // Step active room pets (barriers, turrets, walls, teleporters).
        if !self.room_pets.is_empty() {
            let mut dead_pets = Vec::new();
            let attached_poses: Vec<(u32, Vec3, i32)> = self
                .room_pets
                .iter()
                .filter_map(|(&id, pet)| {
                    let parent = pet.attach?;
                    let (pos, angles) = frame.as_deref()?.actor_pose(parent)?;
                    Some((id, pos, angles[0]))
                })
                .collect();
            for (id, pos, yaw) in attached_poses {
                if let Some(pet) = self.room_pets.get_mut(&id) {
                    pet.position = pos;
                    pet.yaw = yaw;
                }
            }
            for (&id, pet) in &mut self.room_pets {
                if let (Some(component), Some(world)) = (pet.component.as_mut(), pet.world.as_mut())
                {
                    world.tick = tick;
                    world.position = pet.position;
                    world.spatial_frame = frame.clone();
                    if component.advance_unposed(tick, false, world).is_err() {
                        pet.component = None;
                    } else if let Some(shots) = component.take_reported_shots() {
                        if !shots.is_empty() {
                            queue_deployable_volley_hits(component, world, &shots, tick);
                        }
                    }
                }
                if let Some(vitals) = pet.vitals.as_mut() {
                    self.scripted_health
                        .extend(take_entity_health(id, vitals, pet.wire_vitals));
                    if vitals.death().is_some() {
                        dead_pets.push(id);
                    }
                }
            }
            for id in dead_pets {
                self.despawn_room_pet(id);
            }
        }

        if self.room_deployables.is_empty() {
            return;
        }
        let mut expired = Vec::new();
        for (&id, deployable) in &mut self.room_deployables {
            if let (Some(component), Some(world)) =
                (deployable.component.as_mut(), deployable.world.as_mut())
            {
                world.tick = tick;
                world.spatial_frame = frame.clone();
                if component.advance_unposed(tick, false, world).is_err() {
                    deployable.component = None;
                } else if let Some(shots) = component.take_reported_shots() {
                    if !shots.is_empty() {
                        queue_deployable_volley_hits(component, world, &shots, tick);
                    }
                }
            }
            if let Some(vitals) = deployable.vitals.as_mut() {
                self.scripted_health
                    .extend(take_entity_health(id, vitals, deployable.wire_vitals));
            }
            if deployable.is_expired(tick) {
                expired.push(id);
            }
        }
        for id in expired {
            if let Some(dep) = self.room_deployables.shift_remove(&id) {
                let _ = self.world.despawn(dep.ecs_entity);
                Arc::make_mut(&mut self.retired_entity_ids).insert(id);
                self.retired_enemies.push(id);
            }
        }
    }

    /// Step specialized data-driven hero mechanics each tick:
    /// - Autonomous combat turrets / B.O.B. (charge + knockup + LOS target acquisition & cadence fire)
    /// - Gliding Self-Destruct bombs (3s glide + 1000-dmg 20m LOS/barrier-occluded blast)
    /// - Echo Duplicate 15s expiry revert
    /// - Mercy Resurrect soul anchor expiry
    fn step_hero_mechanics(&mut self, tick: u32) {
        let now =
            Duration::from_secs_f64(f64::from(tick) * crate::game_clock::TICK_PERIOD.as_secs_f64());
        let dt = crate::game_clock::TICK_PERIOD.as_secs_f64();

        // 1. Expire old fallen-ally Soul Anchors (10s window).
        self.soul_anchors
            .retain(|anchor| tick < anchor.expires_tick);

        // 2. Expire timed Echo Duplicates -> revert cleanly to original hero body.
        let expired_dups: Vec<(u32, u64)> = self
            .active_duplicates
            .iter()
            .filter(|(_, dup)| tick >= dup.expires_tick)
            .map(|(&owner, dup)| (owner, dup.original_body))
            .collect();
        for (owner, orig_body) in expired_dups {
            self.active_duplicates.remove(&owner);
            self.pending_duplicate_reverts.insert(owner, orig_body);
        }

        // 3. Step gliding Self-Destruct bombs and detonate with LOS + barrier occlusion.
        let mut detonated_bombs = Vec::new();
        let frame_arc = self.spatial_frame.as_ref().map(|(_, f)| Arc::clone(f));
        for bomb in &mut self.self_destruct_bombs {
            let speed = bomb.velocity[0].hypot(bomb.velocity[2]);
            if speed > 0.1 {
                let step = [
                    bomb.velocity[0] * dt,
                    bomb.velocity[1] * dt,
                    bomb.velocity[2] * dt,
                ];
                let step_len = step[0].hypot(step[1]).hypot(step[2]);
                let blocked = if step_len > 1e-4 {
                    let dir = [step[0] / step_len, step[1] / step_len, step[2] / step_len];
                    frame_arc.as_deref().is_some_and(|frame| {
                        let origin = [bomb.position[0], bomb.position[1] + 0.9, bomb.position[2]];
                        let site = Site {
                            entity: bomb.owner,
                            instance: 0,
                            graph: 0,
                            node: None,
                        };
                        frame
                            .ray_cast(site, tick, origin, dir, step_len + 0.7)
                            .flatten()
                            .is_some()
                    })
                } else {
                    false
                };
                if blocked {
                    bomb.velocity = [0.0; 3];
                } else {
                    bomb.position[0] += step[0];
                    bomb.position[1] += step[1];
                    bomb.position[2] += step[2];
                    // Gentle drag + gravity settling
                    bomb.velocity[0] *= 0.97;
                    bomb.velocity[2] *= 0.97;
                }
            }
            if tick >= bomb.detonate_tick {
                detonated_bombs.push(*bomb);
            }
        }
        self.self_destruct_bombs
            .retain(|bomb| tick < bomb.detonate_tick);

        for bomb in detonated_bombs {
            let origin = [bomb.position[0], bomb.position[1] + 1.0, bomb.position[2]];
            let radius = 20.0_f64;
            let targets: Vec<(u32, Vec3, f64)> = frame_arc
                .as_deref()
                .map(|frame| {
                    frame
                        .candidates()
                        .iter()
                        .filter(|c| self.free_for_all || c.team != Some(bomb.team))
                        .filter_map(|c| {
                            let target_pos = [c.position[0], c.position[1] + 1.0, c.position[2]];
                            let dx = target_pos[0] - origin[0];
                            let dy = target_pos[1] - origin[1];
                            let dz = target_pos[2] - origin[2];
                            let d = dx.hypot(dy).hypot(dz);
                            (d <= radius).then_some((c.entity, target_pos, d))
                        })
                        .collect()
                })
                .unwrap_or_default();
            let death = Death {
                time: now.as_secs_f64(),
                frame: tick,
                killer: bomb.owner,
            };
            for (target, target_pos, d) in targets {
                let dir = if d > 1e-4 {
                    [
                        (target_pos[0] - origin[0]) / d,
                        (target_pos[1] - origin[1]) / d,
                        (target_pos[2] - origin[2]) / d,
                    ]
                } else {
                    [0.0, 1.0, 0.0]
                };
                // Strict world LOS check
                let world_blocked = d > 0.5
                    && frame_arc.as_deref().is_some_and(|frame| {
                        let site = Site {
                            entity: bomb.owner,
                            instance: 0,
                            graph: 0,
                            node: None,
                        };
                        frame
                            .ray_cast(site, tick, origin, dir, d - 0.35)
                            .flatten()
                            .is_some()
                    });
                if world_blocked {
                    continue;
                }
                // Barrier occlusion (Reinhardt/Sigma/Orisa/Winston/Mei Wall blocks Self-Destruct!)
                if let Some((barrier_wire, is_pet, bdist, _)) =
                    self.probe_barrier_ray(Some(bomb.team), self.free_for_all, origin, dir, d)
                {
                    if bdist < d {
                        let dmg = (1000.0 * (1.0 - (bdist / radius) * 0.9)).clamp(100.0, 1000.0);
                        self.damage_barrier_or_deployable(
                            barrier_wire,
                            is_pet,
                            dmg,
                            bomb.owner,
                            tick,
                            now,
                        );
                        continue;
                    }
                }
                let dmg = (1000.0 * (1.0 - (d / radius) * 0.9)).clamp(100.0, 1000.0);
                let _ = self.apply_health(
                    target,
                    crate::game_combat::HealthEffect::Damage(dmg),
                    death,
                    now,
                    None,
                );
                let _ =
                    self.queue_combat_impulse(target, tick, [dir[0] * 12.0, 7.5, dir[2] * 12.0]);
            }
        }

        // 4. Autonomous combat pets (Torbjörn Turret 250 HP & Ashe B.O.B. >= 800 HP) +
        //    Junkrat Steel Trap proximity root from HeroAbilityRules.
        if let Some(frame) = frame_arc {
            let trap_rule = self.ability_rules.junkrat_trap.clone();
            let mut triggered_traps: Vec<(u32, u32, u32)> = Vec::new();
            for (&dep_wire, dep) in &self.room_deployables {
                let is_trap = trap_rule.matches_entity(dep.definition)
                    || dep.vitals.as_ref().is_some_and(|v| {
                        (90.0..=110.0).contains(&v.maximum()) && dep.component.is_none()
                    });
                if !is_trap || dep.vitals.as_ref().is_some_and(|v| v.death().is_some()) {
                    continue;
                }
                for c in frame.candidates() {
                    if c.entity == dep.owner || c.entity == dep_wire {
                        continue;
                    }
                    if !self.free_for_all && c.team == Some(dep.team) {
                        continue;
                    }
                    let dist =
                        (c.position[0] - dep.position[0]).hypot(c.position[2] - dep.position[2]);
                    if dist <= trap_rule.trigger_radius
                        && (c.position[1] - dep.position[1]).abs() <= trap_rule.trigger_height
                    {
                        triggered_traps.push((dep_wire, dep.owner, c.entity));
                        break;
                    }
                }
            }
            for (trap_wire, trap_owner, victim) in triggered_traps {
                if let Some(dep) = self.room_deployables.shift_remove(&trap_wire) {
                    let _ = self.world.despawn(dep.ecs_entity);
                    Arc::make_mut(&mut self.retired_entity_ids).insert(trap_wire);
                    self.retired_enemies.push(trap_wire);
                }
                let death = Death {
                    time: now.as_secs_f64(),
                    frame: tick,
                    killer: trap_owner,
                };
                let _ = self.apply_health(
                    victim,
                    crate::game_combat::HealthEffect::Damage(trap_rule.damage),
                    death,
                    now,
                    None,
                );
                let st = self.actor_statuses.entry(victim).or_default();
                st.rooted_until = Some(
                    st.rooted_until
                        .unwrap_or(0)
                        .max(tick.saturating_add(trap_rule.root_ticks)),
                );
            }

            let mut ai_hits: Vec<(u32, u32, f64, Option<Vec3>)> = Vec::new();
            for (&pet_wire, pet) in &mut self.room_pets {
                if pet.attach.is_some() || pet.solid_wall {
                    continue;
                }
                let Some(max_hp) = pet
                    .vitals
                    .as_ref()
                    .filter(|v| v.death().is_none())
                    .map(Vitals::maximum)
                else {
                    continue;
                };
                let is_bob = max_hp >= 800.0;
                let is_turret = (180.0..=300.0).contains(&max_hp) && pet.component.is_none();
                if !is_bob && !is_turret {
                    continue;
                }
                if is_bob && pet.charge_ticks > 0 {
                    pet.charge_ticks -= 1;
                    let rad = f64::from(pet.yaw) * std::f64::consts::TAU / 65_536.0;
                    let (sin_y, cos_y) = rad.sin_cos();
                    let dir = [sin_y, 0.0, cos_y];
                    let step = 15.0 * dt;
                    let origin = [pet.position[0], pet.position[1] + 1.0, pet.position[2]];
                    let site = Site {
                        entity: pet_wire,
                        instance: 0,
                        graph: 0,
                        node: None,
                    };
                    let hit_wall = frame
                        .ray_cast(site, tick, origin, dir, step + 0.85)
                        .flatten()
                        .is_some();
                    if hit_wall {
                        pet.charge_ticks = 0;
                    } else {
                        pet.position[0] += dir[0] * step;
                        pet.position[2] += dir[2] * step;
                        pet.published_movement = true;
                        for c in frame.candidates() {
                            if c.entity == pet.owner || c.entity == pet_wire {
                                continue;
                            }
                            if !self.free_for_all && c.team == Some(pet.team) {
                                continue;
                            }
                            let dist = (c.position[0] - pet.position[0])
                                .hypot(c.position[2] - pet.position[2]);
                            if dist <= 2.1 && (c.position[1] - pet.position[1]).abs() <= 2.2 {
                                ai_hits.push((
                                    pet.owner,
                                    c.entity,
                                    120.0,
                                    Some([dir[0] * 4.0, 9.5, dir[2] * 4.0]),
                                ));
                                pet.charge_ticks = 0;
                                break;
                            }
                        }
                    }
                    continue;
                }

                if pet.ai_fire_cooldown > 0 {
                    pet.ai_fire_cooldown -= 1;
                    continue;
                }
                let origin = [pet.position[0], pet.position[1] + 1.1, pet.position[2]];
                let site = Site {
                    entity: pet_wire,
                    instance: 0,
                    graph: 0,
                    node: None,
                };
                let mut best: Option<(f64, u32, f64, f64)> = None;
                for c in frame.candidates() {
                    if c.entity == pet.owner || c.entity == pet_wire {
                        continue;
                    }
                    if !self.free_for_all && c.team == Some(pet.team) {
                        continue;
                    }
                    let tpos = [c.position[0], c.position[1] + 1.0, c.position[2]];
                    let dx = tpos[0] - origin[0];
                    let dy = tpos[1] - origin[1];
                    let dz = tpos[2] - origin[2];
                    let dist = dx.hypot(dy).hypot(dz);
                    if dist <= 0.1 || dist > 40.0 || best.is_some_and(|(prev, ..)| dist >= prev) {
                        continue;
                    }
                    let dir = [dx / dist, dy / dist, dz / dist];
                    if frame
                        .ray_cast(site, tick, origin, dir, dist - 0.3)
                        .flatten()
                        .is_some()
                    {
                        continue;
                    }
                    best = Some((dist, c.entity, dx, dz));
                }
                if let Some((_, target, dx, dz)) = best {
                    let yaw_rad = dx.atan2(dz);
                    let new_yaw = (yaw_rad * 65_536.0 / std::f64::consts::TAU).round() as i32;
                    if new_yaw != pet.yaw {
                        pet.yaw = new_yaw;
                        pet.published_movement = true;
                    }
                    pet.ai_fire_cooldown = if is_bob { 8 } else { 15 };
                    ai_hits.push((pet.owner, target, 14.0, None));
                } else {
                    pet.ai_fire_cooldown = 6;
                }
            }
            for (owner, target, dmg, impulse) in ai_hits {
                let death = Death {
                    time: now.as_secs_f64(),
                    frame: tick,
                    killer: owner,
                };
                let _ = self.apply_health(
                    target,
                    crate::game_combat::HealthEffect::Damage(dmg),
                    death,
                    now,
                    None,
                );
                if let Some(imp) = impulse {
                    let _ = self.queue_combat_impulse(target, tick, imp);
                }
            }
        }
    }

    /// Scale continuous beam weapon ticks cleanly from `HeroAbilityRules`:
    /// - Symmetra LMB: 1.0x -> 2.0x -> 3.0x (`beam_charge` ramp)
    /// - Echo Focusing Beam: execute multiplier when target HP <= configured ratio
    pub(super) fn scale_continuous_beam_damage(
        &mut self,
        source_wire: u32,
        target_wire: u32,
        base_damage: f64,
        tick: u32,
    ) -> f64 {
        let beam = self.ability_rules.beam_scaling;
        if base_damage <= 0.0
            || base_damage > beam.max_base_tick_damage
            || source_wire == target_wire
        {
            return base_damage;
        }
        let source_body = self
            .players_by_wire
            .get(&source_wire)
            .and_then(|&ent| self.world.get::<super::PlayerBodyState>(ent))
            .map(|p| p.body_template);
        if source_body == Some(beam.symmetra_body) {
            let st = self.actor_statuses.entry(source_wire).or_default();
            st.beam_charge =
                (st.beam_charge + beam.symmetra_charge_per_tick).min(beam.symmetra_max_charge);
            st.beam_last_hit_tick = tick;
            let mult = 1.0 + f64::from(st.beam_charge.floor());
            base_damage * mult
        } else if source_body == Some(beam.echo_body) {
            let low_hp = self
                .by_wire
                .get(&target_wire)
                .and_then(|&ent| self.world.get::<super::BotState>(ent))
                .is_some_and(|b| {
                    b.vitals.total() <= b.vitals.maximum() * beam.echo_execute_hp_ratio
                })
                || self
                    .players_by_wire
                    .get(&target_wire)
                    .and_then(|&ent| self.world.get::<super::PlayerBodyState>(ent))
                    .and_then(|p| p.vitals())
                    .is_some_and(|v| v.total() <= v.maximum() * beam.echo_execute_hp_ratio);
            if low_hp {
                base_damage * beam.echo_execute_multiplier
            } else {
                base_damage
            }
        } else {
            base_damage
        }
    }

    /// Engage Roadhog 2-stage Chain Hook pull (with LOS check in `step_status_effects`).
    pub(super) fn attach_roadhog_hook_pull(
        &mut self,
        puller_wire: u32,
        victim_wire: u32,
        tick: u32,
    ) {
        if puller_wire == victim_wire || self.actor_is_cc_immune(victim_wire, tick) {
            return;
        }
        let hook = self.ability_rules.roadhog_hook;
        let Some((puller_pos, puller_yaw)) = self
            .players_by_wire
            .get(&puller_wire)
            .and_then(|&ent| self.world.get::<super::PlayerBodyState>(ent))
            .map(|p| {
                let pose = p.mover.pose();
                (pose.position, pose.yaw)
            })
        else {
            return;
        };
        let Some(victim_pos) = self
            .sample_health_body(victim_wire)
            .and_then(|s| s.position(&self.world))
        else {
            return;
        };
        let dist = (victim_pos[0] - puller_pos[0]).hypot(victim_pos[2] - puller_pos[2]);
        if !(hook.min_distance..=hook.max_distance).contains(&dist) {
            return;
        }
        let rad = f64::from(puller_yaw) * std::f64::consts::TAU / 65_536.0;
        let (sin_y, cos_y) = rad.sin_cos();
        let dest = [
            puller_pos[0] + sin_y * hook.pull_stop_distance,
            puller_pos[1],
            puller_pos[2] + cos_y * hook.pull_stop_distance,
        ];
        self.apply_actor_stun(victim_wire, tick, hook.pull_ticks);
        let st = self.actor_statuses.entry(victim_wire).or_default();
        st.hook_pull = Some((puller_wire, dest, tick.saturating_add(hook.pull_ticks)));
    }

    /// Detonate owner's oldest Junkrat Concussion Mine for AoE damage +
    /// knockback and zero-self-damage mine-jumping impulse.
    pub(super) fn detonate_owner_concussion_mine(
        &mut self,
        owner_wire: u32,
        tick: u32,
        now: Duration,
    ) {
        let mine = self.ability_rules.junkrat_mine;
        let Some((mine_wire, mine_team, center)) = self
            .room_deployables
            .iter()
            .find(|(_, d)| d.owner == owner_wire && d.definition == mine.entity)
            .map(|(&w, d)| (w, d.team, d.position))
        else {
            return;
        };
        if let Some(dep) = self.room_deployables.shift_remove(&mine_wire) {
            let _ = self.world.despawn(dep.ecs_entity);
            Arc::make_mut(&mut self.retired_entity_ids).insert(mine_wire);
            self.retired_enemies.push(mine_wire);
        }
        let targets: Vec<(u32, Vec3, f64)> = self
            .spatial_frame
            .as_ref()
            .map(|(_, frame)| {
                frame
                    .candidates()
                    .iter()
                    .filter(|c| {
                        c.entity == owner_wire || self.free_for_all || c.team != Some(mine_team)
                    })
                    .filter_map(|c| {
                        let d = (c.position[0] - center[0])
                            .hypot(c.position[2] - center[2])
                            .hypot((c.position[1] + 0.8) - center[1]);
                        (d <= mine.blast_radius).then_some((c.entity, c.position, d))
                    })
                    .collect()
            })
            .unwrap_or_default();
        let death = Death {
            time: now.as_secs_f64(),
            frame: tick,
            killer: owner_wire,
        };
        let falloff_span = 1.0 - mine.min_damage_ratio;
        for (target, pos, d) in targets {
            let dx = pos[0] - center[0];
            let dz = pos[2] - center[2];
            let hlen = dx.hypot(dz).max(1e-4);
            if target == owner_wire {
                // Zero self-damage mine jump!
                let _ = self.queue_combat_impulse(
                    owner_wire,
                    tick,
                    [
                        dx / hlen * mine.self_horizontal_impulse,
                        mine.self_vertical_impulse,
                        dz / hlen * mine.self_horizontal_impulse,
                    ],
                );
            } else {
                let falloff = (1.0 - (d / mine.blast_radius) * falloff_span)
                    .clamp(mine.min_damage_ratio, 1.0);
                let _ = self.apply_health(
                    target,
                    crate::game_combat::HealthEffect::Damage(mine.max_damage * falloff),
                    death,
                    now,
                    None,
                );
                let _ = self.queue_combat_impulse(
                    target,
                    tick,
                    [
                        dx / hlen * mine.enemy_horizontal_impulse,
                        mine.enemy_vertical_impulse,
                        dz / hlen * mine.enemy_horizontal_impulse,
                    ],
                );
            }
        }
    }

    /// Activate Echo Duplicate (`STUStatescriptStateCreateEntity` -> `STU_0AC15F78`):
    /// look up `target_wire`'s hero body definition, schedule body swap into that hero,
    /// and register the 15s / 0-HP revert back to `original_body`.
    pub(super) fn activate_echo_duplicate(
        &mut self,
        echo_wire: u32,
        target_wire: u32,
        original_body: u64,
        tick: u32,
    ) {
        let Some(owner) = self
            .players_by_wire
            .get(&echo_wire)
            .and_then(|&ent| self.world.get::<super::PlayerBodyState>(ent))
            .map(|p| p.owner)
        else {
            return;
        };
        let target_body = self
            .players_by_wire
            .get(&target_wire)
            .and_then(|&ent| self.world.get::<super::PlayerBodyState>(ent))
            .map(|p| p.body_template)
            .or_else(|| {
                self.by_wire
                    .get(&target_wire)
                    .and_then(|&ent| self.world.get::<super::BotState>(ent))
                    .map(|b| b.bot.spawn.body)
            });
        let Some(target_body) = target_body.filter(|&b| b != 0 && b != original_body) else {
            return;
        };
        let valid_body = self
            .deployable_graphs()
            .is_some_and(|(g, _)| g.entity_body(target_body).is_some());
        if !valid_body {
            return;
        }
        self.start_echo_duplicate(owner, original_body, tick);
        self.pending_duplicate_reverts.insert(owner, target_body);
    }

    /// Mercy Resurrect: revive the nearest fallen ally soul anchor within 5.5m
    /// by clearing their respawn timer immediately.
    pub(super) fn try_resurrect_nearest_ally(
        &mut self,
        healer_team: u8,
        healer_pos: Vec3,
        tick: u32,
        now: Duration,
    ) -> Option<super::SoulAnchor> {
        let idx = self
            .soul_anchors
            .iter()
            .enumerate()
            .filter(|(_, a)| a.team == healer_team && tick < a.expires_tick)
            .filter_map(|(i, a)| {
                let d = (a.position[0] - healer_pos[0])
                    .hypot(a.position[2] - healer_pos[2])
                    .hypot(a.position[1] - healer_pos[1]);
                (d <= 5.5).then_some((i, d))
            })
            .min_by(|a, b| a.1.total_cmp(&b.1))
            .map(|(i, _)| i)?;
        let anchor = self.soul_anchors.swap_remove(idx);
        self.schedule_player_respawn(anchor.body, now);
        Some(anchor)
    }

    /// Register an active Echo Duplicate transform.
    pub(super) fn start_echo_duplicate(&mut self, owner: u32, original_body: u64, tick: u32) {
        let duration = self.ability_rules.echo_duplicate.duration_ticks;
        self.active_duplicates.insert(
            owner,
            super::DuplicateState {
                original_body,
                expires_tick: tick.saturating_add(duration),
            },
        );
    }

    pub(crate) fn take_pending_duplicate_revert(&mut self, owner: u32) -> Option<u64> {
        self.pending_duplicate_reverts.remove(&owner)
    }

    /// Spawn a gliding D.Va Self-Destruct bomb that explodes after its configured fuse.
    pub(crate) fn spawn_self_destruct_bomb(
        &mut self,
        owner: u32,
        team: u8,
        position: Vec3,
        velocity: Vec3,
        tick: u32,
    ) {
        let fuse = self.ability_rules.dva_mech.self_destruct_fuse_ticks;
        self.self_destruct_bombs.push(super::SelfDestructBomb {
            owner,
            team,
            position,
            velocity,
            detonate_tick: tick.saturating_add(fuse),
        });
    }

    /// D.Va Call Mech crush: AoE damage + knockback to nearby enemies.
    pub(crate) fn apply_call_mech_crush(
        &mut self,
        owner_wire: u32,
        team: u8,
        center: Vec3,
        tick: u32,
        now: Duration,
    ) {
        let dva = self.ability_rules.dva_mech;
        let targets: Vec<(u32, Vec3)> = self
            .spatial_frame
            .as_ref()
            .map(|(_, frame)| {
                frame
                    .candidates()
                    .iter()
                    .filter(|c| {
                        c.entity != owner_wire && (self.free_for_all || c.team != Some(team))
                    })
                    .filter_map(|c| {
                        let d = (c.position[0] - center[0])
                            .hypot(c.position[2] - center[2])
                            .hypot(c.position[1] - center[1]);
                        (d <= dva.call_mech_crush_radius).then_some((c.entity, c.position))
                    })
                    .collect()
            })
            .unwrap_or_default();
        let death = Death {
            time: now.as_secs_f64(),
            frame: tick,
            killer: owner_wire,
        };
        for (target, pos) in targets {
            let dx = pos[0] - center[0];
            let dz = pos[2] - center[2];
            let len = dx.hypot(dz).max(1e-4);
            let _ = self.apply_health(
                target,
                crate::game_combat::HealthEffect::Damage(dva.call_mech_crush_damage),
                death,
                now,
                None,
            );
            let _ = self.queue_combat_impulse(target, tick, [dx / len * 9.0, 5.5, dz / len * 9.0]);
        }
    }

    pub(super) fn drain_deployable_script_requests(
        &mut self,
    ) -> Vec<(u32, Vec<RoomScriptRequest>)> {
        let mut out = Vec::new();
        for pet in self.room_pets.values_mut() {
            if let Some(world) = pet.world.as_mut() {
                let reqs = world.script_requests.take();
                if !reqs.is_empty() {
                    out.push((pet.owner, reqs));
                }
            }
        }
        for deployable in self.room_deployables.values_mut() {
            if let Some(world) = deployable.world.as_mut() {
                let reqs = world.script_requests.take();
                if !reqs.is_empty() {
                    out.push((deployable.owner, reqs));
                }
            }
        }
        out
    }

    #[cfg(test)]
    pub(crate) fn active_room_pets(&self) -> usize {
        self.room_pets.len()
    }

    #[cfg(test)]
    pub(crate) fn active_room_deployables(&self) -> usize {
        self.room_deployables.len()
    }
}

fn queue_deployable_volley_hits(
    component: &Component,
    world: &mut DeployableWorld,
    shots: &[ReportedShot],
    tick: u32,
) {
    let Some(frame) = world.spatial_frame.clone() else {
        return;
    };
    let origin = [
        world.position[0],
        world.position[1] + 0.85,
        world.position[2],
    ];
    for report in shots {
        let shot = report.event;
        let Some(node) = component.shot_node(&shot) else {
            continue;
        };
        let amount = node
            .field("m_6396149F")
            .and_then(|v| v.get("m_modifyHealth"))
            .and_then(|v| v.get("m_amount"))
            .or_else(|| node.field("m_modifyHealth").and_then(|v| v.get("m_amount")))
            .and_then(|expr| component.evaluate_shot_config(&shot, expr, world).ok())
            .and_then(|v| match v {
                Value::Float(n) if n.is_finite() => Some(n),
                Value::Integer(n) => Some(n as f64),
                _ => None,
            })
            .unwrap_or(0.0);
        if amount == 0.0 {
            continue;
        }
        let max_range = node
            .field("m_range")
            .or_else(|| node.field("m_244723D6"))
            .and_then(|expr| component.evaluate_shot_config(&shot, expr, world).ok())
            .and_then(|v| match v {
                Value::Float(n) if n.is_finite() && n > 0.0 => Some(n),
                Value::Integer(n) if n > 0 => Some(n as f64),
                _ => None,
            })
            .unwrap_or(40.0);
        let filter = node.field("m_filter");
        let team_mode = filter
            .and_then(|f| f.get("m_66747E7D"))
            .and_then(Json::as_u64)
            .unwrap_or(0);
        let hostile_flag = filter
            .and_then(|f| f.get("m_89831602"))
            .and_then(Json::as_u64)
            .unwrap_or(0);
        let site = Site {
            entity: world.entity,
            instance: shot.instance,
            graph: shot.graph,
            node: Some(shot.raw_state),
        };
        let mut best: Option<(f64, u32)> = None;
        for candidate in frame.candidates() {
            if candidate.entity == world.entity || candidate.entity == world.owner {
                continue;
            }
            let team_ok = match (team_mode, hostile_flag) {
                (1, 0) => candidate.team == Some(world.team),
                (2, _) | (_, 1) => candidate.team != Some(world.team),
                _ if amount > 0.0 => candidate.team == Some(world.team),
                _ => candidate.team != Some(world.team),
            };
            if !team_ok {
                continue;
            }
            let target_pos = [
                candidate.position[0],
                candidate.position[1] + 1.0,
                candidate.position[2],
            ];
            let dx = target_pos[0] - origin[0];
            let dy = target_pos[1] - origin[1];
            let dz = target_pos[2] - origin[2];
            let dist = dx.hypot(dy).hypot(dz);
            if dist <= 1e-4 || dist > max_range {
                continue;
            }
            if best.is_some_and(|(prev, _)| dist >= prev) {
                continue;
            }
            let dir = [dx / dist, dy / dist, dz / dist];
            if let Some(Some(hit)) = frame.ray_cast(site, tick, origin, dir, dist) {
                if hit.distance + 0.35 < dist {
                    continue;
                }
            }
            best = Some((dist, candidate.entity));
        }
        if let Some((_, target)) = best {
            if amount < 0.0 {
                let _ = world.script_requests.queue_contact_damage(
                    &frame,
                    tick,
                    -amount,
                    &[(target, None)],
                );
            } else {
                let _ = world.script_requests.queue_health(&frame, target, amount);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        mover::VelocityChange,
        mover_modifiers::{Operation as ModOp, SPEED_SLOT},
        script_init::{PetSpawn, VelocityOp},
    };
    use serde_json::json;

    #[derive(Default)]
    struct StubWorld {
        owner: u32,
        spawned: Vec<(u32, PetSpawn)>,
        despawned: Vec<u32>,
        velocity_ops: Vec<(u32, Vec<VelocityOp>)>,
    }

    impl ScriptWorld for StubWorld {
        fn admits_generic_player_runtime(&self, site: Site) -> bool {
            site.entity == self.owner
        }

        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }

        fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
            (entity == self.owner).then_some([1.0, 2.0, 3.0])
        }

        fn spawn_pet(&mut self, site: Site, spawn: PetSpawn) -> Option<u32> {
            if site.entity != self.owner {
                return None;
            }
            let id = 0xc008_0042;
            self.spawned.push((id, spawn));
            Some(id)
        }

        fn despawn_pet(&mut self, site: Site, entity: u32) -> bool {
            if site.entity != self.owner {
                return false;
            }
            self.despawned.push(entity);
            true
        }

        fn apply_velocity_ops(&mut self, site: Site, frame: u32, ops: &[VelocityOp]) -> bool {
            if site.entity != self.owner {
                return false;
            }
            self.velocity_ops.push((frame, ops.to_vec()));
            true
        }
    }

    fn single_state_store(state_node: Json) -> Arc<GraphStore> {
        let bundle = json!({
            "version": 1,
            "bodies": {},
            "missing": [],
            "graphs": [{
                "index": 1,
                "items": 2,
                "states": [1],
                "entries": [0],
                "remote": [],
                "sync_vars": [],
                "containers": {},
                "unknown_nodes": [],
                "fields": {},
                "header": {
                    "m_nodesBitCount": 4,
                    "m_statesBitCount": 4,
                    "m_remoteSyncNodesBitCount": 4,
                    "m_syncVarsBitCount": 4
                },
                "nodes": [
                    {
                        "pos": 0, "uid": 0, "rid": 0, "remote": -1,
                        "class": "STUStatescriptEntry",
                        "state": null, "server_only": false, "client_only": false,
                        "fields": {
                            "m_outPlug": {
                                "$": "STUStatescriptOutputPlug",
                                "links": [[1, "m_beginPlug"]]
                            }
                        }
                    },
                    state_node
                ]
            }]
        });
        Arc::new(GraphStore::from_json(bundle.to_string().as_bytes()).unwrap())
    }

    #[test]
    fn pet_state_spawns_writes_output_variable_and_despawns_on_end() {
        let graphs = single_state_store(json!({
            "pos": 1, "uid": 1, "rid": 1, "remote": -1,
            "class": "STUStatescriptStatePet",
            "state": 0, "server_only": false, "client_only": false,
            "fields": {
                "m_2BBEEAB8": 1,
                "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                "m_entityDefinition": { "$": "STU_8556841E", "m_entityDef": "0x040000000000011A" },
                "m_888BCD8B": { "$": "STUConfigVarBone", "m_bone": "0x088000000000008C" },
                "m_AA26E7DD": { "$": "STU_B5A0CAF0" },
                "m_B3019338": { "$": "STUConfigVarDynamic", "m_60DB8F99": 0, "m_identifier": 38 }
            }
        }));
        let owner = 0xa000_0101;
        let mut world = StubWorld {
            owner,
            ..Default::default()
        };
        let mut component = Component::start(
            graphs,
            owner,
            Variables::new(),
            vec![Root::new(1, 1)],
            1,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert_eq!(world.spawned.len(), 1);
        assert_eq!(world.spawned[0].0, 0xc008_0042);
        assert_eq!(world.spawned[0].1.definition, 0x0400_0000_0000_011a);
        assert_eq!(world.spawned[0].1.attach, Some(owner));
        let snap = component.snapshot().unwrap();
        assert_eq!(
            snap.instances[&1].variables.get(&38),
            Some(&Value::Entity(0xc008_0042))
        );

        component.debug_end_state(1, 0, &mut world).unwrap();
        assert_eq!(world.despawned, vec![0xc008_0042]);
        let snap = component.snapshot().unwrap();
        assert_eq!(snap.instances[&1].variables.get(&38), Some(&Value::Null));
    }

    #[test]
    fn room_pet_and_impact_deployable_replicate_and_expire() {
        let mut sim = RoomSimulation::new(Vec::new()).unwrap();
        let owner = 0xa000_0101;
        let pet_id = next_pet_wire_id();
        sim.spawn_room_pet(
            owner,
            0,
            pet_id,
            0x0400_0000_0000_0223,
            None,
            [3.0, 1.0, -4.0],
            0,
            None,
            None,
            10,
        );
        assert_eq!(sim.active_room_pets(), 1);
        let created = sim.take_created();
        assert_eq!(created.len(), 1);
        assert_eq!(created[0].entity, pet_id);

        sim.spawn_impact_deployable(
            owner,
            0,
            ImpactSpawn {
                definition: 0x0400_0000_0000_14a1,
                on_spawn_graph: None,
            },
            [5.0, 0.0, 2.0],
            0,
            10,
        );
        assert_eq!(sim.active_room_deployables(), 1);
        let dep_creates = sim.take_created();
        assert_eq!(dep_creates.len(), 1);
        let dep_wire = dep_creates[0].entity;

        // Advancing past scriptless TTL retires the deployable automatically.
        sim.advance_deployables(10 + SCRIPTLESS_DEPLOYABLE_TICKS);
        assert_eq!(sim.active_room_deployables(), 0);
        assert!(sim.take_retired_enemies().contains(&dep_wire));

        // Retiring the owner cleans up active pets cleanly.
        sim.retire_owner_pets_and_deployables(owner);
        assert_eq!(sim.active_room_pets(), 0);
        assert!(sim.take_retired_enemies().contains(&pet_id));
    }

    #[test]
    fn late_observers_receive_live_deployables_and_their_retirement() {
        use std::borrow::Borrow;

        use crate::{
            game_egress::GameEgress,
            game_heroes::HeroCatalog,
            game_replication::WorldDelivery,
            match_scene::{PlayerAppearance, PlayerCard},
            scene_assets::PreparedScene,
            world::health,
        };

        #[derive(serde::Deserialize)]
        struct Input {
            spawn_tick: u32,
            join_tick: u32,
            expire_tick: u32,
            definition: String,
            health: f64,
            positions: [[f64; 3]; 2],
            observed_positions: [[f64; 3]; 2],
            observed_yaws: [i32; 2],
            damage_before_join: [f64; 2],
            damage_before_create_ack: f64,
        }
        let fixture: Json = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/late-join-deployables-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["evidence"], "synthetic-rust");
        let case: Input = serde_json::from_value(fixture["input"].clone()).unwrap();
        let definition = crate::accounts::parse_guid(&case.definition).unwrap();
        let body_graph = crate::game_pools::BODY_GRAPH;
        let schema = json!({
            "version": 1, "missing": [], "bodies": {},
            "graphs": [{
                "index": body_graph, "items": 0,
                "nodes": [], "states": [], "entries": [], "remote": [], "sync_vars": [],
                "containers": {}, "unknown_nodes": [],
                "header": {"m_nodesBitCount": 1, "m_statesBitCount": 1,
                    "m_remoteSyncNodesBitCount": 1, "m_syncVarsBitCount": 1},
                "fields": {"m_publicSchema": {"m_entries": [{
                    "m_0D09D2D9": "0x0D800000000000C7",
                    "m_value": {"m_value": case.health}
                }]}}
            }],
            "entities": {
                (case.definition.clone()): {
                    "name": "Synthetic deployable", "weapons": [], "graphs": [{"graph": body_graph, "m_75408640": 0}]
                }
            }
        });
        let graphs =
            Arc::new(GraphStore::from_json(&serde_json::to_vec(&schema).unwrap()).unwrap());
        let mut scene = PreparedScene::fixture_for_owner_admission(HeroCatalog::default());
        let sim = scene.simulation_mut();
        sim.set_script_graphs(graphs, 1);
        let peer = 0xa000_0100;
        let owner = 0xa000_0201;
        let pet = next_pet_wire_id();
        sim.spawn_room_pet(
            owner,
            1,
            pet,
            definition,
            None,
            case.positions[0],
            0,
            None,
            None,
            case.spawn_tick,
        );
        sim.spawn_impact_deployable(
            owner,
            1,
            ImpactSpawn {
                definition,
                on_spawn_graph: None,
            },
            case.positions[1],
            0,
            case.spawn_tick,
        );
        let created = sim.take_created();
        assert_eq!(created.len(), 2);
        assert_eq!(created[0].entity, pet);
        let deployable = created[1].entity;
        let wires = [pet, deployable];
        let room_pet = sim.room_pets.get_mut(&pet).unwrap();
        room_pet.position = case.observed_positions[0];
        room_pet.yaw = case.observed_yaws[0];
        let room_deployable = sim.room_deployables.get_mut(&deployable).unwrap();
        room_deployable.position = case.observed_positions[1];
        room_deployable.yaw = case.observed_yaws[1];
        for (index, wire) in wires.into_iter().enumerate() {
            let damage = case.damage_before_join[index];
            assert_eq!(
                sim.damage_barrier_or_deployable(
                    wire,
                    index == 0,
                    damage,
                    peer,
                    case.join_tick - 1,
                    Duration::ZERO
                ),
                damage
            );
        }
        assert_eq!(sim.take_scripted_health().len(), 2);
        assert!(sim.take_created().is_empty());

        let card = PlayerCard::default();
        let viewer = PlayerAppearance {
            entity: peer,
            slot: 0,
            team: 0,
            card: &card,
        };
        let mut delivery = WorldDelivery::new(peer);
        let mut egress = GameEgress::default();
        delivery.start(&scene, &viewer, case.join_tick).unwrap();
        for wire in wires {
            assert!(delivery.tracks_current_bot(wire));
            assert!(!delivery.identity_ready(wire));
        }
        let sim = scene.simulation_mut();
        assert_eq!(
            sim.damage_barrier_or_deployable(
                pet,
                true,
                case.damage_before_create_ack,
                peer,
                case.join_tick,
                Duration::ZERO
            ),
            case.damage_before_create_ack
        );
        for update in sim.take_scripted_health() {
            delivery.queue_health(update);
        }
        assert!(delivery.health_pending(pet));

        let ready = delivery.ready();
        for (index, wire) in wires.into_iter().enumerate() {
            let mut pending = ready.iter().filter_map(|item| {
                let update: &EntityUpdate = item.borrow();
                (update.entity == wire).then_some(update)
            });
            let Some(Operation::Create(create)) =
                &pending.next().expect("late-join create").operation
            else {
                panic!("expected durable deployable create");
            };
            assert!(pending.next().is_none(), "health must wait for create ACK");
            assert_eq!(create.definition, definition);
            assert_eq!(
                create.position,
                case.observed_positions[index].map(|axis| axis as f32)
            );
            assert_eq!(
                create.rotation,
                yaw_rotation(f64::from(case.observed_yaws[index]))
            );
            assert!(create.movement_parent.is_none());
            assert!(create.components.contains(&health(
                (case.health - case.damage_before_join[index]) as f32,
                case.health as f32,
                0.0,
                0.0
            )));
        }

        let send = |delivery: &mut WorldDelivery, egress: &mut GameEgress<_>, tick, sequence| {
            delivery
                .send_frame(tick, Vec::new(), false, None, egress, |_| {
                    Ok::<_, ()>(sequence)
                })
                .unwrap();
            delivery.acknowledge(egress.acknowledge(sequence + 1, 1), Duration::ZERO);
        };
        let mut sequence = 0;
        for _ in 0..8 {
            send(&mut delivery, &mut egress, case.join_tick, sequence);
            sequence += 1;
            if wires.into_iter().all(|wire| delivery.identity_ready(wire)) {
                break;
            }
        }
        assert!(wires.into_iter().all(|wire| delivery.identity_ready(wire)));
        assert!(delivery.ready().iter().any(|item| {
            let update: &EntityUpdate = item.borrow();
            update.entity == pet && matches!(update.operation, Some(Operation::Update(_)))
        }));
        send(&mut delivery, &mut egress, case.join_tick + 1, sequence);
        assert!(!delivery.health_pending(pet));

        let sim = scene.simulation_mut();
        sim.advance_deployables(case.expire_tick);
        sim.despawn_room_pet(pet);
        let retired = sim.take_retired_enemies();
        assert_eq!(retired.len(), 2);
        for wire in wires {
            assert!(retired.contains(&wire));
            delivery.retire_enemy(wire);
            assert!(!delivery.tracks_current_bot(wire));
            assert!(delivery.identity_ready(wire));
        }
        let pending = delivery.ready();
        assert!(wires.into_iter().all(|wire| pending.iter().any(|item| {
            let update: &EntityUpdate = item.borrow();
            update.entity == wire && update.operation == Some(Operation::Destroy)
        })));
        send(&mut delivery, &mut egress, case.expire_tick, sequence + 1);
        assert!(wires.into_iter().all(|wire| !delivery.identity_ready(wire)));

        let mut later = WorldDelivery::new(peer);
        later.start(&scene, &viewer, case.expire_tick + 1).unwrap();
        assert!(
            wires
                .into_iter()
                .all(|wire| !later.tracks_current_bot(wire))
        );
    }

    #[test]
    fn movement_mod_handles_forced_velocity_and_inline_mover_component() {
        let graphs = single_state_store(json!({
            "pos": 1, "uid": 1, "rid": 1, "remote": -1,
            "class": "STU_316CFEF2",
            "state": 0, "server_only": false, "client_only": false,
            "fields": {
                "m_2BBEEAB8": 1,
                "m_beginPlug": { "$": "STUStatescriptInputPlug", "links": [] },
                "m_BC5E91CF": [
                    {
                        "$": "STU_09F9B1FE",
                        "m_CFEC5F76": 1,
                        "m_9B7A63EA": {
                            "$": "STUConfigVarDynamic",
                            "m_60DB8F99": 1,
                            "m_identifier": 100
                        }
                    },
                    {
                        "$": "STU_ED2B22BD",
                        "m_9B7A63EA": {
                            "$": "STUCharacterMoverComponent",
                            "m_19452F15": 11.0,
                            "m_E631BA84": 7.0,
                            "m_1FF06061": 0
                        }
                    }
                ]
            }
        }));
        let owner = 0xa000_0101;
        let mut world = StubWorld {
            owner,
            ..Default::default()
        };
        let mut component = Component::start(
            graphs,
            owner,
            Variables::from([(100, Value::Vector([12.0, 99.0, -4.0]))]),
            vec![Root::new(1, 1)],
            1,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert_eq!(world.velocity_ops.len(), 1);
        assert_eq!(
            world.velocity_ops[0].1,
            vec![VelocityOp::SetHorizontalVector([12.0, -4.0])]
        );
        // Verify horizontal velocity change preserves Y component.
        let mut vel = [0.0, -7.5, 0.0];
        VelocityChange::SetHorizontalVelocity([12.0, -4.0]).apply(&mut vel);
        assert_eq!(vel, [12.0, -7.5, -4.0]);

        // Verify inline STUCharacterMoverComponent modifiers were published.
        let mods = component.movement_modifiers(&mut world).unwrap().unwrap();
        assert!(mods.values().any(|m| {
            m.index == SPEED_SLOT && m.operation == ModOp::Multiply && (m.value - 2.0).abs() < 1e-4
        }));
        assert!(mods.values().any(|m| {
            m.index == 2 && m.operation == ModOp::Multiply && (m.value - 2.0).abs() < 1e-4
        }));
        assert!(
            mods.values()
                .any(|m| m.index == 22 && m.operation == ModOp::Set && m.value == 0.0)
        );

        // Verify next tick re-applies continuous forced velocity automatically.
        component.advance_unposed(2, false, &mut world).unwrap();
        assert!(world.velocity_ops.len() >= 2);
    }

    #[test]
    fn play_script_stun_extracts_authored_duration_from_shared_control_graph() {
        let graphs =
            GraphStore::from_json(br#"{"version":1,"graphs":[],"bodies":{},"missing":[]}"#)
                .unwrap();
        let req = PlayScript {
            policy: 0,
            overrides: vec![(STUN_DURATION_VAR, Value::Float(0.8))],
            graph: SHARED_STUN_GRAPH,
            source: 0xa000_0101,
            parameters: vec![],
            auto_retire: false,
        };
        assert_eq!(extract_play_script_stun_ticks(&graphs, &req), Some(50));

        let other = PlayScript {
            graph: 0x1234,
            ..req
        };
        assert_eq!(extract_play_script_stun_ticks(&graphs, &other), None);
    }

    #[test]
    fn ice_wall_pillars_block_both_teams_and_expose_solid_capsules() {
        let mut sim = RoomSimulation::new(Vec::new()).unwrap();
        let owner = 0xa000_0101;
        let pillars =
            sim.spawn_ice_wall_pillars(owner, 0, 0x0400_0000_0000_08ba, [0.0, 0.0, 6.0], 0, 10);
        assert_eq!(pillars.len(), 5);
        assert_eq!(sim.solid_wall_capsules().len(), 5);
        // Rays from BOTH allied (team 0) and enemy (team 1) are blocked by the solid wall.
        assert!(
            sim.probe_barrier_ray(Some(0), false, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 20.0)
                .is_some()
        );
        assert!(
            sim.probe_barrier_ray(Some(1), false, [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 20.0)
                .is_some()
        );
        // Breaking the center pillar opens a gap while the remaining 4 pillars stay solid.
        let center_wire = pillars[2];
        let now = Duration::from_secs(1);
        sim.damage_barrier_or_deployable(center_wire, true, 450.0, 0xa000_0201, 12, now);
        sim.advance_deployables(13);
        assert_eq!(sim.solid_wall_capsules().len(), 4);
    }

    #[test]
    fn duplicate_expiry_and_soul_anchor_resurrect_settle_cleanly() {
        let mut sim = RoomSimulation::new(Vec::new()).unwrap();
        let owner = 0xa000_0100;
        let orig_body = 0x0400_0000_0000_2593;
        sim.start_echo_duplicate(owner, orig_body, 100);
        sim.advance_deployables(100 + 62 * 14);
        assert_eq!(sim.take_pending_duplicate_revert(owner), None);
        sim.advance_deployables(100 + 62 * 15);
        assert_eq!(sim.take_pending_duplicate_revert(owner), Some(orig_body));

        let body = crate::game_replication::BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        sim.soul_anchors.push(crate::game_combat::SoulAnchor {
            body,
            team: 0,
            position: [10.0, 0.0, 10.0],
            expires_tick: 1000,
        });
        assert!(
            sim.try_resurrect_nearest_ally(0, [25.0, 0.0, 25.0], 200, Duration::from_secs(3))
                .is_none()
        );
        let revived =
            sim.try_resurrect_nearest_ally(0, [12.0, 0.0, 11.0], 200, Duration::from_secs(3));
        assert_eq!(revived.map(|a| a.body), Some(body));
        assert!(sim.soul_anchors.is_empty());
    }

    #[test]
    fn mei_ice_wall_entity_auto_spawns_five_solid_pillars_and_cleans_up_together() {
        let mut sim = RoomSimulation::new(Vec::new()).unwrap();
        let owner = 0xa000_0101;
        let root_pet = next_pet_wire_id();
        sim.spawn_room_pet(
            owner,
            0,
            root_pet,
            0x0400_0000_0000_090b,
            None,
            [0.0, 0.0, 8.0],
            0,
            None,
            None,
            20,
        );
        // Center + 4 flanking pillars = 5 independent solid wall pillars!
        assert_eq!(sim.active_room_pets(), 5);
        assert_eq!(sim.solid_wall_capsules().len(), 5);
        // Despawning the root pet cleans up all 4 flanking sub-pillars automatically.
        sim.despawn_room_pet(root_pet);
        assert_eq!(sim.active_room_pets(), 0);
        assert_eq!(sim.solid_wall_capsules().len(), 0);
    }
}
