//! Wire encoders and delta framing for projected statescript snapshots.

use std::collections::{BTreeMap, BTreeSet};

use crate::bits::BitWriter;
use crate::script_codecs::{Codec, CodecTable, PayloadError, PayloadMode, StatePayload};
use crate::script_expr::Scope;
use crate::script_graph::Graph;
use crate::script_init::FrameAction;
use crate::script_stream::{BodyStream, ChangeKey, DeltaKind, ScriptChunk, StreamError};
use crate::script_wire::{self, Instance, Layout, Value, ValueEncoding, Variables, WireError};

use super::{
    COUNTDOWN_STATES, COUNTDOWN_VAR, CountdownFrame, ProjectedInstance, ProjectedSnapshot,
    ProjectedState, ProjectedVariables, SnapshotEncoder, SnapshotError, StateSite, View,
};

pub(crate) fn encounter_countdown_delta(
    graph: &Graph,
    codecs: &CodecTable,
    instance: u16,
    countdown: &CountdownFrame,
) -> Result<BitWriter, WireError> {
    let mut states = BTreeMap::new();
    for (raw, chase) in COUNTDOWN_STATES.into_iter().zip(&countdown.states) {
        let node = graph
            .state(usize::from(raw))
            .filter(|node| node.class == "STUStatescriptStateChaseVar")
            .ok_or(WireError::InvalidValue)?;
        let slot = graph
            .remote_nodes
            .iter()
            .position(|pos| *pos == Some(node.pos))
            .filter(|_| graph.remote_positions().contains(&node.pos))
            .ok_or(WireError::SlotOutOfRange)?;
        states.insert(slot, chase);
    }
    let codec = codecs
        .codec("STUStatescriptStateChaseVar")
        .filter(|codec| !codec.stec())
        .ok_or(WireError::InvalidValue)?;
    let mut out = BitWriter::new();
    out.bits(0, 2)?; // H=0, existing encounter instance
    out.w_u16(instance);
    out.bits(0, 2)?; // no removal or descriptor
    out.w_u16(0);
    script_wire::variable_list(
        &mut out,
        &Variables::from([(COUNTDOWN_VAR, countdown.value.clone())]),
        ValueEncoding::Compact,
    )?;
    write_delta_variables(&mut out, instance, graph, &ProjectedVariables::default())?;
    write_mask(
        &mut out,
        graph.remote_nodes.len(),
        &states.keys().copied().collect(),
    );
    for chase in states.into_values() {
        out.bit(chase.is_some());
        if let Some(chase) = chase {
            let mut payload = StatePayload::from([
                ("cur".into(), chase.current.clone()),
                ("reached".into(), Value::Bool(chase.reached)),
            ]);
            if let Some(remaining) = chase.remaining_ms {
                payload.insert("remaining".into(), Value::Integer(remaining));
            }
            let payload = codec
                .payload(&payload, PayloadMode::Remote { full: false })
                .map_err(|_| WireError::InvalidValue)?;
            out.append(&payload);
        }
    }
    out.bits(0, 2)?; // no frame action
    Ok(out)
}

pub(crate) fn encounter_message_delta(
    graph: &Graph,
    codecs: &CodecTable,
    instance: (u16, Option<(u16, u16)>),
    raw: u16,
    entity_variables: &Variables,
    instance_variables: Variables,
) -> Result<BitWriter, WireError> {
    let node = graph
        .state(usize::from(raw))
        .filter(|node| node.class == "STU_38EE1100" && node.is_networked())
        .ok_or(WireError::InvalidValue)?;
    let slot = graph
        .remote_nodes
        .iter()
        .position(|pos| *pos == Some(node.pos))
        .filter(|&slot| graph.remote_slot(slot).is_some())
        .ok_or(WireError::SlotOutOfRange)?;
    let codec = codecs.codec(&node.class).ok_or(WireError::InvalidValue)?;
    let payload = codec
        .payload(&StatePayload::new(), PayloadMode::Remote { full: false })
        .map_err(|_| WireError::InvalidValue)?;

    let (id, parent) = instance;
    let mut out = BitWriter::new();
    out.bits(0, 2)?; // H=0, observer delta
    out.w_u16(id);
    out.bit(false); // instance is present
    out.bit(parent.is_some());
    if let Some(parent) = parent {
        let index = u16::try_from(graph.index).map_err(|_| WireError::GraphIndex(graph.index))?;
        script_wire::write_descriptor(&mut out, index, Some(parent))?;
    }
    out.w_u16(0);
    script_wire::variable_list(&mut out, entity_variables, ValueEncoding::Compact)?;
    write_delta_variables(
        &mut out,
        id,
        graph,
        &ProjectedVariables {
            values: instance_variables,
            ..Default::default()
        },
    )?;
    write_mask(&mut out, graph.remote_nodes.len(), &BTreeSet::from([slot]));
    if codec.stec() {
        out.bit(true);
    }
    out.bit(true);
    out.append(&payload);
    out.bits(0, 2)?; // no frame action
    Ok(out)
}

