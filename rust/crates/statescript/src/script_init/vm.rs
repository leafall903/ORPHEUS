//! StateScript virtual machine execution, scheduling, abilities, and node dispatch.

use std::{
    collections::{BTreeMap, HashSet},
    sync::Arc,
};

use indexmap::IndexMap;
use orpheus_runtime::format::parse_guid;
use orpheus_wire::commands::Command;
use serde_json::Value as Json;

use crate::{
    script_expr::{self, EvalError, Scope, StackPriority, truthy},
    script_graph::{
        Graph, GraphStore, Node, asset_guid, decode_link, directed_numeric_payload,
        message_parameter_id,
    },
    script_runtime::{ButtonEdge, DispatchOrigin, FRAME_MS, button_edges, frame_start},
    script_wire::{Value, Variables},
};

use super::{
    ABILITY_HELD, ABILITY_PUSHED, ABILITY_RUNNING, ABORT_PLUG, MAX_EVENTS_PER_UPDATE,
    MAX_PLAY_INSTANCES, MAX_PLUG_DEPTH, Root, SCRIPT_SOURCE,
    machine::{
        AbilityPress, Event, LinkValue, LogicalButtonStage, Machine, ObservationInput,
        ObservedSample, Startup, StateKey, UnavailableSample, VarKey, VariableLink,
    },
    shapes::{
        ability_is_stacked, array_field, configured, field, is_entry, is_self_target, same,
        source_key, stored,
    },
    types::{
        AbilityCooldown, BufferedPress, Diagnostic, InitFault, Instance, InstanceScope, Issue,
        State, StateKind,
    },
    voice::InstanceContext,
    world::{HostHealthContext, PlayScript, RoomCue, RoomMessage, Site, World},
};

mod contact_combat;
mod effects;
mod nodes;
mod retail_catalog;
mod static_trace;
mod target_tracking;
mod transitions;

pub(super) struct Vm<'graph, 'world, 'machine, W> {
    pub(super) graphs: &'graph GraphStore,
    pub(super) world: &'world mut W,
    pub(super) machine: &'machine mut Machine,
    pub(super) parameters: Option<Arc<Variables>>,
    health_context: HostHealthContext,
    observed_input: Option<ObservationInput>,
    pub(super) command_frame: Option<u32>,
    origin: DispatchOrigin,
    settled_damage_receiver: Option<u16>,
    pub(super) output_write_depth: usize,
}

impl<'graph, 'world, 'machine, W: World> Vm<'graph, 'world, 'machine, W> {
    pub(in crate::script_init) fn new(
        graphs: &'graph GraphStore,
        world: &'world mut W,
        machine: &'machine mut Machine,
    ) -> Self {
        Self {
            graphs,
            world,
            machine,
            parameters: None,
            health_context: HostHealthContext::default(),
            observed_input: None,
            command_frame: None,
            origin: DispatchOrigin::Ordinary,
            settled_damage_receiver: None,
            output_write_depth: 0,
        }
    }

    pub(in crate::script_init) fn start(&mut self, mut roots: Vec<Root>) -> Result<(), InitFault> {
        let mut seen = HashSet::new();
        for root in &roots {
            let valid = if root.is_server_only() {
                root.id == 0
            } else {
                root.id != 0 && seen.insert(root.id)
            };
            if !valid {
                return Err(InitFault::InvalidRoot(root.id));
            }
        }
        let private_count = roots.len() - seen.len();
        if private_count != 0 {
            let highest_public = seen.into_iter().max().unwrap_or(0);
            if private_count > usize::from(u16::MAX - highest_public) {
                return Err(InitFault::InstanceIdExhausted);
            }
            roots.sort_by_key(Root::is_server_only);
        }
        for root in roots {
            let (id, scope) = if root.is_server_only() {
                (
                    self.machine.next_instance_id(true)?,
                    InstanceScope::Isolated,
                )
            } else {
                (root.id, InstanceScope::Projected)
            };
            let startup = if root.run_startup {
                Startup::Ordinary
            } else {
                Startup::Passive
            };
            if self.create(id, root.graph, None, root.variables, startup, scope)? {
                if let Some(raw) = root.initial_state {
                    self.post(id, Event::Begin(raw))?;
                }
            }
        }
        self.run()
    }

    fn instance_scope(&self, id: u16) -> InstanceScope {
        self.machine
            .snapshot
            .instances
            .get(&id)
            .map_or(InstanceScope::Projected, |instance| instance.scope)
    }

    pub(in crate::script_init) fn spend(
        &mut self,
        operation: &'static str,
    ) -> Result<(), InitFault> {
        self.machine.work_left =
            self.machine
                .work_left
                .checked_sub(1)
                .ok_or(InitFault::WorkBudget {
                    limit: self.machine.max_work,
                    operation,
                })?;
        Ok(())
    }

    pub(in crate::script_init) fn create(
        &mut self,
        id: u16,
        graph: u32,
        parent: Option<(u16, u16)>,
        mut variables: Variables,
        startup: Startup,
        scope: InstanceScope,
    ) -> Result<bool, InitFault> {
        self.spend("instance creation")?;
        let Some(definition) = self.graphs.graph(graph) else {
            self.record_diagnostic(Diagnostic {
                site: Site {
                    entity: self.machine.snapshot.entity,
                    instance: id,
                    graph,
                    node: None,
                },
                issue: Issue::MissingGraph(graph),
                registration_rejected: false,
                server_only: scope.is_server_only(),
            });
            return Ok(false);
        };
        let apply_schema =
            self.machine.public_schemas && !matches!(startup, Startup::Played { .. });
        self.machine.claim_instance_id(id, scope.is_isolated())?;
        self.machine.snapshot_dirty = true;
        self.machine.snapshot.instances.insert(
            id,
            Instance {
                graph,
                start_ms: self.machine.now_ms,
                retire_after_frame: matches!(startup, Startup::Played { auto_retire: true })
                    .then(|| self.machine.frame.saturating_add(1)),
                scope,
                parent,
                variables: if apply_schema {
                    Variables::new()
                } else {
                    std::mem::take(&mut variables)
                },
                states: IndexMap::new(),
                join_inputs: BTreeMap::new(),
            },
        );
        if apply_schema {
            let site = Site {
                entity: self.machine.snapshot.entity,
                instance: id,
                graph,
                node: None,
            };
            let schema = field(&definition.fields, "m_publicSchema");
            let mut defaults = Variables::new();
            if !schema.is_null() {
                if !matches!(
                    field(schema, "$").as_str(),
                    Some("STUStatescriptSchema" | "STU_77990ED7")
                ) || (!field(schema, "m_89EAAC40").is_null()
                    && (field(schema, "$").as_str() != Some("STU_77990ED7")
                        || field(schema, "m_89EAAC40").as_str().and_then(parse_guid)
                            != Some(0x0580_0000_0000_0000 | u64::from(graph))))
                {
                    self.report(site, Issue::UnsupportedField("played graph schema"));
                } else {
                    let rows = field(schema, "m_entries");
                    if !rows.is_null() && !rows.is_array() {
                        self.report(site, Issue::UnsupportedField("played graph schema entries"));
                    }
                    for row in array_field(schema, "m_entries") {
                        self.spend("public schema evaluation")?;
                        if !row.is_object() {
                            self.report(site, Issue::UnsupportedField("played graph schema row"));
                            continue;
                        }
                        let config = field(row, "m_value");
                        if config.is_null() {
                            continue;
                        }
                        let identifier_config = field(row, "m_0D09D2D9");
                        if identifier_config.is_null() {
                            continue;
                        }
                        let Some(identifier) = identifier_config.as_str().and_then(parse_guid)
                        else {
                            self.report(
                                site,
                                Issue::UnsupportedField("played graph schema identifier"),
                            );
                            continue;
                        };
                        if identifier == 0 {
                            continue;
                        }
                        if let Some(value) = self.evaluate_at(site, config, None) {
                            defaults.insert(u32::from(identifier as u16), value);
                        }
                    }
                }
            }
            defaults.extend(variables);
            self.machine
                .snapshot
                .instances
                .get_mut(&id)
                .expect("created instance")
                .variables = defaults;
        }
        if !matches!(startup, Startup::Passive | Startup::Played { .. }) {
            for position in definition.entries.iter().flatten() {
                if definition
                    .node(*position)
                    .is_some_and(|node| node.runs_on_server() && is_entry(node))
                {
                    self.post(id, Event::Entry(*position))?;
                }
            }
        }
        Ok(true)
    }

    pub(in crate::script_init) fn run(&mut self) -> Result<(), InitFault> {
        loop {
            self.update()?;
            self.notify_code(1)?;
            if self
                .machine
                .queues
                .next_due()
                .is_none_or(|due| due > self.machine.now_ms)
            {
                return Ok(());
            }
        }
    }

    pub(in crate::script_init) fn advance_frame(
        &mut self,
        frame: u32,
        command: Option<Command>,
    ) -> Result<(), InitFault> {
        self.machine.frame = frame;
        self.machine.now_ms = frame_start(frame);
        if let Some(command) = command {
            self.queue_buttons(command)?;
        }
        let end = self.machine.now_ms + FRAME_MS - 1;
        loop {
            let due = self
                .machine
                .queues
                .next_due()
                .filter(|&due| due <= end)
                .unwrap_or(end);
            self.machine.now_ms = self.machine.now_ms.max(due);
            self.update()?;
            self.notify_code(1)?;
            if self.machine.now_ms == end
                && self.machine.queues.next_due().is_none_or(|due| due > end)
            {
                return self.report_active_volleys();
            }
        }
    }

    pub(in crate::script_init) fn queue_buttons(
        &mut self,
        mut command: Command,
    ) -> Result<(), InitFault> {
        if self.machine.control_blocked {
            command.buttons = 0;
            command.action = 0;
            command.forward = 0;
            command.right = 0;
        }
        let next_directions = crate::script_runtime::direction_bits(command.forward, command.right);
        let edges: Vec<_> = button_edges(
            self.machine.command_buttons,
            command.buttons,
            command.action,
        )
        .chain(crate::script_runtime::direction_edges(
            self.machine.command_directions,
            next_directions,
        ))
        .collect();
        self.machine
            .queues
            .reserve(edges.len() * self.machine.snapshot.instances.len())?;
        for &id in self.machine.snapshot.instances.keys() {
            for &edge in &edges {
                self.machine
                    .queues
                    .push(id, self.machine.now_ms, Event::Button(edge))?;
            }
        }
        self.machine.command_buttons = command.buttons;
        self.machine.command_directions = next_directions;
        Ok(())
    }

