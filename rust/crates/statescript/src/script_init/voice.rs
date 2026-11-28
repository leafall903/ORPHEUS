//! Voice stimulus host and StateScript expression evaluation context.

use std::collections::BTreeMap;

use serde_json::Value as Json;

use crate::{
    script_expr::{self, Context, EntityRelation, OnFire, StackPriority, Variable},
    script_graph::{GraphStore, Node},
    script_runtime::PoolKind,
    script_wire::{Value, Variables},
};

use super::{
    machine::{Event, Machine, ObservedSample, StateKey, VarKey},
    shapes::{field, source_key},
    types::{InitFault, Issue, StateKind},
    vm::Vm,
    world::{HealthTargetFacts, Site, World},
};

pub(super) enum VoiceFault {
    Service(&'static str),
    Reported,
    Init(InitFault),
}

pub(super) struct VoiceHost<'a, 'g, 'w, 'm, W> {
    pub(super) vm: &'a mut Vm<'g, 'w, 'm, W>,
    pub(super) key: StateKey,
    pub(super) node: &'a Node,
}

impl<W: World> VoiceHost<'_, '_, '_, '_, W> {
    fn check(&mut self) -> Result<Site, VoiceFault> {
        self.vm
            .spend("voice service callback")
            .map_err(VoiceFault::Init)?;
        if !self
            .vm
            .state(self.key)
            .is_some_and(|state| state.active && !state.ending)
        {
            return Err(VoiceFault::Service("voice stimulus owner retired"));
        }
        Ok(self.vm.site(self.key.instance, self.node))
    }
}

impl<W: World> crate::script_voice::Host for VoiceHost<'_, '_, '_, '_, W> {
    type Error = VoiceFault;
    fn context(&mut self) -> Result<bool, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_context(site)
            .ok_or(VoiceFault::Service("voice stimulus context"))
    }
    fn manager(&mut self) -> Result<bool, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_manager(site)
            .ok_or(VoiceFault::Service("voice manager lookup"))
    }
    fn clock(&mut self) -> Result<f64, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_clock(site, self.vm.machine.now_ms)
            .ok_or(VoiceFault::Service("voice global clock"))
    }
    fn scalar(&mut self, field_name: &'static str, initial: Value) -> Result<Value, VoiceFault> {
        let site = self.check()?;
        self.vm
            .evaluate_selected_at(
                site,
                field(&self.node.fields, field_name),
                Some(self.key),
                initial,
                true,
            )
            .ok_or(VoiceFault::Reported)
    }
    fn load(&mut self, guid: u64) -> Result<Option<crate::script_voice::Stimulus>, VoiceFault> {
        let site = self.check()?;
        let stimulus = self
            .vm
            .world
            .voice_stimulus(site, guid)
            .ok_or(VoiceFault::Service("voice stimulus resource"))?;
        if stimulus.is_some_and(|stimulus| stimulus.guid != guid) {
            return Err(VoiceFault::Service("voice stimulus resource"));
        }
        Ok(stimulus)
    }
    fn allow(&mut self, stimulus: crate::script_voice::Stimulus) -> Result<bool, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_allow(site, stimulus)
            .ok_or(VoiceFault::Service("voice stimulus permission"))
    }
    fn targets(&mut self) -> Result<Vec<u32>, VoiceFault> {
        self.check()?;
        let values = self
            .vm
            .assignment_group(
                self.key.instance,
                self.node,
                field(&self.node.fields, "m_targets"),
            )
            .map_err(VoiceFault::Init)?
            .ok_or(VoiceFault::Reported)?;
        let mut targets = Vec::new();
        let Value::Array(values) = values else {
            return Err(VoiceFault::Service("voice target iterator"));
        };
        for value in values {
            self.vm
                .spend("voice target candidate")
                .map_err(VoiceFault::Init)?;
            if let Value::Entity(entity) = value {
                if script_expr::valid_entity(entity) && !targets.contains(&entity) {
                    targets.push(entity);
                }
            }
        }
        Ok(targets)
    }
    fn submit(
        &mut self,
        stimulus: crate::script_voice::Stimulus,
        targets: &[u32],
    ) -> Result<u32, VoiceFault> {
        let site = self.check()?;
        let actor = if self.vm.world.voice_actor_required(site, stimulus) {
            Some(
                self.vm
                    .evaluate(
                        self.key.instance,
                        self.node,
                        field(&self.node.fields, "m_8C8C5285"),
                        None,
                    )
                    .ok_or(VoiceFault::Reported)?,
            )
        } else {
            None
        };
        self.vm
            .world
            .voice_submit(site, stimulus, targets, actor.as_ref())
            .map_err(VoiceFault::Service)?
            .ok_or(VoiceFault::Service("voice stimulus submission"))
    }
    fn duration(&mut self, handle: u32, guid: u64, mode: bool) -> Result<f64, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_duration(site, handle, guid, mode)
            .ok_or(VoiceFault::Service("voice duration status"))
    }
    fn part(&mut self, number: u16) -> Result<f64, VoiceFault> {
        let site = self.check()?;
        self.vm
            .world
            .voice_part(site, number)
            .ok_or(VoiceFault::Service("voice next part"))
    }
}