pub(crate) fn mode_action_delta(
    graph: &Graph,
    instance: u16,
    position: u16,
    stamp: u32,
) -> Result<BitWriter, WireError> {
    let node = graph
        .node(position)
        .filter(|node| node.is_action() && node.is_networked() && node.remote >= 0)
        .ok_or(WireError::InvalidValue)?;
    let mut entry = BitWriter::new();
    entry.bit(true); // target instance is present
    entry.w_u16(instance);
    entry.bits(node.remote as u64, usize::from(graph.header.remote_bits))?;

    let mut out = BitWriter::new();
    out.bits(0, 2)?; // H=0, observer delta
    out.w_u16(0); // no instance changes
    script_wire::variable_list(&mut out, &Variables::new(), ValueEncoding::Compact)?;
    out.bit(true);
    out.w_u32(stamp);
    out.w_var(u32::try_from(10 + entry.bit_len()).map_err(|_| WireError::ChunkRange)?);
    out.bits(1, 2)?; // one entry with a short length and no node payload
    out.bits((8 + entry.bit_len()) as u64, 8)?;
    out.append(&entry);
    out.bits(0, 3)?; // end of entries, groups, and ungrouped entries
    Ok(out)
}

impl<'a> SnapshotEncoder<'a> {
    pub(crate) fn mounted_delta(
        &self,
        previous: Option<&ProjectedSnapshot>,
        current: &ProjectedSnapshot,
    ) -> Result<Option<BitWriter>, SnapshotError> {
        let mut baseline;
        let base = if let Some(previous) = previous {
            previous
        } else {
            baseline = current.clone();
            baseline.variables = ProjectedVariables::default();
            for instance in baseline.instances.values_mut() {
                instance.variables = ProjectedVariables::default();
                instance.states.clear();
            }
            &baseline
        };
        let known = base.instances.keys().copied().collect();
        let delta = self.differences(base, current, &BTreeSet::new(), &known)?;
        if delta.keys.is_empty() {
            return Ok(None);
        }
        self.delta_frame(current, &delta, DeltaKind::Observer { resend: true }, 16)
            .map(Some)
    }

    pub(crate) fn post_match_delta(
        &self,
        previous: Option<&ProjectedSnapshot>,
        current: &ProjectedSnapshot,
    ) -> Result<Option<BitWriter>, SnapshotError> {
        let mut baseline;
        let base = if let Some(previous) = previous {
            previous
        } else {
            baseline = current.clone();
            baseline.variables = ProjectedVariables::default();
            baseline.instances.retain(|_, instance| {
                instance.graph == 0x180a && instance.parent.is_some_and(|(_, raw)| raw == 45)
            });
            for instance in baseline.instances.values_mut() {
                instance.variables = ProjectedVariables::default();
                instance.states.clear();
            }
            &baseline
        };
        let known = base.instances.keys().copied().collect();
        let delta = self.differences(base, current, &BTreeSet::new(), &known)?;
        if delta.keys.is_empty() {
            return Ok(None);
        }
        self.delta_frame(current, &delta, DeltaKind::Observer { resend: true }, 16)
            .map(Some)
    }

    pub(crate) fn biotic_field_frame(&self, faded: bool) -> Result<BitWriter, SnapshotError> {
        let graph = self.graph(1, 0x0258)?;
        let mut instance = ProjectedInstance {
            graph: graph.index,
            start_ms: 0,
            parent: None,
            variables: ProjectedVariables::default(),
            states: BTreeMap::new(),
            events: Vec::new(),
        };
        for (raw, class) in [
            (2, "STU_691BFA55"),
            (3, "STUStatescriptStateCosmeticEntity"),
            (5, "STUStatescriptStateDataFlowMapping"),
        ] {
            if faded && raw == 5 {
                continue;
            }
            let site = StateSite {
                instance: 1,
                graph: graph.index,
                state: raw,
            };
            let node = graph
                .state(usize::from(raw))
                .filter(|node| node.class == class && graph.remote_positions().contains(&node.pos))
                .ok_or(SnapshotError::MissingState(site))?;
            let payload = if node.class == "STU_691BFA55" {
                StatePayload::from([("counter".into(), Value::Integer(1))])
            } else {
                StatePayload::new()
            };
            instance.states.insert(
                raw,
                ProjectedState {
                    active: true,
                    payload,
                    link: None,
                    volley: None,
                },
            );
        }
        let mut wire = Instance::new(1, Layout::try_from(graph)?, None);
        self.remote_states(1, graph, &instance, &mut wire)?;
        Ok(script_wire::remote_full(&[wire], &Variables::new())?)
    }

