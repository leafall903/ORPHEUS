//! Observer effects reached when a landed projectile finishes its arming wait.

use super::*;
use crate::script_graph::{Node, ProjectileDefinition, asset_guid};
use serde_json::Value as Json;

#[derive(Debug)]
pub(crate) struct ArmingEffects {
    pub(crate) ticks: u32,
    roots: Vec<(u32, Vec<u16>)>,
    inhibit: Option<u32>,
}

fn linked<'a>(graph: &'a Graph, from: &Node, plug: &str) -> Vec<&'a Node> {
    from.links(plug)
        .filter_map(|(pos, input)| (input == "m_beginPlug").then_some(pos).flatten())
        .filter_map(|pos| graph.node(pos))
        .collect()
}

fn literal_seconds(expr: &Json) -> Option<f64> {
    matches!(expr["$"].as_str()?, "STUConfigVarFloat" | "STUConfigVarInt")
        .then(|| expr["m_value"].as_f64())
        .flatten()
        .filter(|v| v.is_finite() && *v >= 0.0)
}

pub(crate) fn runs_at_creation(graph: &Graph, assignment: u16) -> bool {
    let mut todo: Vec<_> = graph.entries.iter().flatten().copied().collect();
    let mut visited = BTreeSet::new();
    while let Some(pos) = todo.pop() {
        if !visited.insert(pos) {
            continue;
        }
        let Some(node) = graph.node(pos) else {
            continue;
        };
        if pos == assignment {
            return true;
        }
        if matches!(
            node.class.as_str(),
            "STUStatescriptEntry" | "STUStatescriptActionSetVar" | "STU_12F5D52D"
        ) {
            todo.extend(
                node.links("m_outPlug")
                    .filter_map(|(next, input)| (input == "m_inPlug").then_some(next).flatten()),
            );
        }
    }
    false
}

/// Follow a uniquely initialized graph register to its authored expression.
pub(crate) fn initial_operand<'a>(graph: &'a Graph, operand: &'a Json) -> Option<&'a Json> {
    let Some(var) = crate::script_expr::lvalue(operand).ok().flatten() else {
        return Some(operand);
    };
    let mut writes = graph
        .nodes
        .iter()
        .filter(|n| n.class == "STU_12F5D52D")
        .flat_map(|node| {
            node.field("m_143D4C5B")
                .and_then(Json::as_array)
                .into_iter()
                .flatten()
                .map(move |row| (node.pos, row))
        })
        .filter(|(_, row)| {
            crate::script_expr::lvalue(&row["m_out_Var"]).ok().flatten() == Some(var)
        });
    let (pos, row) = writes.next()?;
    if writes.next().is_some() || !runs_at_creation(graph, pos) {
        return None;
    }
    row.get("m_value")
}

fn wait_ticks(graph: &Graph, wait: &Node) -> Option<u32> {
    let seconds = literal_seconds(initial_operand(graph, wait.field("m_timeout")?)?)?;
    let ticks = (seconds * 1000.0 / crate::script_runtime::FRAME_MS as f64).ceil();
    (ticks <= f64::from(u32::MAX)).then_some(ticks as u32)
}

fn effect_states(graph: &Graph, branch: &[&Node]) -> Option<Vec<u16>> {
    if !branch
        .iter()
        .any(|n| n.class == "STUStatescriptStateTrackTargets")
    {
        return None;
    }
    let mut stack = branch.to_vec();
    let mut states = BTreeSet::new();
    while let Some(node) = stack.pop() {
        if node.class != "STU_691BFA55" {
            continue;
        }
        let effect = asset_guid(node.field("m_effect.m_effect")?)?;
        let state = node.state?;
        if effect >> 48 != 0x0300
            || !graph.remote_positions().contains(&node.pos)
            || !states.insert(state)
        {
            return None;
        }
        stack.extend(linked(graph, node, "m_subgraphPlug"));
    }
    (!states.is_empty()).then(|| states.into_iter().collect())
}

pub(crate) fn inactive_gate(gate: &Node) -> Option<Option<u32>> {
    let condition = gate.field("m_condition")?;
    if condition["$"] == "STUConfigVarBool" {
        let value = match &condition["m_value"] {
            Json::Bool(v) => *v,
            Json::Number(v) => v.as_u64()? != 0,
            _ => return None,
        };
        return (!value).then_some(None);
    }
    let variable = crate::script_expr::lvalue(condition).ok()??;
    (variable.scope == Scope::Entity).then_some(Some(variable.id))
}