impl<W: World> Vm<'_, '_, '_, W> {
    pub(super) fn fail_voice_states(&mut self) {
        let keys = self
            .machine
            .snapshot
            .instances
            .iter()
            .flat_map(|(&instance, state)| {
                state.states.iter().filter_map(move |(&raw, state)| {
                    (state.kind == StateKind::VoiceStimulus).then_some(StateKey { instance, raw })
                })
            })
            .collect::<Vec<_>>();
        for key in keys {
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("voice state");
            self.world.voice_context(self.site(key.instance, node));
            self.machine
                .queues
                .retain(key.instance, |_, event| event.state() != Some(key.raw));
            if let Some(state) = self.state_mut(key) {
                state.active = false;
                state.ending = false;
            }
        }
        self.machine.voice_poll.clear();
    }

    pub(super) fn voice_fault(
        &mut self,
        key: StateKey,
        error: VoiceFault,
    ) -> Result<(), InitFault> {
        self.fail_voice_states();
        match error {
            VoiceFault::Init(error) => return Err(error),
            VoiceFault::Reported => (),
            VoiceFault::Service(service) => {
                let node = self
                    .graph(key.instance)
                    .state(usize::from(key.raw))
                    .expect("voice state");
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField(service),
                );
            }
        }
        Ok(())
    }

    pub(super) fn begin_voice(&mut self, key: StateKey, node: &Node) -> Result<bool, InitFault> {
        let result = crate::script_voice::begin(&mut VoiceHost {
            vm: self,
            key,
            node,
        });
        match result {
            Ok(Some(voice)) => {
                self.state_mut(key).expect("active voice state").voice = Some(Box::new(voice));
                self.machine.voice_poll.push(key);
            }
            Ok(None) => (),
            Err(error) => {
                self.voice_fault(key, error)?;
                return Ok(false);
            }
        }
        Ok(true)
    }

    pub(super) fn poll_voice(&mut self) -> Result<(), InitFault> {
        let registry = self.machine.voice_poll.clone();
        for &key in &registry {
            self.spend("voice stimulus poll")?;
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("registered voice state");
            let Some(mut voice) = self
                .state(key)
                .filter(|state| state.active && !state.ending)
                .and_then(|state| state.voice.as_deref())
                .copied()
            else {
                self.report(
                    self.site(key.instance, node),
                    Issue::UnsupportedField("voice stimulus uninitialized poll"),
                );
                continue;
            };
            let result = crate::script_voice::poll(
                &mut voice,
                &mut VoiceHost {
                    vm: self,
                    key,
                    node,
                },
            );
            if let Some(state) = self.state_mut(key) {
                *state.voice.as_deref_mut().expect("registered voice cache") = voice;
            }
            match result {
                Ok(actions) => {
                    for action in actions {
                        match action {
                            crate::script_voice::Action::Timer(parameter) => self.post_after(
                                key.instance,
                                0,
                                Event::Timer(key.raw, u16::from(parameter)),
                            )?,
                            crate::script_voice::Action::Finish => {
                                self.post(key.instance, Event::Finish(key.raw))?
                            }
                        }
                    }
                }
                Err(error) => {
                    self.voice_fault(key, error)?;
                    return Ok(());
                }
            }
            if registry != self.machine.voice_poll {
                self.voice_fault(key, VoiceFault::Service("voice poll registry mutation"))?;
                break;
            }
        }
        Ok(())
    }

    pub(super) fn voice_timer(
        &mut self,
        key: StateKey,
        node: &Node,
        parameter: u16,
    ) -> Result<(), InitFault> {
        use crate::script_voice::Host;
        match (VoiceHost {
            vm: self,
            key,
            node,
        })
        .context()
        {
            Ok(false) => return Ok(()),
            Ok(true) => (),
            Err(error) => return self.voice_fault(key, error),
        }
        if parameter == 1 {
            let output = field(&node.fields, "m_FC2F7AAA");
            if !output.is_null() {
                let Ok(Some(output)) = script_expr::dynamic(output) else {
                    return self.voice_fault(key, VoiceFault::Service("voice duration output"));
                };
                if output.id != 0 {
                    let duration = self
                        .state(key)
                        .and_then(|state| state.voice.as_deref())
                        .map_or(Value::Null, |voice| Value::Float(voice.duration));
                    self.write(source_key(key.instance, output), duration)?;
                }
            }
        }
        if matches!(parameter, 1 | 2) {
            self.follow(key.instance, node, "m_onFinishedPlug", Some(key), 0)?;
        }
        Ok(())
    }

    pub(super) fn notify_voice_failure(&mut self, key: StateKey) -> Result<bool, InitFault> {
        if !self.machine.voice_poll.contains(&key)
            || !self
                .state(key)
                .is_some_and(|state| state.active && !state.ending)
        {
            return Ok(false);
        }
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("registered voice state");
        self.follow(key.instance, node, "m_F96FC964", Some(key), 0)?;
        Ok(true)
    }
}

