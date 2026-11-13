//! Staged body/controller startup for authored scene enemies.

use crate::{
    commands::{Command, FIRE, SECONDARY},
    game_content::{TUTORIAL_MODE, is_junkenstein_mode},
    game_hitscan::{HitDefinition, HitscanShot, TargetPose, aim_direction},
    junkenstein_messages::{
        self, BOSS_INTRO_ENDED, BOSS_INTRO_STARTED, DefenderMessage, DefenderSighting, SIGHTING,
        SIGHTING_TAGS, SightingSource,
    },
    junkenstein_navigation::{
        BossTarget, GrenadeArc, JUNKRAT, REAPER, ROADHOG, SHOCK_TIRE, SUMMONER, WITCH, ZOMBARDIER,
    },
    junkenstein_voice::{self, IntroCue, VoiceRequest},
    mover::sample_movement_flags,
    npc_catalog::NpcDefinition,
    projectile_flight::{ProjectileBallistics, ProjectileFlight, authored_gravity},
    script_expr::{EntityRelation, OnFire, native_shape},
    script_graph::{EnemyPair, GraphStore, asset_guid},
    script_init::{
        self, AiWeaponOverride, BeamContact, Component, MAX_INIT_WORK, PlayScript, Site,
        TargetTagEffect, TargetTagEffects, World,
    },
    script_wire::{Value, Variables},
    vitals::{PoolKind, Vitals},
    world::{GAME_MODE_ENTITY, Movement},
};
use indexmap::IndexMap;
use serde_json::Value as Json;
use std::{
    collections::{BTreeSet, HashSet, VecDeque},
    sync::Arc,
};

#[derive(Default)]
pub(crate) struct AiControlRecords {
    overrides: IndexMap<(u32, u16, u16), script_init::AiControlOverride>,
    facing: IndexMap<Site, script_init::AiFacing>,
    cinematic_aim: IndexMap<Site, [i32; 2]>,
    room_boss_pause: bool,
    control_blocked: bool,
    weapons: IndexMap<Site, AiWeaponOverride>,
    display_names: IndexMap<Site, u64>,
    model_flags: u64,
    same_team_contact_suppressions: HashSet<Site>,
    outgoing_damage_filters: IndexMap<Site, OutgoingDamageFilter>,
    velocity_changes: Vec<(u32, crate::mover::VelocityChange)>,
    defenders: Arc<[u32]>,
    sightings: Vec<DefenderSighting>,
    defender_messages: Vec<DefenderMessage>,
    boss_intro_starts: Vec<Variables>,
    voice_requests: Vec<VoiceRequest>,
    voice_variation: u64,
    scripted_damage: Vec<EnemyDamage>,
    objective_route_active: Option<bool>,
    weapon_target: Option<BossTarget>,
    summoner_targets: [Option<u32>; 2],
    summoner_rays: [BeamContact; 2],
    witch_ally: Option<u32>,
    witch_switch: Option<(u16, u16)>,
    witch_target_distance: Option<f64>,
    witch_staff_effect: Option<(u32, f64)>,
    witch_staff_sources: IndexMap<u32, WitchStaffSource>,
    ai_control_setting: Option<(f32, bool)>,
}

pub(crate) struct WitchStaffSource {
    pub controller: u32,
    pub name: String,
    pub health: [f64; 2],
    pub capacity: [f64; 2],
}

struct OutgoingDamageFilter {
    adjustment: f64,
    excluded_tags: Vec<u64>,
}

#[derive(Default)]
pub(crate) struct EnemyWeaponShots {
    pub hitscan: Vec<EnemyHitscanVolley>,
    pub projectiles: Vec<EnemyProjectile>,
}

#[derive(bevy_ecs::component::Component)]
pub(crate) struct EnemyProjectile {
    pub source: u32,
    pub team: u8,
    pub flight: ProjectileFlight,
    pub damage_tags: Arc<[u64]>,
    pub split: Option<ProjectilePattern>,
}

pub(crate) struct ProjectilePattern {
    ballistics: ProjectileBallistics,
    gravity: Option<f32>,
    directions: Vec<[f64; 3]>,
    damage_tags: Arc<[u64]>,
}

impl ProjectilePattern {
    pub(crate) fn launch(
        self,
        source: u32,
        team: u8,
        origin: [f64; 3],
        frame: u32,
    ) -> impl Iterator<Item = EnemyProjectile> {
        let falloff = self.directions.len() > 1;
        self.directions.into_iter().filter_map(move |direction| {
            let mut flight =
                ProjectileFlight::new(self.ballistics.clone(), origin, direction, Some(frame))?;
            if let Some(gravity) = self.gravity {
                flight = flight.with_gravity(gravity);
            }
            Some(EnemyProjectile {
                source,
                team,
                flight: if falloff {
                    flight.with_direct_falloff()
                } else {
                    flight
                },
                damage_tags: self.damage_tags.clone(),
                split: None,
            })
        })
    }
}

pub(crate) struct EnemyHitscanVolley {
    pub pellets: Vec<HitscanShot>,
    pub damage_tags: Vec<u64>,
}

struct PairDelivery {
    target: u32,
    input: PairInput,
}

enum PairInput {
    Message {
        sender: u32,
        message: u64,
        parameters: Variables,
        required_graph: Option<u32>,
    },
    PlayScript {
        caller: Site,
        request: PlayScript,
    },
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct EnemyGate {
    pub entity: u32,
    pub position: [f64; 3],
}

pub(crate) struct SceneMessage {
    pub target: u32,
    pub message: u64,
    pub parameters: Variables,
}

#[derive(Clone)]
pub(crate) struct EnemyTarget {
    pub hero: Option<u64>,
    pub filter_flags: Option<u64>,
    pub tags: script_init::TargetTagSnapshot,
    pub health: Option<f64>,
    pub velocity: Option<[f64; 3]>,
    pub script_state: crate::script_entity_state::EntityScriptState,
    pub entity: u32,
    pub team: u8,
    pub pose: TargetPose,
    pub alive: bool,
}

pub(crate) struct EnemyDamage {
    pub source: u32,
    pub target: u32,
    pub amount: f64,
}

pub(crate) struct EnemyScripts {
    pub body: Component,
    pub controller: Component,
    control_records: AiControlRecords,
    behavior_records: indexmap::IndexMap<(u32, u16, u16), script_init::AiBehaviorRequest>,
    target_tag_effects: TargetTagEffects,
    messages: VecDeque<PairDelivery>,
    scene_messages: Vec<SceneMessage>,
    pub(crate) retirement_requested: bool,
    movement_modifiers: crate::mover_modifiers::ModifierTimeline,
}

pub(crate) struct EnemySpawn<'a> {
    pub graphs: Arc<GraphStore>,
    pub codecs: Arc<crate::script_codecs::CodecTable>,
    pub definition: &'a NpcDefinition,
    pub request: &'a script_init::EntityCreation,
    pub mode: u64,
    pub mode_variables: Arc<Variables>,
    pub tick: u32,
    pub walk: crate::game_bots::BotWalk,
    pub gate: Option<EnemyGate>,
}

pub(crate) struct EnemyBinding {
    pub mode_voice_set: Option<u64>,
    pub body: u32,
    pub controller: u32,
    pub hero: u64,
    pub pair: EnemyPair,
    pub mode: u64,
    pub mode_variables: Arc<Variables>,
    pub team: u8,
    pub skin: Option<u64>,
    pub pose: Movement,
    pub gate: Option<EnemyGate>,
    pub roster: Option<Arc<Vec<EnemyTarget>>>,
    /// Explicit room retirement facts; absence from a target roster alone is unknown.
    pub retired_entities: Arc<BTreeSet<u32>>,
    /// Authoritative room clock, independent of optional movement wire fields.
    pub frame: u32,
    pub now_secs: f64,
    pub spawn_anchors: Option<Arc<crate::scene_assets::SpawnAnchors>>,
}

pub(crate) struct EnemyWorld<'a> {
    retirement_requested: &'a mut bool,
    target_tag_effects: &'a mut TargetTagEffects,
    messages: &'a mut VecDeque<PairDelivery>,
    pub behavior_records:
        &'a mut indexmap::IndexMap<(u32, u16, u16), script_init::AiBehaviorRequest>,
    pub control_records: &'a mut AiControlRecords,
    pub binding: &'a EnemyBinding,
    pub graphs: &'a GraphStore,
    pub vitals: &'a mut Vitals,
    pub body_variables: &'a Variables,
    pub controller_variables: &'a Variables,
}

fn flashbang_entry_admits(
    graphs: &GraphStore,
    mut entity_value: impl FnMut(crate::script_expr::Variable) -> Option<Value>,
) -> Result<bool, String> {
    use crate::script_expr::{Context, Scope, Variable};
    struct Inputs(Vec<(Variable, Value)>);
    impl Context for Inputs {
        fn variable(&mut self, variable: Variable) -> Value {
            self.0
                .iter()
                .find(|(key, _)| *key == variable)
                .map_or(Value::Null, |(_, value)| value.clone())
        }
    }
    let graph = graphs
        .graph(0x224e)
        .ok_or("Flashbang effect graph unavailable")?;
    let condition = graph
        .node(4)
        .and_then(|node| node.field("m_condition"))
        .ok_or("Flashbang entry predicate unavailable")?;
    let variables = condition
        .get("m_expression")
        .and_then(|v| v.get("m_dynamicVars"))
        .and_then(Json::as_array)
        .ok_or("Flashbang entry variables unavailable")?;
    let schema = graph
        .fields
        .get("m_publicSchema")
        .and_then(|v| v.get("m_entries"))
        .and_then(Json::as_array)
        .ok_or("Flashbang entry defaults unavailable")?;
    let overrides = graphs
        .graph(0x0228)
        .and_then(|g| g.node(7))
        .and_then(|n| n.field("m_69F4B52C.m_6525E98B.m_1EB5A024"))
        .and_then(Json::as_array)
        .ok_or("Flashbang wrapper overrides unavailable")?;
    let mut inputs = Inputs(Vec::new());
    for config in variables {
        let variable = crate::script_expr::dynamic(config)
            .map_err(|e| e.to_string())?
            .ok_or("Flashbang entry variable unavailable")?;
        let value = match variable.scope {
            Scope::Entity => entity_value(variable).ok_or("Flashbang target component halted")?,
            Scope::Instance => {
                let identifier = 0x0d80_0000_0000_0000 | u64::from(variable.id);
                // The admitted 0228 path uses schema defaults for this guard.
                // A changed wrapper must not silently bypass an override.
                if overrides
                    .iter()
                    .any(|row| row.get("m_0D09D2D9").and_then(asset_guid) == Some(identifier))
                {
                    return Err("Flashbang entry override requires evaluation".into());
                }
                let default = schema
                    .iter()
                    .find(|row| row.get("m_0D09D2D9").and_then(asset_guid) == Some(identifier))
                    .and_then(|row| row.get("m_value"))
                    .ok_or("Flashbang entry default unavailable")?;
                crate::script_expr::evaluate(default, &mut inputs).map_err(|e| e.to_string())?
            }
        };
        inputs.0.push((variable, value));
    }
    crate::script_expr::evaluate(condition, &mut inputs)
        .map(|value| crate::script_expr::truthy(&value))
        .map_err(|e| e.to_string())
}

fn flashbang_stack_admits(body: &Component, graphs: &GraphStore) -> Result<bool, String> {
    let config = graphs
        .graph(0x224e)
        .and_then(|graph| graph.node(122))
        .and_then(|node| node.field("m_condition"))
        .filter(|config| config["$"] == "STU_3ACE35FB")
        .ok_or("Flashbang control-stack predicate unavailable")?;
    let variable = crate::script_expr::dynamic(&config["m_E50CF556"])
        .map_err(|error| error.to_string())?
        .filter(|variable| variable.scope == crate::script_expr::Scope::Entity)
        .ok_or("Flashbang control-stack target unavailable")?;
    let priority = crate::script_expr::stack_priority(&config["m_012B7AF7"])
        .map_err(|error| error.to_string())?;
    body.permits_entity_stack(variable.id, priority)
        .ok_or_else(|| "Flashbang target component halted".into())
}

fn flashbang_exclusions_admit(
    filter: &Json,
    presence: impl FnMut(u64) -> Option<bool>,
) -> Result<bool, String> {
    if filter["$"] != "STUConfigVarFilter" {
        return Err("Flashbang exclusion filter unavailable".into());
    }
    // Only this independent tag clause is checked here. Native actor category
    // initialization remains separate from the room timed-control policy.
    crate::script_expr::excluded_tags_admit(&filter["m_8B2A4DF3"], presence)
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "Flashbang target tags unavailable".into())
}

impl EnemyScripts {
    pub(crate) fn flashbang_control_allowed(
        &self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
    ) -> Result<bool, String> {
        if !flashbang_entry_admits(graphs, |variable| self.body.resolved_variable(0, variable))? {
            return Ok(false);
        }
        if !flashbang_stack_admits(&self.body, graphs)? {
            return Ok(false);
        }
        let filter = graphs
            .graph(0x224e)
            .and_then(|graph| graph.node(9))
            .and_then(|node| node.field("m_condition.m_filter"))
            .ok_or("Flashbang exclusion filter unavailable")?;
        flashbang_exclusions_admit(filter, |tag| self.body_has_target_tag(graphs, binding, tag))
    }

    /// Queue the admitted Flashbang wrapper before its hit entry. Normal pair
    /// delivery owns VM execution, schema overrides and observer state lifetime.
    pub(crate) fn queue_flashbang_effect(&mut self, binding: &EnemyBinding, caller: Site) {
        if !is_junkenstein_mode(binding.mode) || (caller.graph, caller.node) != (0x01e9, Some(7)) {
            return;
        }
        self.messages.push_back(PairDelivery {
            target: binding.body,
            input: PairInput::PlayScript {
                caller,
                request: PlayScript {
                    policy: 2,
                    overrides: vec![],
                    graph: 0x0228,
                    source: caller.entity,
                    parameters: vec![],
                    auto_retire: false,
                },
            },
        });
        self.messages.push_back(PairDelivery {
            target: binding.body,
            input: PairInput::Message {
                sender: caller.entity,
                message: 0x0240_0000_0000_000f,
                parameters: Variables::new(),
                required_graph: Some(0x0228),
            },
        });
    }

    pub(crate) fn set_control_blocked(&mut self, blocked: bool) {
        self.control_records.control_blocked = blocked;
    }

    pub(crate) fn take_velocity_changes(&mut self) -> Vec<(u32, crate::mover::VelocityChange)> {
        std::mem::take(&mut self.control_records.velocity_changes)
    }

    pub(crate) fn movement_speed_scale(&self, frame: u32) -> f64 {
        self.movement_modifiers.at(frame).intent(0, 1.0).max(0.0)
    }

    fn refresh_movement_modifiers(
        &mut self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        vitals: &mut Vitals,
        frame: u32,
    ) -> Result<(), String> {
        let empty = Variables::new();
        let mut world = EnemyWorld {
            retirement_requested: &mut self.retirement_requested,
            messages: &mut self.messages,
            target_tag_effects: &mut self.target_tag_effects,
            behavior_records: &mut self.behavior_records,
            control_records: &mut self.control_records,
            binding,
            graphs,
            vitals,
            body_variables: &empty,
            controller_variables: &self
                .controller
                .snapshot()
                .ok_or("enemy controller halted")?
                .variables,
        };
        let modifiers = self
            .body
            .movement_modifiers(&mut world)
            .map_err(|error| error.to_string())?
            .ok_or("enemy movement modifier projection is unsupported")?;
        self.movement_modifiers.track(frame, &modifiers);
        Ok(())
    }
    pub(crate) fn teardown(
        &mut self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        vitals: &mut Vitals,
    ) -> Result<(), String> {
        let empty = Variables::new();
        {
            let other = &self
                .controller
                .snapshot()
                .ok_or("controller teardown halted")?
                .variables;
            let mut world = EnemyWorld {
                retirement_requested: &mut self.retirement_requested,
                messages: &mut self.messages,
                target_tag_effects: &mut self.target_tag_effects,
                behavior_records: &mut self.behavior_records,
                control_records: &mut self.control_records,
                binding,
                graphs,
                vitals: &mut *vitals,
                body_variables: &empty,
                controller_variables: other,
            };
            self.body
                .teardown(&mut world)
                .map_err(|error| error.to_string())?;
        }
        let other = &self
            .body
            .snapshot()
            .ok_or("body teardown halted")?
            .variables;
        let mut world = EnemyWorld {
            retirement_requested: &mut self.retirement_requested,
            messages: &mut self.messages,
            target_tag_effects: &mut self.target_tag_effects,
            behavior_records: &mut self.behavior_records,
            control_records: &mut self.control_records,
            binding,
            graphs,
            vitals: &mut *vitals,
            body_variables: other,
            controller_variables: &empty,
        };
        self.controller
            .teardown(&mut world)
            .map_err(|error| error.to_string())?;
        self.deliver_messages(graphs, binding, vitals)
    }
    pub(crate) fn take_scene_messages(&mut self) -> Vec<SceneMessage> {
        std::mem::take(&mut self.scene_messages)
    }

    #[cfg(test)]
    pub(crate) fn ai_control_setting(&self) -> Option<(f32, bool)> {
        self.control_records.ai_control_setting
    }

    pub(crate) fn take_scripted_damage(&mut self) -> Vec<EnemyDamage> {
        std::mem::take(&mut self.control_records.scripted_damage)
    }

    pub(crate) fn set_objective_route(&mut self, active: bool) {
        self.control_records.objective_route_active = Some(active);
    }
    pub(crate) fn boss_pause_active(&self) -> bool {
        self.body
            .snapshot()
            .is_some_and(script_init::Snapshot::boss_pause_active)
    }

    pub(crate) fn set_room_boss_pause(&mut self, active: bool) {
        self.control_records.room_boss_pause = active;
    }

    pub(crate) fn boss_intro_finished(&self) -> bool {
        self.body.snapshot().is_some_and(|body| {
            body.instances.values().any(|instance| {
                // The 0CE0:4 wait sends 06AC as it ends.
                instance.graph == 0x0ce0
                    && instance
                        .states
                        .get(&3)
                        .is_some_and(|wait| wait.activation != 0 && !wait.active)
            })
        })
    }

    pub(crate) fn boss_combat_handoff(&self, binding: &EnemyBinding) -> bool {
        if self.boss_intro_finished()
            || self.controller.snapshot().is_some_and(|controller| {
                controller.instances.values().any(|instance| {
                    // 0C95:5 is the controller's authored facing state.
                    instance.graph == 0x0c95
                        && instance
                            .states
                            .get(&2)
                            .is_some_and(|state| state.activation != 0 && !state.active)
                })
            })
        {
            return true;
        }
        // The boss-specific branches at 0CBC:31/32/33 bypass the intro
        // when the mode has already introduced this boss and enables skipping.
        let (seen, wave_state) = match binding.hero {
            REAPER => (8520, 6),
            ROADHOG => (8521, 4),
            JUNKRAT => (8522, 5),
            _ => return false,
        };
        [seen, 8566].into_iter().all(|id| {
            binding
                .mode_variables
                .get(&id)
                .is_some_and(crate::script_expr::truthy)
        }) && self.body.snapshot().is_some_and(|body| {
            !body
                .instances
                .values()
                .any(|instance| instance.graph == 0x0ce0)
                && body.instances.values().any(|instance| {
                    instance.graph == 0x0cbc
                        && instance
                            .states
                            .get(&wave_state)
                            .is_some_and(|state| state.active)
                })
        })
    }

    pub(crate) fn set_weapon_target(&mut self, target: Option<BossTarget>) {
        self.control_records.weapon_target = target;
    }

    pub(crate) fn caster_ready(&self) -> bool {
        !self.behavior_records.is_empty()
            && !self.boss_pause_active()
            && self.cinematic_aim().is_none()
            && self.facing_direction().is_none()
    }

    pub(crate) fn set_witch_target_distance(&mut self, distance: Option<f64>) {
        self.control_records.witch_target_distance = distance;
    }