    /// Encode the armed observer baseline from this projectile's own .003 script roots.
    pub fn armed_projectile_frame(
        &self,
        definition: u64,
    ) -> Result<Option<BitWriter>, SnapshotError> {
        self.graphs
            .persistent_projectile(definition)
            .and_then(|body| super::ArmingEffects::from_definition(self.graphs, body))
            .map(|effects| self.full(&effects.snapshot(0)))
            .transpose()
    }

    /// The owner's first tree and the observer's first frame share the H0
    /// descriptor, variable, and remote slot writer.
    pub fn full(&self, snapshot: &ProjectedSnapshot) -> Result<BitWriter, SnapshotError> {
        let mut instances = Vec::with_capacity(snapshot.instances.len());
        for (&id, instance) in &snapshot.instances {
            let graph = self.graph(id, instance.graph)?;
            let mut wire = Instance::new(id, Layout::try_from(graph)?, instance.parent);
            wire.variables = instance.variables.values.clone();
            for (slot, (_, var)) in graph.presence_vars().enumerate() {
                if let Some(value) = var.0.and_then(|var| wire.variables.swap_remove(&var)) {
                    wire.presence.insert(slot, value);
                }
            }
            if snapshot.view == View::Observer {
                self.remote_states(id, graph, instance, &mut wire)?;
            }
            instances.push(wire);
        }
        Ok(script_wire::remote_full(
            &instances,
            &snapshot.variables.values,
        )?)
    }

    /// Prepare the baseline for this body's delivery stream. The body create
    /// acknowledgement still controls when the resulting chunk may be sent.
    pub fn queue_full(
        &self,
        stream: &mut BodyStream<ProjectedSnapshot>,
        snapshot: &ProjectedSnapshot,
    ) -> Result<ScriptChunk, SnapshotError> {
        let payload = self.full(snapshot)?;
        let mut baseline = snapshot.clone();
        if baseline.view == View::Owner {
            baseline.variables.bindings.clear();
            for instance in baseline.instances.values_mut() {
                instance.variables.bindings.clear();
                instance.states.clear();
                instance.events.clear();
            }
        }
        if let Some(action) = baseline.callouts.last() {
            baseline.callout = baseline.callout.max(action.callout.serial);
        }
        baseline.callouts.clear();
        let chunk = stream.full(&payload, baseline)?;
        #[cfg(test)]
        let chunk = {
            let mut chunk = chunk;
            if snapshot.view == View::Owner {
                chunk.owner_rifle_ammo_written =
                    explicit_rifle_ammo(snapshot.instances.values().map(|i| (i, &i.variables)));
            }
            chunk
        };
        Ok(chunk)
    }

