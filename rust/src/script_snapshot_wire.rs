//! Project an initialized statescript body into the client baseline stream.
//!
//! The owner receives an H0 tree with all networked states off. An observer
//! receives the same tree and the initialized remote-synced states.

use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fmt,
};

use crate::bits::{BitError, BitWriter};
use crate::script_codecs::{Codec, CodecTable, PayloadError, PayloadMode, StatePayload};
use crate::script_expr::{Scope, Variable};
use crate::script_graph::{Graph, GraphStore};
pub use crate::script_init::FrameCallout;
use crate::script_init::{ChasePayload, FrameAction, ShotEvent, Snapshot, StackBinding, StateKind};
use crate::script_stream::StreamError;
use crate::script_wire::{self, Bindings, Value, ValueEncoding, Variables, WireError};

pub(super) const OWNER_HEALTH: u32 = 253;
pub(crate) const COUNTDOWN_VAR: u32 = 1303;
// Hero readiness, battle countdown, and overtime all write entity clock 1303.
pub(crate) const COUNTDOWN_STATES: [u16; 4] = [52, 53, 37, 60];

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CountdownFrame {
    pub value: Value,
    pub states: [Option<ChasePayload>; 4],
}

mod arming;
mod encode;
pub(crate) use arming::ArmingEffects;
pub(crate) use arming::{inactive_gate, initial_operand, runs_at_creation};
#[cfg(test)]
pub(crate) mod identity_test_reader;
mod projection;
#[cfg(test)]
mod tests;

pub(crate) use encode::{
    binding_delta, encounter_countdown_delta, encounter_message_delta, mode_action_delta,
};
#[cfg(test)]
pub(crate) use projection::uncertain_polled_switch;
#[cfg(test)]
pub(crate) use tests::CODECS as FIXTURE_CODECS;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum View {
    Owner,
    Observer,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ProjectedSnapshot {
    pub(super) entity: u32,
    pub(super) view: View,
    pub(super) entity_exclusions: BTreeSet<u32>,
    pub(super) variables: ProjectedVariables,
    pub(super) instances: BTreeMap<u16, ProjectedInstance>,
    pub(super) callout: u32,
    pub(super) callouts: Vec<FrameAction>,
    pub(super) emit_events: bool,
}

impl FrozenVolley {
    pub(crate) fn presentation_keys(&self) -> impl Iterator<Item = (u16, u32)> + '_ {
        self.presentation.iter().map(|(variable, _)| {
            let owner = match variable.scope {
                Scope::Entity => 0,
                Scope::Instance => self.event.instance,
            };
            (owner, variable.id)
        })
    }
}

impl ProjectedSnapshot {
    pub(crate) fn scene(entity: u32, values: Variables) -> Self {
        Self {
            entity,
            view: View::Observer,
            entity_exclusions: BTreeSet::new(),
            variables: ProjectedVariables {
                values,
                ..Default::default()
            },
            instances: BTreeMap::new(),
            callout: 0,
            callouts: Vec::new(),
            emit_events: false,
        }
    }

    pub(crate) fn mount(
        &mut self,
        id: u16,
        graph: &Graph,
        parent: Option<(u16, u16)>,
        values: Variables,
        states: impl IntoIterator<Item = (u16, StatePayload)>,
    ) -> Result<(), SnapshotError> {
        let states = states
            .into_iter()
            .map(|(state, payload)| {
                let site = StateSite {
                    instance: id,
                    graph: graph.index,
                    state,
                };
                graph
                    .state(usize::from(state))
                    .filter(|node| graph.remote_positions().contains(&node.pos))
                    .ok_or(SnapshotError::MissingState(site))?;
                Ok((
                    state,
                    ProjectedState {
                        active: true,
                        payload,
                        link: None,
                        volley: None,
                    },
                ))
            })
            .collect::<Result<_, SnapshotError>>()?;
        self.instances.insert(
            id,
            ProjectedInstance {
                graph: graph.index,
                start_ms: 0,
                parent,
                variables: ProjectedVariables {
                    values,
                    ..Default::default()
                },
                states,
                events: Vec::new(),
            },
        );
        Ok(())
    }

    pub(crate) fn mark_state_inactive(&mut self, instance: u16, state: u16) {
        if let Some(state) = self
            .instances
            .get_mut(&instance)
            .and_then(|instance| instance.states.get_mut(&state))
        {
            state.active = false;
        }
    }

    const PRACTICE_NAMEPLATE: u32 = 0x0d24;

    pub fn entity(&self) -> u32 {
        self.entity
    }