    pub(crate) fn witch_staff_effect(&self) -> Option<(u32, f64)> {
        self.control_records
            .witch_staff_effect
            .filter(|&(target, _)| {
                self.control_records.witch_ally == Some(target)
                    && self.control_records.witch_target_distance.is_some()
            })
    }

    pub(crate) fn record_witch_staff_source(&mut self, body: u32, source: WitchStaffSource) {
        self.control_records
            .witch_staff_sources
            .insert(body, source);
    }

    pub(crate) fn sync_witch_staff(
        &mut self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        vitals: &mut Vitals,
        detach: Option<(u32, u16)>,
        attach: Option<(u32, f64)>,
    ) -> Result<Option<u16>, String> {
        let mut world = EnemyWorld {
            retirement_requested: &mut self.retirement_requested,
            messages: &mut self.messages,
            target_tag_effects: &mut self.target_tag_effects,
            behavior_records: &mut self.behavior_records,
            control_records: &mut self.control_records,
            binding,
            graphs,
            vitals,
            body_variables: &Variables::new(),
            controller_variables: &self
                .controller
                .snapshot()
                .ok_or("enemy controller halted")?
                .variables,
        };
        if let Some((source, instance)) = detach {
            self.body
                .detach_witch_staff(instance, &mut world)
                .map_err(|error| error.to_string())?;
            world
                .control_records
                .witch_staff_sources
                .shift_remove(&source);
        }
        let instance = attach
            .map(|(source, amount)| self.body.attach_witch_staff(source, amount, &mut world))
            .transpose()
            .map_err(|error| error.to_string())?;
        let body = self.body.snapshot().ok_or("enemy body halted")?;
        if !body.initialization_complete() {
            return Err(format!("enemy body: {:?}", body.diagnostics));
        }
        self.deliver_messages(graphs, binding, vitals)?;
        Ok(instance)
    }

    pub(crate) fn publish_witch_targets(
        &mut self,
        frame: u32,
        enemy: Option<u32>,
        ally: Option<u32>,
    ) -> Result<(), script_init::InitFault> {
        self.control_records.weapon_target = enemy.map(|entity| BossTarget {
            entity,
            secondary: false,
        });
        self.control_records.witch_ally = ally;
        self.publish_weapon_targets(frame, 0x0cd2, [(0, 2299, enemy), (1, 7126, ally)])
    }

    pub(crate) fn summoner_reaches(&self) -> Option<[f64; 2]> {
        let controller = self
            .controller
            .snapshot()?
            .instances
            .values()
            .find(|i| i.graph == 0x185d)?;
        let [primary, secondary] = [7198, 4074].map(|id| {
            controller
                .variables
                .get(&id)
                .map(crate::script_expr::to_float)
        });
        let [Some(primary), Some(secondary)] = [primary, secondary] else {
            return None;
        };
        (primary.is_finite() && primary > 0.0 && secondary.is_finite() && secondary > 0.0)
            .then_some([primary, secondary])
    }

    pub(crate) fn summoner_beam_reach(&self) -> Option<f64> {
        let (&id, _) = self
            .body
            .snapshot()?
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == 0x157c)?;
        let variable = crate::script_expr::Variable {
            scope: crate::script_expr::Scope::Instance,
            id: 1648,
        };
        let reach = crate::script_expr::to_float(&self.body.resolved_variable(id, variable)?);
        (reach.is_finite() && reach > 0.0).then_some(reach)
    }

    pub(crate) fn set_summoner_rays(&mut self, rays: [BeamContact; 2]) {
        self.control_records.summoner_rays = rays;
    }

    pub(crate) fn publish_summoner_targets(
        &mut self,
        frame: u32,
        [primary, secondary]: [Option<u32>; 2],
        target: Option<BossTarget>,
    ) -> Result<(), script_init::InitFault> {
        self.control_records.summoner_targets = [primary, secondary];
        self.control_records.weapon_target = target;
        self.publish_weapon_targets(frame, 0x185d, [(0, 8066, primary), (1, 11652, secondary)])
    }

    fn publish_weapon_targets(
        &mut self,
        frame: u32,
        graph: u32,
        targets: [(i64, u32, Option<u32>); 2],
    ) -> Result<(), script_init::InitFault> {
        let Some((id, updates)) = self.controller.snapshot().and_then(|snapshot| {
            let (&id, controller) = snapshot.instances.iter().find(|(_, i)| i.graph == graph)?;
            let updates = targets.map(|(slot, variable, target)| {
                let current = match controller.variables.get(&variable) {
                    Some(Value::Entity(entity)) if crate::script_expr::valid_entity(*entity) => {
                        Some(*entity)
                    }
                    _ => None,
                };
                (current != target).then_some((slot, target.unwrap_or(0)))
            });
            Some((id, updates))
        }) else {
            return Ok(());
        };
        for (slot, entity) in updates.into_iter().flatten() {
            self.controller.queue_frame_message(
                frame,
                id,
                0x0240_0000_0000_0079,
                Variables::from([(0x57, Value::Integer(slot)), (0x59, Value::Entity(entity))]),
            )?;
        }
        Ok(())
    }

    pub(crate) fn grenade_arc(
        &self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
    ) -> Option<GrenadeArc> {
        let (graph, volley) = match binding.hero {
            JUNKRAT
                if self.ordinary_boss_weapon(binding)
                    && self
                        .controller
                        .snapshot()?
                        .instances
                        .values()
                        .any(|instance| {
                            instance.graph == 0x0cb5
                                && instance.states.get(&11).is_some_and(|state| state.active)
                        }) =>
            {
                (0x022b, 31)
            }
            ZOMBARDIER => (0x0ca1, 14),
            _ => return None,
        };
        let body = self.body.snapshot()?;
        let (&instance, _) = body
            .instances
            .iter()
            .find(|(_, instance)| instance.graph == graph)?;
        let node = graphs.graph(graph)?.node(volley)?;
        let resolve = |config: &Json| match crate::script_expr::dynamic(config)? {
            Some(variable) => self
                .body
                .resolved_variable(instance, variable)
                .ok_or(ShotConfigError::MalformedConfig("grenade setting")),
            None => crate::script_expr::evaluate(config, &mut crate::script_expr::EmptyContext),
        };
        let speed = crate::script_expr::to_float(&resolve(node.field("m_projectileSpeed")?).ok()?);
        let range = crate::script_expr::to_float(&resolve(node.field("m_FEC435C6")?).ok()?);
        let gravity = authored_gravity(&node.fields, resolve)?;
        (speed.is_finite() && speed > 0.0 && range.is_finite() && range > 0.0).then_some(
            GrenadeArc {
                speed,
                gravity,
                range,
            },
        )
    }

    pub(crate) fn outgoing_damage(&self, amount: f64, tags: &[u64]) -> f64 {
        let adjustment = self
            .control_records
            .outgoing_damage_filters
            .values()
            .filter(|filter| filter.excluded_tags.iter().all(|tag| !tags.contains(tag)))
            .map(|filter| filter.adjustment)
            .sum::<f64>();
        if adjustment == 0.0 {
            amount
        } else {
            let scale = ((1.0 + adjustment) as f32).max(0.0);
            f64::from((amount * f64::from(scale)) as f32)
        }
    }

    pub(crate) fn take_weapon_shots(
        &mut self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        vitals: &mut Vitals,
        frame: u32,
    ) -> EnemyWeaponShots {
        let shots = self.body.take_shots().unwrap_or_default();
        if self.control_records.control_blocked {
            return Default::default();
        }
        let mut weapons = EnemyWeaponShots::default();
        if shots.is_empty() || vitals.death().is_some() {
            return weapons;
        }
        let Some(controller) = self.controller.snapshot() else {
            return weapons;
        };
        let empty = Variables::new();
        let mut world = EnemyWorld {
            retirement_requested: &mut self.retirement_requested,
            messages: &mut self.messages,
            target_tag_effects: &mut self.target_tag_effects,
            behavior_records: &mut self.behavior_records,
            control_records: &mut self.control_records,
            binding,
            graphs,
            vitals,
            body_variables: &empty,
            controller_variables: &controller.variables,
        };
        let [x, y, z] = binding.pose.position;
        let origin = [
            x,
            y + crate::game_combat::eye_height(binding.hero, false),
            z,
        ];
        let forward = aim_direction(&Command {
            yaw: binding.pose.yaw,
            pitch: binding.pose.pitch,
            ..Command::default()
        });
        for shot in shots {
            let Some(node) = self.body.shot_node(&shot) else {
                continue;
            };
            let mut evaluate =
                |config: &Json| self.body.evaluate_shot_config(&shot, config, &mut world);
            match (binding.hero, shot.graph, node.pos) {
                (REAPER, 0x0019, 10) => {
                    let Some(reach) = node
                        .field("m_FEC435C6")
                        .and_then(|v| evaluate(v).ok())
                        .map(|v| crate::script_expr::to_float(&v))
                        .filter(|v| v.is_finite() && *v > 0.0)
                    else {
                        continue;
                    };
                    let Some(direct) = node.field("m_6396149F") else {
                        continue;
                    };
                    let Ok(Some(hit)) = HitDefinition::from_authored(direct, &mut evaluate) else {
                        continue;
                    };
                    let Some(directions) =
                        shotgun_directions(&node.fields, forward, &shot, node.pos, &mut evaluate)
                    else {
                        continue;
                    };
                    weapons.hitscan.push(EnemyHitscanVolley {
                        damage_tags: damage_tags(direct, &mut evaluate).collect(),
                        pellets: directions
                            .into_iter()
                            .map(|direction| HitscanShot {
                                origin,
                                direction,
                                reach,
                                hit,
                            })
                            .collect(),
                    });
                }
                (ROADHOG, 0x01c6, 26 | 29)
                | (JUNKRAT, 0x022b, 31)
                | (ZOMBARDIER, 0x0ca1, 14)
                | (WITCH, 0x006d, 10)
                | (SUMMONER, 0x157c, 85) => {
                    let Some(mut pattern) =
                        projectile_pattern(&node.fields, forward, &shot, node.pos, &mut evaluate)
                    else {
                        continue;
                    };
                    if matches!(binding.hero, JUNKRAT | ZOMBARDIER) {
                        let Some(gravity) = authored_gravity(&node.fields, &mut evaluate) else {
                            continue;
                        };
                        pattern.gravity = Some(gravity);
                    }
                    let mut projectiles = pattern.launch(binding.body, binding.team, origin, frame);
                    if (binding.hero, node.pos) == (ROADHOG, 29) {
                        let Some(mut projectile) = projectiles.next() else {
                            continue;
                        };
                        if let Some(transition) = node
                            .field("m_747ECB51")
                            .filter(|v| v["$"] == "STU_DB5FDC79")
                        {
                            projectile.split = projectile_pattern(
                                &transition["m_volley"],
                                forward,
                                &shot,
                                node.pos,
                                &mut evaluate,
                            );
                        }
                        weapons.projectiles.push(projectile);
                    } else {
                        weapons.projectiles.extend(projectiles);
                    }
                }
                _ => {}
            }
        }
        weapons
    }

    fn weapon_command(&self, binding: &EnemyBinding, frame: u32) -> Command {
        let secondary = self
            .control_records
            .weapon_target
            .is_some_and(|target| target.secondary);
        let (graph, button_state, button) = match (binding.hero, secondary) {
            (REAPER, _) => (0x0019, 11, FIRE),
            (ROADHOG, false) => (0x01c6, 8, FIRE),
            (ROADHOG, true) => (0x01c6, 11, SECONDARY),
            (JUNKRAT, _) => (0x022b, 8, FIRE),
            (ZOMBARDIER, _) => (0x0ca1, 10, SECONDARY),
            _ => unreachable!("hero has no authored weapon input"),
        };
        let listening = self.body.snapshot().is_some_and(|body| {
            body.instances.values().any(|instance| {
                instance.graph == graph
                    && instance
                        .states
                        .get(&button_state)
                        .is_some_and(|state| state.active)
            })
        });
        Command {
            frame,
            yaw: binding.pose.yaw,
            pitch: binding.pose.pitch,
            buttons: if self.control_records.weapon_target.is_some()
                && (binding.hero == ZOMBARDIER || self.ordinary_boss_weapon(binding))
                && (listening || self.body.command_buttons() & button != 0)
            {
                button
            } else {
                0
            },
            ..Command::default()
        }
    }

    fn witch_weapon_command(&mut self, binding: &EnemyBinding, frame: u32) -> Command {
        let mut command = Command {
            frame,
            yaw: binding.pose.yaw,
            pitch: binding.pose.pitch,
            ..Command::default()
        };
        if !self.caster_ready() || self.control_records.room_boss_pause {
            return command;
        }
        let (raw, action, graph, trigger, weapon_override) =
            if self.control_records.witch_ally.is_some() {
                (29, 1, 0x006b, 50, 9)
            } else {
                (31, 2, 0x006d, 5, 12)
            };
        let activation = self.controller.snapshot().and_then(|snapshot| {
            snapshot
                .instances
                .values()
                .find(|instance| instance.graph == 0x0cd2)?
                .states
                .get(&raw)
                .filter(|state| state.active)
                .map(|state| state.activation)
        });
        if let Some(activation) = activation {
            let request = (raw, activation);
            if self.control_records.witch_switch != Some(request) {
                self.control_records.witch_switch = Some(request);
                command.action = action;
            }
        }
        if command.action == 0
            && self.control_records.witch_target_distance.is_some()
            && self.control_records.weapons.keys().any(|site| {
                (site.entity, site.graph, site.node)
                    == (binding.controller, 0x0cd2, Some(weapon_override))
            })
            && self.body.snapshot().is_some_and(|body| {
                body.instances.values().any(|instance| {
                    instance.graph == graph
                        && (instance
                            .states
                            .get(&trigger)
                            .is_some_and(|state| state.active)
                            || self.body.command_buttons() & FIRE != 0)
                })
            })
        {
            command.buttons = FIRE;
        }
        command
    }

    fn summoner_weapon_command(&self, binding: &EnemyBinding, frame: u32) -> Command {
        let buttons = self
            .control_records
            .weapon_target
            .filter(|_| self.caster_ready() && !self.control_records.room_boss_pause)
            .map_or(0, |target| {
                let (node, button) = if target.secondary {
                    (21, SECONDARY)
                } else {
                    (18, FIRE)
                };
                if self.control_records.weapons.keys().any(|site| {
                    (site.entity, site.graph, site.node) == (binding.controller, 0x185d, Some(node))
                }) {
                    button
                } else {
                    0
                }
            });
        Command {
            frame,
            yaw: binding.pose.yaw,
            pitch: binding.pose.pitch,
            buttons,
            ..Command::default()
        }
    }

    fn ordinary_boss_weapon(&self, binding: &EnemyBinding) -> bool {
        let mut body_record = false;
        let mut grenade_record = false;
        for (site, record) in self
            .control_records
            .weapons
            .iter()
            .filter(|(_, record)| record.slot == 0)
        {
            if record.target != binding.body
                || record.identifier != 0x0d80_0000_0000_023a
                || record.enabled
            {
                return false;
            }
            match (site.entity, site.graph, site.node) {
                (entity, 0x08b6, Some(3)) if entity == binding.body && !body_record => {
                    body_record = true
                }
                (entity, 0x0cb5, Some(17)) if entity == binding.controller && !grenade_record => {
                    grenade_record = true
                }
                _ => return false,
            }
        }
        body_record && grenade_record == (binding.hero == JUNKRAT)
    }

    pub(crate) fn cinematic_aim(&self) -> Option<[i32; 2]> {
        self.control_records
            .cinematic_aim
            .last()
            .map(|(_, &aim)| aim)
    }

    pub(crate) fn facing_direction(&self) -> Option<[f64; 3]> {
        self.control_records
            .facing
            .values()
            .fold(None, |winner: Option<&script_init::AiFacing>, record| {
                if winner.is_none_or(|winner| {
                    record.priority.level > winner.priority.level
                        || (record.priority.level == winner.priority.level
                            && record.priority.above_equals)
                }) {
                    Some(record)
                } else {
                    winner
                }
            })
            .map(|record| record.direction)
    }

    pub(crate) fn queue_wave_script(
        &mut self,
        binding: &EnemyBinding,
        caller: Site,
        request: PlayScript,
    ) -> bool {
        if (caller.entity, caller.graph, caller.node, request.graph)
            != (GAME_MODE_ENTITY, 0x0cba, Some(9), 0x0c95)
            || !is_junkenstein_mode(binding.mode)
            || self.messages.len() >= MAX_INIT_WORK
        {
            return false;
        }
        self.messages.push_back(PairDelivery {
            target: binding.controller,
            input: PairInput::PlayScript { caller, request },
        });
        true
    }

    #[cfg(test)]
    pub(crate) fn controller_display_name(&self) -> Option<u64> {
        self.control_records
            .display_names
            .last()
            .map(|(_, &name)| name)
    }

    #[cfg(test)]
    pub(crate) fn behavior_requests(
        &self,
    ) -> impl Iterator<Item = &script_init::AiBehaviorRequest> {
        self.behavior_records.values()
    }
    pub(crate) fn queue_common_corpse_retirement(
        &mut self,
        binding: &EnemyBinding,
        vitals: &Vitals,
        frame: u32,
    ) -> Result<(), String> {
        if !is_junkenstein_mode(binding.mode)
            || !crate::junkenstein_host::common_corpse_due(
                binding.hero,
                vitals.death().map(|death| death.frame),
                frame,
            )
        {
            return Ok(());
        }
        let body = self
            .body
            .snapshot()
            .ok_or("common corpse body unavailable")?;
        let controller = self
            .controller
            .snapshot()
            .ok_or("common corpse controller unavailable")?;
        if body.entity != binding.body
            || controller.entity != binding.controller
            || body.entity == controller.entity
            || !body.initialization_complete()
            || !controller.initialization_complete()
        {
            return Err("common corpse pair identity or script state is unavailable".into());
        }
        self.retirement_requested = true;
        Ok(())
    }

    pub(crate) fn queue_death(
        &mut self,
        source: crate::vitals::Death,
        critical: bool,
        loadout: Option<u64>,
    ) -> Result<(), String> {
        self.body.set_owner_dead(true);
        let parameters = Variables::from([
            (0x12, Value::Entity(source.killer)),
            (0x95, Value::Bool(critical)),
            (0x3e6, loadout.map_or(Value::Null, Value::Asset)),
            (0x3e7, Value::Array(vec![])),
        ]);
        const DIED: u64 = 0x0240_0000_0000_003e;
        if u64::from(source.frame) * crate::script_runtime::FRAME_MS >= self.body.now_ms() {
            self.body
                .queue_frame_message_from(source.frame, source.killer, DIED, parameters)
        } else {
            // Damage may land after this body's same-frame script update.
            self.body
                .queue_entity_message_from(source.killer, DIED, parameters)
        }
        .map(|_| ())
        .map_err(|error| error.to_string())
    }

    pub fn advance(
        &mut self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        vitals: &mut Vitals,
        frame: u32,
    ) -> Result<(), String> {
        self.body.set_owner_dead(vitals.death().is_some());
        let command = match binding.hero {
            WITCH => Some(self.witch_weapon_command(binding, frame)),
            SUMMONER => Some(self.summoner_weapon_command(binding, frame)),
            REAPER | ROADHOG | JUNKRAT | ZOMBARDIER => Some(self.weapon_command(binding, frame)),
            _ => None,
        };
        let room_pause = self.control_records.room_boss_pause;
        let blocked = self.control_records.control_blocked;
        let empty = Variables::new();
        {
            let mut world = EnemyWorld {
                retirement_requested: &mut self.retirement_requested,
                messages: &mut self.messages,
                target_tag_effects: &mut self.target_tag_effects,
                behavior_records: &mut self.behavior_records,
                control_records: &mut self.control_records,
                binding,
                graphs,
                vitals: &mut *vitals,
                body_variables: &empty,
                controller_variables: &self
                    .controller
                    .snapshot()
                    .ok_or("enemy controller halted")?
                    .variables,
            };
            self.body
                .set_control_blocked(blocked, &mut world)
                .map_err(|error| error.to_string())?;
            match command {
                Some(command) => self.body.advance_command(command, &mut world),
                None => self.body.advance_frame(frame, &mut world),
            }
            .map_err(|error| error.to_string())?;
            self.body
                .refresh_boss_pause(&mut world, room_pause)
                .map_err(|error| error.to_string())?;
        }
        let body = self.body.snapshot().ok_or("enemy body halted")?;
        if !body.initialization_complete() {
            return Err(format!("enemy body: {:?}", body.diagnostics));
        }
        // Health services may have killed this body during its own update.
        self.body.set_owner_dead(vitals.death().is_some());
        let body = self.body.snapshot().ok_or("enemy body halted")?;
        let mut world = EnemyWorld {
            retirement_requested: &mut self.retirement_requested,
            messages: &mut self.messages,
            target_tag_effects: &mut self.target_tag_effects,
            behavior_records: &mut self.behavior_records,
            control_records: &mut self.control_records,
            binding,
            graphs,
            vitals: &mut *vitals,
            body_variables: &body.variables,
            controller_variables: &empty,
        };
        self.controller
            .advance_frame(frame, &mut world)
            .map_err(|error| error.to_string())?;
        let controller = self
            .controller
            .snapshot()
            .ok_or("enemy controller halted")?;
        if !controller.initialization_complete() {
            return Err(format!("enemy controller: {:?}", controller.diagnostics));
        }
        self.deliver_messages(graphs, binding, vitals)?;
        self.refresh_movement_modifiers(graphs, binding, vitals, frame)
    }

    pub(crate) fn has_polled_voice(&self) -> bool {
        self.body.has_polled_voice() || self.controller.has_polled_voice()
    }

    /// Called by the room only after every actor's ordinary component updates.
    pub(crate) fn poll_voice(
        &mut self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        vitals: &mut Vitals,
    ) -> Result<(), String> {
        let empty = Variables::new();
        for body_phase in [true, false] {
            let (receiver, other) = if body_phase {
                (&mut self.body, &self.controller)
            } else {
                (&mut self.controller, &self.body)
            };
            if !receiver.has_polled_voice() {
                continue;
            }
            let other = &other.snapshot().ok_or("enemy voice peer halted")?.variables;
            let mut world = EnemyWorld {
                retirement_requested: &mut self.retirement_requested,
                messages: &mut self.messages,
                target_tag_effects: &mut self.target_tag_effects,
                behavior_records: &mut self.behavior_records,
                control_records: &mut self.control_records,
                binding,
                graphs,
                vitals: &mut *vitals,
                body_variables: if body_phase { &empty } else { other },
                controller_variables: if body_phase { other } else { &empty },
            };
            receiver
                .poll_voice(&mut world)
                .map_err(|error| error.to_string())?;
            let snapshot = receiver.snapshot().ok_or("enemy voice component halted")?;
            if !snapshot.initialization_complete() {
                return Err(format!("enemy voice: {:?}", snapshot.diagnostics));
            }
        }
        Ok(())
    }

    fn deliver_messages(
        &mut self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        vitals: &mut Vitals,
    ) -> Result<(), String> {
        let empty = Variables::new();
        let mut delivered = 0;
        while let Some(message) = self.messages.pop_front() {
            delivered += 1;
            if delivered > MAX_INIT_WORK {
                return Err("enemy cross-component message budget exhausted".into());
            }
            if message.target == GAME_MODE_ENTITY
                || binding
                    .gate
                    .is_some_and(|gate| gate.entity == message.target)
            {
                let PairInput::Message {
                    message: id,
                    parameters,
                    ..
                } = message.input
                else {
                    return Err("PlayScript cannot target the scene message queue".into());
                };
                self.scene_messages.push(SceneMessage {
                    target: message.target,
                    message: id,
                    parameters,
                });
                continue;
            }
            let (receiver, other) = if message.target == binding.body {
                (&mut self.body, &self.controller)
            } else {
                (&mut self.controller, &self.body)
            };
            let other = &other
                .snapshot()
                .ok_or("enemy message peer halted")?
                .variables;
            let mut world = EnemyWorld {
                retirement_requested: &mut self.retirement_requested,
                messages: &mut self.messages,
                target_tag_effects: &mut self.target_tag_effects,
                behavior_records: &mut self.behavior_records,
                control_records: &mut self.control_records,
                binding,
                graphs,
                vitals: &mut *vitals,
                body_variables: if message.target == binding.body {
                    &empty
                } else {
                    other
                },
                controller_variables: if message.target == binding.controller {
                    &empty
                } else {
                    other
                },
            };
            match message.input {
                PairInput::Message {
                    sender,
                    message,
                    parameters,
                    required_graph,
                } => {
                    if required_graph.is_some_and(|graph| {
                        !receiver.snapshot().is_some_and(|snapshot| {
                            snapshot.initialization_complete()
                                && snapshot
                                    .instances
                                    .values()
                                    .any(|instance| instance.graph == graph)
                        })
                    }) {
                        continue;
                    }
                    receiver
                        .queue_entity_message_from(sender, message, parameters)
                        .map_err(|error| error.to_string())?;
                    receiver.run_now(&mut world)
                }
                PairInput::PlayScript { caller, request } => {
                    receiver.receive_play_script(caller, &request, &mut world)
                }
            }
            .map_err(|error| error.to_string())?;
        }
        // A PlayScript start can already have submitted the peer's work
        // before encountering an unsupported branch on the sender.
        for (kind, receiver) in [("body", &self.body), ("controller", &self.controller)] {
            let snapshot = receiver.snapshot().ok_or("enemy script receiver halted")?;
            if !snapshot.initialization_complete() {
                return Err(format!("enemy {kind} delivery: {:?}", snapshot.diagnostics));
            }
        }
        Ok(())
    }
    /// Nothing is registered in the room until both components have started.
    pub fn start(
        graphs: Arc<GraphStore>,
        definition: &NpcDefinition,
        binding: &EnemyBinding,
        frame: u32,
        vitals: &mut Vitals,
    ) -> Result<Self, String> {
        if definition.hero != binding.hero || definition.body != binding.pair.body {
            return Err("enemy definition differs from the authored pair".into());
        }
        let record = graphs
            .enemy_body(binding.pair.body)
            .ok_or("missing enemy body record")?;
        let control = graphs
            .enemy_body(binding.pair.controller)
            .ok_or("missing enemy controller record")?;
        let initial =
            script_init::player_entity_variables(record, vitals.total(), vitals.maximum());
        let roots = if matches!(binding.hero, REAPER | ROADHOG | JUNKRAT | WITCH | SUMMONER) {
            script_init::player_roots(record, &graphs)
        } else {
            script_init::body_roots(record)
        }
        .map_err(|error| error.to_string())?;
        // The common body graph owns its pools. Keep the profile's initial
        // bindings, then let its live states register each pool exactly once.
        // NPCs using 0C89's capacity binding retain their catalog-owned pools.
        if roots
            .iter()
            .any(|root| root.graph == crate::game_pools::BODY_GRAPH)
        {
            *vitals = Vitals::empty();
        }
        let empty = Variables::new();
        let mut control_records = AiControlRecords::default();
        let mut behavior_records = indexmap::IndexMap::new();
        let mut messages = VecDeque::new();
        let mut retirement_requested = false;
        let mut target_tag_effects = TargetTagEffects::default();
        let mut world = EnemyWorld {
            retirement_requested: &mut retirement_requested,
            messages: &mut messages,
            target_tag_effects: &mut target_tag_effects,
            behavior_records: &mut behavior_records,
            control_records: &mut control_records,
            binding,
            graphs: &graphs,
            vitals,
            body_variables: &initial,
            controller_variables: &empty,
        };
        let body = Component::start_with_public_schema(
            Arc::clone(&graphs),
            binding.body,
            initial.clone(),
            roots,
            frame,
            &mut world,
            MAX_INIT_WORK,
        )
        .map_err(|error| error.to_string())?;
        let snapshot = body.snapshot().ok_or("enemy body startup halted")?;
        if !snapshot.initialization_complete() {
            return Err(format!("enemy body startup: {:?}", snapshot.diagnostics));
        }
        world.body_variables = &snapshot.variables;
        let controller = Component::start_with_public_schema(
            Arc::clone(&graphs),
            binding.controller,
            Variables::new(),
            script_init::body_roots(control).map_err(|error| error.to_string())?,
            frame,
            &mut world,
            MAX_INIT_WORK,
        )
        .map_err(|error| error.to_string())?;
        let snapshot = controller
            .snapshot()
            .ok_or("enemy controller startup halted")?;
        if !snapshot.initialization_complete() {
            return Err(format!(
                "enemy controller startup: {:?}",
                snapshot.diagnostics
            ));
        }
        let mut scripts = Self {
            body,
            controller,
            control_records,
            behavior_records,
            target_tag_effects,
            messages,
            scene_messages: Vec::new(),
            retirement_requested,
            movement_modifiers: Default::default(),
        };
        scripts.deliver_messages(&graphs, binding, vitals)?;
        scripts.refresh_movement_modifiers(&graphs, binding, vitals, frame)?;
        Ok(scripts)
    }
}