    /// Encode a cumulative projection from the acknowledged baseline. The
    /// caller supplies the owner phase selected by the body's stream.
    pub fn queue_delta(
        &self,
        stream: &mut BodyStream<ProjectedSnapshot>,
        snapshot: &ProjectedSnapshot,
        kind: DeltaKind,
        frame_ms: u32,
    ) -> Result<Option<ScriptChunk>, SnapshotError> {
        let (_, base) = stream.base().ok_or(StreamError::BaselinePending)?;
        let view = match kind {
            DeltaKind::Observer { .. } => View::Observer,
            DeltaKind::InitialOwner(_) | DeltaKind::Correction(_) => View::Owner,
        };
        if base.entity != snapshot.entity
            || base.view != view
            || snapshot.view != view
            || base.entity_exclusions != snapshot.entity_exclusions
        {
            return Err(SnapshotError::DifferentStream);
        }
        let touched = stream.touched();
        let known: BTreeSet<_> = if view == View::Observer {
            base.instances
                .keys()
                .copied()
                .chain(
                    stream
                        .pending()
                        .flat_map(|(_, pending, _)| pending.instances.keys().copied()),
                )
                .collect()
        } else {
            BTreeSet::new()
        };
        let next_chunk = stream
            .last()
            .checked_add(1)
            .ok_or(StreamError::ChunkExhausted)?;
        let mut delta = self.differences(base, snapshot, &touched, &known)?;
        let action = apply_callout(base, snapshot, &touched, next_chunk, &mut delta);
        if view == View::Observer && delta.keys.is_empty() {
            return Ok(None);
        }
        let mut delivered = snapshot.clone();
        delivered.callout = delta
            .callout
            .map_or(base.callout, |(serial, ..)| serial)
            .max(delivered.callout);
        delta.seed_owner_ammo(&mut delivered);
        if let Some(action) = action {
            delta.seed_callout(snapshot, &mut delivered, action);
        }
        let frame = self.delta_frame(snapshot, &delta, kind, frame_ms)?;
        #[cfg(test)]
        let written = if view == View::Owner {
            explicit_rifle_ammo(
                delta
                    .instances
                    .values()
                    .filter_map(|i| i.current.map(|v| (v, &i.variables))),
            )
        } else {
            None
        };
        let chunk = stream.delta(&frame, delivered, delta.keys, kind)?;
        #[cfg(test)]
        let chunk = {
            let mut chunk = chunk;
            chunk.owner_rifle_ammo_written = written;
            chunk
        };
        Ok(Some(chunk))
    }