    pub fn view(&self) -> View {
        self.view
    }

    pub fn instance_count(&self) -> usize {
        self.instances.len()
    }

    pub(crate) fn has_instance(&self, instance: u16) -> bool {
        self.instances.contains_key(&instance)
    }

    pub(crate) fn state_payload(&self, instance: u16, state: u16) -> Option<&StatePayload> {
        Some(&self.instances.get(&instance)?.states.get(&state)?.payload)
    }

    pub fn acknowledged_callout(&self) -> u32 {
        self.callout
    }

    pub fn with_callout(mut self, callout: FrameCallout) -> Self {
        self.callouts.push(FrameAction {
            callout,
            values: BTreeMap::new(),
        });
        self
    }

    /// Omit per-instance event queues when matching action-only owner deltas.
    pub fn without_events(mut self) -> Self {
        self.emit_events = false;
        self
    }

    pub(crate) fn has_graph(&self, graph: u32) -> bool {
        self.instances
            .values()
            .any(|instance| instance.graph == graph)
    }

    pub(crate) fn has_practice_nameplate(&self) -> bool {
        self.has_graph(Self::PRACTICE_NAMEPLATE)
    }

    /// Introduce a Practice Range nameplate after the viewer has acknowledged
    /// both the mode HUD and its body-script parent. The introducing delta
    /// changes only the deferred subtree.
    pub(crate) fn stage_practice_nameplates(&mut self, base: Option<&Self>, can_introduce: bool) {
        let mut unseen: BTreeSet<_> = self
            .instances
            .iter()
            .filter_map(|(&id, instance)| {
                (instance.graph == Self::PRACTICE_NAMEPLATE
                    && base.is_none_or(|base| !base.instances.contains_key(&id)))
                .then_some(id)
            })
            .collect();
        if unseen.is_empty() {
            return;
        }
        if let Some(base) = base.filter(|_| can_introduce) {
            let mut ready: BTreeSet<_> = unseen
                .iter()
                .copied()
                .filter(|id| {
                    let Some((parent, _)) = self.instances[id].parent else {
                        return false;
                    };
                    base.instances
                        .get(&parent)
                        .zip(self.instances.get(&parent))
                        .is_some_and(|(previous, current)| {
                            previous.graph == current.graph && previous.parent == current.parent
                        })
                })
                .collect();
            if !ready.is_empty() {
                self.extend_descendants(&mut ready);
                let mut introduction = base.clone();
                for id in ready {
                    if let Some(instance) = self.instances.remove(&id) {
                        introduction.instances.insert(id, instance);
                    }
                }
                introduction.callouts.clear();
                *self = introduction;
                return;
            }
        }
        self.extend_descendants(&mut unseen);
        self.instances.retain(|id, _| !unseen.contains(id));
        for instance in self.instances.values_mut() {
            for state in instance.states.values_mut() {
                if state
                    .link
                    .is_some_and(|link| unseen.contains(&link.instance))
                {
                    state.link = None;
                }
            }
        }
        self.callouts
            .retain(|action| !unseen.contains(&action.callout.instance));
    }

    fn extend_descendants(&self, selected: &mut BTreeSet<u16>) {
        loop {
            let mut changed = false;
            for (&id, instance) in &self.instances {
                if instance
                    .parent
                    .is_some_and(|(parent, _)| selected.contains(&parent))
                {
                    changed |= selected.insert(id);
                }
            }
            if !changed {
                break;
            }
        }
    }

    pub(crate) fn active_spray(&self, instance: u16, state: u16) -> Option<(u64, u64)> {
        let owner = self.instances.get(&instance)?;
        let state = owner.states.get(&state)?;
        match state.payload.get("unlock") {
            Some(Value::Asset(unlock)) if state.active => Some((owner.start_ms, *unlock)),
            _ => None,
        }
    }

    /// The activation counter and absolute start in milliseconds from this frame.
    pub fn volley_identity(&self, instance: u16, state: u16) -> Option<(u16, u64)> {
        self.instances
            .get(&instance)?
            .states
            .get(&state)?
            .volley
            .map(|volley| volley.identity)
    }

    #[cfg(test)]
    pub(crate) fn freeze_volley(
        &self,
        event: ShotEvent,
        presentation: Vec<(Variable, Value)>,
    ) -> Option<FrozenVolley> {
        self.freeze_volley_with_states(event, presentation, &[])
    }