fn next_states<'a>(graph: &'a Graph, parents: &[&Node], plug: &str, class: &str) -> Vec<&'a Node> {
    parents
        .iter()
        .flat_map(|node| linked(graph, node, plug))
        .filter(|node| node.class == class)
        .collect()
}

struct ArmingRoute {
    ticks: u32,
    effects: Vec<u16>,
    inhibit: Option<u32>,
}

fn arming_route(graph: &Graph) -> Result<Option<ArmingRoute>, ()> {
    let entries: Vec<_> = graph
        .nodes
        .iter()
        .filter(|node| node.class == "STUStatescriptEntryGameMessage")
        .collect();
    let lifetime = next_states(graph, &entries, "m_outPlug", "STUStatescriptStateWait");
    let timers = next_states(
        graph,
        &lifetime,
        "m_subgraphPlug",
        "STUStatescriptStateWait",
    );
    let mut route = None;
    for timer in timers {
        for gate in next_states(
            graph,
            &[timer],
            "m_onFinishedPlug",
            "STUStatescriptStateBooleanSwitch",
        ) {
            let effects = effect_states(graph, &linked(graph, gate, "m_falseSubgraphPlug"));
            if let Some(effects) = effects {
                if route.is_some() || !linked(graph, gate, "m_trueSubgraphPlug").is_empty() {
                    return Err(());
                }
                route = Some(ArmingRoute {
                    ticks: wait_ticks(graph, timer).ok_or(())?,
                    effects,
                    inhibit: inactive_gate(gate).ok_or(())?,
                });
            }
        }
    }
    Ok(route)
}

impl ArmingEffects {
    pub(crate) fn from_definition(
        graphs: &GraphStore,
        body: &ProjectileDefinition,
    ) -> Option<Self> {
        if body.graphs.len() > usize::from(u16::MAX) {
            return None;
        }
        let components = &body.components;
        if ![
            "STUPetComponent",
            "STUProjectileVisualComponent",
            "STUStatescriptComponent",
        ]
        .iter()
        .all(|name| components.contains(*name))
        {
            return None;
        }
        let mut route = None;
        let mut roots = Vec::new();
        for binding in &body.graphs {
            let graph = graphs.graph(binding.graph?)?;
            if binding.flags != 0
                || binding
                    .sync
                    .as_ref()
                    .is_some_and(|overrides| !overrides.is_empty())
            {
                return None;
            }
            let states = if let Some(arming) = arming_route(graph).ok()? {
                if route.replace((arming.ticks, arming.inhibit)).is_some() {
                    return None;
                }
                arming.effects
            } else {
                Vec::new()
            };
            roots.push((graph.index, states));
        }
        let (ticks, inhibit) = route?;
        Some(Self {
            ticks,
            roots,
            inhibit,
        })
    }

    pub(crate) fn enabled(&self, snapshot: Option<&Snapshot>) -> bool {
        self.inhibit
            .and_then(|id| {
                let snapshot = snapshot?;
                snapshot
                    .resolved_bindings
                    .get(&(0, id))
                    .or_else(|| snapshot.variables.get(&id))
            })
            .is_none_or(|value| !crate::script_expr::truthy(value))
    }

    pub(crate) fn snapshot(&self, entity: u32) -> ProjectedSnapshot {
        let instances = self
            .roots
            .iter()
            .enumerate()
            .map(|(i, (graph, states))| {
                let states = states
                    .iter()
                    .map(|&raw| {
                        (
                            raw,
                            ProjectedState {
                                active: true,
                                payload: StatePayload::from([(
                                    "counter".into(),
                                    Value::Integer(1),
                                )]),
                                link: None,
                                volley: None,
                            },
                        )
                    })
                    .collect();
                (
                    (i + 1) as u16,
                    ProjectedInstance {
                        graph: *graph,
                        start_ms: 0,
                        parent: None,
                        variables: ProjectedVariables::default(),
                        states,
                        events: Vec::new(),
                    },
                )
            })
            .collect();
        ProjectedSnapshot {
            entity,
            view: View::Observer,
            entity_exclusions: BTreeSet::new(),
            variables: ProjectedVariables::default(),
            instances,
            callout: 0,
            callouts: Vec::new(),
            emit_events: false,
        }
    }
}