    pub(super) fn differences<'s>(
        &self,
        base: &ProjectedSnapshot,
        current: &'s ProjectedSnapshot,
        touched: &BTreeSet<ChangeKey>,
        known: &BTreeSet<u16>,
    ) -> Result<SnapshotDelta<'s>, SnapshotError> {
        let mut delta = SnapshotDelta::default();
        delta.variables = delta_variables(
            0,
            Some(&base.variables),
            &current.variables,
            touched,
            &mut delta.keys,
        );
        for (&id, next) in &current.instances {
            let before = base.instances.get(&id);
            if before
                .is_some_and(|before| before.graph != next.graph || before.parent != next.parent)
            {
                return Err(SnapshotError::ReboundInstance(id));
            }
            let descriptor = before.is_none();
            if descriptor {
                delta.keys.insert(ChangeKey::Instance(id));
            }
            let variables = delta_variables(
                id,
                before.map(|before| &before.variables),
                &next.variables,
                touched,
                &mut delta.keys,
            );
            let graph = self.graph(id, next.graph)?;
            let raw_slots: BTreeSet<_> = next
                .states
                .keys()
                .copied()
                .chain(
                    before
                        .into_iter()
                        .flat_map(|before| before.states.keys().copied()),
                )
                .chain(
                    touched
                        .range(ChangeKey::State(id, 0)..=ChangeKey::State(id, u16::MAX))
                        .filter_map(|key| match key {
                            ChangeKey::State(_, state) => Some(*state),
                            _ => None,
                        }),
                )
                .collect();
            let mut states = BTreeMap::new();
            for raw in raw_slots {
                let now = next.states.get(&raw);
                let was = before.and_then(|before| before.states.get(&raw));
                let key = ChangeKey::State(id, raw);
                if now == was && !touched.contains(&key) {
                    continue;
                }
                let site = StateSite {
                    instance: id,
                    graph: graph.index,
                    state: raw,
                };
                let node = graph
                    .state(usize::from(raw))
                    .ok_or(SnapshotError::MissingState(site))?;
                if now.is_none() && self.codecs.codec(&node.class).is_some_and(Codec::stec) {
                    continue;
                }
                states.insert(raw, now);
                delta.keys.insert(key);
                let Some((now, volley)) = now
                    .filter(|_| current.view == View::Owner)
                    .and_then(|state| state.volley.map(|volley| (state, volley)))
                else {
                    continue;
                };
                let previous = was.and_then(|state| state.volley);
                let introduced = previous.is_none_or(|was| was.identity != volley.identity);
                if introduced {
                    delta.keys.insert(ChangeKey::VolleyIntroduction(id, raw));
                }
                if let Some((variable, ammo)) = volley.ammo.filter(|_| {
                    now.active && (introduced || !was.is_some_and(|state| state.active))
                }) {
                    let owner = match variable.scope {
                        Scope::Entity => 0,
                        Scope::Instance => id,
                    };
                    delta.ammo_seeds.insert((owner, variable.id), ammo);
                }
            }
            if descriptor || !variables.values.is_empty() || !states.is_empty() {
                delta.instances.insert(
                    id,
                    InstanceDelta {
                        current: Some(next),
                        descriptor,
                        variables,
                        states,
                    },
                );
            }
        }
        let previous_ids: BTreeSet<_> = base
            .instances
            .keys()
            .copied()
            .chain(
                touched
                    .range(ChangeKey::Instance(0)..=ChangeKey::Instance(u16::MAX))
                    .filter_map(|key| match key {
                        ChangeKey::Instance(id) => Some(*id),
                        _ => None,
                    }),
            )
            .collect();
        for id in previous_ids {
            if !current.instances.contains_key(&id)
                && (current.view == View::Owner || known.contains(&id))
            {
                delta.instances.insert(id, InstanceDelta::default());
                delta.keys.insert(ChangeKey::Instance(id));
            }
        }
        Ok(delta)
    }

    pub(super) fn delta_frame(
        &self,
        current: &ProjectedSnapshot,
        delta: &SnapshotDelta<'_>,
        kind: DeltaKind,
        frame_ms: u32,
    ) -> Result<BitWriter, SnapshotError> {
        let (cmfd, correction, mode) = match kind {
            DeltaKind::Observer { .. } => (None, false, PayloadMode::Remote { full: false }),
            DeltaKind::InitialOwner(frame) | DeltaKind::Correction(frame) => {
                if frame == 0 {
                    return Err(WireError::ZeroCommandFrame.into());
                }
                let frame_time = i64::from(frame)
                    .checked_mul(i64::from(frame_ms))
                    .ok_or(SnapshotError::FrameTime)?;
                (
                    Some(frame),
                    matches!(kind, DeltaKind::Correction(_)),
                    PayloadMode::Owner {
                        full: false,
                        frame_time,
                        frame_ms,
                    },
                )
            }
        };
        let mut body = BitWriter::new();
        let mut last = 0;
        for (&id, instance) in &delta.instances {
            body.w_u16(id - last);
            last = id;
            body.bit(instance.current.is_none());
            if let Some(current) = instance.current {
                body.bit(instance.descriptor);
                if instance.descriptor {
                    let graph = u16::try_from(current.graph)
                        .map_err(|_| WireError::GraphIndex(current.graph))?;
                    script_wire::write_descriptor(&mut body, graph, current.parent)?;
                }
            }
        }
        body.w_u16(0);
        delta.variables.write(&mut body, 0)?;
        for (&id, instance) in &delta.instances {
            let Some(current) = instance.current else {
                continue;
            };
            let graph = self.graph(id, current.graph)?;
            write_delta_variables(&mut body, id, graph, &instance.variables)?;
            self.delta_states(&mut body, id, graph, &instance.states, mode)?;
        }
        if let PayloadMode::Owner { frame_time, .. } = mode {
            let frame_time = u64::try_from(frame_time).map_err(|_| SnapshotError::FrameTime)?;
            self.owner_events(&mut body, current, frame_time)?;
        }
        let mut frame = BitWriter::new();
        frame.bit(false);
        frame.bit(cmfd.is_some());
        if let Some(cmfd) = cmfd {
            frame.bit(correction);
            frame.w_var(cmfd);
            frame.w_var(u32::try_from(body.bit_len()).map_err(|_| WireError::ChunkRange)?);
        }
        frame.append(&body);
        if cmfd.is_some() {
            frame.bit(false);
        }
        self.write_frame_action(&mut frame, delta.callout, current, mode)?;
        Ok(frame)
    }

    fn write_frame_action(
        &self,
        out: &mut BitWriter,
        action: Option<(u32, u32, u16, u16)>,
        current: &ProjectedSnapshot,
        mode: PayloadMode,
    ) -> Result<(), SnapshotError> {
        let Some((_, stamp, target, position)) = action else {
            out.bits(0, 2)?;
            return Ok(());
        };
        let instance = current
            .instances
            .get(&target)
            .ok_or(WireError::InvalidValue)?;
        let graph = self.graph(target, instance.graph)?;
        let node = graph
            .node(position)
            .filter(|node| node.is_action() && node.is_networked() && node.remote >= 0)
            .ok_or(WireError::InvalidValue)?;
        let (index, width) = match mode {
            PayloadMode::Owner { .. } => {
                (u64::from(node.rid), usize::from(graph.header.nodes_bits))
            }
            PayloadMode::Remote { .. } => (
                u64::try_from(node.remote).map_err(|_| WireError::InvalidValue)?,
                usize::from(graph.header.remote_bits),
            ),
        };
        let mut entry = BitWriter::new();
        entry.bit(true); // TrID present
        entry.w_u16(target);
        entry.bits(index, width)?;

        out.bit(true);
        out.w_u32(stamp);
        let entry_bits = u32::try_from(entry.bit_len()).map_err(|_| WireError::ChunkRange)?;
        out.w_var(10 + entry_bits); // RMSz excludes the group terminator
        out.bit(true); // one entry
        out.bit(false); // short length and no node payload
        out.bits(8 + u64::from(entry_bits), 8)?;
        out.append(&entry);
        out.bit(false); // no more entries
        out.bit(false); // no more groups
        out.bit(false); // no ungrouped entries
        Ok(())
    }

    fn owner_events(
        &self,
        out: &mut BitWriter,
        current: &ProjectedSnapshot,
        frame_time: u64,
    ) -> Result<(), SnapshotError> {
        // Even an empty event list replaces that instance's former queue.
        let has_lists = current.emit_events && !current.instances.is_empty();
        out.bit(has_lists);
        if !has_lists {
            return Ok(());
        }
        let mut last_id = 0;
        for (&id, instance) in &current.instances {
            out.bit(false);
            out.w_u16(id - last_id);
            last_id = id;
            let graph = self.graph(id, instance.graph)?;
            let mut events = instance.events.iter().collect::<Vec<_>>();
            events.sort_by_key(|event| event.due_ms);
            let mut last_time = frame_time;
            for event in events {
                if event.finish {
                    out.bits(2, 2)?;
                } else if event.parameter == 1 {
                    out.bits(0, 2)?;
                } else {
                    out.bits(1, 2)?;
                    out.bit(event.parameter == 0);
                    if event.parameter != 0 {
                        out.w_u16(event.parameter);
                    }
                }
                out.bits(
                    u64::from(event.raw_state),
                    usize::from(graph.header.states_bits),
                )?;
                out.bit(event.due_ms == last_time);
                if event.due_ms != last_time {
                    out.bit(event.due_ms > last_time);
                    let elapsed = u32::try_from(event.due_ms.abs_diff(last_time))
                        .map_err(|_| SnapshotError::FrameTime)?;
                    out.w_var(elapsed);
                }
                last_time = event.due_ms;
            }
            out.bits(3, 2)?;
        }
        out.bit(false);
        out.w_u16(0);
        Ok(())
    }

    fn delta_states(
        &self,
        out: &mut BitWriter,
        id: u16,
        graph: &Graph,
        changed: &BTreeMap<u16, Option<&ProjectedState>>,
        mode: PayloadMode,
    ) -> Result<(), SnapshotError> {
        let owner = matches!(mode, PayloadMode::Owner { .. });
        let mut slots = BTreeMap::new();
        if owner {
            for (&raw, &state) in changed {
                let site = StateSite {
                    instance: id,
                    graph: graph.index,
                    state: raw,
                };
                let node = graph
                    .state(usize::from(raw))
                    .filter(|node| node.is_networked())
                    .ok_or(SnapshotError::MissingState(site))?;
                slots.insert(usize::from(raw), (site, node, state));
            }
        } else {
            for index in 0..graph.remote_nodes.len() {
                if graph.remote_slot(index).is_none() {
                    continue;
                }
                let node = graph
                    .remote_node(index)
                    .expect("compiled remote slot references node");
                let raw = node.state.ok_or(SnapshotError::MissingStateIndex {
                    instance: id,
                    graph: graph.index,
                    node: node.pos,
                })?;
                if let Some(&state) = changed.get(&raw) {
                    let site = StateSite {
                        instance: id,
                        graph: graph.index,
                        state: raw,
                    };
                    slots.insert(index, (site, node, state));
                }
            }
        }
        let width = if owner {
            graph.states.len()
        } else {
            graph.remote_nodes.len()
        };
        write_mask(out, width, &slots.keys().copied().collect());
        for (site, node, state) in slots.into_values() {
            let Some(state) = state else {
                out.bit(false);
                continue;
            };
            let codec = self
                .codecs
                .codec(&node.class)
                .ok_or_else(|| SnapshotError::Payload {
                    site,
                    source: PayloadError::Unverified(node.class.clone()),
                })?;
            if codec.stec() {
                out.bit(true);
            }
            out.bit(state.active);
            if state.active || codec.stec() {
                let payload = state
                    .encode_payload(codec, mode)
                    .map_err(|source| SnapshotError::Payload { site, source })?;
                out.append(&payload);
            }
        }
        Ok(())
    }

    fn remote_states(
        &self,
        id: u16,
        graph: &Graph,
        instance: &ProjectedInstance,
        wire: &mut Instance,
    ) -> Result<(), SnapshotError> {
        for (slot, &position) in graph.remote_positions().iter().enumerate() {
            let node = graph
                .node(position)
                .expect("compiled remote positions reference nodes");
            let raw = node.state.ok_or(SnapshotError::MissingStateIndex {
                instance: id,
                graph: graph.index,
                node: position,
            })?;
            let Some(state) = instance.states.get(&raw) else {
                continue;
            };
            let site = StateSite {
                instance: id,
                graph: graph.index,
                state: raw,
            };
            let codec = self
                .codecs
                .codec(&node.class)
                .ok_or_else(|| SnapshotError::Payload {
                    site,
                    source: PayloadError::Unverified(node.class.clone()),
                })?;
            let mut bits = BitWriter::new();
            bits.bit(true); // X: present in this full frame
            if codec.stec() {
                bits.bit(true); // Y: this StEC entry has a payload
            }
            bits.bit(state.active);
            let payload = state
                .encode_payload(codec, PayloadMode::Remote { full: true })
                .map_err(|source| SnapshotError::Payload { site, source })?;
            bits.append(&payload);
            wire.states.insert(slot, bits);
        }
        Ok(())
    }
}