impl EnemyWorld<'_> {
    fn live_staff_source(&self, source: u32) -> bool {
        self.control_records
            .witch_staff_sources
            .contains_key(&source)
            && self.binding.roster.as_ref().is_some_and(|roster| {
                roster.iter().any(|target| {
                    target.entity == source && target.alive && target.team == self.binding.team
                })
            })
    }
}

impl World for EnemyWorld<'_> {
    fn edit_velocity(&mut self, site: Site, frame: u32, value: Value, cap: bool) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x224e, Some(31))
            || !is_junkenstein_mode(self.binding.mode)
            || self.vitals.death().is_some()
            || !cap
            || self.control_records.velocity_changes.len() >= MAX_INIT_WORK
        {
            return false;
        }
        let Value::Float(high) = value else {
            return false;
        };
        let change = crate::mover::VelocityChange::HorizontalCap(high);
        if !change.valid() {
            return false;
        }
        self.control_records
            .velocity_changes
            .push((frame.saturating_add(1), change));
        true
    }

    fn engine_setting(&self, _site: Site, key: u32) -> crate::script_expr::EngineSetting {
        if is_junkenstein_mode(self.binding.mode) {
            crate::junkenstein_host::engine_setting(key)
        } else {
            crate::script_expr::EngineSetting::Unavailable
        }
    }

    fn summoner_beam_contact(&self, site: Site) -> Option<BeamContact> {
        if self.binding.hero != SUMMONER || site.entity != self.binding.body || site.graph != 0x157c
        {
            return None;
        }
        match site.node {
            Some(202) => Some(self.control_records.summoner_rays[0]),
            Some(203) => Some(self.control_records.summoner_rays[1]),
            _ => None,
        }
    }

    fn summoner_beam_damage(&mut self, site: Site, target: u32, amount: f64) -> bool {
        if self.binding.hero != SUMMONER
            || (site.entity, site.graph, site.node) != (self.binding.body, 0x157c, Some(170))
        {
            return false;
        }
        let [thin, wide] = self.control_records.summoner_rays;
        if thin.entity.or(wide.entity) == Some(target) && !self.control_records.room_boss_pause {
            self.control_records.scripted_damage.push(EnemyDamage {
                source: site.entity,
                target,
                amount,
            });
        }
        true
    }
    fn ruleset(&mut self, id: u32) -> Option<Value> {
        if !is_junkenstein_mode(self.binding.mode) {
            return None;
        }
        match (self.binding.hero, id) {
            (REAPER | ROADHOG | WITCH | SUMMONER, 0x2819) => Some(Value::Float(1.0)),
            (ROADHOG | SUMMONER, 0x290c) => Some(Value::Bool(true)),
            _ => None,
        }
    }

    fn objective_route_active(&self, site: Site) -> Option<bool> {
        if site.entity == self.binding.controller {
            self.control_records.objective_route_active
        } else {
            None
        }
    }

    fn request_corpse_retirement(&mut self, site: Site, entity: u32, phase_exit: bool) -> bool {
        if site.entity != self.binding.body
            || ![self.binding.body, self.binding.controller].contains(&entity)
            || (!phase_exit && self.vitals.death().is_none())
        {
            return false;
        }
        *self.retirement_requested = true;
        true
    }
    fn body_angle(
        &self,
        entity: u32,
        axis: crate::script_expr::BodyAngle,
    ) -> crate::script_expr::AngleQuery {
        use crate::script_expr::{AngleQuery, BodyAngle};
        if !is_junkenstein_mode(self.binding.mode) {
            return AngleQuery::Unavailable;
        }
        if entity == 0 {
            return AngleQuery::Unbound;
        }
        if entity != self.binding.body {
            return AngleQuery::Unavailable;
        }
        AngleQuery::from_packed(match axis {
            BodyAngle::Yaw => self.binding.pose.yaw,
            BodyAngle::Pitch => self.binding.pose.pitch,
        })
    }

    fn entity_velocity(&self, entity: u32) -> crate::script_expr::VelocityQuery {
        use crate::script_expr::VelocityQuery;
        if !is_junkenstein_mode(self.binding.mode) {
            return VelocityQuery::Unavailable;
        }
        if entity == 0 {
            return VelocityQuery::Unbound;
        }
        if entity == self.binding.body {
            return VelocityQuery::Present(self.binding.pose.velocity);
        }
        self.binding
            .roster
            .as_ref()
            .and_then(|roster| roster.iter().find(|target| target.entity == entity))
            .and_then(|target| target.velocity)
            .map_or(VelocityQuery::Unavailable, VelocityQuery::Present)
    }

    fn entity_exists(&self, entity: u32) -> Option<bool> {
        if self.binding.retired_entities.contains(&entity) {
            return Some(false);
        }
        (entity == self.binding.controller
            || entity == GAME_MODE_ENTITY
            || self.known_position(entity).is_some())
        .then_some(true)
    }

    fn known_position(&self, entity: u32) -> Option<[f64; 3]> {
        if entity == self.binding.body {
            Some(self.binding.pose.position)
        } else if let Some(gate) = self.binding.gate.filter(|gate| gate.entity == entity) {
            Some(gate.position)
        } else if let Some(target) = self
            .binding
            .roster
            .as_ref()
            .and_then(|roster| roster.iter().find(|target| target.entity == entity))
        {
            Some(target.pose.feet)
        } else {
            self.binding.spawn_anchors.as_ref()?.position(entity)
        }
    }
    fn emit_contact_effect(
        &mut self,
        site: Site,
        source: u32,
        resource: u64,
        targets: &[u32],
    ) -> bool {
        site.entity == self.binding.body
            && source == self.binding.body
            && (site.graph, site.node) == (0x0c89, Some(49))
            && resource == 0x0120_0000_0000_0015
            && targets
                .iter()
                .all(|target| self.known_position(*target).is_some())
    }
    fn entity_team(&self, _site: Site, entity: u32) -> Option<i8> {
        if self.binding.retired_entities.contains(&entity) {
            Some(-1)
        } else if entity == self.binding.body {
            i8::try_from(self.binding.team).ok()
        } else {
            self.binding
                .roster
                .as_ref()?
                .iter()
                .find(|target| target.entity == entity)
                .and_then(|target| i8::try_from(target.team).ok())
        }
    }
    fn team_members(&self, team: u8) -> Option<u32> {
        is_junkenstein_mode(self.binding.mode).then(|| {
            if team == 0 {
                self.control_records.defenders.len() as u32
            } else {
                0
            }
        })
    }
    fn self_damage(&mut self, site: Site, amount: f64) -> bool {
        if site.entity != self.binding.body {
            return false;
        }
        self.vitals.damage(
            amount,
            crate::vitals::Death {
                time: self.binding.now_secs,
                frame: self.binding.frame,
                killer: self.binding.body,
            },
        );
        true
    }
    fn recharge_shields(&mut self, site: Site, amount: f64) -> bool {
        if (site.entity, site.graph, site.node) != (self.binding.body, 0x0033, Some(5))
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
        {
            return false;
        }
        self.vitals.recharge_shields(amount);
        true
    }
    fn explosion_damage(&mut self, site: Site, target: u32, amount: f64) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x0c89, Some(38))
            || !self.binding.roster.as_ref().is_some_and(|roster| {
                roster
                    .iter()
                    .any(|candidate| candidate.entity == target && candidate.alive)
            })
        {
            return false;
        }
        self.control_records.scripted_damage.push(EnemyDamage {
            source: site.entity,
            target,
            amount,
        });
        true
    }
    fn tracked_targets(
        &mut self,
        site: Site,
        origin: u32,
        offset: [f64; 3],
        radius: f64,
        filter: &Json,
    ) -> Option<Vec<u32>> {
        if site.entity != self.binding.body || origin != self.binding.body || site.graph != 0x0c89 {
            return None;
        }
        if site.node == Some(47) && filter.get("m_84065FD1").and_then(Json::as_u64) == Some(0x80) {
            // No damageable scene actor carries the gate's mask bit; its damage uses a message.
            return Some(Vec::new());
        }
        if site.node != Some(52) {
            return None;
        }
        let center: [f64; 3] = std::array::from_fn(|axis| {
            f64::from((self.binding.pose.position[axis] + offset[axis]) as f32)
        });
        let mut targets = Vec::new();
        for target in self.binding.roster.as_ref()?.iter() {
            if target.entity == origin
                || target
                    .pose
                    .feet
                    .iter()
                    .zip(center)
                    .map(|(a, b)| (a - b).powi(2))
                    .sum::<f64>()
                    .sqrt()
                    > radius
            {
                continue;
            }
            if target.alive {
                targets.push(target.entity);
            }
        }
        Some(targets)
    }
    fn witch_staff_targets(&self, site: Site, radius: f64) -> Option<Vec<u32>> {
        if self.binding.hero != WITCH || site.entity != self.binding.body {
            return None;
        }
        Some(
            self.control_records
                .witch_ally
                .filter(|_| {
                    self.control_records
                        .witch_target_distance
                        .is_some_and(|distance| distance <= radius)
                })
                .into_iter()
                .collect(),
        )
    }
    fn set_witch_staff_effect(&mut self, site: Site, effect: Option<(u32, f64)>) -> bool {
        if self.binding.hero != WITCH || site.entity != self.binding.body {
            return false;
        }
        self.control_records.witch_staff_effect = effect;
        true
    }

    fn heal_from_witch_staff(&mut self, site: Site, source: u32, amount: f64) -> bool {
        if site.entity != self.binding.body
            || site.entity == source
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        if !self.control_records.room_boss_pause && self.live_staff_source(source) {
            self.vitals.heal(amount);
        }
        true
    }
    fn combat_targets(&self, site: Site, config: &Json) -> Option<Vec<u32>> {
        if site.entity != self.binding.body
            || !matches!(site.graph, 0x0ccd | 0x0071)
            || !is_junkenstein_mode(self.binding.mode)
        {
            return None;
        }
        let primary = &config["m_9C7BD8B8"];
        match site.graph {
            0x0ccd
                if crate::script_expr::target_tag_config(primary).is_some()
                    && config["m_filter"]["m_9C7BD8B8"].is_null() => {}
            0x0071
                if primary.is_null()
                    && config["m_filter"]["m_66747E7D"] == 1
                    && config["m_filter"]["m_89831602"] == 4 => {}
            _ => return None,
        }
        Some(
            self.binding
                .roster
                .as_ref()?
                .iter()
                .map(|target| target.entity)
                .collect(),
        )
    }
    fn static_target(&self, tag: u64) -> Option<u32> {
        if tag == crate::scene_assets::JunkensteinGate::TAG {
            return self.binding.gate.map(|gate| gate.entity);
        }
        let &[entity] = self.binding.spawn_anchors.as_ref()?.targets(tag)? else {
            return None;
        };
        Some(entity)
    }
    fn for_code_transport(&self, _site: Site) -> Option<bool> {
        Some(false)
    }

    fn route_game_message(&mut self, site: Site, message: u64, parameters: &Variables) -> bool {
        if !parameters.is_empty() {
            return false;
        }
        let Some(request) = DefenderMessage::authored(site, message) else {
            return false;
        };
        if site.entity != self.binding.body
            || !is_junkenstein_mode(self.binding.mode)
            || self.control_records.defender_messages.len() >= MAX_INIT_WORK
            || (message == BOSS_INTRO_ENDED && self.messages.len() >= MAX_INIT_WORK)
        {
            return false;
        }
        self.control_records.defender_messages.push(request);
        if message == BOSS_INTRO_ENDED {
            self.messages.push_back(PairDelivery {
                target: self.binding.controller,
                input: PairInput::Message {
                    sender: site.entity,
                    message,
                    parameters: Variables::new(),
                    required_graph: Some(0x0cdc),
                },
            });
        }
        true
    }

    fn send_message(
        &mut self,
        site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if (site.entity, site.graph, site.node, target, message)
            == (
                self.binding.body,
                0x0cbc,
                Some(30),
                GAME_MODE_ENTITY,
                BOSS_INTRO_STARTED,
            )
            && is_junkenstein_mode(self.binding.mode)
            && parameters.len() == 1
            && parameters.get(&8) == Some(&Value::Entity(site.entity))
            && self.control_records.boss_intro_starts.len() < MAX_INIT_WORK
        {
            self.control_records
                .boss_intro_starts
                .push(parameters.clone());
            return true;
        }
        if message == junkenstein_messages::FLASHBANG_HIT
            && site.entity == self.binding.body
            && is_junkenstein_mode(self.binding.mode)
            && self.control_records.defenders.contains(&target)
        {
            if self.control_records.defender_messages.len() >= MAX_INIT_WORK {
                return false;
            }
            if let Some(request) = DefenderMessage::flashbang(site, target, parameters) {
                self.control_records.defender_messages.push(request);
                return true;
            }
        }
        if ![self.binding.body, self.binding.controller, GAME_MODE_ENTITY].contains(&target)
            && !self.binding.gate.is_some_and(|gate| gate.entity == target)
            || self.messages.len() >= MAX_INIT_WORK
        {
            return self.queue_defender_sighting(site, target, message, parameters);
        }
        self.messages.push_back(PairDelivery {
            target,
            input: PairInput::Message {
                sender: site.entity,
                message,
                parameters: parameters.clone(),
                required_graph: None,
            },
        });
        true
    }
    fn play_script(&mut self, caller: Site, target: u32, request: PlayScript) -> bool {
        if (caller.entity, target, caller.graph)
            != (self.binding.controller, self.binding.body, 0x0c95)
            || !matches!(
                (caller.node, request.graph),
                (Some(2), 0x0cbc) | (Some(3), 0x0ccd) | (Some(6), 0x0ce3)
            )
            || !is_junkenstein_mode(self.binding.mode)
            || self.messages.len() >= MAX_INIT_WORK
        {
            return false;
        }
        self.messages.push_back(PairDelivery {
            target,
            input: PairInput::PlayScript { caller, request },
        });
        true
    }
    fn ai_behavior_request(
        &mut self,
        site: Site,
        record: script_init::AiBehaviorRequest,
        active: bool,
    ) -> Option<bool> {
        if site.entity != self.binding.controller {
            return None;
        }
        let key = (site.entity, site.instance, site.node?);
        if !active {
            self.behavior_records.shift_remove(&key);
            return Some(true);
        }
        if let Some(previous) = self.behavior_records.get_mut(&key) {
            *previous = record;
            return Some(true);
        }
        let previous = std::mem::take(self.behavior_records);
        let mut following = Vec::new();
        for (key, other) in previous {
            if record.priority > other.priority
                || (record.prefer_later && record.priority == other.priority)
            {
                following.push((key, other));
            } else {
                self.behavior_records.insert(key, other);
            }
        }
        self.behavior_records.insert(key, record);
        self.behavior_records.extend(following);
        Some(true)
    }
    fn entity_hero(&self, site: Site, target: u32) -> Option<u64> {
        (target == self.binding.body
            && [self.binding.body, self.binding.controller].contains(&site.entity))
        .then_some(self.binding.hero)
    }
    fn entity_definition(&self, site: Site, target: u32) -> Option<u64> {
        if ![self.binding.body, self.binding.controller].contains(&site.entity) {
            return None;
        }
        if target == self.binding.body {
            Some(self.binding.pair.body)
        } else if target == self.binding.controller {
            Some(self.binding.pair.controller)
        } else {
            None
        }
    }
    fn entity_health(
        &self,
        site: Site,
        target: u32,
        kind: Option<PoolKind>,
        maximum: bool,
    ) -> Option<f64> {
        if (site.graph, site.node) == (0x01cf, Some(8)) {
            let source = self.control_records.witch_staff_sources.get(&target)?;
            let pool = match kind? {
                PoolKind::Health => 0,
                PoolKind::Armour => 1,
                PoolKind::Shields => return None,
            };
            return Some(if maximum {
                source.capacity[pool]
            } else {
                source.health[pool]
            });
        }
        (target == self.binding.body
            && [self.binding.body, self.binding.controller].contains(&site.entity))
        .then(|| self.vitals.amount(kind, maximum))
    }
    fn body_display_name(&self, site: Site, entity: u32) -> Option<String> {
        if (site.graph, site.node) != (0x01cf, Some(7)) {
            return None;
        }
        Some(
            self.control_records
                .witch_staff_sources
                .get(&entity)?
                .name
                .clone(),
        )
    }
    fn outline_context_available(&self, _site: Site) -> Option<bool> {
        Some(false)
    }
    fn set_encounter_model_enabled(&mut self, site: Site, enabled: bool) -> bool {
        const MASK: u64 = (1 << 45) | (1 << 15);
        if site.entity != self.binding.body
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
            || !matches!(
                (site.graph, site.node),
                (0x01cb, Some(20)) | (0x0ce0, Some(17)) | (0x0d03, Some(2))
            )
        {
            return false;
        }
        if enabled {
            self.control_records.model_flags &= !MASK;
        } else {
            self.control_records.model_flags |= MASK;
        }
        true
    }
    fn set_captured_aim_lock(&mut self, site: Site, degrees: Option<[f64; 2]>) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x224e, Some(34))
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        // These room-owned scene bodies have an unparented authoritative mover.
        // Foreign links and player owner-full cache replication are not supplied here.
        if let Some(degrees) = degrees {
            let Some(aim) = crate::linked_aim::unparented_lock(degrees) else {
                return false;
            };
            self.control_records.cinematic_aim.insert(site, aim);
        } else {
            self.control_records.cinematic_aim.shift_remove(&site);
        }
        true
    }

    fn set_cinematic_aim_lock(&mut self, site: Site, active: bool) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x0ce0, Some(45))
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
        {
            return false;
        }
        if active {
            let pose = self.binding.pose;
            self.control_records
                .cinematic_aim
                .insert(site, [pose.yaw, pose.pitch]);
        } else {
            self.control_records.cinematic_aim.shift_remove(&site);
        }
        true
    }
    fn set_same_team_contact_suppressed(&mut self, site: Site, suppressed: bool) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node) != (0x0cbc, Some(129))
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
        {
            return false;
        }
        if suppressed {
            self.control_records
                .same_team_contact_suppressions
                .insert(site);
        } else {
            self.control_records
                .same_team_contact_suppressions
                .remove(&site);
        }
        true
    }
    fn set_outgoing_damage_filter(&mut self, site: Site, adjustment: Option<f64>) -> bool {
        if site.entity != self.binding.body
            || (site.graph, site.node, self.binding.hero) != (0x0cbc, Some(141), ROADHOG)
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        if let Some(adjustment) = adjustment {
            let Some(excluded_tags) = self
                .graphs
                .graph(site.graph)
                .and_then(|graph| graph.node(141))
                .and_then(|node| node.field("m_5CFFCBAE"))
                .and_then(Json::as_array)
                .filter(|tags| !tags.is_empty())
                .and_then(|tags| {
                    tags.iter()
                        .map(|tag| asset_guid(tag).filter(|&guid| guid != 0))
                        .collect::<Option<Vec<_>>>()
                })
            else {
                return false;
            };
            self.control_records.outgoing_damage_filters.insert(
                site,
                OutgoingDamageFilter {
                    adjustment,
                    excluded_tags,
                },
            );
        } else {
            self.control_records
                .outgoing_damage_filters
                .shift_remove(&site);
        }
        true
    }

    fn voice_context(&mut self, site: Site) -> Option<bool> {
        (site.entity == self.binding.body
            && is_junkenstein_mode(self.binding.mode)
            && crate::npc_catalog::is_junkenstein_boss(self.binding.hero))
        .then_some(true)
    }
    fn voice_manager(&mut self, site: Site) -> Option<bool> {
        self.voice_context(site)
    }
    fn voice_clock(&mut self, site: Site, now_ms: u64) -> Option<f64> {
        self.voice_context(site).map(|_| now_ms as f64 / 1000.0)
    }
    fn voice_stimulus(
        &mut self,
        site: Site,
        guid: u64,
    ) -> Option<Option<crate::script_voice::Stimulus>> {
        self.voice_context(site)?;
        let kind = junkenstein_voice::boss_speech(self.binding.hero, self.binding.skin, guid)?;
        Some(Some(crate::script_voice::Stimulus {
            guid,
            playable: true,
            mode: kind.mode(),
        }))
    }
    fn voice_allow(&mut self, site: Site, stimulus: crate::script_voice::Stimulus) -> Option<bool> {
        self.voice_context(site)?;
        let kind =
            junkenstein_voice::boss_speech(self.binding.hero, self.binding.skin, stimulus.guid)?;
        junkenstein_voice::speech_allowed(
            kind,
            self.binding.mode_voice_set,
            junkenstein_voice::body_voice_set(self.binding.hero, self.binding.skin),
        )
    }
    fn voice_actor_required(&self, _site: Site, stimulus: crate::script_voice::Stimulus) -> bool {
        matches!(
            junkenstein_voice::boss_speech(self.binding.hero, self.binding.skin, stimulus.guid),
            Some(junkenstein_voice::BossSpeech::Defeat(_))
        )
    }
    fn voice_submit(
        &mut self,
        site: Site,
        stimulus: crate::script_voice::Stimulus,
        targets: &[u32],
        actor: Option<&Value>,
    ) -> Result<Option<u32>, &'static str> {
        use junkenstein_voice::BossSpeech;
        if self.voice_context(site).is_none() {
            return Ok(None);
        }
        let Some(kind) =
            junkenstein_voice::boss_speech(self.binding.hero, self.binding.skin, stimulus.guid)
        else {
            return Ok(None);
        };
        let hero = if matches!(kind, BossSpeech::Defeat(_)) {
            let actor = actor.ok_or("voice killer hero facts")?;
            let killer = crate::script_expr::entity_scalar(actor);
            if !crate::script_expr::valid_entity(killer)
                || [GAME_MODE_ENTITY, self.binding.controller].contains(&killer)
            {
                None
            } else if killer == self.binding.body {
                Some(self.binding.hero)
            } else {
                Some(
                    self.binding
                        .roster
                        .as_ref()
                        .and_then(|roster| roster.iter().find(|target| target.entity == killer))
                        .and_then(|target| target.hero)
                        .ok_or("voice killer hero facts")?,
                )
            }
        } else {
            None
        };
        let draw = if kind.needs_draw() {
            junkenstein_voice::voice_draw(site.entity, &mut self.control_records.voice_variation)
        } else {
            0.0
        };
        let (handle, request) =
            junkenstein_voice::speech_submission(site, kind, stimulus.guid, hero, targets, draw)?;
        if let Some(request) = request {
            if self.control_records.voice_requests.len() >= MAX_INIT_WORK {
                return Ok(None);
            }
            self.control_records.voice_requests.push(request);
        }
        Ok(Some(handle))
    }
    fn voice_duration(&mut self, site: Site, handle: u32, guid: u64, mode: bool) -> Option<f64> {
        self.voice_context(site)?;
        junkenstein_voice::boss_duration(handle, guid, mode)
    }
    fn voice_part(&mut self, site: Site, _number: u16) -> Option<f64> {
        self.voice_context(site).map(|_| 0.0)
    }

    fn play_boss_intro_voice(&mut self, site: Site, cue: IntroCue) -> Option<u64> {
        if site.entity != self.binding.body
            || site.graph != 0x0ce0
            || !is_junkenstein_mode(self.binding.mode)
            || !crate::npc_catalog::is_junkenstein_boss(self.binding.hero)
            || self.control_records.voice_requests.len() >= MAX_INIT_WORK
        {
            return None;
        }
        let lines = junkenstein_voice::intro_lines(cue, self.binding.hero, self.binding.skin);
        let Some((line, duration_ms)) = junkenstein_voice::select_line(
            lines,
            site.entity,
            &mut self.control_records.voice_variation,
        ) else {
            return Some(0);
        };
        self.control_records.voice_requests.push(VoiceRequest {
            anchor: site.entity,
            speaker: if cue.narrator {
                GAME_MODE_ENTITY
            } else {
                site.entity
            },
            stimulus: cue.stimulus,
            line,
        });
        Some(duration_ms)
    }
    fn set_ai_control_setting(&mut self, site: Site, scalar: f32, flag: bool) -> bool {
        if site.entity != self.binding.body
            || self.binding.hero != SHOCK_TIRE
            || !is_junkenstein_mode(self.binding.mode)
        {
            return false;
        }
        self.control_records.ai_control_setting = Some((scalar, flag));
        true
    }

    fn local_combat_context_available(&self, _site: Site) -> Option<bool> {
        Some(false)
    }
    fn ai_control_override(
        &mut self,
        site: Site,
        record: script_init::AiControlOverride,
        active: bool,
    ) -> Option<bool> {
        if record.target != self.binding.body {
            return None;
        }
        let key = (site.entity, site.instance, site.node?);
        self.control_records.overrides.shift_remove(&key);
        if active {
            let previous = std::mem::take(&mut self.control_records.overrides);
            let mut following = Vec::new();
            for (key, other) in previous {
                if record.priority > other.priority
                    || (record.prefer_later && record.priority == other.priority)
                {
                    following.push((key, other));
                } else {
                    self.control_records.overrides.insert(key, other);
                }
            }
            self.control_records.overrides.insert(key, record);
            self.control_records.overrides.extend(following);
        }
        Some(true)
    }
    fn ai_facing(
        &mut self,
        site: Site,
        record: script_init::AiFacing,
        active: bool,
    ) -> Option<bool> {
        if site.entity != self.binding.controller
            || !is_junkenstein_mode(self.binding.mode)
            || !matches!(
                (site.graph, site.node),
                (0x0c95, Some(5)) | (0x185d, Some(57))
            )
        {
            return None;
        }
        if record.target != self.binding.body || (active && self.vitals.death().is_some()) {
            return Some(false);
        }
        if active {
            self.control_records.facing.insert(site, record);
        } else {
            self.control_records.facing.shift_remove(&site);
        }
        Some(true)
    }

    fn ai_weapon_override(
        &mut self,
        site: Site,
        record: AiWeaponOverride,
        active: bool,
    ) -> Option<bool> {
        if record.target != self.binding.body
            || ![self.binding.body, self.binding.controller].contains(&site.entity)
            || site.node.is_none()
        {
            return None;
        }
        let control = self.graphs.enemy_body(self.binding.pair.controller)?;
        let identifiers = control
            .ai_weapon_identifiers
            .get(&record.slot.to_string())
            .and_then(Json::as_array);
        if identifiers.is_none_or(|values| {
            !values
                .iter()
                .any(|value| value.as_u64() == Some(record.identifier))
        }) {
            return Some(false);
        }
        self.control_records.weapons.shift_remove(&site);
        if active {
            let index = self
                .control_records
                .weapons
                .values()
                .position(|other| {
                    record.priority > other.priority
                        || (record.prefer_later && record.priority == other.priority)
                })
                .unwrap_or(self.control_records.weapons.len());
            self.control_records
                .weapons
                .shift_insert(index, site, record);
        }
        Some(true)
    }
    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if self.binding.retired_entities.contains(&entity) {
            return Some(Value::Null);
        }
        let variables = if entity == GAME_MODE_ENTITY {
            &self.binding.mode_variables
        } else if entity == self.binding.body {
            self.body_variables
        } else if entity == self.binding.controller {
            self.controller_variables
        } else {
            return self
                .binding
                .roster
                .as_ref()?
                .iter()
                .find(|target| target.entity == entity)?
                .script_state
                .read(id);
        };
        Some(variables.get(&id).cloned().unwrap_or(Value::Null))
    }
    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.known_position(entity)
    }
    fn forward(&self, entity: u32) -> Option<[f64; 3]> {
        self.binding.spawn_anchors.as_ref()?.forward(entity)
    }
    fn targets_with_tag(&self, site: Site, tag: u64, excluded: u64) -> Option<Vec<u32>> {
        if (site.entity, site.graph, site.node) != (self.binding.body, 0x00ca, Some(33))
            || !is_junkenstein_mode(self.binding.mode)
            || (tag, excluded) != (0x0d60_0000_0000_0350, 0x0d60_0000_0000_0353)
        {
            return None;
        }
        let anchors = self.binding.spawn_anchors.as_ref()?;
        let excluded_placements = anchors.targets(excluded)?;
        let mut targets: Vec<_> = anchors
            .targets(tag)?
            .iter()
            .copied()
            .filter(|target| !excluded_placements.contains(target))
            .collect();
        // These encounter tags have no producer on foreign combat bodies.
        if body_has_target_tag(self.graphs, self.binding, self.target_tag_effects, tag)
            == Some(true)
            && body_has_target_tag(self.graphs, self.binding, self.target_tag_effects, excluded)
                != Some(true)
        {
            targets.push(self.binding.body);
        }
        Some(targets)
    }

    fn tagged_placements(&self, site: Site, tag: u64) -> Option<&[u32]> {
        if (site.entity, site.graph, site.node) != (self.binding.controller, 0x0c95, Some(9))
            || !is_junkenstein_mode(self.binding.mode)
        {
            return None;
        }
        self.binding.spawn_anchors.as_ref()?.targets(tag)
    }
    fn movement_sample(&self, site: Site) -> Option<u16> {
        (site.entity == self.binding.body)
            .then(|| sample_movement_flags(&self.binding.pose))
            .flatten()
    }
    fn total_health(&self, site: Site, entity: u32) -> crate::script_expr::HealthQuery {
        use crate::script_expr::HealthQuery;
        if ![self.binding.body, self.binding.controller].contains(&site.entity)
            || !is_junkenstein_mode(self.binding.mode)
        {
            return HealthQuery::Unavailable;
        }
        if entity == 0 {
            return HealthQuery::Unbound;
        }
        if entity == self.binding.body {
            return HealthQuery::Present(self.vitals.total());
        }
        self.binding
            .roster
            .as_ref()
            .and_then(|roster| roster.iter().find(|target| target.entity == entity))
            .and_then(|target| target.health)
            .map_or(HealthQuery::Unavailable, HealthQuery::Present)
    }

    fn body_health(&self, site: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
        (site.entity == self.binding.body).then(|| self.vitals.amount(kind, maximum))
    }
    fn add_health_pool(
        &mut self,
        site: Site,
        kind: PoolKind,
        maximum: f64,
        current: f64,
    ) -> Option<u32> {
        (site.entity == self.binding.body)
            .then(|| self.vitals.add_pool(kind, maximum, current))
            .flatten()
    }
    fn health_pool_current(&self, site: Site, pool: u32) -> Option<f64> {
        (site.entity == self.binding.body)
            .then(|| self.vitals.pool_current(pool))
            .flatten()
    }
    fn remove_health_pool(&mut self, site: Site, pool: u32) -> Option<f64> {
        (site.entity == self.binding.body)
            .then(|| self.vitals.remove_pool(pool))
            .flatten()
    }
    fn add_wave_health(&mut self, site: Site, amount: f64) -> Option<Option<u32>> {
        if site.entity != self.binding.body || site.graph != 0x0cbc {
            return None;
        }
        if self.vitals.death().is_some() {
            return Some(None);
        }
        self.vitals.add_wave_health(amount).map(Some)
    }
    fn body_scale(&mut self, site: Site, target: u32, overridden: &Value) -> Option<f64> {
        (site.entity == self.binding.body
            && target == self.binding.body
            && matches!(overridden, Value::Null | Value::Integer(1)))
        .then_some(1.0)
    }
    fn has_target_tag(&self, site: Site, entity: u32, tag: u64) -> Option<bool> {
        if site.entity != self.binding.body && site.entity != self.binding.controller {
            return None;
        }
        if entity == self.binding.body {
            return body_has_target_tag(self.graphs, self.binding, self.target_tag_effects, tag);
        }
        if entity != self.binding.controller {
            return self
                .binding
                .roster
                .as_ref()?
                .iter()
                .find(|target| target.entity == entity)?
                .tags
                .presence(tag);
        }
        let tags: &BTreeSet<_> = self
            .graphs
            .enemy_body(self.binding.pair.controller)?
            .target_tags
            .as_ref()?;
        Some(tags.contains(&tag))
    }
    fn set_target_tag_effect(&mut self, site: Site, effect: TargetTagEffect, active: bool) -> bool {
        site.entity == self.binding.body
            && effect.entity == self.binding.body
            && self.target_tag_effects.apply(effect, active)
    }
    fn body_on_fire(&mut self, site: Site, entity: u32) -> Option<OnFire> {
        // The scene controller is linked, but has no GMP player stats.
        ((site.entity == self.binding.body || site.entity == self.binding.controller)
            && entity == self.binding.body)
            .then_some(OnFire::NoStats)
    }

    fn entity_component92(&self, site: Site, entity: u32) -> Option<bool> {
        if site.entity != self.binding.body && site.entity != self.binding.controller {
            return None;
        }
        let definition = if entity == self.binding.controller {
            self.binding.pair.controller
        } else if entity == self.binding.body {
            self.binding.pair.body
        } else if site.graph == 0x0079
            && self
                .control_records
                .witch_staff_sources
                .values()
                .any(|source| source.controller == entity)
        {
            self.graphs.enemy_pair(WITCH)?.controller
        } else {
            return None;
        };
        self.graphs.enemy_body(definition)?.component92_present
    }

    fn related_entity(&mut self, site: Site, relation: EntityRelation, entity: u32) -> Option<u32> {
        match relation {
            EntityRelation::OwningController
            | EntityRelation::CurrentPossessor
            | EntityRelation::AiControlEntity
                if entity == self.binding.body =>
            {
                Some(self.binding.controller)
            }
            EntityRelation::AiControlEntity if entity == self.binding.controller => {
                Some(self.binding.controller)
            }
            EntityRelation::PossessedBody if entity == self.binding.controller => {
                Some(self.binding.body)
            }
            EntityRelation::AiControlEntity if site.graph == 0x0079 => self
                .control_records
                .witch_staff_sources
                .get(&entity)
                .map(|source| source.controller),
            _ => None,
        }
    }
    fn linked_entity(&mut self, _site: Site, entity: u32, slot: u32) -> Option<u32> {
        if entity != self.binding.controller {
            return None;
        }
        match (self.binding.hero, slot) {
            (SUMMONER, 0 | 1) => {
                Some(self.control_records.summoner_targets[slot as usize].unwrap_or(0))
            }
            (_, 0) => Some(
                self.control_records
                    .weapon_target
                    .map_or(0, |target| target.entity),
            ),
            (WITCH, 1) => Some(self.control_records.witch_ally.unwrap_or(0)),
            _ => None,
        }
    }

    fn scene_filter_policy(
        &self,
        site: Site,
        filter: &Json,
    ) -> Option<crate::script_expr::SceneFilterPolicy> {
        use crate::script_expr::{FilterPredicate, FilterRelation, SceneFilterPolicy};
        if site.entity != self.binding.body || !is_junkenstein_mode(self.binding.mode) {
            return None;
        }
        let hostile = matches!(site.graph, 0x05ac | 0x01a3 | 0x157c)
            && filter["m_66747E7D"] == 2
            && filter
                .get("m_89831602")
                .is_none_or(|value| value.is_null() || value.as_u64() == Some(0));
        let friendly = matches!(site.graph, 0x006b | 0x0071)
            && filter["m_66747E7D"] == 1
            && filter["m_89831602"] == 4;
        if !hostile && !friendly {
            return None;
        }
        let predicate = filter
            .get("m_condition")
            .is_some_and(|value| !value.is_null());
        if hostile && predicate && site.graph != 0x01a3 {
            return None;
        }
        for key in ["m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3"] {
            if filter.get(key).is_some_and(|value| {
                !value.is_null() && crate::script_expr::target_tag_config(value).is_none()
            }) {
                return None;
            }
        }
        Some(SceneFilterPolicy {
            relation: if friendly {
                FilterRelation::FriendlyOther
            } else {
                FilterRelation::Hostile
            },
            predicate: if !predicate {
                FilterPredicate::None
            } else if friendly {
                FilterPredicate::Target
            } else {
                FilterPredicate::Scalar
            },
        })
    }

    fn entity_filter_flags(&self, _site: Site, entity: u32) -> Option<u64> {
        if entity == self.binding.body {
            return Some(
                1 | (0x0080_0000 << self.binding.team)
                    | if self.vitals.death().is_some() {
                        0x10
                    } else {
                        0
                    },
            );
        }
        self.binding
            .roster
            .as_ref()?
            .iter()
            .find(|target| target.entity == entity)?
            .filter_flags
    }
    fn native_uses_initial(&self, site: Site, config: &Json) -> bool {
        if site.entity == self.binding.body
            && self.binding.hero == SHOCK_TIRE
            && is_junkenstein_mode(self.binding.mode)
            && native_shape(config, "STU_0340A42A", Some("m_entity"))
            && native_shape(&config["m_entity"], "STU_B5A0CAF0", None)
        {
            // The autonomous tire has no owning scene participant.
            return true;
        }
        site.entity == self.binding.controller
            && site.graph == 0x0c83
            && is_junkenstein_mode(self.binding.mode)
            && crate::script_expr::native_fields(config, &["m_resourceKey", "m_00903303"])
            && config
                .get("m_00903303")
                .is_some_and(|owner| native_shape(owner, "STU_B5A0CAF0", None))
    }

    fn selects_ai_behavior(&mut self, site: Site) -> Option<bool> {
        // The encounter host drives its own behavior requests. These dormant
        // candidates have no selecting service in the reference scene.
        (site.entity == self.binding.controller
            && is_junkenstein_mode(self.binding.mode)
            && matches!(
                (site.graph, site.node),
                (0x06fe, Some(45))
                    | (0x0bc8 | 0x0bca | 0x0bd0 | 0x0cdc | 0x0ce2, Some(2))
                    | (0x0c83, Some(3))
                    | (0x0c88, Some(0))
                    | (0x0cc1, Some(20))
                    | (0x0b6f | 0x0cd0, Some(1))
                    | (0x0cd9, Some(25))
            ))
        .then_some(false)
    }

    fn set_display_name(&mut self, site: Site, entity: u32, name: u64, active: bool) -> bool {
        if entity != self.binding.controller || self.entity_component92(site, entity) != Some(true)
        {
            return false;
        }
        if active {
            self.control_records.display_names.insert(site, name);
        } else {
            self.control_records.display_names.shift_remove(&site);
        }
        true
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        if self.binding.hero == SUMMONER
            && (site.entity, site.graph, site.node) == (self.binding.body, 0x157c, Some(48))
            && crate::script_expr::native_fields(config, &["m_entity", "m_filter"])
            && config["$"] == "STU_AC829876"
            && config["m_filter"]["m_84065FD1"] == 64
            && crate::script_expr::dynamic(&config["m_entity"])
                .ok()
                .flatten()
                .is_some_and(|target| target.id == 536)
        {
            // The beam can only acquire defenders, which have no world-object mask bit 0x40.
            return Some(Value::Bool(false));
        }
        if (site.entity, site.graph, site.node) == (self.binding.body, 0x0079, Some(0))
            && is_junkenstein_mode(self.binding.mode)
            && config["$"] == "STU_4049CF77"
            && crate::script_expr::dynamic(&config["m_C5D0FA56"])
                .ok()
                .flatten()
                .is_some_and(|source| source.id == 27)
            && asset_guid(&config["m_F00C2F3D"]["m_identifier"]) == Some(0x0d80_0000_0000_2162)
        {
            let tier = match self.binding.mode_variables.get(&6751)? {
                Value::Asset(0x0d80_0000_0000_195e) => 1.0,
                Value::Asset(0x0d80_0000_0000_195f) => 2.0,
                Value::Asset(0x0d80_0000_0000_26df) => 3.0,
                Value::Asset(0x0d80_0000_0000_1960) => 4.0,
                _ => return None,
            };
            return Some(Value::Float(tier));
        }
        if (site.entity, site.graph, site.node, self.binding.hero)
            == (self.binding.body, 0x157e, Some(17), 0x02e0_0000_0000_0016)
            && is_junkenstein_mode(self.binding.mode)
            && config["$"] == "STU_967463AB"
            && crate::script_expr::native_fields(config, &["m_92D12BE6", "m_C8655030"])
            && native_shape(&config["m_92D12BE6"], "STU_DB742151", Some("m_entity"))
            && native_shape(&config["m_92D12BE6"]["m_entity"], "STU_B5A0CAF0", None)
            && asset_guid(&config["m_C8655030"]["m_entityDef"]) == Some(0x0400_0000_0000_2658)
        {
            // A newly initialized Summoner owns no deployables.
            return Some(Value::Integer(0));
        }
        if site.entity == self.binding.body
            && is_junkenstein_mode(self.binding.mode)
            && native_shape(config, "STU_D3685C04", None)
        {
            return Some(Value::Bool(false));
        }
        if site.entity == self.binding.body && is_junkenstein_mode(self.binding.mode) {
            if config.get("$").and_then(Json::as_str) == Some("STU_4049CF77")
                && config.get("m_C5D0FA56").is_none_or(Json::is_null)
                && config["m_F00C2F3D"]["$"] == "STUConfigVarIdentifier"
                && crate::script_expr::native_fields(
                    &config["m_F00C2F3D"],
                    &["m_identifier", "m_resourceKey"],
                )
                && config["m_F00C2F3D"]["m_resourceKey"].is_null()
            {
                let identifier = asset_guid(&config["m_F00C2F3D"]["m_identifier"])?;
                let amounts = match (
                    site.graph,
                    site.node,
                    self.binding.pair.body,
                    self.binding.pair.controller,
                    identifier,
                ) {
                    (
                        0x0019,
                        Some(10),
                        0x0400_0000_0000_0022,
                        0x0400_0000_0000_1958,
                        0x0d80_0000_0000_1961,
                    ) => [1.75, 3.5, 5.25, 7.0],
                    (
                        0x01c6,
                        Some(26 | 29),
                        0x0400_0000_0000_028f,
                        0x0400_0000_0000_1959,
                        0x0d80_0000_0000_1961 | 0x0d80_0000_0000_1962,
                    ) => [f64::from(1.8000001_f32), 4.5, 6.75, 9.0],
                    (
                        0x01c6,
                        Some(29),
                        0x0400_0000_0000_028f,
                        0x0400_0000_0000_1959,
                        0x0d80_0000_0000_1c98,
                    ) => [10.0, 25.0, 37.5, 50.0],
                    (
                        0x022b,
                        Some(31),
                        0x0400_0000_0000_0380,
                        0x0400_0000_0000_16fb,
                        0x0d80_0000_0000_1961,
                    ) => [15.0, 30.0, 30.0, 30.0],
                    (
                        0x022b,
                        Some(31),
                        0x0400_0000_0000_0380,
                        0x0400_0000_0000_16fb,
                        0x0d80_0000_0000_1962,
                    ) => [10.0, 20.0, 20.0, 20.0],
                    (
                        0x006d,
                        Some(10),
                        0x0400_0000_0000_00e4,
                        0x0400_0000_0000_172e,
                        0x0d80_0000_0000_1961,
                    ) => [5.0, 10.0, 15.0, 20.0],
                    (
                        0x0ca1,
                        Some(0),
                        0x0400_0000_0000_171c,
                        0x0400_0000_0000_171d,
                        0x0d80_0000_0000_1961,
                    ) => [30.0, 60.0, 60.0, 90.0],
                    (
                        0x157c,
                        Some(60),
                        0x0400_0000_0000_26f0,
                        0x0400_0000_0000_1dd3,
                        0x0d80_0000_0000_1961,
                    ) => [f64::from(3.2_f32), 8.0, 12.0, 16.0],
                    (
                        0x157c,
                        Some(207),
                        0x0400_0000_0000_26f0,
                        0x0400_0000_0000_1dd3,
                        0x0d80_0000_0000_1962,
                    ) => [16.0, 40.0, 60.0, 80.0],
                    _ => return None,
                };
                let difficulty = match self.binding.mode_variables.get(&6751)? {
                    Value::Asset(0x0d80_0000_0000_195e) => 0,
                    Value::Asset(0x0d80_0000_0000_195f) => 1,
                    Value::Asset(0x0d80_0000_0000_26df) => 2,
                    Value::Asset(0x0d80_0000_0000_1960) => 3,
                    _ => return None,
                };
                return Some(Value::Float(amounts[difficulty]));
            }
            if matches!(
                (site.graph, site.node),
                (0x01ca, Some(126)) | (0x00cb | 0x14b1, _)
            ) && junkenstein_messages::sighting_selector(config)
            {
                return Some(junkenstein_messages::recipients(
                    &self.control_records.defenders,
                ));
            }
            if matches!(
                (site.graph, site.node),
                (0x01ca, Some(127)) | (0x00cb | 0x14b1, _)
            ) && junkenstein_messages::self_team_query(config)
            {
                return Some(Value::Integer(i64::from(self.binding.team)));
            }
        }
        if native_shape(config, "STU_A3DEF368", None) {
            Some(Value::Entity(GAME_MODE_ENTITY))
        } else if native_shape(config, "STU_37F20D24", Some("m_resourceKey"))
            && config.get("m_resourceKey").is_none_or(Json::is_null)
        {
            Some(Value::Asset(self.binding.mode))
        } else if native_shape(config, "STU_A77AC2AC", None) {
            Some(Value::Bool(self.binding.mode == TUTORIAL_MODE))
        } else {
            None
        }
    }
}