    pub(crate) fn freeze_volley_with_states(
        &self,
        event: ShotEvent,
        presentation: Vec<(Variable, Value)>,
        presentation_states: &[u16],
    ) -> Option<FrozenVolley> {
        let instance = self.instances.get(&event.instance)?;
        let state = instance.states.get(&event.raw_state)?;
        let identity = state.volley?.identity;
        if self.entity != event.entity
            || self.view != View::Observer
            || instance.graph != event.graph
            || identity != (event.activation, event.activation_ms)
        {
            return None;
        }
        let Value::Integer(count) = state.payload.get("volleys")? else {
            return None;
        };
        let mut extra_states = Vec::new();
        for &raw in presentation_states {
            if raw == event.raw_state {
                continue;
            }
            if !extra_states.iter().any(|(existing, _)| *existing == raw) {
                extra_states.push((
                    raw,
                    ProjectedState {
                        active: true,
                        payload: StatePayload::new(),
                        link: None,
                        volley: None,
                    },
                ));
            }
        }
        Some(FrozenVolley {
            event,
            parent: instance.parent,
            state: state.clone(),
            count: u16::try_from(*count).ok()?,
            presentation: presentation
                .into_iter()
                .filter(|(variable, value)| {
                    !matches!(value, Value::Null | Value::Map(_) | Value::IdentifierMap(_))
                        && !(variable.scope == Scope::Entity
                            && self.entity_exclusions.contains(&variable.id))
                })
                .collect(),
            extra_states,
        })
    }

    pub(crate) fn retain_volley(&mut self, volley: &FrozenVolley) -> bool {
        if self.entity != volley.event.entity {
            return false;
        }
        if let Some(instance) = self
            .instances
            .get_mut(&volley.event.instance)
            .filter(|instance| {
                instance.graph == volley.event.graph && instance.parent == volley.parent
            })
        {
            instance
                .states
                .insert(volley.event.raw_state, volley.state.clone());
            for (raw, extra) in &volley.extra_states {
                instance.states.insert(*raw, extra.clone());
            }
            for (variable, value) in &volley.presentation {
                let variables = match variable.scope {
                    Scope::Entity => &mut self.variables,
                    Scope::Instance => &mut instance.variables,
                };
                variables.seed_value(variable.id, value.clone());
            }
            true
        } else {
            false
        }
    }

    pub(crate) fn has_volley(&self, volley: &FrozenVolley) -> bool {
        let event = volley.event;
        self.entity == event.entity
            && self
                .instances
                .get(&event.instance)
                .filter(|instance| {
                    instance.graph == event.graph && instance.parent == volley.parent
                })
                .is_some_and(|instance| {
                    let state_matches = if self.view == View::Owner {
                        instance.states.get(&event.raw_state).is_some_and(|s| {
                            s.active == volley.state.active
                                && s.volley.map(|v| v.identity)
                                    == volley.state.volley.map(|v| v.identity)
                                && s.payload.get("volleys") == volley.state.payload.get("volleys")
                        })
                    } else {
                        instance.states.get(&event.raw_state) == Some(&volley.state)
                    };
                    state_matches
                        && volley
                            .extra_states
                            .iter()
                            .all(|(raw, extra)| instance.states.get(raw) == Some(extra))
                        && volley.presentation.iter().all(|(variable, value)| {
                            let variables = match variable.scope {
                                Scope::Entity => &self.variables,
                                Scope::Instance => &instance.variables,
                            };
                            variables.values.get(&variable.id) == Some(value)
                                && !variables.bindings.contains_key(&variable.id)
                        })
                })
    }

    #[cfg(feature = "inspector")]
    pub(crate) fn trace_owner_volley_delta(
        &self,
        chunk: u32,
        frame: u32,
        changes: &BTreeSet<crate::script_stream::ChangeKey>,
        detailed: bool,
        playtest: bool,
    ) {
        use crate::script_expr::Scope;
        use crate::script_stream::ChangeKey;

        for (&id, instance) in &self.instances {
            for (&raw, state) in &instance.states {
                let Some(volley) = state.volley else {
                    continue;
                };
                let sent_state = changes.contains(&ChangeKey::State(id, raw));
                let written_ammo = volley.ammo.and_then(|(variable, _)| {
                    let (owner, variables) = match variable.scope {
                        Scope::Entity => (0, &self.variables.values),
                        Scope::Instance => (id, &instance.variables.values),
                    };
                    if !changes.contains(&ChangeKey::Variable(owner, variable.id)) {
                        return None;
                    }
                    match variables.get(&variable.id) {
                        Some(Value::Integer(ammo)) => Some(*ammo),
                        _ => None,
                    }
                });
                if detailed && (sent_state || written_ammo.is_some()) {
                    let (counter, start_ms) = volley.identity;
                    let introduced = changes.contains(&ChangeKey::VolleyIntroduction(id, raw));
                    crate::owner_trace::record(format_args!(
                        "owner-volley delta body={:08x} chunk={chunk} frame={frame} instance={id} state={raw} sent_state={sent_state} active={} counter={counter} start_ms={start_ms} introduced={introduced} written_ammo={written_ammo:?}",
                        self.entity, state.active
                    ));
                }
                if let Some(ammo) = written_ammo.filter(|_| playtest) {
                    let (counter, start_ms) = volley.identity;
                    crate::owner_trace::playtest(format_args!(
                        "owner-ammo body={:08x} chunk={chunk} frame={frame} instance={id} state={raw} active={} counter={counter} start_ms={start_ms} sent_ammo={ammo}",
                        self.entity, state.active,
                    ));
                }
            }
        }
    }