    pub(in crate::script_init) fn button_edge(
        &mut self,
        id: u16,
        edge: ButtonEdge,
    ) -> Result<(), InitFault> {
        if edge.pressed {
            if !self.machine.held_buttons.contains(&edge.button) {
                self.machine.held_buttons.push(edge.button);
            }
        } else {
            self.machine
                .held_buttons
                .retain(|&button| button != edge.button);
        }
        let graph = self.graph(id);
        for raw in graph.logical_button_slots() {
            let Some(node) = graph.state(usize::from(raw)) else {
                continue;
            };
            if !node.runs_on_server() {
                continue;
            }
            let key = StateKey { instance: id, raw };
            if node.class == "STUStatescriptStateAbility" {
                self.spend("ability button edge")?;
                self.ability_edge(key, node, edge)?;
                continue;
            }
            if node.class != "STUStatescriptStateLogicalButton" {
                continue;
            }
            self.spend("logical button edge")?;
            if self.logical_button_id(id, node, None) != Some(edge.button)
                || self.logical_button_blocked(key, node, edge.button)
            {
                continue;
            }
            if edge.pressed && !self.logical_button_allows_press(id, node) {
                continue;
            }
            if edge.pressed {
                let state = self.machine.snapshot.instances[&id]
                    .states
                    .entry(raw)
                    .or_insert_with(|| State::dormant(StateKind::LogicalButton { down: false }));
                state.buffered_press = Some(BufferedPress {
                    time_ms: self.machine.now_ms,
                    activation: state.activation.wrapping_add(u16::from(!state.active)),
                    button: edge.button,
                });
            }
            if self.state(key).is_some_and(|state| {
                state.active && !state.ending
                    && matches!(state.kind, StateKind::LogicalButton { down } if down != edge.pressed)
            }) {
                self.set_button_down(key, node, edge.pressed, 0)?;
            }
        }
        if edge.pressed {
            let mut recheck = Vec::new();
            for condition in &self.graph(id).nodes {
                if condition.class != "STU_7DEB522D" || !configured(condition, "m_00994FF0") {
                    continue;
                }
                if self.logical_button_id(id, condition, None) != Some(edge.button) {
                    continue;
                }
                for target in array_field(&condition.fields, "m_122DF0CA") {
                    if let Some(raw) = target.as_u64().and_then(|value| u16::try_from(value).ok()) {
                        recheck.push(raw);
                    }
                }
            }
            for raw in recheck {
                let key = StateKey { instance: id, raw };
                if self
                    .state(key)
                    .is_some_and(|state| state.active && !state.ending)
                {
                    if let Some(node) = self.graph(id).state(usize::from(raw)) {
                        self.try_complete(key, node, 0)?;
                    }
                }
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn logical_button_id(
        &mut self,
        id: u16,
        node: &Node,
        watcher: Option<StateKey>,
    ) -> Option<u8> {
        let value = self.evaluate(id, node, field(&node.fields, "m_logicalButton"), watcher)?;
        u8::try_from(script_expr::to_int(&value, 0)).ok()
    }

    pub(in crate::script_init) fn button_held(&mut self, key: StateKey, node: &Node) -> bool {
        self.logical_button_id(key.instance, node, None)
            .is_some_and(|button| self.machine.held_buttons.contains(&button))
    }

    pub(in crate::script_init) fn button_is_blocked(&self, button: u8) -> bool {
        self.machine.control_blocked || self.machine.button_blocks[usize::from(button)] != 0
    }

    pub(in crate::script_init) fn effective_button_held(&self, button: u8) -> bool {
        self.machine.held_buttons.contains(&button) && !self.button_is_blocked(button)
    }

    pub(in crate::script_init) fn logical_button_blocked(
        &mut self,
        key: StateKey,
        node: &Node,
        button: u8,
    ) -> bool {
        self.button_is_blocked(button)
            && !self
                .evaluate(key.instance, node, field(&node.fields, "m_53C4B6A6"), None)
                .is_some_and(|value| truthy(&value))
    }

    fn logical_button_allows_press(&mut self, id: u16, node: &Node) -> bool {
        !self.machine.owner_dead
            || self
                .evaluate(id, node, field(&node.fields, "m_5AE05408"), None)
                .is_some_and(|value| truthy(&value))
    }

    fn logical_button_admitted_and_held(&mut self, key: StateKey, node: &Node, button: u8) -> bool {
        self.machine.held_buttons.contains(&button)
            && !self.logical_button_blocked(key, node, button)
            && self.logical_button_allows_press(key.instance, node)
    }

    pub(in crate::script_init) fn resync_logical_buttons(
        &mut self,
        button_filter: Option<u8>,
        depth: usize,
    ) -> Result<(), InitFault> {
        let keys: Vec<_> = self
            .machine
            .snapshot
            .instances
            .iter()
            .flat_map(|(&instance, script)| {
                script.states.iter().filter_map(move |(&raw, state)| {
                    (state.active
                        && !state.ending
                        && matches!(state.kind, StateKind::LogicalButton { .. }))
                    .then_some(StateKey { instance, raw })
                })
            })
            .collect();
        for key in keys {
            self.spend("logical button resync")?;
            let Some(state) = self
                .state(key)
                .filter(|state| state.active && !state.ending)
            else {
                continue;
            };
            let StateKind::LogicalButton { down } = state.kind else {
                continue;
            };
            if button_filter.is_none() && !down {
                continue;
            }
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("logical button state");
            let Some(button) = self
                .logical_button_id(key.instance, node, None)
                .filter(|&button| button_filter.is_none_or(|filter| filter == button))
            else {
                continue;
            };
            let held = self.logical_button_admitted_and_held(key, node, button);
            if held != down {
                self.set_button_down(key, node, held, depth)?;
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn release_button_block(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(state) = self.state_mut(key) else {
            return Ok(());
        };
        let StateKind::ButtonBlock(Some(button)) = state.kind else {
            return Ok(());
        };
        // Clear ownership before button callbacks can end or destroy this state again.
        state.kind = StateKind::ButtonBlock(None);
        let count = &mut self.machine.button_blocks[usize::from(button)];
        *count -= 1;
        self.resync_logical_buttons(Some(button), depth)
    }

    pub(in crate::script_init) fn enter_button(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let state = self.state(key).expect("active logical button");
        let activation = state.activation;
        let initial_stage = LogicalButtonStage {
            activation,
            revision: state.logical_button_revision,
            down: false,
        };
        let buffer = state
            .buffered_press
            .filter(|press| press.activation == activation);
        let button = self.logical_button_id(key.instance, node, Some(key));
        self.state_mut(key)
            .expect("active logical button")
            .logical_button_binding = button;
        let buffer = buffer.filter(|press| button == Some(press.button));
        let blocked = button.is_some_and(|button| self.logical_button_blocked(key, node, button))
            || !self.logical_button_allows_press(key.instance, node);
        let duration = field(&node.fields, "m_246438AD");
        let early = if let Some(press) = buffer.filter(|_| !duration.is_null() && !blocked) {
            let waited = ((self.machine.now_ms - press.time_ms) as f64 / 1000.0) as f32;
            self.evaluate(key.instance, node, duration, None)
                .is_some_and(|value| f64::from(waited) <= script_expr::to_float(&value))
        } else {
            false
        };
        if early {
            self.follow(key.instance, node, "m_onGoingDownPlug", Some(key), depth)?;
        }
        if !self.logical_button_stage_is_current(key, initial_stage) {
            return Ok(());
        }
        let blocked = blocked
            || (early
                && (button.is_some_and(|button| self.logical_button_blocked(key, node, button))
                    || !self.logical_button_allows_press(key.instance, node)));
        let require_fresh_press = self
            .evaluate(key.instance, node, field(&node.fields, "m_4B925089"), None)
            .is_some_and(|value| truthy(&value));
        let held = !require_fresh_press && !blocked && self.button_held(key, node);
        let buffered = early && !blocked;
        let down = held || buffered;
        self.state_mut(key).expect("active logical button").kind =
            StateKind::LogicalButton { down };
        let stage = LogicalButtonStage {
            down,
            ..initial_stage
        };
        if early {
            self.queue_logical_button_branch(key, stage)?;
        } else {
            self.follow_logical_button_branch(
                key,
                stage,
                self.current_observation_sample(),
                depth,
            )?;
        }
        if buffered && !held {
            self.post_after(key.instance, 1, Event::Timer(key.raw, 1))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn refresh_logical_button_binding(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let button = self.logical_button_id(key.instance, node, Some(key));
        let state = self.state_mut(key).expect("active logical button");
        let StateKind::LogicalButton { down } = state.kind else {
            return Ok(());
        };
        if state.ending || state.logical_button_binding == button {
            return Ok(());
        }
        state.logical_button_binding = button;
        state.buffered_press = state
            .buffered_press
            .filter(|press| Some(press.button) == button);
        // A synthetic release scheduled for the old buffer cannot release the new binding.
        self.machine.queues.retain(
            key.instance,
            |_, event| !matches!(event, Event::Timer(raw, 1) if *raw == key.raw),
        );
        let held =
            button.is_some_and(|button| self.logical_button_admitted_and_held(key, node, button));
        if held != down {
            self.set_button_down(key, node, held, 0)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn set_button_down(
        &mut self,
        key: StateKey,
        node: &Node,
        down: bool,
        depth: usize,
    ) -> Result<(), InitFault> {
        let state = self.state_mut(key).expect("active logical button");
        if state.kind == (StateKind::LogicalButton { down }) {
            return Ok(());
        }
        state.kind = StateKind::LogicalButton { down };
        // A listener can leave and re-enter the same branch before its queue runs.
        state.logical_button_revision = state.logical_button_revision.wrapping_add(1);
        let stage = LogicalButtonStage {
            activation: state.activation,
            revision: state.logical_button_revision,
            down,
        };
        let (previous, edge) = if down {
            ("m_1C9EF058", "m_onGoingDownPlug")
        } else {
            ("m_80AF45FB", "m_onComingUpPlug")
        };
        self.exit_subgraph(key.instance, node, previous, depth + 1)?;
        if !self.logical_button_stage_is_current(key, stage) {
            return Ok(());
        }
        self.follow(key.instance, node, edge, Some(key), depth)?;
        if self.logical_button_stage_is_current(key, stage) {
            self.queue_logical_button_branch(key, stage)?;
        }
        Ok(())
    }

    fn queue_logical_button_branch(
        &mut self,
        key: StateKey,
        stage: LogicalButtonStage,
    ) -> Result<(), InitFault> {
        // Edge callbacks can schedule state changes for this same instant.
        self.post(
            key.instance,
            Event::LogicalButtonBranch {
                state: key.raw,
                stage,
                sample: self.current_observation_sample(),
            },
        )
    }

    fn current_observation_sample(&self) -> Option<Arc<ObservedSample>> {
        self.observed_input
            .as_ref()
            .map(|input| Arc::clone(&input.sample))
    }

    fn logical_button_stage_is_current(&self, key: StateKey, stage: LogicalButtonStage) -> bool {
        self.state(key).is_some_and(|state| {
            state.active
                && !state.ending
                && state.activation == stage.activation
                && state.logical_button_revision == stage.revision
                && state.kind == (StateKind::LogicalButton { down: stage.down })
        })
    }

    fn follow_logical_button_branch(
        &mut self,
        key: StateKey,
        stage: LogicalButtonStage,
        sample: Option<Arc<ObservedSample>>,
        depth: usize,
    ) -> Result<(), InitFault> {
        if self.logical_button_stage_is_current(key, stage) {
            let node = self
                .graph(key.instance)
                .state(usize::from(key.raw))
                .expect("logical button state");
            let path = if stage.down {
                "m_80AF45FB"
            } else {
                "m_1C9EF058"
            };
            let follow = |vm: &mut Self| {
                vm.follow(key.instance, node, path, Some(key), depth)
                    .map(|_| ())
            };
            if let Some(sample) = sample {
                // Once the input transition commits, its branch owns the lifecycle.
                self.resume_observed(
                    ObservationInput {
                        sample,
                        on_unavailable: UnavailableSample::ReadCurrent,
                    },
                    follow,
                )?;
            } else {
                follow(self)?;
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn ability_buttons(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Option<[u8; 3]> {
        let mut buttons = [0; 3];
        for (button, field_name) in
            buttons
                .iter_mut()
                .zip(["m_A5F1A77B", "m_A368407D", "m_8CBD15DA"])
        {
            let value = self.evaluate(key.instance, node, field(&node.fields, field_name), None)?;
            *button = u8::try_from(script_expr::to_int(&value, 0)).ok()?;
        }
        Some(buttons)
    }

    pub(in crate::script_init) fn ability_edge(
        &mut self,
        key: StateKey,
        node: &Node,
        edge: ButtonEdge,
    ) -> Result<(), InitFault> {
        if !edge.pressed
            || edge.button == 0
            || self.button_is_blocked(edge.button)
            || !self
                .ability_buttons(key, node)
                .is_some_and(|buttons| buttons.contains(&edge.button))
        {
            return Ok(());
        }
        let state = self.state(key);
        let active = state.is_some_and(|state| state.active);
        let activation = state
            .map_or(0, |state| state.activation)
            .wrapping_add(u16::from(!active));
        let now = self.machine.now_ms;
        let presses = self.machine.ability_presses.entry(key).or_default();
        presses.retain(|press| now - press.time_ms < 2000);
        presses.push(AbilityPress {
            button: edge.button,
            time_ms: now,
            activation,
        });
        if active {
            self.post(key.instance, Event::AbilityCheck(key.raw))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn take_ability_press(
        &mut self,
        key: StateKey,
        node: &Node,
        button: u8,
    ) -> bool {
        self.ability_press(key, node, button, false)
    }

    pub(in crate::script_init) fn ability_press(
        &mut self,
        key: StateKey,
        node: &Node,
        button: u8,
        retain: bool,
    ) -> bool {
        let early = field(&node.fields, "m_246438AD");
        let window = if early.is_null() {
            0.0
        } else {
            let Some(value) = self.evaluate(key.instance, node, early, None) else {
                return false;
            };
            ((script_expr::to_float(&value) * 1000.0) as f32).round()
        };
        let now = self.machine.now_ms;
        let activation = self.state(key).expect("active ability").activation;
        let Some(presses) = self.machine.ability_presses.get_mut(&key) else {
            return false;
        };
        let Some(index) = presses.iter().position(|press| {
            press.button == button
                && (now - press.time_ms) as f32 <= window
                && press.activation <= activation
        }) else {
            return false;
        };
        if !retain {
            presses.remove(index);
        }
        true
    }

    pub(in crate::script_init) fn ability_flags(&self, key: StateKey) -> Option<u8> {
        let StateKind::Ability { flags } = self.state(key).filter(|state| state.active)?.kind
        else {
            return None;
        };
        Some(flags)
    }

    pub(in crate::script_init) fn set_ability_flags(&mut self, key: StateKey, flags: u8) {
        self.state_mut(key).expect("active ability").kind = StateKind::Ability { flags };
    }

    pub(in crate::script_init) fn ability_condition(
        &mut self,
        key: StateKey,
        node: &Node,
        name: &str,
    ) -> bool {
        let config = field(&node.fields, name);
        config.is_null()
            || self
                .evaluate(key.instance, node, config, Some(key))
                .is_some_and(|value| truthy(&value))
    }

    pub(in crate::script_init) fn ability_stack_allows(
        &mut self,
        key: StateKey,
        node: &Node,
        name: &str,
    ) -> bool {
        let config = field(&node.fields, name);
        if config.is_null() {
            return true;
        }
        let variable = match script_expr::lvalue(field(&node.fields, "m_out_StackVar")) {
            Ok(Some(variable)) => source_key(key.instance, variable),
            Ok(None) => return true,
            Err(error) => {
                self.report(self.site(key.instance, node), Issue::Expression(error));
                return false;
            }
        };
        match script_expr::stack_priority(config) {
            Ok(priority) => {
                self.watch(variable, key);
                self.machine.allows_stack(variable, priority, Some(key))
            }
            Err(error) => {
                self.report(self.site(key.instance, node), Issue::Expression(error));
                false
            }
        }
    }

    pub(in crate::script_init) fn activate_ability(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(flags) = self.ability_flags(key) else {
            return Ok(());
        };
        let node = self
            .graph(key.instance)
            .state(usize::from(key.raw))
            .expect("ability state");
        if self.machine.owner_dead || self.machine.control_blocked {
            return self.stop_ability(key, node, depth);
        }
        let Some(buttons) = self.ability_buttons(key, node) else {
            return Ok(());
        };
        let active_flag = if ability_is_stacked(node) {
            ABILITY_PUSHED
        } else {
            ABILITY_RUNNING
        };
        if flags & active_flag != 0 {
            self.keep_ability(key, node, flags, buttons, depth)
        } else {
            self.try_start_ability(key, node, flags, buttons, depth)
        }
    }

    pub(in crate::script_init) fn keep_ability(
        &mut self,
        key: StateKey,
        node: &Node,
        mut flags: u8,
        [start, hold, stop]: [u8; 3],
        depth: usize,
    ) -> Result<(), InitFault> {
        if !self.effective_button_held(start) {
            flags &= !ABILITY_HELD;
            self.set_ability_flags(key, flags);
        }
        let keep_held = field(&node.fields, "m_755F9921");
        if hold == 0
            || (!keep_held.is_null()
                && self
                    .evaluate(key.instance, node, keep_held, Some(key))
                    .is_some_and(|value| truthy(&value)))
        {
            if stop != 0 && self.take_ability_press(key, node, stop) {
                if stop == start {
                    self.set_ability_flags(key, flags | ABILITY_HELD);
                }
                return self.stop_ability(key, node, depth);
            }
        } else if !self.effective_button_held(hold) {
            return self.stop_ability(key, node, depth);
        }
        if !self.ability_condition(key, node, "m_F495731F")
            || !self.ability_stack_allows(key, node, "m_343E99EE")
        {
            self.stop_ability(key, node, depth)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn try_start_ability(
        &mut self,
        key: StateKey,
        node: &Node,
        flags: u8,
        [start, hold, _]: [u8; 3],
        depth: usize,
    ) -> Result<(), InitFault> {
        let mut pressed = false;
        if start != 0 {
            pressed = self.ability_press(key, node, start, true);
            if !pressed {
                if !self.effective_button_held(start) {
                    self.set_ability_flags(key, flags & !ABILITY_HELD);
                    return Ok(());
                }
                if flags & ABILITY_HELD != 0 {
                    return Ok(());
                }
            }
        } else if hold != 0 && !self.effective_button_held(hold) {
            return Ok(());
        }
        if self.cooldown_remaining(key) > 0.0
            || !self.ability_condition(key, node, "m_150F0D92")
            || !self.ability_condition(key, node, "m_F495731F")
            || !self.ability_stack_allows(key, node, "m_3C0EB4A6")
        {
            return Ok(());
        }
        if pressed {
            self.take_ability_press(key, node, start);
        }
        self.set_ability_flags(key, flags | if start != 0 { ABILITY_HELD } else { 0 });
        if ability_is_stacked(node) {
            self.push_ability(key, node, depth)
        } else {
            self.start_ability(key, node, depth)
        }
    }

    pub(in crate::script_init) fn push_ability(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let target = match script_expr::lvalue(field(&node.fields, "m_out_StackVar")) {
            Ok(Some(target)) => source_key(id, target),
            Ok(None) => return Ok(()),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        let priority = match script_expr::stack_priority(field(&node.fields, "m_5E561E77")) {
            Ok(priority) => priority,
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        if self.state(key).and_then(|state| state.linked_variable) == Some(target) {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("overlapping ability outputs"),
            );
            return Ok(());
        }
        let state = self.state_mut(key).expect("active ability");
        let StateKind::Ability { flags } = state.kind else {
            unreachable!("active ability")
        };
        state.kind = StateKind::Ability {
            flags: flags | ABILITY_PUSHED,
        };
        state.ability_stack = Some(target);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority,
                output: LinkValue::AbilityStack,
                stack: true,
            },
            depth,
        )
    }

    pub(in crate::script_init) fn start_ability(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let flags = self.ability_flags(key).expect("active ability");
        self.set_ability_flags(key, flags | ABILITY_RUNNING);
        self.follow(id, node, "m_B969AFC9", Some(key), depth)?;
        self.follow(id, node, "m_4E22B5C4", Some(key), depth)?;
        self.ability_vars(id, node, "m_EF3C5C73")
    }

    pub(in crate::script_init) fn stop_ability(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        self.unlink_ability_stack(key, depth)?;
        let Some(flags) = self
            .ability_flags(key)
            .filter(|flags| flags & ABILITY_RUNNING != 0)
        else {
            return Ok(());
        };
        let id = key.instance;
        self.set_ability_flags(key, flags & !ABILITY_RUNNING);
        self.exit_subgraph(id, node, "m_B969AFC9", depth + 1)?;
        self.follow(id, node, "m_6EEEC0AA", Some(key), depth)?;
        self.ability_vars(id, node, "m_19899EAC")
    }

    pub(in crate::script_init) fn unlink_ability_stack(
        &mut self,
        key: StateKey,
        depth: usize,
    ) -> Result<(), InitFault> {
        let Some(flags) = self
            .ability_flags(key)
            .filter(|flags| flags & ABILITY_PUSHED != 0)
        else {
            return Ok(());
        };
        self.set_ability_flags(key, flags & !ABILITY_PUSHED);
        if let Some(target) = self
            .state_mut(key)
            .and_then(|state| state.ability_stack.take())
        {
            self.remove_link(key, target, None, depth + 1)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn cooldown_remaining(&self, key: StateKey) -> f64 {
        self.state(key)
            .and_then(|state| state.ability_cooldown)
            .map_or(0.0, |cooldown| cooldown.remaining(self.machine.now_ms))
    }

    pub(in crate::script_init) fn restart_cooldown(
        &mut self,
        key: StateKey,
        node: &Node,
        depth: usize,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let target = match script_expr::lvalue(field(&node.fields, "m_out_CooldownVar")) {
            Ok(Some(target)) => source_key(id, target),
            Ok(None) => return Ok(()),
            Err(error) => {
                self.report(self.site(id, node), Issue::Expression(error));
                return Ok(());
            }
        };
        let modifier = self.machine.value(VarKey::Entity(3652), None);
        let mut rate = (script_expr::to_float(&modifier) + 1.0) as f32;
        if (self.machine.cooldown_scale - 1.0).abs() > 1e-9 && self.machine.cooldown_scale > 0.0 {
            rate = (f64::from(rate) / self.machine.cooldown_scale.clamp(0.05, 10.0)) as f32;
        }
        let scalar = field(&node.fields, "m_F7CAF887");
        if !scalar.is_null() {
            let Some(value) = self.evaluate(id, node, scalar, None) else {
                return Ok(());
            };
            rate = (f64::from(rate) * script_expr::to_float(&value)) as f32;
        }
        let current = script_expr::to_float(&stored(&self.machine.snapshot, target)).max(0.0);
        let delay = ((current * 1000.0) as f32 / rate).round();
        if !current.is_finite() || !rate.is_finite() || (rate > 0.0 && !delay.is_finite()) {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("ability cooldown"),
            );
            return Ok(());
        }
        // The reference removes its previous slot before relinking at the same priority.
        if let Some(entries) = self.machine.links.get_mut(&target) {
            entries.retain(|entry| entry.state != key);
        }
        let cooldown = AbilityCooldown {
            current,
            rate: f64::from(rate),
            last_ms: self.machine.now_ms,
        };
        let state = self.state_mut(key).expect("active ability");
        state.linked_variable = Some(target);
        state.ability_cooldown = Some(cooldown);
        self.insert_link(
            target,
            VariableLink {
                state: key,
                priority: StackPriority {
                    level: 0,
                    above_equals: true,
                },
                output: LinkValue::Cooldown,
                stack: false,
            },
            depth,
        )?;
        self.machine.queues.retain(
            id,
            |_, event| !matches!(event, Event::Timer(raw, 2) if *raw == key.raw),
        );
        if current > 0.0 && rate > 0.0 {
            self.post_after(id, delay.max(1.0) as u64, Event::Timer(key.raw, 2))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn finish_cooldown(
        &mut self,
        key: StateKey,
    ) -> Result<(), InitFault> {
        let remaining = self.cooldown_remaining(key);
        if let Some(target) = self
            .state_mut(key)
            .and_then(|state| state.linked_variable.take())
        {
            self.unlink_output(key, target, Some(Value::Float(remaining)))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn ability_vars(
        &mut self,
        id: u16,
        node: &Node,
        name: &str,
    ) -> Result<(), InitFault> {
        for item in array_field(&node.fields, name)
            .iter()
            .filter(|item| item.is_object())
        {
            self.store(id, node, item, false)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn update(&mut self) -> Result<(), InitFault> {
        self.spend("component update")?;
        self.retire_stale_actor_children()?;
        // Release scopes that cannot accept input before timers or observations
        // sample their next frame. Physical input remains available to opt-ins.
        if std::mem::take(&mut self.machine.logical_buttons_need_resync) {
            self.resync_logical_buttons(None, 0)?;
        }
        let instances: Vec<_> = self.machine.snapshot.instances.keys().copied().collect();
        for id in instances {
            let mut events = 0;
            loop {
                if !self.machine.snapshot.instances.contains_key(&id) {
                    break;
                }
                // Settle script work before the next input edge at this instant.
                // A press and release can share a timestamp.
                while let Some((event, origin)) =
                    self.machine
                        .queues
                        .pop_due_with_origin(id, self.machine.now_ms, |event| {
                            !matches!(event, Event::Button(_))
                        })
                {
                    if events == MAX_EVENTS_PER_UPDATE {
                        return Err(InitFault::EventBudget { instance: id });
                    }
                    events += 1;
                    self.spend("event dispatch")?;
                    self.origin = origin;
                    self.command_frame = match origin {
                        DispatchOrigin::Command(frame) => Some(frame),
                        _ => None,
                    };
                    let key = |raw| StateKey { instance: id, raw };
                    match event {
                        Event::Entry(position) => {
                            if let Some(node) = self.graph(id).node(position) {
                                self.run_node(id, node, None, 0)?;
                            }
                        }
                        Event::Assign(target, value) => self.write(target, value)?,
                        Event::ExitInstance => self.exit_instance(id)?,
                        Event::Begin(raw) => self.begin(key(raw), 0)?,
                        Event::BeginObserved { state, input } => {
                            self.resume_observed(input, |vm| vm.begin(key(state), 0))?;
                        }
                        Event::Finish(raw) => self.end(key(raw), true, 0)?,
                        Event::Abort(raw) => self.end(key(raw), false, 0)?,
                        Event::Changed(raw) => self.changed(key(raw))?,
                        Event::Reevaluate(raw) => self.reevaluate(key(raw), 0)?,
                        Event::Timer(raw, parameter) => self.timer(key(raw), parameter)?,
                        Event::Button(edge) => self.button_edge(id, edge)?,
                        Event::LogicalButtonBranch {
                            state,
                            stage,
                            sample,
                        } => self.follow_logical_button_branch(
                            StateKey {
                                instance: id,
                                raw: state,
                            },
                            stage,
                            sample,
                            0,
                        )?,
                        Event::AbilityCheck(raw) => self.timer(key(raw), 1)?,
                        Event::CheckTransitions(raw) => {
                            if self.state(key(raw)).is_some_and(|state| state.active) {
                                let node =
                                    self.graph(id).state(usize::from(raw)).expect("chase state");
                                self.try_complete(key(raw), node, 0)?;
                            }
                        }
                        Event::WatchChanged(raw) => self.deliver_watch(key(raw))?,
                        Event::ObserveSample {
                            raw,
                            activation,
                            frame,
                            available,
                        } => {
                            self.deliver_observed_sample(key(raw), activation, frame, available)?;
                        }

                        Event::SpawnResult { key, positions } => {
                            self.finish_spawn_search(key, positions)?
                        }
                        Event::MoveResult { key, outcome } => {
                            self.deliver_move_result(key, outcome)?
                        }
                        Event::Message {
                            position,
                            sender,
                            parameters,
                            health_context,
                            settled_damage,
                            room_directed,
                        } => {
                            if !room_directed || !self.machine.owner_dead {
                                self.deliver(
                                    id,
                                    position,
                                    sender,
                                    parameters,
                                    health_context,
                                    settled_damage,
                                )?;
                            }
                        }
                    }
                    self.command_frame = None;
                    self.origin = DispatchOrigin::Ordinary;
                }
                if !self.machine.snapshot.instances.contains_key(&id) {
                    break;
                }
                let samples_queued = self.tick_states(id)?;
                let deferred = self.run_deferred(id)?;
                if !deferred && !samples_queued {
                    break;
                }
            }
            self.retire_played_instance(id)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn run_deferred(&mut self, id: u16) -> Result<bool, InitFault> {
        let pending = self.machine.deferred.remove(&id).unwrap_or_default();
        if pending.is_empty() {
            return Ok(false);
        }
        // Swap out the batch before delivery. Registrations made by a callback
        // run after another ordinary event/tick phase in the same update.
        for (_, raw) in pending.into_iter().rev() {
            self.spend("deferred state callback")?;
            let key = StateKey { instance: id, raw };
            if self.state(key).is_some_and(|state| state.active) {
                self.timer(key, 1)?;
            }
        }
        Ok(true)
    }

    pub(in crate::script_init) fn graph(&self, id: u16) -> &'graph Graph {
        let graph = self.machine.snapshot.instances[&id].graph;
        self.graphs.graph(graph).expect("registered graph")
    }

    pub(in crate::script_init) fn site(&self, instance: u16, node: &Node) -> Site {
        Site {
            entity: self.machine.snapshot.entity,
            instance,
            graph: self.machine.snapshot.instances[&instance].graph,
            node: Some(node.pos),
        }
    }

    #[inline]
    #[cfg_attr(
        not(feature = "node-observations"),
        allow(
            clippy::needless_pass_by_ref_mut,
            reason = "Inspector builds record the target"
        )
    )]
    fn observed_server_path(&mut self, id: u16, node: &Node) -> bool {
        let runs_on_server = node.runs_on_server();
        #[cfg(feature = "node-observations")]
        {
            let graph = self.graph(id).index;
            self.machine
                .snapshot
                .node_observations
                .record(graph, node.pos, !runs_on_server);
        }
        #[cfg(not(feature = "node-observations"))]
        let _ = id;
        runs_on_server
    }

    pub(in crate::script_init) fn report(&mut self, site: Site, issue: Issue) {
        self.report_diagnostic(site, issue, false);
    }

    fn report_diagnostic(&mut self, site: Site, issue: Issue, registration_rejected: bool) {
        let server_only = self
            .machine
            .snapshot
            .instances
            .get(&site.instance)
            .is_some_and(Instance::is_server_only)
            || site
                .node
                .and_then(|position| self.graphs.graph(site.graph)?.node(position))
                .is_some_and(|node| {
                    node.target_child_binding().is_some()
                        || node.declares_stack_claim()
                        || node.declares_room_cue_action()
                });
        self.record_diagnostic(Diagnostic {
            site,
            issue,
            registration_rejected,
            server_only,
        });
    }

    fn record_diagnostic(&mut self, diagnostic: Diagnostic) {
        if !self
            .machine
            .snapshot
            .diagnostics
            .iter()
            .any(|existing| existing.site == diagnostic.site && existing.issue == diagnostic.issue)
        {
            self.machine.snapshot_dirty = true;
            self.machine.snapshot.diagnostics.push(diagnostic);
        }
    }

    pub(in crate::script_init) fn post(&mut self, id: u16, event: Event) -> Result<(), InitFault> {
        self.post_after(id, 0, event)
    }

    pub(in crate::script_init) fn post_after(
        &mut self,
        id: u16,
        delay_ms: u64,
        event: Event,
    ) -> Result<(), InitFault> {
        // Carry the input view through immediate graph work, including child
        // startup. Later timers sample their own current frame.
        let origin = if matches!(self.origin, DispatchOrigin::BodyDeath(_))
            && matches!(
                &event,
                Event::Begin(_) | Event::Finish(_) | Event::Timer(..)
            ) {
            self.origin
        } else if delay_ms == 0 {
            self.command_frame
                .map_or(DispatchOrigin::Ordinary, DispatchOrigin::Command)
        } else {
            DispatchOrigin::Ordinary
        };
        self.machine.queues.push_with_origin(
            id,
            self.machine.now_ms.saturating_add(delay_ms),
            event,
            origin,
        )?;
        Ok(())
    }

    pub(in crate::script_init) fn deliver(
        &mut self,
        id: u16,
        position: u16,
        sender: Option<u32>,
        parameters: Arc<Variables>,
        health_context: HostHealthContext,
        settled_damage: bool,
    ) -> Result<(), InitFault> {
        let Some(node) = self.graph(id).node(position) else {
            return Ok(());
        };
        if !self.observed_server_path(id, node) {
            return Ok(());
        }
        self.parameters = Some(parameters);
        self.health_context = health_context;
        self.settled_damage_receiver = settled_damage.then_some(id);
        let result = self.deliver_entry(id, node, sender);
        self.settled_damage_receiver = None;
        self.health_context = HostHealthContext::default();
        self.parameters = None;
        result
    }

    pub(in crate::script_init) fn deliver_entry(
        &mut self,
        id: u16,
        node: &Node,
        sender: Option<u32>,
    ) -> Result<(), InitFault> {
        let path = if node.is_state() {
            let Some(raw) = node.state else { return Ok(()) };
            let key = StateKey { instance: id, raw };
            let Some(state) = self.state(key).filter(|state| state.active) else {
                return Ok(());
            };
            if state.kind != StateKind::MessageIdle {
                self.report(
                    self.site(id, node),
                    Issue::UnsupportedField("state game message delivery"),
                );
                return Ok(());
            }
            ("m_452862E6", Some(key))
        } else {
            ("m_outPlug", None)
        };
        let site = self.site(id, node);
        let mut writes = Vec::new();
        for field in array_field(&node.fields, "m_params") {
            if !field.is_object() {
                continue;
            }
            let target = match script_expr::lvalue(field.get("m_out_Var").unwrap_or(&Json::Null)) {
                Ok(Some(target)) => source_key(id, target),
                Ok(None) => continue,
                Err(error) => {
                    self.report(site, Issue::Expression(error));
                    return Ok(());
                }
            };
            let key = message_parameter_id(field);
            let value = self
                .parameters
                .as_deref()
                .and_then(|params| params.get(&key))
                .cloned()
                .unwrap_or(Value::Null);
            writes.push((target, value));
        }
        if let Some(sender) = sender.filter(|_| path.1.is_none()) {
            for output in ["m_B60ABDA4", "m_FF703F20"] {
                match script_expr::dynamic(field(&node.fields, output)) {
                    Ok(Some(target)) => {
                        writes.push((source_key(id, target), Value::Entity(sender)));
                    }
                    Ok(None) => (),
                    Err(error) => {
                        self.report(site, Issue::Expression(error));
                        return Ok(());
                    }
                }
            }
        }
        // A malformed later binding must leave earlier outputs and the receiver's
        // active branch intact. Valid deliveries still exit before applying writes.
        if path.1.is_some() {
            self.exit_subgraph(id, node, "m_33DA5163", 1)?;
        }
        for (target, value) in writes {
            self.write(target, value)?;
        }
        self.follow(id, node, path.0, path.1, 0)?;
        Ok(())
    }

    pub(in crate::script_init) fn play_script(
        &mut self,
        id: u16,
        node: &Node,
        depth: usize,
    ) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let overrides_config = field(&node.fields, "m_69F4B52C");
        let mut resource = None;
        if !overrides_config.is_null() {
            if !script_expr::native_shape(overrides_config, "STU_9DB46B98", Some("m_6525E98B")) {
                self.report(site, Issue::UnsupportedField("PlayScript override config"));
                return Ok(false);
            }
            match self.evaluate(id, node, overrides_config, None) {
                Some(Value::Map(rows)) => {
                    let mut graph = None;
                    let mut overrides = Vec::new();
                    for (key, value) in rows {
                        let Value::Integer(key) = key else {
                            return Ok(false);
                        };
                        if key == 0x4065 {
                            if let Value::Asset(guid) = value {
                                graph = Some(guid);
                            }
                        } else if key != 0 {
                            overrides.push((0x0d80_0000_0000_0000 | key as u64, value));
                        }
                    }
                    resource = graph.map(|graph| (graph, overrides));
                }
                Some(Value::Integer(0)) => (),
                _ => return Ok(false),
            }
        }
        if resource.is_none() {
            let script = field(&node.fields, "m_script");
            if !script.is_null()
                && (script.get("$").and_then(Json::as_str) != Some("STU_105E1BCC")
                    || !script_expr::native_fields(script, &["m_graph", "m_resourceKey"])
                    || !field(script, "m_resourceKey").is_null())
            {
                self.report(site, Issue::UnsupportedField("PlayScript resource"));
                return Ok(false);
            }
            resource = asset_guid(field(script, "m_graph")).map(|graph| (graph, Vec::new()));
        }
        let Some((guid, overrides)) = resource else {
            self.follow(id, node, "m_outPlug", None, depth)?;
            return Ok(true);
        };
        if guid >> 48 != 0x0580
            || guid & 0x0000_ffff_ffff_ffff == 0
            || guid & 0x0000_ffff_ffff_ffff > u64::from(u32::MAX)
        {
            self.report(site, Issue::UnsupportedField("PlayScript graph identity"));
            return Ok(false);
        }
        let graph = (guid & 0x0000_ffff_ffff_ffff) as u32;
        let policy = match field(&node.fields, "m_AA4DE426") {
            Json::Null => 0,
            value => match value
                .as_u64()
                .and_then(|value| u8::try_from(value).ok())
                .filter(|value| *value < 4)
            {
                Some(value) => value,
                None => {
                    self.report(site, Issue::UnsupportedField("PlayScript policy"));
                    return Ok(false);
                }
            },
        };
        let auto_retire = match field(&node.fields, "m_7ABA409C") {
            Json::Null => false,
            value => match value.as_u64().and_then(|value| u8::try_from(value).ok()) {
                Some(value) => value == 0,
                None => {
                    self.report(site, Issue::UnsupportedField("PlayScript wire flag"));
                    return Ok(false);
                }
            },
        };
        if !field(&node.fields, "m_6D7701B7").is_null()
            || !matches!(field(&node.fields, "m_params"), Json::Null | Json::Array(_))
        {
            self.report(
                site,
                Issue::UnsupportedField("PlayScript resource or policy"),
            );
            return Ok(false);
        }
        let Some(target) = self.evaluate(id, node, field(&node.fields, "m_targets"), None) else {
            return Ok(false);
        };
        match target {
            Value::Null => (),
            Value::Entity(target) if !script_expr::valid_entity(target) => (),
            Value::Entity(target) => {
                let Some(params) = self.play_parameters(id, node)? else {
                    return Ok(false);
                };
                let source = match self
                    .machine
                    .value(VarKey::Instance(id, SCRIPT_SOURCE), None)
                {
                    Value::Entity(source) if script_expr::valid_entity(source) => source,
                    _ => site.entity,
                };
                let request = PlayScript {
                    policy,
                    overrides,
                    graph,
                    source,
                    parameters: params,
                    auto_retire,
                };
                if target == site.entity {
                    self.start_loaded_script(site, &request)?;
                } else if !self.world.play_script(site, target, request) {
                    self.report(site, Issue::UnsupportedField("PlayScript target component"));
                    return Ok(false);
                }
            }
            Value::Array(targets) if self.world.admits_generic_player_runtime(site) => {
                let Some(params) = self.play_parameters(id, node)? else {
                    return Ok(false);
                };
                let source = match self
                    .machine
                    .value(VarKey::Instance(id, SCRIPT_SOURCE), None)
                {
                    Value::Entity(source) if script_expr::valid_entity(source) => source,
                    _ => site.entity,
                };
                for item in targets {
                    if let Value::Entity(target) = item {
                        if !script_expr::valid_entity(target) {
                            continue;
                        }
                        let request = PlayScript {
                            policy,
                            overrides: overrides.clone(),
                            graph,
                            source,
                            parameters: params.clone(),
                            auto_retire,
                        };
                        if target == site.entity {
                            self.start_loaded_script(site, &request)?;
                        } else {
                            let _ = self.world.play_script(site, target, request);
                        }
                    }
                }
            }
            _ => {
                self.report(site, Issue::UnsupportedField("PlayScript target component"));
                return Ok(false);
            }
        }
        if self.machine.snapshot.instances.contains_key(&id) {
            self.follow(id, node, "m_outPlug", None, depth)?;
        }
        Ok(true)
    }

    pub(in crate::script_init) fn play_parameters(
        &mut self,
        id: u16,
        node: &Node,
    ) -> Result<Option<Vec<(u64, Value)>>, InitFault> {
        let site = self.site(id, node);
        let mut params = Vec::new();
        for row in array_field(&node.fields, "m_params") {
            self.spend("PlayScript parameter")?;
            let identifier = field(row, "m_B5051BCE");
            if row.is_null() || (row.is_object() && identifier.is_null()) {
                continue;
            }
            let Some(parameter) = asset_guid(identifier) else {
                self.report(
                    site,
                    Issue::UnsupportedField("PlayScript parameter identifier"),
                );
                return Ok(None);
            };
            let Some(value) = self.evaluate(id, node, field(row, "m_value"), None) else {
                return Ok(None);
            };
            let mut values = Vec::new();
            let mut pending = match value {
                Value::Array(items) => items,
                Value::Null => Vec::new(),
                item => vec![item],
            };
            pending.reverse();
            while let Some(item) = pending.pop() {
                self.spend("PlayScript parameter item")?;
                match item {
                    Value::Array(items) => pending.extend(items.into_iter().rev()),
                    _ => values.push(item),
                }
            }
            params.push((parameter, Value::Array(values)));
        }
        Ok(Some(params))
    }

    pub(in crate::script_init) fn start_loaded_script(
        &mut self,
        site: Site,
        request: &PlayScript,
    ) -> Result<(), InitFault> {
        let PlayScript {
            graph,
            source,
            parameters,
            auto_retire,
            policy,
            overrides,
        } = request;
        if *policy > 3 {
            self.report(site, Issue::UnsupportedField("PlayScript policy"));
            return Ok(());
        }
        // Instance IDs belong to their component. Only a local caller's
        // instance can determine the scope inside this component.
        let scope = if site.entity == self.machine.snapshot.entity {
            self.instance_scope(site.instance)
        } else {
            InstanceScope::Projected
        };
        let matches: Vec<_> = self
            .machine
            .snapshot
            .instances
            .iter()
            .filter_map(|(&id, instance)| {
                (instance.graph == *graph && instance.scope == scope).then_some(id)
            })
            .collect();
        if *policy == 0 && !matches.is_empty() {
            return Ok(());
        }
        if *policy == 3 {
            for id in &matches {
                self.destroy(*id)?;
            }
        }
        if self.machine.snapshot.instances.len() > MAX_PLAY_INSTANCES {
            return Ok(());
        }
        if self.graphs.graph(*graph).is_none() {
            self.report(site, Issue::MissingGraph(*graph));
            return Ok(());
        }
        let reused = if *policy == 2 {
            matches.first().copied()
        } else {
            None
        };
        let id = if let Some(id) = reused {
            id
        } else {
            let id = self.machine.next_instance_id(scope.is_isolated())?;
            self.create(
                id,
                *graph,
                None,
                Variables::from([(SCRIPT_SOURCE, Value::Entity(*source))]),
                Startup::Played {
                    auto_retire: *auto_retire,
                },
                scope,
            )?;
            id
        };
        self.queue_play_schema(id, *graph, overrides)?;
        if reused.is_none() {
            let definition = self.graphs.graph(*graph).expect("created played graph");
            for position in definition.entries.iter().flatten() {
                if definition
                    .node(*position)
                    .is_some_and(|node| node.runs_on_server() && is_entry(node))
                {
                    self.post(id, Event::Entry(*position))?;
                }
            }
        }
        self.queue_script_start(id, *graph, site.entity, parameters)
    }

    pub(in crate::script_init) fn queue_play_schema(
        &mut self,
        id: u16,
        graph: u32,
        overrides: &[(u64, Value)],
    ) -> Result<(), InitFault> {
        let definition = self.graphs.graph(graph).expect("played graph");
        let site = Site {
            entity: self.machine.snapshot.entity,
            instance: id,
            graph,
            node: None,
        };
        let schema = field(&definition.fields, "m_publicSchema");
        if !schema.is_null() {
            if !matches!(
                field(schema, "$").as_str(),
                Some("STUStatescriptSchema" | "STU_77990ED7")
            ) || (!field(schema, "m_89EAAC40").is_null()
                && asset_guid(field(schema, "m_89EAAC40"))
                    != Some(0x0580_0000_0000_0000 | u64::from(graph)))
                || !matches!(field(schema, "m_entries"), Json::Null | Json::Array(_))
            {
                self.report(site, Issue::UnsupportedField("played graph schema"));
                return Ok(());
            }
            for row in array_field(schema, "m_entries") {
                self.spend("played schema evaluation")?;
                if !row.is_object() {
                    self.report(site, Issue::UnsupportedField("played graph schema row"));
                    continue;
                }
                let config = field(row, "m_value");
                let identifier = field(row, "m_0D09D2D9");
                if config.is_null() || identifier.is_null() {
                    continue;
                }
                let Some(key) = asset_guid(identifier) else {
                    self.report(
                        site,
                        Issue::UnsupportedField("played graph schema identifier"),
                    );
                    continue;
                };
                if key != 0 {
                    if let Some(value) = self.evaluate_at(site, config, None) {
                        self.post(
                            id,
                            Event::Assign(VarKey::Instance(id, u32::from(key as u16)), value),
                        )?;
                    }
                }
            }
        }
        for (key, value) in overrides {
            self.spend("played override assignment")?;
            self.post(
                id,
                Event::Assign(VarKey::Instance(id, u32::from(*key as u16)), value.clone()),
            )?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn queue_script_start(
        &mut self,
        id: u16,
        graph: u32,
        source: u32,
        parameters: &[(u64, Value)],
    ) -> Result<(), InitFault> {
        let definition = self.graphs.graph(graph).expect("created played graph");
        for position in definition.entries.iter().flatten() {
            let Some(node) = definition.node(*position).filter(|node| {
                node.runs_on_server()
                    && node.class == "STU_BB4AC733"
                    && !configured(node, "m_5AEA0A51")
            }) else {
                continue;
            };
            if self
                .machine
                .disabled_start_entries
                .contains(&(id, *position))
            {
                continue;
            }
            if configured(node, "m_FDBDCB70") {
                self.machine.disabled_start_entries.insert((id, *position));
            }
            self.spend("PlayScript start entry")?;
            let rows = array_field(&node.fields, "m_params");
            for (index, (parameter, value)) in parameters.iter().enumerate() {
                for (row_index, row) in rows.iter().enumerate() {
                    self.spend("PlayScript start parameter binding")?;
                    let matches = if *parameter == 0 {
                        row_index == index
                    } else {
                        asset_guid(field(row, "m_B5051BCE")) == Some(*parameter)
                    };
                    let output = field(row, "m_out_Var");
                    if matches && !output.is_null() {
                        self.queue_play_binding(id, node, output, value.clone())?;
                        break;
                    }
                }
            }
            if script_expr::valid_entity(source) {
                self.queue_play_binding(
                    id,
                    node,
                    field(&node.fields, "m_B60ABDA4"),
                    Value::Entity(source),
                )?;
            }
            self.post(id, Event::Entry(*position))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn scoped_children(&self, key: StateKey) -> Vec<u16> {
        self.machine
            .snapshot
            .instances
            .iter()
            .filter_map(|(&id, child)| {
                (child.parent == Some((key.instance, key.raw))).then_some(id)
            })
            .collect()
    }

    pub(in crate::script_init) fn refresh_phase_scripts(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        let Ok(Some(target)) = script_expr::dynamic(field(&node.fields, "m_3DC6FAAA")) else {
            self.report(
                site,
                Issue::UnsupportedField("phase script list destination"),
            );
            return Ok(());
        };
        if target.scope != Scope::Instance {
            self.report(
                site,
                Issue::UnsupportedField("phase script list destination"),
            );
            return Ok(());
        }
        let Some(value) = self.evaluate(id, node, field(&node.fields, "m_814F8EC3"), Some(key))
        else {
            return Ok(());
        };
        let mut remaining = self.scoped_children(key);
        let mut added = Vec::new();
        if let Value::Array(items) = value {
            for value in items.into_iter().filter(|item| *item != Value::Null) {
                self.spend("phase script list item")?;
                let mut reused = None;
                for (index, &child) in remaining.iter().enumerate() {
                    self.spend("phase script list match")?;
                    if same(
                        &value,
                        &self.machine.value(VarKey::Instance(child, target.id), None),
                    ) {
                        reused = Some(index);
                        break;
                    }
                }
                if let Some(index) = reused {
                    remaining.remove(index);
                } else {
                    added.push(value);
                }
            }
        }
        for child in remaining {
            self.destroy(child)?;
        }
        let scope = self.instance_scope(id);
        for value in added {
            let child = self.machine.next_instance_id(scope.is_isolated())?;
            let values = Variables::from([
                (target.id, value),
                (SCRIPT_SOURCE, Value::Entity(site.entity)),
            ]);
            if self.create(
                child,
                site.graph,
                Some((id, key.raw)),
                values,
                Startup::Passive,
                scope,
            )? {
                self.follow(child, node, "m_C41A9BE2", None, 0)?;
            }
        }
        Ok(())
    }

    pub(in crate::script_init) fn begin_phase_script(
        &mut self,
        key: StateKey,
        node: &Node,
    ) -> Result<(), InitFault> {
        let id = key.instance;
        let site = self.site(id, node);
        if self.machine.snapshot.instances[&id].parent.is_none()
            || !is_self_target(node, "m_targets")
            || !script_expr::native_shape(
                field(&node.fields, "m_69F4B52C"),
                "STU_9DB46B98",
                Some("m_6525E98B"),
            )
            || node
                .field("m_69F4B52C.m_6525E98B")
                .is_some_and(|value| !value.is_null())
        {
            self.report(site, Issue::UnsupportedField("phase script scope"));
            return Ok(());
        }
        let Some(Value::Asset(guid)) =
            self.evaluate(id, node, field(&node.fields, "m_script"), None)
        else {
            return Ok(());
        };
        let Some(graph) = (guid >> 48 == 0x0580)
            .then(|| u32::try_from(guid & 0x0000_ffff_ffff_ffff).ok())
            .flatten()
        else {
            self.report(site, Issue::UnsupportedField("phase script graph"));
            return Ok(());
        };
        let Some(parameters) = self.play_parameters(id, node)? else {
            return Ok(());
        };
        let scope = self.instance_scope(id);
        let child = self.machine.next_instance_id(scope.is_isolated())?;
        let values = Variables::from([(SCRIPT_SOURCE, Value::Entity(site.entity))]);
        if self.create(
            child,
            graph,
            Some((id, key.raw)),
            values,
            Startup::Played { auto_retire: false },
            scope,
        )? {
            self.queue_script_start(child, graph, site.entity, &parameters)?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn queue_play_binding(
        &mut self,
        id: u16,
        node: &Node,
        output: &Json,
        value: Value,
    ) -> Result<(), InitFault> {
        match script_expr::dynamic(output) {
            Ok(Some(target)) if target.id != 0 => {
                self.post(id, Event::Assign(source_key(id, target), value))?;
            }
            Ok(_) if output.is_null() => (),
            Ok(Some(_)) => (),
            Ok(None) => self.report(
                self.site(id, node),
                Issue::UnsupportedField("PlayScript start binding"),
            ),
            Err(error) => self.report(self.site(id, node), Issue::Expression(error)),
        }
        Ok(())
    }

    pub(in crate::script_init) fn retire_played_instance(
        &mut self,
        id: u16,
    ) -> Result<(), InitFault> {
        let Some(instance) = self.machine.snapshot.instances.get(&id) else {
            return Ok(());
        };
        if instance
            .retire_after_frame
            .is_none_or(|frame| self.machine.frame <= frame)
            || instance.states.values().any(|state| state.active)
            || self.machine.queues.iter(id).next().is_some()
        {
            return Ok(());
        }
        let graph = self.graph(id);
        for node in graph
            .entries
            .iter()
            .flatten()
            .filter_map(|position| graph.node(*position))
        {
            self.spend("PlayScript retirement entry")?;
            if !node.is_message_entry()
                && node.host_event_kind().is_none()
                && !matches!(node.class.as_str(), "STU_BB4AC733" | "STU_8A7E9DA8")
            {
                return Ok(());
            }
        }
        self.destroy(id)
    }

    fn expired_health_event_body(&self, wire: u32) -> bool {
        if wire == self.machine.snapshot.entity {
            return false;
        }
        [
            self.health_context.source_body,
            self.health_context.target_body,
        ]
        .into_iter()
        .flatten()
        .any(|body| {
            body.wire == wire && self.world.room_body_generation(wire) != Some(body.generation)
        })
    }

    fn forwarded_health_context(&self, parameters: &Variables) -> HostHealthContext {
        let context = &self.health_context;
        let carries_entity = |wire| {
            parameters
                .values()
                .any(|value| matches!(value, Value::Entity(entity) if *entity == wire))
        };
        HostHealthContext {
            source_body: context.source_body.filter(|body| carries_entity(body.wire)),
            target_body: context.target_body.filter(|body| carries_entity(body.wire)),
            target_facts: context
                .target_facts
                .as_ref()
                .filter(|facts| carries_entity(facts.target))
                .cloned(),
            named_pool_losses: None,
            critical_hit: None,
            damage_frame: None,
        }
    }

    pub(in crate::script_init) fn send_game_message(
        &mut self,
        id: u16,
        node: &Node,
    ) -> Result<bool, InitFault> {
        let Some(message) = node.game_message_id().filter(|&id| id != 0) else {
            return Ok(true);
        };
        let site = self.site(id, node);
        let target = node
            .fields
            .get("m_targets")
            .or_else(|| node.fields.get("m_target"))
            .unwrap_or(&Json::Null);
        let value = if target.is_null() {
            Value::Entity(site.entity)
        } else {
            let Some(value) = self.evaluate(id, node, target, None) else {
                return Ok(false);
            };
            value
        };
        let directed_parameter = node.directed_numeric_parameter();
        if directed_parameter.is_some() && !matches!(value, Value::Entity(_) | Value::Null) {
            self.report(
                site,
                Issue::UnsupportedField("directed numeric message target"),
            );
            return Ok(false);
        }
        let entities: Vec<_> = match value {
            Value::Entity(entity) if entity != 0 => vec![entity],
            Value::Array(items) => items
                .into_iter()
                .filter_map(|value| {
                    let Value::Entity(entity) = value else {
                        return None;
                    };
                    (entity != 0 && entity & 0x1fff_ffff != 0x1fff_ffff).then_some(entity)
                })
                .collect(),
            _ => Vec::new(),
        };
        // The reference evaluates the parameters before the recipients, but
        // a message without recipients delivers none of them. Skipping them
        // also skips any random draw they would make.
        if entities.is_empty() {
            return Ok(true);
        }
        let Some(parameters) = self.game_message_parameters(id, node)? else {
            return Ok(false);
        };
        for entity in entities {
            self.spend("game message routing")?;
            if entity == site.entity {
                // A local relay can carry the authenticated identity of each
                // event participant named by a typed parameter.
                let health_context = self.forwarded_health_context(&parameters);
                self.machine.queue_local_game_message(
                    self.graphs,
                    message,
                    parameters.clone(),
                    health_context,
                )?;
            } else {
                let delivered = if let Some(slot) = directed_parameter {
                    script_expr::valid_entity(entity)
                        && directed_numeric_payload(slot, &parameters)
                        && (self.expired_health_event_body(entity)
                            || self.world.queue_room_message(
                                site,
                                entity,
                                RoomMessage {
                                    id: message,
                                    parameters: parameters.clone(),
                                },
                            ))
                } else {
                    self.world.send_message(site, entity, message, &parameters)
                };
                if !delivered {
                    self.report(
                        site,
                        Issue::UnsupportedField("external game message delivery"),
                    );
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    pub(in crate::script_init) fn send_room_message(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
    ) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let Some(target) = self.evaluate(id, node, field(&node.fields, "m_target"), caller) else {
            return Ok(false);
        };
        let Value::Entity(target) = target else {
            self.report(
                site,
                Issue::Expression(EvalError::MalformedConfig(
                    "room message target must be a bound entity",
                )),
            );
            return Ok(false);
        };
        if !script_expr::valid_entity(target) || target == site.entity {
            self.report(
                site,
                Issue::Expression(EvalError::MalformedConfig(
                    "room message target must be a different bound entity",
                )),
            );
            return Ok(false);
        }
        let Some(message) = node.game_message_id() else {
            return Ok(false);
        };
        let Some(parameters) = self.message_parameters(id, node, caller)? else {
            return Ok(false);
        };
        let message = RoomMessage {
            id: message,
            parameters,
        };
        if !message.is_valid() {
            self.report(
                site,
                Issue::Expression(EvalError::MalformedConfig(
                    "room message parameters must be bounded scalar or finite vector values",
                )),
            );
            return Ok(false);
        }
        self.spend("room message admission")?;
        if !self.expired_health_event_body(target)
            && !self.world.queue_room_message(site, target, message)
        {
            self.report(site, Issue::UnsupportedField("room message request"));
            return Ok(false);
        }
        Ok(true)
    }

    fn capture_frame_action(&mut self, id: u16, node: &Node) -> bool {
        use super::{FrameAction, FrameCallout};

        const HISTORY: usize = 32;
        let Some(remote) = usize::try_from(node.remote).ok() else {
            return false;
        };
        if !node.is_action()
            || !node.is_networked()
            || self.machine.snapshot.instances[&id].is_server_only()
            || self
                .graph(id)
                .remote_node(remote)
                .is_none_or(|mapped| mapped.pos != node.pos)
        {
            return false;
        }
        let Some(variables) = node.frame_action_variables() else {
            return false;
        };
        let values = variables
            .into_iter()
            .map(|variable| {
                let key = source_key(id, variable);
                let owner = match key {
                    VarKey::Entity(_) => 0,
                    VarKey::Instance(instance, _) => instance,
                };
                ((owner, variable.id), self.machine.value(key, None))
            })
            .collect();
        self.machine.snapshot_dirty = true;
        let history = &mut self.machine.snapshot.frame_actions;
        let previous = history.back().map_or(0, |action| action.callout.serial);
        let Some(serial) = previous.checked_add(1) else {
            return false;
        };
        if history.len() == HISTORY {
            history.pop_front();
        }
        history.push_back(FrameAction {
            callout: FrameCallout {
                serial,
                instance: id,
                position: node.pos,
            },
            values,
        });
        true
    }

    fn play_actor_criterion(&mut self, id: u16, node: &Node) {
        let site = self.site(id, node);
        let from_death = match self.origin {
            DispatchOrigin::BodyDeath(generation) if generation == self.machine.life_generation => {
                true
            }
            DispatchOrigin::BodyDeath(_) => return,
            _ if self.machine.owner_dead => return,
            _ => false,
        };
        if !from_death && !self.world.actor_voice_available(site) {
            return;
        }
        // Criteria may belong to other services. An unavailable voice operand
        // leaves the ordinary continuation intact.
        let Ok(Value::Asset(criterion)) = self.evaluate_with_context(
            site,
            field(&node.fields, "m_identifier"),
            None,
            Value::Null,
            false,
        ) else {
            return;
        };
        let targets_field = field(&node.fields, "m_targets");
        let speaker = if script_expr::assignment_group_source(targets_field).is_some() {
            self.assignment_group(id, node, targets_field)
                .ok()
                .flatten()
        } else {
            self.evaluate_with_context(site, targets_field, None, Value::Null, false)
                .ok()
        };
        let speakers = match speaker {
            Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => vec![entity],
            Some(Value::Array(items)) if items.len() <= RoomCue::MAX_TARGETS => {
                let mut seen = std::collections::BTreeSet::new();
                items
                    .into_iter()
                    .filter_map(|item| match item {
                        Value::Entity(entity)
                            if script_expr::valid_entity(entity) && seen.insert(entity) =>
                        {
                            Some(entity)
                        }
                        _ => None,
                    })
                    .collect()
            }
            _ => return,
        };
        let subject_field = field(&node.fields, "m_8C8C5285");
        let subject = if subject_field.is_null() {
            None
        } else {
            match self.evaluate_with_context(site, subject_field, None, Value::Null, false) {
                Ok(Value::Entity(wire)) if script_expr::valid_entity(wire) => Some(wire),
                _ => None,
            }
        };
        let audience_field = field(&node.fields, "m_297E0B63");
        let explicit_audience = if audience_field.is_null() {
            None
        } else {
            let evaluated = if script_expr::assignment_group_source(audience_field).is_some() {
                self.assignment_group(id, node, audience_field)
                    .ok()
                    .flatten()
            } else {
                self.evaluate_with_context(site, audience_field, None, Value::Null, false)
                    .ok()
            };
            match evaluated {
                Some(Value::Entity(entity)) if script_expr::valid_entity(entity) => {
                    Some(vec![entity])
                }
                Some(Value::Array(items)) => {
                    let list: Vec<u32> = items
                        .into_iter()
                        .filter_map(|item| match item {
                            Value::Entity(entity) if script_expr::valid_entity(entity) => {
                                Some(entity)
                            }
                            _ => None,
                        })
                        .collect();
                    Some(list)
                }
                _ => None,
            }
        };
        for speaker in speakers {
            let mut choose =
                |count| ((u128::from(self.machine.random_u64()) * count as u128) >> 64) as usize;
            if from_death && self.machine.owner_dead {
                if speaker == site.entity {
                    self.world.queue_owner_death_voice(
                        site,
                        criterion,
                        subject,
                        explicit_audience.as_deref(),
                        &mut choose,
                    );
                }
            } else {
                self.world.queue_actor_voice_with_audience(
                    site,
                    criterion,
                    speaker,
                    subject,
                    explicit_audience.as_deref(),
                    &mut choose,
                );
            }
        }
    }

    fn send_room_cue(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
    ) -> Result<bool, InitFault> {
        let site = self.site(id, node);
        let Some(audience) = self.evaluate(id, node, field(&node.fields, "audience"), caller)
        else {
            return Ok(false);
        };
        let Value::Array(audience) = audience else {
            self.report(
                site,
                Issue::Expression(EvalError::MalformedConfig(
                    "expected a room cue audience array",
                )),
            );
            return Ok(false);
        };
        let mut seen = HashSet::new();
        let mut targets = Vec::new();
        for listener in audience {
            self.spend("room cue listener")?;
            let entity = match listener {
                Value::Entity(entity) if script_expr::valid_entity(entity) => entity,
                _ => {
                    self.report(
                        site,
                        Issue::Expression(EvalError::MalformedConfig("invalid room cue listener")),
                    );
                    return Ok(false);
                }
            };
            if !self.expired_health_event_body(entity) && seen.insert(entity) {
                if targets.len() == RoomCue::MAX_TARGETS {
                    self.report(site, Issue::UnsupportedField("room cue audience limit"));
                    return Ok(false);
                }
                targets.push(entity);
            }
        }
        let Some(cue) = node.room_cue() else {
            return Ok(false);
        };
        if targets.is_empty() {
            return Ok(true);
        }
        self.spend("room cue admission")?;
        if !self.world.queue_room_cue(site, &targets, cue) {
            self.report(site, Issue::UnsupportedField("room cue request"));
            return Ok(false);
        }
        Ok(true)
    }

    fn request_radial_health(&mut self, id: u16, node: &Node, caller: Option<StateKey>) -> bool {
        let site = self.site(id, node);
        let result = self.with_instance_context(site, caller, |context| {
            script_expr::radial_health_changes(
                field(&node.fields, "area"),
                field(&node.fields, "delta"),
                node.field("edge_scale"),
                context,
            )
        });
        let changes = match result {
            Ok(changes) => changes,
            Err(error) => {
                self.report(site, Issue::Expression(error));
                return false;
            }
        };
        if changes.is_empty() || self.world.queue_health_changes(site, &changes) {
            true
        } else {
            self.report(site, Issue::UnsupportedField("radial health request"));
            false
        }
    }

    pub(in crate::script_init) fn game_message_parameters(
        &mut self,
        id: u16,
        node: &Node,
    ) -> Result<Option<Variables>, InitFault> {
        self.message_parameters(id, node, None)
    }

    fn message_parameters(
        &mut self,
        id: u16,
        node: &Node,
        caller: Option<StateKey>,
    ) -> Result<Option<Variables>, InitFault> {
        if node
            .game_message_id()
            .is_some_and(|topic| self.graphs.is_projected_message_topic(topic))
        {
            self.report(self.site(id, node), Issue::UnsupportedMessageRegistration);
            return Ok(None);
        }
        let mut parameters = Variables::new();
        for parameter in array_field(&node.fields, "m_params")
            .iter()
            .filter(|value| value.is_object())
        {
            self.spend("game message parameter")?;
            let Some(value) = self.evaluate(id, node, field(parameter, "m_value"), caller) else {
                return Ok(None);
            };
            parameters.insert(message_parameter_id(parameter), value);
        }
        Ok(Some(parameters))
    }

    pub(in crate::script_init) fn state(&self, key: StateKey) -> Option<&State> {
        self.machine
            .snapshot
            .instances
            .get(&key.instance)?
            .states
            .get(&key.raw)
    }

    pub(in crate::script_init) fn state_mut(&mut self, key: StateKey) -> Option<&mut State> {
        let state = self
            .machine
            .snapshot
            .instances
            .get_mut(&key.instance)?
            .states
            .get_mut(&key.raw)?;
        self.machine.snapshot_dirty = true;
        Some(state)
    }

    pub(in crate::script_init) fn evaluate(
        &mut self,
        id: u16,
        node: &Node,
        config: &Json,
        watcher: Option<StateKey>,
    ) -> Option<Value> {
        let site = self.site(id, node);
        self.evaluate_at(site, config, watcher)
    }

    pub(in crate::script_init) fn evaluate_at(
        &mut self,
        site: Site,
        config: &Json,
        watcher: Option<StateKey>,
    ) -> Option<Value> {
        self.evaluate_at_with_initial(site, config, watcher, Value::Null)
    }

    pub(in crate::script_init) fn evaluate_at_with_initial(
        &mut self,
        site: Site,
        config: &Json,
        watcher: Option<StateKey>,
        initial: Value,
    ) -> Option<Value> {
        self.evaluate_selected_at(site, config, watcher, initial, false)
    }

    pub(in crate::script_init) fn evaluate_selected_at(
        &mut self,
        site: Site,
        config: &Json,
        watcher: Option<StateKey>,
        initial: Value,
        scalar: bool,
    ) -> Option<Value> {
        match self.evaluate_with_context(site, config, watcher, initial, scalar) {
            Ok(value) => Some(value),
            Err(error) => {
                self.report(site, Issue::Expression(error));
                None
            }
        }
    }

    fn evaluate_with_context(
        &mut self,
        site: Site,
        config: &Json,
        watcher: Option<StateKey>,
        initial: Value,
        scalar: bool,
    ) -> Result<Value, EvalError> {
        self.with_instance_context(site, watcher, |context| {
            if scalar {
                script_expr::evaluate_scalar_with_initial(config, context, initial)
            } else {
                script_expr::evaluate_with_initial(config, context, initial)
            }
        })
    }

    fn with_instance_context<T>(
        &mut self,
        site: Site,
        watcher: Option<StateKey>,
        evaluate: impl FnOnce(&mut InstanceContext<'_, W>) -> Result<T, EvalError>,
    ) -> Result<T, EvalError> {
        let mut context = InstanceContext {
            machine: self.machine,
            graphs: self.graphs,
            world: self.world,
            site,
            reads: Vec::new(),
            foreign_reads: Vec::new(),
            parameters: self.parameters.as_deref(),
            cycle_candidate: None,
            health_target_facts: self.health_context.target_facts.as_deref(),
            observed_sample: self
                .observed_input
                .as_ref()
                .map(|input| input.sample.as_ref()),
            settled_damage: self.settled_damage_receiver == Some(site.instance),
            watcher,
            read_native: false,
            read_clock: false,
            untrusted_native: false,
        };
        let result = evaluate(&mut context);
        let reads = context.reads;
        let foreign_reads = context.foreign_reads;
        let needs_ticks = context.read_native || context.read_clock;
        if context.untrusted_native {
            // Like the reference, the record outlives the activation.
            if let Some(state) = watcher.and_then(|key| self.state_mut(key)) {
                state.untrusted_native = true;
            }
        }
        if let Some(watcher) = watcher.filter(|&key| {
            self.state(key)
                .is_some_and(|state| state.active && !state.ending)
        }) {
            for key in reads {
                self.watch(key, watcher);
            }
            for key in foreign_reads {
                let watchers = self.machine.foreign_watchers.entry(key).or_default();
                if !watchers.contains(&watcher) {
                    watchers.push(watcher);
                    self.machine
                        .state_foreign_watches
                        .entry(watcher)
                        .or_default()
                        .push(key);
                }
            }
            if let Some(start) = self
                .state(watcher)
                .filter(|state| {
                    needs_ticks
                        && matches!(
                            state.kind,
                            StateKind::BooleanSwitch(_)
                                | StateKind::Switch { .. }
                                | StateKind::Watch
                                | StateKind::AiBehavior { .. }
                                | StateKind::ExplosionContact
                                | StateKind::MovementMod
                                | StateKind::LogicalButton { .. }
                        )
                })
                .map(|state| state.start_ms)
            {
                self.register_tick(watcher, start);
            }
        }
        result
    }

    pub(in crate::script_init) fn watch(&mut self, key: VarKey, watcher: StateKey) {
        let watchers = self.machine.watchers.entry(key).or_default();
        if !watchers.contains(&watcher) {
            watchers.push(watcher);
            self.machine
                .state_watches
                .entry(watcher)
                .or_default()
                .push(key);
        }
    }

    pub(in crate::script_init) fn write(
        &mut self,
        key: VarKey,
        value: Value,
    ) -> Result<(), InitFault> {
        let old = self.machine.value(key, None);
        self.set_base(key, value);
        let outputs: Vec<_> = self
            .machine
            .links
            .get(&key)
            .into_iter()
            .flatten()
            .filter_map(|link| match link.output {
                LinkValue::Chase => Some((link.state, false)),
                LinkValue::Cooldown => Some((link.state, true)),
                _ => None,
            })
            .collect();
        for (state, cooldown) in outputs {
            if !self
                .state(state)
                .is_some_and(|state| state.active && state.linked_variable == Some(key))
            {
                continue;
            }
            if self.output_write_depth == MAX_PLUG_DEPTH {
                return Err(InitFault::PlugDepth);
            }
            self.output_write_depth += 1;
            let result = if cooldown {
                let node = self
                    .graph(state.instance)
                    .state(usize::from(state.raw))
                    .expect("ability state");
                self.restart_cooldown(state, node, self.output_write_depth)
            } else {
                self.chase_write(state, key)
            };
            self.output_write_depth -= 1;
            result?;
        }
        self.notify_value(key, &old, None)
    }

    pub(in crate::script_init) fn set_base(&mut self, key: VarKey, value: Value) {
        if let Some(sample) = self
            .observed_input
            .as_mut()
            .map(|input| &mut input.sample)
            .filter(|sample| {
                sample
                    .input
                    .as_ref()
                    .is_some_and(|(output, _)| *output == key)
            })
        {
            Arc::make_mut(sample).input = Some((key, value.clone()));
        }
        let prev = match key {
            VarKey::Entity(id) => self.machine.snapshot.variables.insert(id, value.clone()),
            VarKey::Instance(instance, id) => self.machine.snapshot.instances[&instance]
                .variables
                .insert(id, value.clone()),
        };
        if prev.as_ref().is_none_or(|old| !same(old, &value)) {
            self.machine.snapshot_dirty = true;
        }
    }

    pub(in crate::script_init) fn notify_value(
        &mut self,
        key: VarKey,
        old: &Value,
        source: Option<StateKey>,
    ) -> Result<(), InitFault> {
        let changed = !same(old, &self.machine.value(key, None));
        let watchers = self.machine.watchers.get(&key).cloned().unwrap_or_default();
        for watcher in watchers {
            let Some(state) = self
                .state(watcher)
                .filter(|state| state.active && !state.ending && !state.pending_change)
            else {
                continue;
            };
            if matches!(state.kind, StateKind::Stack { .. }) && state.linked_variable == Some(key) {
                if let Some(source) = source {
                    let above = self.machine.links.get(&key).is_some_and(|entries| {
                        entries
                            .iter()
                            .skip_while(|entry| entry.state != source)
                            .skip(1)
                            .any(|entry| entry.state == watcher)
                    });
                    if !above {
                        continue;
                    }
                }
            } else if !changed {
                continue;
            }
            self.state_mut(watcher)
                .expect("active watcher")
                .pending_change = true;
            self.post(watcher.instance, Event::Changed(watcher.raw))?;
        }
        Ok(())
    }

    pub(in crate::script_init) fn follow(
        &mut self,
        id: u16,
        node: &Node,
        path: &str,
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        self.spend("plug traversal")?;
        let mut taken = false;
        for link in node.links(path) {
            taken |= self.route_link(id, node, (node.pos, path), link, caller, depth)?;
        }
        Ok(taken)
    }

    /// Takes one decoded plug link. A target position naming a subgraph box
    /// with recorded begin edges routes through the box (a taken edge into a
    /// box forwards to the edge leaving it); a tag-only box keeps the loud
    /// missing-node report, because the packer carried no routing for it and
    /// the launch edge's meaning is not something to guess back.
    pub(in crate::script_init) fn route_link(
        &mut self,
        id: u16,
        node: &Node,
        via: (u16, &str),
        link: (Option<u16>, &str),
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        if depth >= MAX_PLUG_DEPTH {
            return Err(InitFault::PlugDepth);
        }
        self.spend("plug target")?;
        let (position, input) = link;
        let Some(position) = position else {
            // The reference treats an unresolved plug as taken.
            return Ok(true);
        };
        let graph = self.graph(id);
        let Some(target) = graph.node(position) else {
            let links = graph
                .container(position)
                .filter(|container| !container.links.is_empty())
                .map(|container| container.links.clone());
            let Some(links) = links else {
                self.report(self.site(id, node), Issue::MissingNode(position));
                return Ok(true);
            };
            let mut taken = false;
            for forwarded in &links {
                let Some(link) = decode_link(forwarded) else {
                    taken = true;
                    continue;
                };
                taken |= self.route_link(id, node, via, link, caller, depth + 1)?;
            }
            return Ok(taken);
        };
        if target.host_event_kind().is_some() || !self.observed_server_path(id, target) {
            return Ok(false);
        }
        if target.is_state() {
            let Some(raw) = target.state else {
                return Ok(false);
            };
            let key = StateKey { instance: id, raw };
            if target.class == "STU_01178155" && input == "m_inPlug" {
                let state = self.machine.snapshot.instances[&id]
                    .states
                    .entry(raw)
                    .or_insert_with(|| State::dormant(StateKind::ActiveQuery));
                let output = if state.active {
                    "m_7273C81B"
                } else {
                    "m_C6F3744E"
                };
                self.follow(id, target, output, Some(key), depth + 1)?;
                return Ok(true);
            }
            if target.class == "STU_7B9E8909" && input == "m_inPlug" {
                // The bridge consumes its input even when inactive. Only an
                // explicit begin request can open it.
                let state = self.machine.snapshot.instances[&id]
                    .states
                    .entry(raw)
                    .or_insert_with(|| State::dormant(StateKind::Plain));
                if state.active {
                    self.follow(id, target, "m_outPlug", Some(key), depth + 1)?;
                }
                return Ok(true);
            }
            if graph.index == 0x0cba && target.class == "STU_5E805B5A" && input == "m_F4DD996D" {
                if self.state(key).is_some_and(|state| state.active) {
                    self.select_recent_input(key, target, depth + 1)?;
                }
                return Ok(true);
            }
            if target.class == "STUStatescriptStateAbility" && input == "m_3B7BED2E" {
                self.stop_ability(key, target, depth + 1)?;
                return Ok(true);
            }
            let event = if input == ABORT_PLUG {
                Event::Abort(raw)
            } else if let Some(input) = &self.observed_input {
                Event::BeginObserved {
                    state: raw,
                    input: input.clone(),
                }
            } else {
                Event::Begin(raw)
            };
            self.post(id, event)?;
            return Ok(true);
        }
        let caller = if target.is_action() { None } else { caller };
        let linked =
            if target.class == "STU_1113EBD5" && self.admits_join_inputs(id, graph.index, target) {
                self.join_inputs(id, target, via, caller, depth + 1)?
            } else {
                self.run_node(id, target, caller, depth + 1)?
            };
        Ok(linked)
    }

    #[inline(never)]
    fn admits_join_inputs(&self, id: u16, graph_index: u32, target: &Node) -> bool {
        matches!((graph_index, target.pos), (0x0cc0, 7) | (0x022f, 8 | 12))
            || self
                .world
                .admits_generic_player_runtime(self.site(id, target))
    }

    #[inline(never)]
    pub(in crate::script_init) fn join_inputs(
        &mut self,
        id: u16,
        node: &Node,
        source: (u16, &str),
        caller: Option<StateKey>,
        depth: usize,
    ) -> Result<bool, InitFault> {
        self.spend("join input")?;
        let states = array_field(&node.fields, "m_122DF0CA")
            .iter()
            .map(|raw| raw.as_u64().and_then(|raw| u16::try_from(raw).ok()))
            .collect::<Option<Vec<_>>>();
        let required = field(&node.fields, "m_B24D1794").as_u64();
        let (Some(states), Some(required @ 2..=64)) = (states, required) else {
            self.report(
                self.site(id, node),
                Issue::UnsupportedField("join configuration"),
            );
            return Ok(false);
        };
        let inputs = &mut self.machine.snapshot.instances[&id].join_inputs;
        let arrived = inputs.entry(node.pos).or_default();
        if !arrived.insert((source.0, source.1.to_owned())) {
            return Ok(false);
        }
        let count = arrived.len() as u64;
        if count == 1 {
            for raw in states {
                self.spend("join completion check")?;
                let key = StateKey { instance: id, raw };
                if Some(key) != caller && self.state(key).is_some_and(|state| state.active) {
                    self.post(id, Event::CheckTransitions(raw))?;
                }
            }
            return Ok(false);
        }
        if count < required {
            return Ok(false);
        }
        inputs.remove(&node.pos);
        if !self.follow(id, node, "m_outPlug", caller, depth)? {
            return Ok(false);
        }
        for raw in states {
            self.spend("join state abort")?;
            if self
                .state(StateKey { instance: id, raw })
                .is_some_and(|state| state.active)
            {
                self.post(id, Event::Abort(raw))?;
            }
        }
        Ok(true)
    }
}