type ShotConfigError = crate::script_expr::EvalError;

fn damage_tags<'a>(
    direct: &'a Json,
    evaluate: &'a mut impl FnMut(&Json) -> Result<Value, ShotConfigError>,
) -> impl Iterator<Item = u64> + 'a {
    direct["m_modifyHealth"]["m_148F3152"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|entry| match evaluate(&entry["m_EE3055BD"]).ok()? {
            Value::Asset(tag) => Some(tag),
            _ => None,
        })
}

fn projectile_pattern(
    fields: &Json,
    forward: [f64; 3],
    shot: &script_init::ShotEvent,
    position: u16,
    evaluate: &mut impl FnMut(&Json) -> Result<Value, ShotConfigError>,
) -> Option<ProjectilePattern> {
    let ballistics = ProjectileBallistics::from_fields(fields, &mut *evaluate).ok()??;
    let directions = match fields["m_5EC1EB38"]["$"].as_str()? {
        "STU_613BDDB0" => shotgun_directions(fields, forward, shot, position, evaluate)?,
        "STU_239B571E" => vec![forward],
        _ => return None,
    };
    Some(ProjectilePattern {
        ballistics,
        gravity: None,
        directions,
        damage_tags: damage_tags(&fields["m_6396149F"], evaluate).collect(),
    })
}