pub(super) struct InstanceContext<'a, W> {
    pub(super) machine: &'a Machine,
    pub(super) graphs: &'a GraphStore,
    pub(super) world: &'a mut W,
    pub(super) site: Site,
    pub(super) reads: Vec<VarKey>,
    pub(super) foreign_reads: Vec<(u32, u32)>,
    pub(super) parameters: Option<&'a Variables>,
    pub(super) cycle_candidate: Option<i64>,
    pub(super) health_target_facts: Option<&'a HealthTargetFacts>,
    pub(super) observed_sample: Option<&'a ObservedSample>,
    pub(super) settled_damage: bool,
    pub(super) watcher: Option<StateKey>,
    pub(super) read_native: bool,
    pub(super) read_clock: bool,
    pub(super) untrusted_native: bool,
}

/// The reference's TRUSTED_NATIVES (driver.py): config classes whose server
/// answer is the owner client's own for a human player's body. A polled
/// switch that reads any other native is kept off the owner stream.
pub(super) const CLIENT_AGREED_NATIVES: [&str; 9] = [
    "STU_1FF67CA6",
    "STU_310D6529",
    "STU_37F20D24",
    "STU_4A101787",
    "STU_A77AC2AC",
    "STU_A3DEF368",
    "STU_DB742151",
    "STU_295D117D",
    "STU_D3EB8D87",
];