#[derive(Default)]
pub(super) struct SnapshotDelta<'a> {
    pub(super) variables: ProjectedVariables,
    pub(super) instances: BTreeMap<u16, InstanceDelta<'a>>,
    pub(super) keys: BTreeSet<ChangeKey>,
    pub(super) ammo_seeds: BTreeMap<(u16, u32), i64>,
    pub(super) callout: Option<(u32, u32, u16, u16)>,
}

/// Choose the next action for this stream and retain its original stamp until ACK.
fn apply_callout<'s>(
    base: &ProjectedSnapshot,
    current: &'s ProjectedSnapshot,
    touched: &BTreeSet<ChangeKey>,
    next_chunk: u32,
    delta: &mut SnapshotDelta<'_>,
) -> Option<&'s FrameAction> {
    let action = current
        .callouts
        .iter()
        .find(|action| action.callout.serial > base.callout)?;
    let callout = action.callout;
    let stamp = touched
        .iter()
        .filter_map(|key| match *key {
            ChangeKey::Callout(serial, stamp) if serial == callout.serial => Some(stamp),
            _ => None,
        })
        .min()
        .unwrap_or(next_chunk);
    delta.keys.insert(ChangeKey::Callout(callout.serial, stamp));
    delta.callout = Some((callout.serial, stamp, callout.instance, callout.position));
    Some(action)
}