    pub(crate) fn entity_variables(&self) -> &Variables {
        &self.variables.values
    }

    pub(crate) fn instance_variables(&self, instance: u16) -> Option<&Variables> {
        Some(&self.instances.get(&instance)?.variables.values)
    }

    /// The raw indices of the states this frame lists for the instance.
    #[cfg(test)]
    pub(crate) fn instance_states(&self, instance: u16) -> Option<BTreeSet<u16>> {
        Some(
            self.instances
                .get(&instance)?
                .states
                .keys()
                .copied()
                .collect(),
        )
    }
}

#[derive(Clone, Debug)]
pub(crate) struct FrozenVolley {
    pub(crate) event: ShotEvent,
    pub(crate) count: u16,
    parent: Option<(u16, u16)>,
    state: ProjectedState,
    presentation: Vec<(Variable, Value)>,
    extra_states: Vec<(u16, ProjectedState)>,
}

#[derive(Clone, Debug, PartialEq)]
pub(super) struct ProjectedInstance {
    pub(super) graph: u32,
    pub(super) start_ms: u64,
    pub(super) parent: Option<(u16, u16)>,
    pub(super) variables: ProjectedVariables,
    pub(super) states: BTreeMap<u16, ProjectedState>,
    pub(super) events: Vec<ProjectedEvent>,
}

#[derive(Clone, Debug, PartialEq)]
pub(super) struct ProjectedState {
    pub(super) active: bool,
    pub(super) payload: StatePayload,
    pub(super) link: Option<ProjectedStateLink>,
    pub(super) volley: Option<VolleyActivation>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) struct ProjectedStateLink {
    pub(super) instance: u16,
    pub(super) index: u16,
    pub(super) width: u8,
}