fn shotgun_directions(
    fields: &Json,
    forward: [f64; 3],
    shot: &script_init::ShotEvent,
    position: u16,
    evaluate: &mut impl FnMut(&Json) -> Result<Value, ShotConfigError>,
) -> Option<Vec<[f64; 3]>> {
    use crate::script_expr::{to_float, to_int};

    let launch = &fields["m_5EC1EB38"];
    if launch["$"] != "STU_613BDDB0" {
        return None;
    }
    let cone = to_float(&evaluate(&launch["m_spread"]).ok()?);
    let count = to_int(&evaluate(&fields["m_numProjectilesPerShot"]).ok()?, 0);
    if !(0.0..90.0).contains(&cone) || !(1..=32).contains(&count) {
        return None;
    }
    let rings: Vec<_> = launch["m_0D30E40C"]
        .as_array()?
        .iter()
        .map(|row| {
            let radius = to_float(&evaluate(&row["m_6425FFB9"]).ok()?);
            let size = to_int(&evaluate(&row["m_B19BD35D"]).ok()?, 0);
            ((0.0..=1.0).contains(&radius) && (1..=count).contains(&size))
                .then_some((radius, size as usize))
        })
        .collect::<Option<_>>()?;
    (rings.iter().map(|(_, size)| size).sum::<usize>() == count as usize).then(|| {
        crate::weapon_spread::ring_directions(forward, cone.to_radians(), &rings, shot, position)
            .collect()
    })
}

impl EnemyScripts {
    pub(crate) fn take_defender_messages(&mut self) -> Vec<DefenderMessage> {
        std::mem::take(&mut self.control_records.defender_messages)
    }

    pub(crate) fn take_voice_requests(&mut self) -> Vec<VoiceRequest> {
        std::mem::take(&mut self.control_records.voice_requests)
    }

    pub(crate) fn take_boss_intro_starts(&mut self) -> Vec<Variables> {
        std::mem::take(&mut self.control_records.boss_intro_starts)
    }

    pub(crate) fn target_tags(
        &self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
    ) -> script_init::TargetTagSnapshot {
        self.target_tag_effects.snapshot(
            graphs
                .enemy_body(binding.pair.body)
                .and_then(|body| body.target_tags.as_ref()),
        )
    }

    pub(crate) fn body_has_target_tag(
        &self,
        graphs: &GraphStore,
        binding: &EnemyBinding,
        tag: u64,
    ) -> Option<bool> {
        body_has_target_tag(graphs, binding, &self.target_tag_effects, tag)
    }

    pub(crate) fn set_defenders(&mut self, defenders: Arc<[u32]>) {
        self.control_records.defenders = defenders;
    }

    pub(crate) fn take_sightings(&mut self) -> Vec<DefenderSighting> {
        std::mem::take(&mut self.control_records.sightings)
    }
}

fn body_has_target_tag(
    graphs: &GraphStore,
    binding: &EnemyBinding,
    effects: &TargetTagEffects,
    tag: u64,
) -> Option<bool> {
    effects.presence(tag).or_else(|| {
        let tags = graphs.enemy_body(binding.pair.body)?.target_tags.as_ref()?;
        Some(tags.contains(&tag))
    })
}

impl EnemyWorld<'_> {
    fn queue_defender_sighting(
        &mut self,
        site: Site,
        target: u32,
        message: u64,
        parameters: &Variables,
    ) -> bool {
        if is_junkenstein_mode(self.binding.mode)
            && site.entity == self.binding.body
            && matches!(
                (site.graph, site.node),
                (0x01ca, Some(127)) | (0x00cb | 0x14b1, _)
            )
            && message == SIGHTING
            && self.control_records.defenders.contains(&target)
        {
            if self.control_records.sightings.len() >= MAX_INIT_WORK {
                return false;
            }
            let tags = SIGHTING_TAGS.map(|tag| self.has_target_tag(site, site.entity, tag));
            self.control_records.sightings.push(DefenderSighting {
                target,
                source: SightingSource {
                    entity: site.entity,
                    position: self.binding.pose.position,
                    tags,
                    filter_flags: self.entity_filter_flags(site, site.entity),
                },
                parameters: parameters.clone(),
            });
            return true;
        }
        false
    }
}

#[cfg(test)]
pub(crate) fn fixture(
    fail_controller: bool,
) -> (
    Arc<GraphStore>,
    Arc<crate::script_codecs::CodecTable>,
    NpcDefinition,
    script_init::EntityCreation,
) {
    fixture_with_graphs(fail_controller, &[])
}