impl<W: World> InstanceContext<'_, W> {
    fn immediate_health_target(&self) -> Option<&HealthTargetFacts> {
        self.health_target_facts.filter(|_| self.watcher.is_none())
    }

    fn settled_target(&self, entity: u32) -> Option<&HealthTargetFacts> {
        self.immediate_health_target()
            .filter(|facts| entity != self.site.entity && facts.target == entity)
    }

    pub(super) fn read(&mut self, key: VarKey) -> Value {
        if !self.reads.contains(&key) {
            self.reads.push(key);
        }
        let below = self.watcher.filter(|watcher| {
            self.machine
                .snapshot
                .instances
                .get(&watcher.instance)
                .and_then(|instance| instance.states.get(&watcher.raw))
                .is_some_and(|state| {
                    matches!(state.kind, StateKind::Stack { .. })
                        && state.linked_variable == Some(key)
                })
        });
        let sampled_base = self
            .observed_sample
            .and_then(|sample| sample.input.as_ref())
            .filter(|(output, _)| *output == key)
            .map(|(_, value)| value);
        let (value, read_clock) =
            self.machine
                .value_with_clock_dependency(key, below, sampled_base);
        self.read_clock |= read_clock;
        value
    }
}

impl<W: World> Context for InstanceContext<'_, W> {
    fn strict(&self) -> bool {
        self.machine.strict
    }

    fn asset_has_category(&mut self, asset: u64, kind: u64, id: u64) -> Option<bool> {
        self.graphs.asset_has_category(asset, kind, id)
    }

    fn equipped_cosmetic(
        &mut self,
        entity: u32,
        slot: usize,
        cosmetic: crate::script_expr::EquippedCosmetic,
    ) -> Option<u64> {
        self.world.equipped_cosmetic(entity, slot, cosmetic)
    }

    fn asset_texture(&mut self, action: u64) -> Option<u64> {
        self.graphs.asset_texture(action)
    }

    fn variable(&mut self, variable: Variable) -> Value {
        self.read(source_key(self.site.instance, variable))
    }

    fn parent_variable(&mut self, levels: u8, variable: Variable) -> Value {
        let mut id = self.site.instance;
        for _ in 0..levels {
            let Some((parent, _)) = self.machine.snapshot.instances[&id].parent else {
                return Value::Null;
            };
            id = parent;
        }
        self.read(source_key(id, variable))
    }

    fn parent_entity(&mut self, _levels: u8) -> Value {
        self.owner()
    }

    fn parameter(&mut self, id: u32) -> Value {
        if let Some(candidate) = self.cycle_candidate {
            return Value::Integer(candidate);
        }
        self.parameters
            .and_then(|parameters| parameters.get(&id))
            .cloned()
            .unwrap_or(Value::Null)
    }

    fn owner(&mut self) -> Value {
        Value::Entity(self.site.entity)
    }

    fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity == self.site.entity {
            Some(self.read(VarKey::Entity(id)))
        } else {
            self.read_native = true;
            if let Some(facts) = self.settled_target(entity) {
                return facts.script_state.read(id);
            }
            if !self.foreign_reads.contains(&(entity, id)) {
                self.foreign_reads.push((entity, id));
            }
            self.world.entity_variable(entity, id)
        }
    }

    fn ruleset(&mut self, id: u32) -> Value {
        match self.read(VarKey::Entity(id)) {
            Value::Null => self.world.ruleset(id).unwrap_or(Value::Integer(0)),
            value => value,
        }
    }

    fn position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.world.position(entity)
    }
    fn entity_velocity(&mut self, entity: u32) -> script_expr::VelocityQuery {
        self.read_native = true;
        self.world.entity_velocity(entity)
    }
    fn body_angle(&mut self, entity: u32, axis: script_expr::BodyAngle) -> script_expr::AngleQuery {
        self.read_native = true;
        self.world.body_angle(entity, axis)
    }

    fn entity_exists(&mut self, entity: u32) -> Option<bool> {
        self.read_native = true;
        self.world.entity_exists(entity)
    }

    fn known_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.read_native = true;
        self.world.known_position(entity)
    }

    fn forward(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.world.forward(entity)
    }

    fn pitch(&mut self, entity: u32) -> Option<f64> {
        self.world.pitch(entity)
    }

    fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.read_native = true;
        self.world.authored_position(entity)
    }

    fn authored_forward(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.read_native = true;
        self.world.authored_forward(entity)
    }

    fn body_eye_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        self.read_native = true;
        self.world.body_eye_position(entity)
    }

    fn body_bone_point(&mut self, entity: u32, bone: u64, local: [f64; 3]) -> Option<[f64; 3]> {
        self.read_native = true;
        self.world.body_bone_point(entity, bone, local)
    }

    fn body_has_hardpoint(&mut self, entity: u32, hardpoint: u64) -> Option<bool> {
        self.read_native = true;
        self.world.body_has_hardpoint(self.site, entity, hardpoint)
    }

    fn body_active(&mut self) -> Option<bool> {
        self.read_native = true;
        self.world.body_active(self.site)
    }

    fn spatial_candidates(&mut self) -> Option<&[script_expr::SpatialCandidate]> {
        self.read_native = true;
        self.world.spatial_candidates()
    }

    fn room_entities(&mut self) -> Option<&BTreeMap<u32, script_expr::RoomEntityFacts>> {
        self.read_native = true;
        self.world.room_entities()
    }

    fn settled_health_target(&self, entity: u32) -> Option<&HealthTargetFacts> {
        self.settled_target(entity)
    }

    fn spatial_line_clear(&mut self, origin: [f64; 3], target: [f64; 3]) -> Option<bool> {
        self.read_native = true;
        self.world.spatial_line_clear(origin, target)
    }

    fn spatial_ray_cast(
        &mut self,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
    ) -> Option<Option<script_expr::SpatialRayHit>> {
        self.read_native = true;
        self.world.spatial_ray_cast(
            self.site,
            self.machine.frame,
            origin,
            direction,
            max_distance,
        )
    }

    fn spatial_actor_ray_cast(
        &mut self,
        origin: [f64; 3],
        direction: [f64; 3],
        max_distance: f64,
        targets: &[u32],
    ) -> Option<Option<script_expr::SpatialActorRayHit>> {
        self.read_native = true;
        self.world.spatial_actor_ray_cast(
            self.site,
            self.machine.frame,
            origin,
            direction,
            max_distance,
            targets,
        )
    }

    fn combat_targets(&mut self, config: &Json) -> Option<Vec<u32>> {
        self.read_native = true;
        self.world.combat_targets(self.site, config)
    }
    fn static_target(&mut self, tag: u64) -> Option<u32> {
        self.world.static_target(tag)
    }

    fn random_u64(&mut self) -> Option<u64> {
        Some(self.machine.random_u64())
    }

    fn body_scale(&mut self, entity: u32) -> Option<f64> {
        if entity != self.site.entity {
            return None;
        }
        self.read_native = true;
        let scale_override = self.read(VarKey::Entity(31_210));
        self.world.body_scale(self.site, entity, &scale_override)
    }

    fn body_health(&mut self, entity: u32, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
        self.read_native = true;
        self.world.entity_health(self.site, entity, kind, maximum)
    }

    fn temporary_shield_current(&mut self, identifier: u64) -> Option<f64> {
        self.read_native = true;
        self.world.temporary_shield_current(self.site, identifier)
    }

    fn total_health(&mut self, entity: u32) -> script_expr::HealthQuery {
        self.read_native = true;
        self.world.total_health(self.site, entity)
    }
    fn body_display_name(&mut self, entity: u32) -> Option<String> {
        self.read_native = true;
        self.world.body_display_name(self.site, entity)
    }

    fn entity_team(&mut self, entity: u32) -> Option<i8> {
        self.read_native = true;
        self.world.entity_team(self.site, entity)
    }

    fn entity_hero(&mut self, entity: u32) -> Option<u64> {
        self.read_native = true;
        self.world.entity_hero(self.site, entity)
    }
    fn entity_definition(&mut self, entity: u32) -> Option<u64> {
        self.read_native = true;
        self.world.entity_definition(self.site, entity)
    }

    fn engine_setting(&mut self, key: u32) -> script_expr::EngineSetting {
        self.read_native = true;
        self.world.engine_setting(self.site, key)
    }

    fn native(&mut self, config: &Json) -> Option<Value> {
        self.read_native = true;
        if script_expr::native_shape(config, "STU_D3685C04", None)
            && self.world.local_command_present(self.site) == Some(false)
        {
            return Some(Value::Bool(false));
        }
        self.world.native(self.site, config)
    }

    fn native_uses_initial(&mut self, config: &Json) -> bool {
        self.read_native = true;
        self.world.native_uses_initial(self.site, config)
    }

    fn watches_natives(&self) -> bool {
        self.watcher.is_some()
    }

    fn reference_native(&mut self, class: &str) {
        if !CLIENT_AGREED_NATIVES.contains(&class) {
            self.untrusted_native = true;
        }
    }

    fn has_target_tag(&mut self, entity: u32, tag: u64) -> Option<bool> {
        self.read_native = true;
        match self.settled_target(entity) {
            Some(facts) => facts.tags.presence(tag),
            None => self.world.has_target_tag(self.site, entity, tag),
        }
    }

    fn scene_filter_policy(&mut self, filter: &Json) -> Option<script_expr::SceneFilterPolicy> {
        self.read_native = true;
        match self
            .immediate_health_target()
            .filter(|_| self.settled_damage)
        {
            Some(facts) => self
                .world
                .settled_scene_filter_policy(self.site, filter, facts),
            None => self.world.scene_filter_policy(self.site, filter),
        }
    }
    fn entity_filter_flags(&mut self, entity: u32) -> Option<u64> {
        self.read_native = true;
        match self.settled_target(entity) {
            Some(facts) => facts.filter_flags,
            None => self.world.entity_filter_flags(self.site, entity),
        }
    }
    fn entities_hostile(&mut self, source: u32, target: u32, option: bool) -> Option<bool> {
        self.read_native = true;
        match self
            .immediate_health_target()
            .filter(|facts| self.settled_damage && facts.target == target)
        {
            Some(facts) => self
                .world
                .settled_entities_hostile(self.site, source, facts, option),
            None => self
                .world
                .entities_hostile(self.site, source, target, option),
        }
    }

    fn body_on_fire(&mut self, entity: u32) -> Option<OnFire> {
        self.read_native = true;
        self.world.body_on_fire(self.site, entity)
    }

    fn team_members(&mut self, team: u8) -> Option<u32> {
        self.read_native = true;
        self.world.team_members(team)
    }

    fn entity_component92(&mut self, entity: u32) -> Option<bool> {
        self.read_native = true;
        self.world.entity_component92(self.site, entity)
    }

    fn button_held(&mut self, entity: u32, button: u8) -> Option<bool> {
        self.read_native = true;
        if entity == self.site.entity {
            Some(
                !self.machine.control_blocked
                    && self.machine.button_blocks[usize::from(button)] == 0
                    && self.machine.held_buttons.contains(&button),
            )
        } else {
            Some(false)
        }
    }

    fn related_entity(&mut self, relation: EntityRelation, entity: u32) -> Option<u32> {
        self.read_native = true;
        self.world.related_entity(self.site, relation, entity)
    }

    fn linked_entity(&mut self, entity: u32, slot: u32) -> Option<u32> {
        self.read_native = true;
        self.world.linked_entity(self.site, entity, slot)
    }

    fn would_stack_to_top(&mut self, variable: Variable, priority: StackPriority) -> Option<bool> {
        let key = source_key(self.site.instance, variable);
        self.read(key);
        Some(self.machine.allows_stack(key, priority, None))
    }
}