impl ProjectedState {
    pub(super) fn encode_payload(
        &self,
        codec: &Codec,
        mode: PayloadMode,
    ) -> Result<BitWriter, PayloadError> {
        let Some(link) = self.link else {
            return codec.payload(&self.payload, mode);
        };
        codec.payload_with_link(&self.payload, mode, |out, _| {
            out.bit(true);
            out.w_u16(link.instance);
            out.bits(u64::from(link.index), usize::from(link.width))?;
            Ok(())
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) struct VolleyActivation {
    pub(super) identity: (u16, u64), // activation counter and start time
    pub(super) ammo: Option<(Variable, i64)>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(super) struct ProjectedVariables {
    pub(super) values: Variables,
    pub(super) bindings: Bindings,
}

impl ProjectedVariables {
    pub(super) fn write(&self, out: &mut BitWriter, owner: u16) -> Result<(), WireError> {
        script_wire::bound_variable_list(
            out,
            &self.values,
            ValueEncoding::Compact,
            owner,
            &self.bindings,
        )
    }

    pub(super) fn seed_value(&mut self, variable: u32, value: Value) {
        self.values.insert(variable, value);
        self.bindings.remove(&variable);
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct ProjectedEvent {
    pub(super) due_ms: u64,
    pub(super) raw_state: u16,
    pub(super) finish: bool,
    pub(super) parameter: u16,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StateSite {
    pub instance: u16,
    pub graph: u32,
    pub state: u16,
}

#[derive(Debug)]
pub enum SnapshotError {
    Incomplete {
        entity: u32,
        diagnostics: Vec<crate::script_init::Diagnostic>,
    },
    MissingBoundValue {
        owner: u16,
        variable: u32,
    },
    UnsupportedMap {
        owner: u16,
        variable: u32,
    },
    InvalidBinding {
        owner: u16,
        variable: u32,
        binding: StackBinding,
    },
    MissingGraph {
        instance: u16,
        graph: u32,
    },
    MissingState(StateSite),
    MissingStateIndex {
        instance: u16,
        graph: u32,
        node: u16,
    },
    UnsupportedState {
        site: StateSite,
        class: String,
        /// Boxed: the payload variants of `StateKind` include the full
        /// static-trace record; the error must stay below Clippy's
        /// large-`Err` threshold that every projection result carries.
        kind: Box<StateKind>,
    },
    UnsupportedTimer {
        site: StateSite,
        class: String,
    },
    Payload {
        site: StateSite,
        source: PayloadError,
    },
    DifferentStream,
    ReboundInstance(u16),
    FrameTime,
    Wire(WireError),
    Stream(StreamError),
}

impl fmt::Display for SnapshotError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Incomplete {
                entity,
                diagnostics,
            } => {
                write!(
                    f,
                    "body {entity:08X} has {} initialization diagnostics: ",
                    diagnostics.len()
                )?;
                for (index, diagnostic) in diagnostics.iter().enumerate() {
                    if index != 0 {
                        f.write_str(", ")?;
                    }
                    let site = diagnostic.site;
                    match site.node {
                        Some(node) => {
                            write!(f, "{:04X}:{node}:{:?}", site.graph, diagnostic.issue)?
                        }
                        None => write!(f, "{:04X}:-:{:?}", site.graph, diagnostic.issue)?,
                    }
                }
                Ok(())
            }
            Self::MissingBoundValue { owner, variable } => {
                write!(
                    f,
                    "missing resolved value for bound variable {owner}:{variable}"
                )
            }
            Self::UnsupportedMap { owner, variable } => {
                write!(
                    f,
                    "variable {owner}:{variable} holds a map without a verified wire form"
                )
            }
            Self::InvalidBinding {
                owner,
                variable,
                binding,
            } => {
                write!(
                    f,
                    "invalid stack binding {binding:?} for variable {owner}:{variable}"
                )
            }
            Self::MissingGraph { instance, graph } => {
                write!(f, "instance {instance} has no graph {graph:04X}")
            }
            Self::MissingState(site) => write!(f, "missing statescript state {site:?}"),
            Self::MissingStateIndex {
                instance,
                graph,
                node,
            } => {
                write!(
                    f,
                    "instance {instance} graph {graph:04X} remote node {node} has no state index"
                )
            }
            Self::UnsupportedState { site, class, kind } => {
                write!(f, "unsupported active state {class} ({kind:?}) at {site:?}")
            }
            Self::UnsupportedTimer { site, class } => {
                write!(f, "unsupported owner timer for {class} at {site:?}")
            }
            Self::Payload { site, source } => write!(f, "statescript state {site:?}: {source}"),
            Self::DifferentStream => f.write_str(
                "statescript baseline belongs to a different body, view or variable scope",
            ),
            Self::ReboundInstance(id) => {
                write!(f, "statescript instance {id} changed its definition")
            }
            Self::FrameTime => f.write_str("statescript frame time is outside the supported range"),
            Self::Wire(error) => error.fmt(f),
            Self::Stream(error) => error.fmt(f),
        }
    }
}

impl Error for SnapshotError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Payload { source, .. } => Some(source),
            Self::Wire(error) => Some(error),
            Self::Stream(error) => Some(error),
            _ => None,
        }
    }
}

impl From<WireError> for SnapshotError {
    fn from(error: WireError) -> Self {
        Self::Wire(error)
    }
}

impl From<BitError> for SnapshotError {
    fn from(error: BitError) -> Self {
        Self::Wire(error.into())
    }
}

impl From<StreamError> for SnapshotError {
    fn from(error: StreamError) -> Self {
        Self::Stream(error)
    }
}

pub(crate) struct SceneProjection<'a> {
    pub snapshot: &'a Snapshot,
    pub encoder: SnapshotEncoder<'a>,
}

/// A room actor's immutable observer frame, shared by all its viewers.
pub(crate) struct StaticSceneBaseline {
    pub snapshot: ProjectedSnapshot,
    pub payload: crate::bits::BitWriter,
}

pub struct SnapshotEncoder<'a> {
    pub(super) graphs: &'a GraphStore,
    pub(super) codecs: &'a CodecTable,
}

impl<'a> SnapshotEncoder<'a> {
    pub fn new(graphs: &'a GraphStore, codecs: &'a CodecTable) -> Self {
        Self { graphs, codecs }
    }

    pub(super) fn graph(&self, instance: u16, graph: u32) -> Result<&Graph, SnapshotError> {
        self.graphs
            .graph(graph)
            .ok_or(SnapshotError::MissingGraph { instance, graph })
    }
}