impl<'s> SnapshotDelta<'s> {
    fn seed_callout(
        &mut self,
        current: &'s ProjectedSnapshot,
        delivered: &mut ProjectedSnapshot,
        action: &FrameAction,
    ) {
        for (&(owner, variable), value) in &action.values {
            let (wire, baseline) = if owner == 0 {
                (&mut self.variables, &mut delivered.variables)
            } else {
                let Some((instance, baseline)) = current
                    .instances
                    .get(&owner)
                    .zip(delivered.instances.get_mut(&owner))
                else {
                    continue;
                };
                let delta = self
                    .instances
                    .entry(owner)
                    .or_insert_with(|| InstanceDelta {
                        current: Some(instance),
                        ..Default::default()
                    });
                (&mut delta.variables, &mut baseline.variables)
            };
            wire.seed_value(variable, value.clone());
            baseline.seed_value(variable, value.clone());
            self.keys.insert(ChangeKey::Variable(owner, variable));
        }
    }

    fn seed_owner_ammo(&mut self, delivered: &mut ProjectedSnapshot) {
        // StartFiring reads the unbound, pre-volley count once after applying
        // the frame. The acknowledged snapshot must match that wire value.
        for (&(owner, variable), &ammo) in &self.ammo_seeds {
            let (wire, baseline) = if owner == 0 {
                (&mut self.variables, &mut delivered.variables)
            } else {
                (
                    &mut self
                        .instances
                        .get_mut(&owner)
                        .expect("introduced volley")
                        .variables,
                    &mut delivered
                        .instances
                        .get_mut(&owner)
                        .expect("introduced volley")
                        .variables,
                )
            };
            wire.seed_value(variable, Value::Integer(ammo));
            baseline.seed_value(variable, Value::Integer(ammo));
            self.keys.insert(ChangeKey::Variable(owner, variable));
        }
    }
}