#[cfg(test)]
fn fixture_with_graphs(
    fail_controller: bool,
    extra: &[Json],
) -> (
    Arc<GraphStore>,
    Arc<crate::script_codecs::CodecTable>,
    NpcDefinition,
    script_init::EntityCreation,
) {
    use serde_json::json;
    let graph = |index, fail| {
        json!({"index":index,"items":if fail {2}else{0},
        "nodes":if fail {json!([{"pos":0,"class":"STUStatescriptEntry","uid":0,"rid":0,"remote":-1,
            "state":null,"client_only":false,"server_only":false,"fields":{"m_outPlug":{"$":"Plug","links":[[1,"m_inPlug"]],"m_FF3DAF1E":[]}}},
            {"pos":1,"class":"UnsupportedFixtureAction","uid":1,"rid":1,"remote":-1,
            "state":null,"client_only":false,"server_only":false,"fields":{"m_ADEB6E05":true}}
            ])}else{json!([])},
        "states":[],"entries":if fail {json!([0])}else{json!([])},"remote":[],"sync_vars":[],"containers":{},
        "unknown_nodes":[],"fields":{},"header":{"m_nodesBitCount":4,"m_statesBitCount":4,
        "m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}})
    };
    let body = json!({"hero":"0x02E0000000000175","name":"Fixture","manager":null,"weapons":[],
        "graphs":[{"graph":42,"m_1EB5A024":null,"m_75408640":0}],"target_tags":[]});
    let control = json!({"hero":null,"name":"Fixture control","manager":null,"weapons":[],
        "ai_weapon_identifiers":{"0":[0x0d80_0000_0000_023au64]},
        "graphs":[{"graph":43,"m_1EB5A024":null,"m_75408640":0}],"target_tags":[]});
    let mut data = json!({"version":1,"graphs":[graph(42,false),graph(43,fail_controller)],"bodies":{},"entities":{},"missing":[],
        "junkenstein":{"pairs":{"0x02E0000000000175":{"body":"0x04000000000016BE","controller":"0x04000000000016BF"}},
        "entities":{"0x04000000000016BE":body,"0x04000000000016BF":control}}});
    data["graphs"]
        .as_array_mut()
        .unwrap()
        .extend_from_slice(extra);
    let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let codecs =
        Arc::new(crate::script_codecs::CodecTable::from_json(r#"{"classes":{}}"#).unwrap());
    let definition = NpcDefinition {
        hero: 0x02e0_0000_0000_0175,
        body: 0x0400_0000_0000_16be,
        name: "Fixture".into(),
        pools: crate::game_pools::HealthPools {
            health: 200.0,
            armour: 50.0,
            shields: 25.0,
        },
    };
    let request = script_init::EntityCreation {
        position: [1.0, 2.0, 3.0],
        kind: script_init::CreationKind::Hero {
            hero: definition.hero,
            team: 1,
            yaw: 90.0,
            flags: [Value::Bool(false), Value::Bool(false)],
            controller: None,
            skin: Some(0x0b20_0000_0000_0010),
        },
    };
    (graphs, codecs, definition, request)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_captured_aim_lifecycle_contract() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-captured-aim-lock-v1.json"
        ))
        .unwrap();
        let (graphs, _, definition, _) = fixture_with_graphs(false, &[fixture["graph"].clone()]);
        let binding = EnemyBinding {
            body: 0xc0001000,
            controller: 0xc0001001,
            hero: definition.hero,
            pair: graphs.enemy_pair(definition.hero).unwrap(),
            mode: crate::game_content::JUNKENSTEIN_REVENGE,
            mode_variables: Arc::default(),
            team: 1,
            skin: None,
            pose: Movement::default(),
            gate: None,
            roster: None,
            retired_entities: Arc::default(),
            frame: 0,
            now_secs: 0.0,
            spawn_anchors: None,
            mode_voice_set: None,
        };
        let mut vitals = definition.pools.vitals();
        let mut retirement_requested = false;
        let mut messages = VecDeque::new();
        let mut tags = TargetTagEffects::default();
        let mut behaviors = Default::default();
        let mut control = AiControlRecords::default();
        let empty = Variables::new();
        let earlier = Site {
            entity: binding.body,
            instance: 99,
            graph: 0x0ce0,
            node: Some(45),
        };
        control.cinematic_aim.insert(earlier, [8192, 0]);
        let mut world = EnemyWorld {
            retirement_requested: &mut retirement_requested,
            messages: &mut messages,
            target_tag_effects: &mut tags,
            behavior_records: &mut behaviors,
            control_records: &mut control,
            binding: &binding,
            graphs: &graphs,
            vitals: &mut vitals,
            body_variables: &empty,
            controller_variables: &empty,
        };
        let mut roots = Vec::new();
        for (id, yaw, pitch) in [(1, 30.5, -22.5), (2, 90.0, 90.0)] {
            let mut root = script_init::Root::new(id, 0x224e);
            root.variables
                .extend([(501, Value::Float(yaw)), (502, Value::Float(pitch))]);
            roots.push(root);
        }
        let mut component = Component::start(
            Arc::clone(&graphs),
            binding.body,
            Variables::new(),
            roots,
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        let first = Site {
            instance: 1,
            graph: 0x224e,
            node: Some(34),
            ..earlier
        };
        let second = Site {
            instance: 2,
            ..first
        };
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert_eq!(world.control_records.cinematic_aim[&first], [5552, -4096]);
        assert_eq!(world.control_records.cinematic_aim[&second], [16384, 16202]);
        assert!(matches!(
            snapshot.instances[&1].states[&0].kind,
            script_init::StateKind::Registered { next: Some((2, 0)) }
        ));
        component
            .set_test_instance_variable(1, 501, Value::Float(-90.0), &mut world)
            .unwrap();
        component.advance_frame(1, &mut world).unwrap();
        assert_eq!(
            world.control_records.cinematic_aim[&first],
            [5552, -4096],
            "selector1 retains captured angles"
        );
        component
            .queue_message(2, 0x0240000000000002, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert!(!world.control_records.cinematic_aim.contains_key(&second));
        assert_eq!(
            world.control_records.cinematic_aim.last(),
            Some((&first, &[5552, -4096]))
        );
        assert!(matches!(
            component.snapshot().unwrap().instances[&1].states[&0].kind,
            script_init::StateKind::Registered { next: None }
        ));
        component
            .queue_message(1, 0x0240000000000002, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(
            world.control_records.cinematic_aim.last(),
            Some((&earlier, &[8192, 0]))
        );
        component
            .queue_message(1, 0x0240000000000003, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(world.control_records.cinematic_aim[&first], [-16384, -4096]);
        assert!(!world.set_captured_aim_lock(
            Site {
                node: Some(35),
                ..first
            },
            Some([0.0, 0.0])
        ));
        assert!(!world.set_captured_aim_lock(first, Some([f64::NAN, 0.0])));
        component
            .queue_message(1, 0x0240000000000002, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(world.control_records.cinematic_aim.len(), 1);
    }

    #[test]
    fn shared_flashbang_velocity_action_contract() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-horizontal-cap-v1.json"
        ))
        .unwrap();
        for (low, high, selector, encounter, accepted) in [
            (0.0, 3.0, 1, true, true),
            (0.0, 1.5, 1, true, true),
            (1.0, 3.0, 1, true, false),
            (0.0, -1.0, 1, true, false),
            (0.0, 3.0, 0, true, false),
            (0.0, 3.0, 1, false, false),
        ] {
            let mut graph = fixture["graph"].clone();
            let row = &mut graph["nodes"][1]["fields"]["m_BC5E91CF"][0];
            row["m_8AD739CE"]["m_value"] = serde_json::json!(low);
            row["m_8213EB22"]["m_value"] = serde_json::json!(high);
            row["m_A9561CA0"] = serde_json::json!(selector);
            let (graphs, _, definition, _) = fixture_with_graphs(false, &[graph]);
            let binding = EnemyBinding {
                body: 0xc000_1000,
                controller: 0xc000_1001,
                hero: definition.hero,
                pair: graphs.enemy_pair(definition.hero).unwrap(),
                mode: if encounter {
                    crate::game_content::JUNKENSTEIN_REVENGE
                } else {
                    TUTORIAL_MODE
                },
                mode_variables: Arc::default(),
                team: 1,
                skin: None,
                pose: Movement::default(),
                gate: None,
                roster: None,
                retired_entities: Arc::default(),
                frame: 100,
                now_secs: 1.6,
                spawn_anchors: None,
                mode_voice_set: None,
            };
            let mut vitals = definition.pools.vitals();
            let mut retirement_requested = false;
            let mut messages = VecDeque::new();
            let mut tags = TargetTagEffects::default();
            let mut behaviors = Default::default();
            let mut control = AiControlRecords::default();
            let empty = Variables::new();
            let mut world = EnemyWorld {
                retirement_requested: &mut retirement_requested,
                messages: &mut messages,
                target_tag_effects: &mut tags,
                behavior_records: &mut behaviors,
                control_records: &mut control,
                binding: &binding,
                graphs: &graphs,
                vitals: &mut vitals,
                body_variables: &empty,
                controller_variables: &empty,
            };
            let component = Component::start(
                Arc::clone(&graphs),
                binding.body,
                Variables::new(),
                vec![script_init::Root::new(1, 0x224e)],
                100,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            assert!(
                snapshot.initialization_complete(),
                "{:?}",
                snapshot.diagnostics
            );
            assert_eq!(
                snapshot.variables.get(&900),
                accepted.then_some(&Value::Bool(true))
            );
            assert_eq!(
                world.control_records.velocity_changes,
                if accepted {
                    vec![(101, crate::mover::VelocityChange::HorizontalCap(high))]
                } else {
                    vec![]
                }
            );
            if accepted {
                let site = Site {
                    entity: binding.body,
                    instance: 1,
                    graph: 0x224e,
                    node: Some(31),
                };
                assert!(!world.edit_velocity(
                    Site {
                        node: Some(30),
                        ..site
                    },
                    100,
                    Value::Float(3.0),
                    true
                ));
                assert!(!world.edit_velocity(site, 100, Value::Float(3.0), false));
                assert!(!world.edit_velocity(site, 100, Value::Float(f64::INFINITY), true));
            }
        }
    }

    #[test]
    fn shared_flashbang_targeted_message_contract() {
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-targeted-message-v1.json"
        ))
        .unwrap();
        let (graphs, _, definition, _) = fixture_with_graphs(
            false,
            &[
                contract["sender_graph"].clone(),
                contract["receiver_graph"].clone(),
            ],
        );
        let source = contract["source"].as_u64().unwrap() as u32;
        let target = contract["target"].as_u64().unwrap() as u32;
        let other = contract["other"].as_u64().unwrap() as u32;
        let amount = Value::Float(contract["parameter"].as_f64().unwrap());
        let binding = EnemyBinding {
            body: source,
            controller: source + 1,
            hero: definition.hero,
            pair: graphs.enemy_pair(definition.hero).unwrap(),
            mode: crate::game_content::JUNKENSTEIN_REVENGE,
            mode_variables: Arc::default(),
            team: 1,
            skin: None,
            pose: Movement::default(),
            gate: None,
            roster: None,
            retired_entities: Arc::default(),
            frame: 0,
            now_secs: 0.0,
            spawn_anchors: None,
            mode_voice_set: None,
        };
        let mut vitals = definition.pools.vitals();
        let mut retirement_requested = false;
        let mut messages = VecDeque::new();
        let mut tags = TargetTagEffects::default();
        let mut behaviors = Default::default();
        let mut control = AiControlRecords {
            defenders: Arc::from([target, other]),
            ..Default::default()
        };
        let empty = Variables::new();
        let mut world = EnemyWorld {
            retirement_requested: &mut retirement_requested,
            messages: &mut messages,
            target_tag_effects: &mut tags,
            behavior_records: &mut behaviors,
            control_records: &mut control,
            binding: &binding,
            graphs: &graphs,
            vitals: &mut vitals,
            body_variables: &empty,
            controller_variables: &empty,
        };
        let mut root = script_init::Root::new(1, 0x224e);
        root.variables
            .extend([(28714, Value::Entity(target)), (1903, amount.clone())]);
        let sender = Component::start(
            Arc::clone(&graphs),
            source,
            Variables::new(),
            vec![root],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(sender.snapshot().unwrap().initialization_complete());
        let site = Site {
            entity: source,
            instance: 1,
            graph: 0x224e,
            node: Some(22),
        };
        let params = Variables::from([(0x2b, amount.clone())]);
        for (invalid_site, invalid_target, invalid_params) in [
            (
                Site {
                    entity: source + 1,
                    ..site
                },
                target,
                params.clone(),
            ),
            (Site { graph: 42, ..site }, target, params.clone()),
            (
                Site {
                    node: Some(23),
                    ..site
                },
                target,
                params.clone(),
            ),
            (site, other + 2, params.clone()),
            (site, target, Variables::new()),
        ] {
            assert!(!world.send_message(
                invalid_site,
                invalid_target,
                junkenstein_messages::FLASHBANG_HIT,
                &invalid_params
            ));
        }
        world.control_records.defender_messages.resize(
            MAX_INIT_WORK,
            DefenderMessage::flashbang(site, target, &params).unwrap(),
        );
        assert!(!world.send_message(site, target, junkenstein_messages::FLASHBANG_HIT, &params));
        world.control_records.defender_messages.truncate(1);
        let request = world.control_records.defender_messages.pop().unwrap();
        assert_eq!(request.source, source);
        assert_eq!(request.target, Some(target));
        assert_eq!(request.parameters, params);
        assert!(world.messages.is_empty());
        for body in [target, other] {
            let mut receiver = Component::start(
                Arc::clone(&graphs),
                body,
                Variables::new(),
                vec![script_init::Root::listener(1, 0x01d8, None)],
                0,
                &mut script_init::EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            if request.accepts_body(body) {
                assert_eq!(
                    receiver
                        .queue_entity_message_from(
                            request.source,
                            request.message,
                            request.parameters.clone()
                        )
                        .unwrap(),
                    1
                );
                receiver.run_now(&mut script_init::EmptyWorld).unwrap();
            }
            let snapshot = receiver.snapshot().unwrap();
            assert!(snapshot.initialization_complete());
            assert_eq!(
                snapshot.instances[&1].variables.get(&215),
                (body == target).then_some(&amount)
            );
            assert_eq!(
                snapshot.instances[&1].variables.get(&40),
                (body == target).then_some(&Value::Entity(source))
            );
        }
        let broadcast = DefenderMessage::authored(
            Site {
                graph: 0x0ce0,
                node: Some(2),
                ..site
            },
            BOSS_INTRO_ENDED,
        )
        .unwrap();
        assert!(broadcast.accepts_body(target) && broadcast.accepts_body(other));
    }

    #[test]
    fn shared_self_damage_clock_contract() {
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/enemy-self-damage-clock-v1.json"
        ))
        .unwrap();
        let (graphs, _, definition, _) = fixture(false);
        for case in contract["cases"].as_array().unwrap() {
            let frame = case["room_frame"].as_u64().unwrap() as u32;
            let time = case["now_secs"].as_f64().unwrap();
            let binding = EnemyBinding {
                mode_voice_set: None,
                body: 0xc000_1000,
                controller: 0xc000_1001,
                hero: definition.hero,
                pair: graphs.enemy_pair(definition.hero).unwrap(),
                mode: crate::game_content::JUNKENSTEIN_REVENGE,
                mode_variables: Arc::default(),
                team: 1,
                skin: None,
                pose: Movement {
                    frame: case["wire_frame"].as_u64().map(|value| value as u32),
                    ..Movement::default()
                },
                gate: None,
                roster: None,
                retired_entities: Arc::default(),
                frame,
                now_secs: time,
                spawn_anchors: None,
            };
            let mut vitals = Vitals::new(contract["health"].as_f64().unwrap(), 0.0, 0.0);
            let mut retirement = false;
            let mut messages = VecDeque::new();
            let mut behaviors = IndexMap::new();
            let mut controls = AiControlRecords::default();
            let mut tags = TargetTagEffects::default();
            let empty = Variables::new();
            let mut world = EnemyWorld {
                retirement_requested: &mut retirement,
                messages: &mut messages,
                behavior_records: &mut behaviors,
                control_records: &mut controls,
                target_tag_effects: &mut tags,
                binding: &binding,
                graphs: &graphs,
                vitals: &mut vitals,
                body_variables: &empty,
                controller_variables: &empty,
            };
            let site = Site {
                entity: binding.body,
                instance: 1,
                graph: 0x0c89,
                node: Some(46),
            };
            assert!(!world.self_damage(
                Site {
                    entity: binding.controller,
                    ..site
                },
                10_000.0
            ));
            assert!(world.vitals.death().is_none());
            assert!(world.self_damage(site, contract["damage"].as_f64().unwrap()));
            assert_eq!(world.vitals.total(), 0.0);
            assert_eq!(
                world.vitals.death(),
                Some(crate::vitals::Death {
                    time,
                    frame,
                    killer: binding.body,
                })
            );
            for (key, expected) in [
                ("corpse_before_due_frames", false),
                ("corpse_due_frames", true),
            ] {
                assert_eq!(
                    crate::junkenstein_host::common_corpse_due(
                        0x02e0_0000_0000_0175,
                        world.vitals.death().map(|death| death.frame),
                        frame + contract[key].as_u64().unwrap() as u32,
                    ),
                    expected,
                    "{case}"
                );
            }
        }
    }

    #[test]
    fn shared_flashbang_exclusion_guard_contract() {
        use serde_json::json;
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-filter-tag-clauses-v1.json"
        ))
        .unwrap();
        let mut checked = 0;
        for case in contract["cases"].as_array().unwrap() {
            if !case["name"].as_str().unwrap().starts_with("flashbang-") {
                continue;
            }
            let tags: Vec<_> = case["excluded"]
                .as_array()
                .unwrap()
                .iter()
                .map(|tag| {
                    json!({"$":"STU_357DABBB", "m_resourceKey":null,
                        "m_7D7C86A1":format!("0x{:016X}", 0x0d60_0000_0000_0000 | tag.as_u64().unwrap())})
                })
                .collect();
            let filter = json!({"$":"STUConfigVarFilter",
                "m_8B2A4DF3":{"$":"STU_06610EBC", "m_configVars":tags}});
            let inventory: BTreeSet<_> = case["inventory"]
                .as_array()
                .unwrap()
                .iter()
                .map(|tag| 0x0d60_0000_0000_0000 | tag.as_u64().unwrap())
                .collect();
            let mut effects = TargetTagEffects::default();
            let actual = flashbang_exclusions_admit(&filter, |tag| {
                effects
                    .presence(tag)
                    .or_else(|| Some(inventory.contains(&tag)))
            })
            .unwrap();
            assert_eq!(actual, case["accepted"].as_bool().unwrap(), "{case}");
            assert!(flashbang_exclusions_admit(&filter, |_| None).is_err());
            let grant = TargetTagEffect {
                entity: 1,
                tag: 0x0d60_0000_0000_02cb,
                present: true,
            };
            assert!(effects.apply(grant, true));
            assert!(
                !flashbang_exclusions_admit(&filter, |tag| {
                    effects
                        .presence(tag)
                        .or_else(|| Some(inventory.contains(&tag)))
                })
                .unwrap()
            );
            assert!(effects.apply(grant, false));
            // A live suppression masks an immutable tag and releasing that
            // suppression restores the base immunity on the next query.
            for &tag in &inventory {
                assert!(effects.apply(
                    TargetTagEffect {
                        entity: 1,
                        tag,
                        present: false
                    },
                    true
                ));
            }
            assert!(
                flashbang_exclusions_admit(&filter, |tag| {
                    effects
                        .presence(tag)
                        .or_else(|| Some(inventory.contains(&tag)))
                })
                .unwrap()
            );
            for &tag in &inventory {
                assert!(effects.apply(
                    TargetTagEffect {
                        entity: 1,
                        tag,
                        present: false
                    },
                    false
                ));
            }
            assert_eq!(
                flashbang_exclusions_admit(&filter, |tag| {
                    effects
                        .presence(tag)
                        .or_else(|| Some(inventory.contains(&tag)))
                })
                .unwrap(),
                actual
            );
            checked += 1;
        }
        assert_eq!(checked, 8);
    }

    #[test]
    fn shared_flashbang_entry_guard_contract() {
        use serde_json::json;
        struct Empty;
        impl World for Empty {
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-entry-guard-v1.json"
        ))
        .unwrap();
        let node = |pos, class, state, fields| {
            json!({
                "pos":pos,"class":class,"uid":pos,"rid":pos,"remote":-1,
                "state":state,"client_only":false,"server_only":false,"fields":fields
            })
        };
        let graph = |index, nodes: Vec<Json>, fields, states, entries| {
            json!({
                "index":index,"items":nodes.iter().map(|n| n["pos"].as_u64().unwrap()+1).max().unwrap_or(0),"nodes":nodes,"fields":fields,
                "states":states,"entries":entries,"remote":[],"sync_vars":[],
                "containers":{},"unknown_nodes":[],"header":{"m_nodesBitCount":4,
                "m_statesBitCount":4,"m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}
            })
        };
        for case in contract["cases"].as_array().unwrap() {
            let schema = json!({"m_publicSchema":{"$":"STUStatescriptSchema","m_entries":[{
                "m_0D09D2D9":"0x0D80000000007312",
                "m_value":{"$":"STUConfigVarBool","m_value":case["force_default"]}
            }]}});
            let overrides = if case["wrapper_override"] == true {
                json!([{
                    "m_0D09D2D9":"0x0D80000000007312", "m_value":true
                }])
            } else {
                json!([])
            };
            let effect = graph(
                0x224e,
                vec![node(
                    4,
                    "STU_3387AB5D",
                    Json::Null,
                    json!({"m_condition":contract["condition"]}),
                )],
                schema,
                json!([]),
                json!([]),
            );
            let wrapper = graph(
                0x228,
                vec![node(
                    7,
                    "STUStatescriptActionPlayScript",
                    Json::Null,
                    json!({"m_69F4B52C":{"m_6525E98B":{"m_1EB5A024":overrides}}}),
                )],
                json!({}),
                json!([]),
                json!([]),
            );
            let linked = !case["stack_value"].is_null();
            let mut nodes = vec![node(
                0,
                "STUStatescriptEntry",
                Json::Null,
                json!({"m_outPlug":{"$":"Plug","links":if linked {json!([[1,"m_beginPlug"]])} else {json!([])}}}),
            )];
            if linked {
                nodes.push(node(
                    1,
                    "STUStatescriptStateStack",
                    json!(0),
                    json!({
                        "m_2BBEEAB8":true,
                        "m_out_Var":{"$":"STUConfigVarDynamic","m_60DB8F99":1,"m_identifier":2547},
                        "m_value":{"$":"STUConfigVarBool","m_value":case["stack_value"]},
                        "m_priority":{"$":"STUConfigVarFloat","m_value":0}
                    }),
                ));
            }
            let body_graph = graph(
                9000,
                nodes,
                json!({}),
                if linked { json!([1]) } else { json!([]) },
                json!([0]),
            );
            let (graphs, _, _, _) = fixture_with_graphs(false, &[effect, wrapper, body_graph]);
            let base = case["base"].as_bool().map_or(Value::Null, Value::Bool);
            let component = Component::start(
                Arc::clone(&graphs),
                42,
                Variables::from([(2547, base)]),
                vec![script_init::Root::new(1, 9000)],
                0,
                &mut Empty,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert!(component.snapshot().unwrap().initialization_complete());
            let actual = flashbang_entry_admits(&graphs, |variable| {
                assert_eq!(variable.scope, crate::script_expr::Scope::Entity);
                assert_eq!(variable.id, 2547);
                if case["unavailable"] == true {
                    None
                } else {
                    component.resolved_variable(0, variable)
                }
            });
            if case["error"] == true {
                assert!(actual.is_err(), "{}", case["name"]);
            } else {
                assert_eq!(
                    actual,
                    Ok(case["accepted"].as_bool().unwrap()),
                    "{}",
                    case["name"]
                );
            }
        }
    }

    #[test]
    fn shared_flashbang_effect_dispatch_contract() {
        use serde_json::json;
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-effect-dispatch-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "flashbang.effect-dispatch.v1");
        for case in contract["input"]["cases"].as_array().unwrap() {
            let (graphs, _, definition, _) =
                fixture_with_graphs(false, &[contract["input"]["graph"].clone()]);
            let binding = EnemyBinding {
                body: 0xc0001000,
                controller: 0xc0001001,
                hero: definition.hero,
                pair: graphs.enemy_pair(definition.hero).unwrap(),
                mode: crate::accounts::parse_guid(case["mode"].as_str().unwrap()).unwrap(),
                mode_variables: Arc::default(),
                team: 1,
                skin: None,
                pose: Default::default(),
                gate: None,
                roster: None,
                retired_entities: Arc::default(),
                frame: 0,
                now_secs: 0.0,
                spawn_anchors: None,
                mode_voice_set: None,
            };
            let mut vitals = definition.pools.vitals();
            let mut scripts = EnemyScripts::start(
                Arc::clone(&graphs),
                &definition,
                &binding,
                1000,
                &mut vitals,
            )
            .unwrap();
            scripts.queue_flashbang_effect(
                &binding,
                Site {
                    entity: u32::try_from(case["source"].as_u64().unwrap()).unwrap(),
                    instance: 1,
                    graph: u32::try_from(case["graph"].as_u64().unwrap()).unwrap(),
                    node: Some(u16::try_from(case["node"].as_u64().unwrap()).unwrap()),
                },
            );
            scripts
                .deliver_messages(&graphs, &binding, &mut vitals)
                .unwrap();
            let snapshot = scripts.body.snapshot().unwrap();
            let marker = snapshot.variables.get(&900).map(|value| match value {
                Value::Integer(value) => json!(value),
                other => panic!("{other:?}"),
            });
            let source = snapshot.variables.get(&901).map(|value| match value {
                Value::Entity(value) => json!(value),
                other => panic!("{other:?}"),
            });
            assert_eq!(
                json!({"marker":marker,"source":source,"wrappers":snapshot.instances.values().filter(|instance| instance.graph == 0x0228).count()}),
                case["expected"],
                "{}",
                case["name"]
            );
        }
    }

    #[test]
    #[ignore = "requires the prepared 104319 Boss intro and controller graphs"]
    fn authored_boss_intro_end_releases_the_paired_controller_facing() {
        use crate::{game_content::JUNKENSTEIN_REVENGE, script_init::Root};
        let runtime = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let hero = 0x02e0_0000_0000_0002;
        let binding = EnemyBinding {
            mode_voice_set: None,
            body: 0xc000_1000,
            controller: 0xc000_1001,
            hero,
            pair: graphs.enemy_pair(hero).unwrap(),
            mode: JUNKENSTEIN_REVENGE,
            mode_variables: Arc::new(Variables::new()),
            team: 1,
            skin: None,
            pose: Movement::default(),
            gate: None,
            roster: None,
            retired_entities: Arc::default(),
            frame: 0,
            now_secs: 0.0,
            spawn_anchors: None,
        };
        let mut vitals = Vitals::new(250.0, 0.0, 0.0);
        let mut messages = VecDeque::new();
        let mut behavior_records = IndexMap::new();
        let mut control_records = AiControlRecords::default();
        let mut target_tag_effects = TargetTagEffects::default();
        let (body, controller) = {
            let empty = Variables::new();
            let mut retirement_requested = false;
            let mut world = EnemyWorld {
                retirement_requested: &mut retirement_requested,
                messages: &mut messages,
                behavior_records: &mut behavior_records,
                control_records: &mut control_records,
                target_tag_effects: &mut target_tag_effects,
                binding: &binding,
                graphs: &graphs,
                vitals: &mut vitals,
                body_variables: &empty,
                controller_variables: &empty,
            };
            let mut intro = Root::listener(1, 0x0ce0, Some(3));
            intro
                .variables
                .extend([(8190, Value::Float(0.05)), (8504, Value::Float(0.0))]);
            let body = Component::start(
                Arc::clone(&graphs),
                binding.body,
                Variables::new(),
                vec![intro],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let mut wave = Root::listener(1, 0x0c95, Some(1));
            wave.variables
                .insert(8370, Value::Array(vec![Value::Vector([74.0, 12.6, -99.0])]));
            let controller = Component::start(
                Arc::clone(&graphs),
                binding.controller,
                Variables::new(),
                vec![wave, Root::listener(2, 0x0ce2, None)],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            (body, controller)
        };
        let mut scripts = EnemyScripts {
            body,
            controller,
            control_records,
            behavior_records,
            target_tag_effects,
            messages,
            scene_messages: Vec::new(),
            retirement_requested: false,
            movement_modifiers: Default::default(),
        };
        assert!(scripts.facing_direction().is_some());
        let snapshot = scripts.controller.snapshot().unwrap();
        assert!(
            snapshot
                .instances
                .values()
                .any(|instance| instance.graph == 0x0cdc)
        );
        for frame in 1..=4 {
            scripts
                .advance(&graphs, &binding, &mut vitals, frame)
                .unwrap();
        }
        let snapshot = scripts.controller.snapshot().unwrap();
        assert!(
            !snapshot
                .instances
                .values()
                .any(|instance| instance.graph == 0x0cdc)
        );
        assert!(!snapshot.instances[&1].states[&1].active);
        assert_eq!(scripts.facing_direction(), None);
        assert_eq!(snapshot.instances[&2].variables[&699], Value::Bool(true));
        assert_eq!(
            scripts
                .take_defender_messages()
                .iter()
                .map(|message| (message.source, message.message))
                .collect::<Vec<_>>(),
            [
                (binding.body, BOSS_INTRO_ENDED),
                (binding.body, junkenstein_messages::RESET_HIGHLIGHTS)
            ],
        );
        scripts.advance(&graphs, &binding, &mut vitals, 5).unwrap();
        assert!(scripts.take_defender_messages().is_empty());
    }

    #[test]
    #[ignore = "requires separately prepared 104319 Flashbang effect graphs"]
    fn authored_flashbang_wrapper_sets_the_loaded_hit_effect_schema() {
        use crate::{game_heroes::HeroCatalog, npc_catalog::encounter_definition};
        use crate::{
            script_codecs::CodecTable,
            script_snapshot_wire::{SnapshotEncoder, View},
        };
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
        let heroes = HeroCatalog::load(root.join("data/game_heroes_174.json")).unwrap();
        let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
        let encoder = SnapshotEncoder::new(&graphs, &codecs);
        for (hero, control_immune, entry_rejected) in [
            (REAPER, false, false),
            (ROADHOG, false, false),
            (SUMMONER, false, false),
            (JUNKRAT, false, false),
            (WITCH, false, false),
            (REAPER, true, false),
            (REAPER, false, true),
        ] {
            // Isolated body-adapter probe: declare Normal difficulty and an
            // explicitly empty combat/placement namespace. This does not run
            // the paired controller or establish the projectile-to-effect bridge.
            let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
            let binding = EnemyBinding {
                mode_voice_set: None,
                body: 0xc000_1000,
                controller: 0xc000_1001,
                hero,
                pair: graphs.enemy_pair(hero).unwrap(),
                mode: crate::game_content::JUNKENSTEIN_REVENGE,
                mode_variables: Arc::new(Variables::from([(
                    6751,
                    Value::Asset(0x0d80_0000_0000_195e),
                )])),
                team: 1,
                skin: None,
                pose: Movement::default(),
                gate: None,
                roster: Some(Arc::new(Vec::new())),
                retired_entities: Arc::default(),
                frame: 0,
                now_secs: 0.0,
                spawn_anchors: Some(Arc::default()),
            };
            let mut vitals = definition.pools.vitals();
            let mut retirement_requested = false;
            let mut messages = VecDeque::new();
            let mut tags = TargetTagEffects::default();
            let mut behaviors = Default::default();
            let mut control = AiControlRecords::default();
            let empty = Variables::new();
            let record = graphs.enemy_body(binding.pair.body).unwrap();
            let initial =
                script_init::player_entity_variables(record, vitals.total(), vitals.maximum());
            let mut roots = script_init::player_roots(record, &graphs).unwrap();
            if control_immune {
                roots.push(script_init::Root::listener(100, 0x01c9, Some(20)));
            }
            vitals = Vitals::empty();
            let mut world = EnemyWorld {
                retirement_requested: &mut retirement_requested,
                messages: &mut messages,
                target_tag_effects: &mut tags,
                behavior_records: &mut behaviors,
                control_records: &mut control,
                binding: &binding,
                graphs: &graphs,
                vitals: &mut vitals,
                body_variables: &initial,
                controller_variables: &empty,
            };
            let mut component = Component::start_with_public_schema(
                Arc::clone(&graphs),
                binding.body,
                initial.clone(),
                roots,
                1000,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let exclusion_filter = graphs
                .graph(0x224e)
                .unwrap()
                .node(9)
                .unwrap()
                .field("m_condition.m_filter")
                .unwrap();
            let tag_site = Site {
                entity: binding.body,
                instance: 0,
                graph: 0x224e,
                node: Some(9),
            };
            assert!(
                flashbang_exclusions_admit(exclusion_filter, |tag| {
                    world.has_target_tag(tag_site, binding.body, tag)
                })
                .unwrap()
            );
            let immunity = TargetTagEffect {
                entity: binding.body,
                tag: 0x0d60_0000_0000_02cb,
                present: true,
            };
            assert!(world.set_target_tag_effect(tag_site, immunity, true));
            assert!(
                !flashbang_exclusions_admit(exclusion_filter, |tag| {
                    world.has_target_tag(tag_site, binding.body, tag)
                })
                .unwrap()
            );
            assert!(world.set_target_tag_effect(tag_site, immunity, false));
            assert!(
                flashbang_exclusions_admit(exclusion_filter, |tag| {
                    world.has_target_tag(tag_site, binding.body, tag)
                })
                .unwrap()
            );
            if entry_rejected {
                component
                    .set_entity_variable(2547, Value::Bool(true), &mut world)
                    .unwrap();
                component.run_now(&mut world).unwrap();
            }
            let entry_guard = graphs
                .graph(0x224e)
                .unwrap()
                .node(4)
                .unwrap()
                .field("m_condition")
                .unwrap();
            let native_entry = component
                .evaluate_state_config(1, 0, entry_guard, &mut world)
                .unwrap();
            let admitted =
                flashbang_entry_admits(&graphs, |var| component.resolved_variable(0, var)).unwrap();
            assert_eq!(admitted, crate::script_expr::truthy(&native_entry));
            if entry_rejected {
                assert!(!admitted);
            }
            let gate = graphs
                .graph(0x224e)
                .unwrap()
                .node(122)
                .unwrap()
                .field("m_condition")
                .unwrap();
            assert_eq!(
                flashbang_stack_admits(&component, &graphs).unwrap(),
                !control_immune
            );
            assert_eq!(
                component
                    .evaluate_state_config(1, 0, gate, &mut world)
                    .unwrap(),
                Value::Bool(!control_immune)
            );
            component
                .receive_play_script(
                    Site {
                        entity: 0xa000_0101,
                        instance: 1,
                        graph: 0x01e9,
                        node: Some(7),
                    },
                    &PlayScript {
                        policy: 2,
                        overrides: vec![],
                        graph: 0x0228,
                        source: 0xa000_0101,
                        parameters: vec![],
                        auto_retire: false,
                    },
                    &mut world,
                )
                .unwrap();
            component
                .queue_entity_message_from(0xa000_0101, 0x0240_0000_0000_000f, Variables::new())
                .unwrap();
            component.run_now(&mut world).unwrap();
            let snapshot = component.snapshot().unwrap();
            let effects: Vec<_> = snapshot
                .instances
                .values()
                .filter(|instance| instance.graph == 0x224e)
                .collect();
            assert_eq!(effects.len(), 1);
            let effect = effects[0];
            assert_eq!(effect.variables[&215], Value::Float(f64::from(0.8f32)));
            assert_eq!(
                effect.variables[&27606],
                Value::Asset(0x0d80_0000_0000_4eb6)
            );
            assert_eq!(effect.variables[&9292], Value::Asset(0x0b90_0000_0000_0060));
            assert_eq!(
                effect.variables[&28383],
                Value::Asset(0x0300_0000_0000_05d9)
            );
            assert_eq!(effect.variables[&0x1b], Value::Entity(0xa000_0101));
            assert!(
                snapshot.initialization_complete(),
                "Boss {hero:X}: {:?}",
                snapshot.diagnostics
            );
            encoder
                .full(&encoder.project(snapshot, View::Observer).unwrap())
                .unwrap();
            assert!(!effect.states[&64].active, "deferred queue drain completed");
            assert!(
                effect.states[&71].active,
                "authored remote-schema state retained"
            );
            assert!(
                snapshot
                    .variables
                    .get(&31528)
                    .is_none_or(|value| { *value == Value::Array(vec![]) })
            );
            for frame in 1001..=1100 {
                component.advance_frame(frame, &mut world).unwrap();
                let snapshot = component.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "Boss {hero:X}, frame {frame}: {:?}",
                    snapshot.diagnostics
                );
                encoder
                    .full(&encoder.project(snapshot, View::Observer).unwrap())
                    .unwrap();
            }
        }
    }

    #[test]
    #[ignore = "requires the external 104319 Boss body/controller graphs"]
    fn prepared_boss_pools_register_without_profile_duplication() {
        use crate::{game_heroes::HeroCatalog, npc_catalog::encounter_definition};
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs = Arc::new(GraphStore::load(root.join("data/game_graphs_174.json.gz")).unwrap());
        let heroes = HeroCatalog::load(root.join("data/game_heroes_174.json")).unwrap();
        for index in [2, 4, 0x16, 0x40, 0x65] {
            let hero = 0x02e0_0000_0000_0000 | index;
            let definition = encounter_definition(None, &graphs, &heroes, hero).unwrap();
            let binding = EnemyBinding {
                mode_voice_set: None,
                body: 0xc000_1000,
                controller: 0xc000_1001,
                hero,
                pair: graphs.enemy_pair(hero).unwrap(),
                mode: crate::game_content::JUNKENSTEIN_REVENGE,
                mode_variables: Arc::new(Variables::new()),
                team: 1,
                skin: None,
                pose: Movement::default(),
                gate: None,
                roster: None,
                retired_entities: Arc::default(),
                frame: 0,
                now_secs: 0.0,
                spawn_anchors: None,
            };
            let mut vitals = definition.pools.vitals();
            let startup =
                EnemyScripts::start(Arc::clone(&graphs), &definition, &binding, 0, &mut vitals);
            assert_eq!(vitals.maximum(), definition.pools.total(), "Boss {hero:X}");
            for (kind, expected) in [
                (PoolKind::Health, definition.pools.health),
                (PoolKind::Armour, definition.pools.armour),
                (PoolKind::Shields, definition.pools.shields),
            ] {
                assert_eq!(
                    vitals.amount(Some(kind), true),
                    expected,
                    "Boss {hero:X} {kind:?}"
                );
                assert_eq!(
                    vitals.amount(Some(kind), false),
                    expected,
                    "Boss {hero:X} {kind:?}"
                );
            }
            // This isolates pool registration even while other Boss operations
            // remain unsupported. It must continue to pass after full startup works.
            if let Err(error) = startup {
                for issue in [
                    "health pool registration",
                    "health pool target",
                    "health pool capacity",
                    "health pool initial amount",
                    "AI weapon control component",
                ] {
                    assert!(!error.contains(issue), "Boss {hero:X}: {error}");
                }
                if matches!(index, 2 | 0x40) {
                    assert!(error.starts_with("enemy controller startup:"), "{error}");
                    for class in [
                        "STU_200B27C5",
                        "STU_AF1A4047",
                        "STU_55F72184",
                        "STU_E834E718",
                        "STU_632B74B9",
                        "STU_D52B9026",
                        "STU_E0D2F3AA",
                    ] {
                        assert!(!error.contains(class), "Boss {hero:X}: {error}");
                    }
                }
                eprintln!("Boss {hero:X} remaining startup diagnostics: {error}");
            }
        }
    }

    #[test]
    fn common_enemy_pools_replace_bootstrap_and_follow_damage_abort_and_reentry() {
        use serde_json::json;
        let node = |pos, class, state: Option<u16>, mut fields: Json| {
            fields["m_2BBEEAB8"] = json!(state.is_some());
            fields["m_ADEB6E05"] = json!(class == "STUStatescriptActionSetVar");
            json!({"pos":pos,"class":class,"uid":pos,"rid":pos,"remote":-1,
                "state":state,"fields":fields,"client_only":false,"server_only":false})
        };
        let variable = |id| json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":true});
        let owner = json!({"$":"STU_B5A0CAF0"});
        let pool = |pos: u16, kind, amount, current, output| {
            node(
                pos,
                "STUStatescriptStateHealthPool",
                Some(pos - 1),
                json!({
                    "m_FA3ECC43":owner,"m_7E8B666C":kind,"m_amount":{"$":"STUConfigVarInt","m_value":amount},
                    "m_BCFDD2E9":current,"m_69A20070":{"$":"STU_FEDD683C","m_entity":owner},
                    "m_4895B8B6":{"$":"STUConfigVarBool","m_value":true},
                    "m_64839023":{"$":"STUConfigVarBool","m_value":false},
                    "m_0E8A5C8E":{"$":"STUConfigVarBool","m_value":false},"m_CEAB19E8":output,
                    "m_onEndPlug":{"$":"STUStatescriptOutputPlug","links":if kind==0 {json!([[6,"m_inPlug"]])}else{json!([])}}
                }),
            )
        };
        let nodes = vec![
            node(
                0,
                "STUStatescriptEntry",
                None,
                json!({"m_outPlug":{"$":"STUStatescriptOutputPlug","links":[[1,"m_beginPlug"],[2,"m_beginPlug"],[3,"m_beginPlug"]]}}),
            ),
            pool(
                1,
                0,
                200,
                json!({"$":"STUConfigVarInt","m_value":175}),
                variable(9001),
            ),
            pool(2, 1, 50, Json::Null, Json::Null),
            pool(3, 2, 25, Json::Null, Json::Null),
            node(
                4,
                "STUStatescriptEntryGameMessage",
                None,
                json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000002"},"m_outPlug":{"$":"STUStatescriptOutputPlug","links":[[1,"m_F198FD3A"]]}}),
            ),
            node(
                5,
                "STUStatescriptEntryGameMessage",
                None,
                json!({"m_gameMessage":{"m_gameMessage":"0x0240000000000003"},"m_outPlug":{"$":"STUStatescriptOutputPlug","links":[[1,"m_beginPlug"]]}}),
            ),
            node(
                6,
                "STUStatescriptActionSetVar",
                None,
                json!({"m_out_Var":variable(9002),"m_value":variable(9001)}),
            ),
        ];
        let graph = |index, nodes: Vec<Json>, states, entries| {
            json!({"index":index,"items":nodes.len(),"nodes":nodes,
            "states":states,"entries":entries,"fields":{},"remote":[],"sync_vars":[],"containers":{},"unknown_nodes":[],
            "header":{"m_nodesBitCount":4,"m_statesBitCount":4,"m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}})
        };
        let body = |index| {
            json!({"hero":if index==51 {Some("0x02E0000000000175")}else{None},"name":"Fixture","manager":null,"weapons":[],"target_tags":[],
            "graphs":[{"graph":index,"m_1EB5A024":null,"m_75408640":0}]})
        };
        let data = json!({"version":1,"graphs":[graph(51,nodes,json!([1,2,3]),json!([0,4,5])),graph(43,vec![],json!([]),json!([]))],"bodies":{},"missing":[],
            "junkenstein":{"pairs":{"0x02E0000000000175":{"body":"0x04000000000016BE","controller":"0x04000000000016BF"}},
                "entities":{"0x04000000000016BE":body(51),"0x04000000000016BF":body(43)}}});
        let graphs = Arc::new(GraphStore::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
        let (_, _, definition, _) = fixture(false);
        let binding = EnemyBinding {
            mode_voice_set: None,
            body: 0xc000_1000,
            controller: 0xc000_1001,
            hero: definition.hero,
            pair: graphs.enemy_pair(definition.hero).unwrap(),
            mode: 0x0230_0000_0000_019f,
            mode_variables: Arc::new(Variables::new()),
            team: 1,
            skin: None,
            pose: Movement::default(),
            gate: None,
            roster: None,
            retired_entities: Arc::default(),
            frame: 0,
            now_secs: 0.0,
            spawn_anchors: None,
        };
        let mut vitals = definition.pools.vitals();
        let mut scripts =
            EnemyScripts::start(Arc::clone(&graphs), &definition, &binding, 0, &mut vitals)
                .unwrap();
        assert_eq!((vitals.total(), vitals.maximum()), (250.0, 275.0));
        let pool_id = match scripts.body.snapshot().unwrap().instances[&1].states[&0].kind {
            script_init::StateKind::HealthPool { pool: Some(id) } => id,
            _ => panic!("missing health state"),
        };
        let mut expected = Vitals::with_current_health(200.0, 50.0, 25.0, 175.0);
        let death = crate::vitals::Death {
            time: 0.0,
            frame: 0,
            killer: 99,
        };
        assert_eq!(vitals.damage(100.0, death), expected.damage(100.0, death));
        scripts.advance(&graphs, &binding, &mut vitals, 1).unwrap();
        let remaining = expected.amount(Some(PoolKind::Health), false);
        assert_eq!(
            scripts.body.snapshot().unwrap().variables[&9001],
            Value::Float(remaining)
        );
        scripts
            .body
            .queue_entity_message(0x0240_0000_0000_0002, Variables::new())
            .unwrap();
        scripts.advance(&graphs, &binding, &mut vitals, 2).unwrap();
        assert_eq!(
            scripts.body.snapshot().unwrap().variables[&9002],
            Value::Float(remaining)
        );
        assert_eq!(vitals.pool_current(pool_id), None);
        assert_eq!(vitals.maximum(), 75.0);
        scripts
            .body
            .queue_entity_message(0x0240_0000_0000_0003, Variables::new())
            .unwrap();
        scripts.advance(&graphs, &binding, &mut vitals, 3).unwrap();
        let next_id = match scripts.body.snapshot().unwrap().instances[&1].states[&0].kind {
            script_init::StateKind::HealthPool { pool: Some(id) } => id,
            _ => panic!("missing restarted health state"),
        };
        assert!(next_id > pool_id);
        assert_eq!(vitals.pool_current(next_id), Some(175.0));
        assert_eq!(vitals.maximum(), 275.0);
        assert_eq!(vitals.heal(25.0), 25.0);
        scripts.advance(&graphs, &binding, &mut vitals, 4).unwrap();
        assert_eq!(
            scripts.body.snapshot().unwrap().variables[&9001],
            Value::Float(200.0)
        );
        assert_eq!(
            scripts.body.snapshot().unwrap().variables[&1737],
            Value::Float(275.0)
        );
    }

    #[test]
    fn shared_retired_entity_contract() {
        use crate::{
            script_entity_state::EntityScriptState,
            script_expr::{Context, Variable},
        };
        use serde_json::json;
        struct Host<'a, 'b> {
            world: &'a mut EnemyWorld<'b>,
            target: u32,
        }
        impl Context for Host<'_, '_> {
            fn variable(&mut self, variable: Variable) -> Value {
                assert_eq!(variable.id, 1);
                Value::Entity(self.target)
            }
            fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
                self.world.entity_variable(entity, id)
            }
            fn entity_exists(&mut self, entity: u32) -> Option<bool> {
                self.world.entity_exists(entity)
            }
            fn entity_team(&mut self, entity: u32) -> Option<i8> {
                self.world.entity_team(
                    Site {
                        entity: self.world.binding.body,
                        instance: 1,
                        graph: 0x7000,
                        node: Some(0),
                    },
                    entity,
                )
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-retired-entity-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["case_id"], "statescript.retired-entity.v1");
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["target_build"], 104319);
        let cases = contract["input"]["cases"].as_array().unwrap();
        let results = contract["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), results.len());
        for (case, expected) in cases.iter().zip(results) {
            assert_eq!(case["name"], expected["name"]);
            let (graphs, _, definition, _) = fixture(false);
            let target = u32::try_from(case["entity"].as_u64().unwrap()).unwrap();
            let state = case["state"].as_str().unwrap();
            let variables = case
                .get("value")
                .and_then(Json::as_bool)
                .map(|value| Variables::from([(6604, Value::Bool(value))]))
                .unwrap_or_default();
            let binding = EnemyBinding {
                mode_voice_set: None,
                body: 0xc000_1000,
                controller: 0xc000_1001,
                hero: definition.hero,
                pair: graphs.enemy_pair(definition.hero).unwrap(),
                mode: 0x0230_0000_0000_0003,
                mode_variables: Arc::default(),
                team: 1,
                skin: None,
                pose: Movement::default(),
                gate: None,
                frame: 0,
                now_secs: 0.0,
                spawn_anchors: None,
                retired_entities: Arc::new(if state == "retired" {
                    BTreeSet::from([target])
                } else {
                    BTreeSet::new()
                }),
                roster: (!matches!(state, "retired" | "unavailable")).then(|| {
                    Arc::new(vec![EnemyTarget {
                        hero: None,
                        filter_flags: None,
                        tags: Default::default(),
                        health: None,
                        velocity: None,
                        entity: target,
                        team: u8::try_from(case["team"].as_u64().unwrap()).unwrap(),
                        pose: TargetPose {
                            feet: [0.0; 3],
                            height: 1.8,
                            radius: 0.35,
                            eye: 1.55,
                        },
                        alive: true,
                        script_state: match state {
                            "running" => EntityScriptState::Running(Arc::new(variables)),
                            "absent" => EntityScriptState::Absent,
                            "faulted" => EntityScriptState::Unavailable,
                            _ => panic!("unknown state {state}"),
                        },
                    }])
                }),
            };
            let mut behaviors = IndexMap::new();
            let mut controls = AiControlRecords::default();
            let mut messages = VecDeque::new();
            let mut retirement_requested = false;
            let mut target_tag_effects = TargetTagEffects::default();
            let mut vitals = definition.pools.vitals();
            let empty = Variables::new();
            let mut world = EnemyWorld {
                retirement_requested: &mut retirement_requested,
                binding: &binding,
                graphs: &graphs,
                vitals: &mut vitals,
                body_variables: &empty,
                controller_variables: &empty,
                behavior_records: &mut behaviors,
                control_records: &mut controls,
                messages: &mut messages,
                target_tag_effects: &mut target_tag_effects,
            };
            let mut host = Host {
                world: &mut world,
                target,
            };
            let mut actual = serde_json::Map::new();
            for (key, config) in contract["input"]["queries"].as_object().unwrap() {
                let result = crate::script_expr::evaluate(config, &mut host);
                let value = match result {
                    Ok(Value::Null) => Json::Null,
                    Ok(Value::Bool(value)) => json!(value),
                    Ok(Value::Integer(value)) => json!(value),
                    Err(
                        crate::script_expr::EvalError::UnavailableEntityVariable { .. }
                        | crate::script_expr::EvalError::UnsupportedConfig(_),
                    ) => json!("unavailable"),
                    other => panic!("{} {key}: {other:?}", case["name"]),
                };
                actual.insert(key.clone(), value);
            }
            assert_eq!(Json::Object(actual), expected["output"], "{}", case["name"]);
        }
    }

    #[test]
    fn enemy_registries_validate_owners_and_preserve_priority_order() {
        let (graphs, _, definition, _) = fixture(false);
        let binding = EnemyBinding {
            mode_voice_set: None,
            body: 0xc000_1000,
            controller: 0xc000_1001,
            hero: definition.hero,
            pair: graphs.enemy_pair(definition.hero).unwrap(),
            mode: 0x0230_0000_0000_019f,
            mode_variables: Arc::new(Variables::new()),
            team: 1,
            skin: None,
            pose: Movement::default(),
            gate: None,
            roster: None,
            retired_entities: Arc::default(),
            frame: 0,
            now_secs: 0.0,
            spawn_anchors: None,
        };
        let mut behaviors = indexmap::IndexMap::new();
        let mut controls = AiControlRecords::default();
        let mut messages = VecDeque::new();
        let mut retirement_requested = false;
        let mut target_tag_effects = TargetTagEffects::default();
        let mut vitals = definition.pools.vitals();
        let empty = Variables::new();
        let mut world = EnemyWorld {
            retirement_requested: &mut retirement_requested,
            binding: &binding,
            graphs: &graphs,
            vitals: &mut vitals,
            body_variables: &empty,
            controller_variables: &empty,
            behavior_records: &mut behaviors,
            control_records: &mut controls,
            messages: &mut messages,
            target_tag_effects: &mut target_tag_effects,
        };
        let site = |node| Site {
            entity: binding.controller,
            instance: 1,
            graph: 0x0cf3,
            node: Some(node),
        };
        let record = |priority, prefer_later| script_init::AiBehaviorRequest {
            class: "STU_B61E6681",
            priority,
            prefer_later,
            flag: false,
            shared: [0.0; 2],
            inputs: [0.0; 5],
        };
        for (node, priority, prefer_later) in [
            (1, 0.0, false),
            (2, 10.0, false),
            (3, 10.0, false),
            (4, 10.0, true),
        ] {
            assert_eq!(
                world.ai_behavior_request(site(node), record(priority, prefer_later), true),
                Some(true)
            );
        }
        assert_eq!(
            world
                .behavior_records
                .keys()
                .map(|key| key.2)
                .collect::<Vec<_>>(),
            [4, 2, 3, 1]
        );
        let mut updated = record(0.0, false);
        updated.inputs[0] = 3.0;
        world.ai_behavior_request(site(1), updated, true);
        assert_eq!(
            world
                .behavior_records
                .keys()
                .map(|key| key.2)
                .collect::<Vec<_>>(),
            [4, 2, 3, 1]
        );
        assert_eq!(
            world.behavior_records[&(binding.controller, 1, 1)].inputs[0],
            3.0
        );
        world.ai_behavior_request(site(2), record(10.0, false), false);
        assert_eq!(
            world
                .behavior_records
                .keys()
                .map(|key| key.2)
                .collect::<Vec<_>>(),
            [4, 3, 1]
        );
        let mut foreign = site(5);
        foreign.entity = binding.body;
        assert_eq!(
            world.ai_behavior_request(foreign, record(10.0, false), true),
            None
        );
        let weapon = AiWeaponOverride {
            target: binding.body,
            slot: 0,
            identifier: 0x0d80_0000_0000_023a,
            enabled: false,
            priority: 0.0,
            prefer_later: false,
        };
        let stronger = AiWeaponOverride {
            enabled: true,
            priority: 10.0,
            ..weapon
        };
        let replace_equal = AiWeaponOverride {
            prefer_later: true,
            ..stronger
        };
        for (node, record) in [
            (1, weapon),
            (2, stronger),
            (3, stronger),
            (4, replace_equal),
        ] {
            assert_eq!(
                world.ai_weapon_override(site(node), record, true),
                Some(true)
            );
        }
        let ordered = |world: &EnemyWorld<'_>| {
            world
                .control_records
                .weapons
                .keys()
                .map(|site| site.node.unwrap())
                .collect::<Vec<_>>()
        };
        assert_eq!(ordered(&world), [4, 2, 3, 1]);
        assert_eq!(world.control_records.weapons[&site(1)], weapon);
        assert_eq!(
            world.ai_weapon_override(site(5), AiWeaponOverride { slot: 1, ..weapon }, true),
            Some(false)
        );
        assert_eq!(
            world.ai_weapon_override(
                site(5),
                AiWeaponOverride {
                    target: binding.controller,
                    ..weapon
                },
                true
            ),
            None
        );
        assert_eq!(ordered(&world), [4, 2, 3, 1]);
        assert_eq!(
            world.ai_weapon_override(site(4), replace_equal, false),
            Some(true)
        );
        assert_eq!(ordered(&world), [2, 3, 1]);

        let source = site(1);
        let body_source = Site {
            entity: binding.body,
            ..source
        };
        let effect = TargetTagEffect {
            entity: binding.body,
            tag: 0x0d60_0000_0000_0422,
            present: true,
        };
        assert_eq!(
            world.has_target_tag(source, binding.body, effect.tag),
            Some(false)
        );
        assert!(!world.set_target_tag_effect(source, effect, true));
        assert!(world.set_target_tag_effect(body_source, effect, true));
        assert_eq!(
            world.has_target_tag(source, binding.body, effect.tag),
            Some(true)
        );
        assert_eq!(
            world.has_target_tag(source, binding.controller, effect.tag),
            Some(false)
        );
        assert!(world.set_target_tag_effect(body_source, effect, false));
        assert_eq!(
            world.has_target_tag(source, binding.body, effect.tag),
            Some(false)
        );

        for config in [
            serde_json::json!({"$":"STU_37F20D24"}),
            serde_json::json!({"$":"STU_37F20D24","m_EE729DCB":0,"m_resourceKey":null}),
        ] {
            assert_eq!(
                world.native(source, &config),
                Some(Value::Asset(binding.mode))
            );
        }
        for config in [
            serde_json::json!({"$":"STU_37F20D24","m_resourceKey":"0x0D80000000000001"}),
            serde_json::json!({"$":"STU_37F20D24","m_unknown":null}),
        ] {
            assert_eq!(world.native(source, &config), None);
        }
        let body_site = Site {
            entity: binding.body,
            ..source
        };
        let foreign_site = Site {
            entity: 999,
            ..source
        };
        assert_eq!(world.health_pool_current(body_site, 3), Some(200.0));
        for invalid in [source, foreign_site] {
            assert_eq!(
                world.add_health_pool(invalid, PoolKind::Health, 100.0, 100.0),
                None
            );
            assert_eq!(world.health_pool_current(invalid, 3), None);
            assert_eq!(world.remove_health_pool(invalid, 3), None);
        }
        assert_eq!(world.vitals.total(), 275.0);
        let wave_site = Site {
            entity: binding.body,
            graph: 0x0cbc,
            ..source
        };
        let bonus = world.add_wave_health(wave_site, 750.0).unwrap().unwrap();
        assert_eq!(world.vitals.total(), 1025.0);
        assert_eq!(world.add_wave_health(wave_site, 750.0), None);
        assert_eq!(world.remove_health_pool(wave_site, bonus), Some(750.0));
        assert_eq!(world.vitals.total(), 275.0);
        assert_eq!(world.entity_hero(source, binding.body), Some(binding.hero));
        assert_eq!(world.entity_hero(source, binding.controller), None);
        assert_eq!(
            world.entity_definition(source, binding.body),
            Some(binding.pair.body)
        );
        assert_eq!(
            world.entity_definition(source, binding.controller),
            Some(binding.pair.controller)
        );
        assert_eq!(world.entity_definition(source, 999), None);
        assert_eq!(world.entity_definition(foreign_site, binding.body), None);
        assert_eq!(
            world.entity_hero(
                Site {
                    entity: 999,
                    ..source
                },
                binding.body
            ),
            None
        );
        assert_eq!(
            world.entity_health(source, binding.body, Some(PoolKind::Health), false),
            Some(200.0)
        );
        assert_eq!(
            world.entity_health(
                Site {
                    entity: 999,
                    ..source
                },
                binding.body,
                None,
                false
            ),
            None
        );
        assert_eq!(
            world.entity_filter_flags(source, binding.body),
            Some(0x1000001)
        );
        world.vitals.damage(
            1000.0,
            crate::vitals::Death {
                time: 0.0,
                frame: 1,
                killer: 999,
            },
        );
        assert_eq!(
            world.entity_filter_flags(source, binding.body),
            Some(0x1000011)
        );
        assert_eq!(
            world.entity_health(source, binding.body, None, false),
            Some(0.0)
        );
        assert_eq!(
            world.entity_health(source, binding.body, Some(PoolKind::Health), true),
            Some(200.0)
        );
        assert_eq!(world.add_wave_health(wave_site, 750.0), Some(None));
    }
}