#[derive(Default)]
pub(super) struct InstanceDelta<'a> {
    pub(super) current: Option<&'a ProjectedInstance>,
    pub(super) descriptor: bool,
    pub(super) variables: ProjectedVariables,
    pub(super) states: BTreeMap<u16, Option<&'a ProjectedState>>,
}

fn delta_variables(
    instance: u16,
    before: Option<&ProjectedVariables>,
    current: &ProjectedVariables,
    touched: &BTreeSet<ChangeKey>,
    changes: &mut BTreeSet<ChangeKey>,
) -> ProjectedVariables {
    let ids: BTreeSet<_> = current
        .values
        .keys()
        .copied()
        .chain(
            before
                .into_iter()
                .flat_map(|vars| vars.values.keys().copied()),
        )
        .chain(
            touched
                .range(ChangeKey::Variable(instance, 0)..=ChangeKey::Variable(instance, u32::MAX))
                .filter_map(|key| match key {
                    ChangeKey::Variable(_, id) => Some(*id),
                    _ => None,
                }),
        )
        .collect();
    let mut result = ProjectedVariables::default();
    for id in ids {
        let now = current.values.get(&id).unwrap_or(&Value::Null);
        let was = before
            .and_then(|vars| vars.values.get(&id))
            .unwrap_or(&Value::Null);
        let bindings = current.bindings.get(&id);
        let previous_bindings = before.and_then(|vars| vars.bindings.get(&id));
        let key = ChangeKey::Variable(instance, id);
        // Replication preserves wire types as well as values. StateScript
        // expression equality intentionally coerces nulls, booleans and numbers.
        if touched.contains(&key) || now != was || bindings != previous_bindings {
            result.values.insert(id, now.clone());
            if let Some(bindings) = bindings {
                result.bindings.insert(id, bindings.clone());
            }
            changes.insert(key);
        }
    }
    result
}

pub(super) fn write_mask(out: &mut BitWriter, width: usize, chosen: &BTreeSet<usize>) {
    if width <= 8 {
        for slot in 0..width {
            out.bit(chosen.contains(&slot));
        }
        return;
    }
    let groups: Vec<_> = (0..width)
        .step_by(8)
        .map(|start| {
            let range = start..(start + 8).min(width);
            let present = chosen.range(range.clone()).next().is_some();
            out.bit(present);
            (range, present)
        })
        .collect();
    for (slots, present) in groups {
        if present {
            for slot in slots {
                out.bit(chosen.contains(&slot));
            }
        }
    }
}

/// Test receipt for the explicit integer passed to the variable encoder in this
/// owner frame. The last acknowledged snapshot can contain values omitted here.
#[cfg(test)]
fn explicit_rifle_ammo<'a>(
    records: impl Iterator<Item = (&'a ProjectedInstance, &'a ProjectedVariables)>,
) -> Option<i64> {
    let mut result = None;
    for (body, variables) in records {
        if body.graph == 0x0254 && body.parent.is_none() {
            if let Some(Value::Integer(ammo)) = variables.values.get(&53) {
                assert!(
                    result.replace(*ammo).is_none(),
                    "ambiguous owner rifle write"
                );
            }
        }
    }
    result
}

fn write_delta_variables(
    out: &mut BitWriter,
    owner: u16,
    graph: &Graph,
    vars: &ProjectedVariables,
) -> Result<(), WireError> {
    let mut sync = BTreeMap::new();
    let mut extra = vars.values.clone();
    for (slot, var) in graph.sync_vars.iter().enumerate() {
        let Some(id) = var.0.filter(|_| var.1 == 0) else {
            continue;
        };
        if let Some(value) = extra.swap_remove(&id) {
            sync.insert(slot, (id, value));
        }
    }
    write_mask(out, graph.sync_vars.len(), &sync.keys().copied().collect());
    for (id, value) in sync.values() {
        let links = vars.bindings.get(id).map_or(&[][..], Vec::as_slice);
        script_wire::bound_variable(out, value, ValueEncoding::Compact, owner, links)?;
    }
    script_wire::bound_variable_list(out, &extra, ValueEncoding::Compact, owner, &vars.bindings)
}
