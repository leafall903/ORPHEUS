//! Lower a landed projectile's two-stage proximity query from its bound StateScript.
use super::*;
use crate::script_expr::{self, Context, EntityRelation};
use crate::script_graph::{Graph, Node, ProjectileDefinition};
use crate::script_snapshot_wire::initial_operand;

#[derive(Clone, Debug)]
pub(super) struct Proximity {
    pub(super) arm_ticks: u32,
    pub(super) inhibit: Option<u32>,
    pub(super) lifetime: u32,
    pub(super) health: f64,
    pub(super) trigger_radius: f64,
    pub(super) damage_radius: f64,
    pub(super) hit: crate::game_hitscan::HitDefinition,
    pub(super) impulse: Option<crate::script_init::Knockback>,
}

pub(super) struct Inputs<'a> {
    pub(super) entity: u32,
    pub(super) creator: u32,
    pub(super) frame: Option<&'a RoomSpatialFrame>,
    pub(super) rules: &'a GraphStore,
    pub(super) mode: u64,
    pub(super) hero: u64,
    pub(super) tick: u32,
}

struct Values<'a> {
    graph: &'a Graph,
    inputs: &'a Inputs<'a>,
    sample: u64,
}

impl Context for Values<'_> {
    fn owner(&mut self) -> Value {
        Value::Entity(self.inputs.entity)
    }
    fn related_entity(&mut self, relation: EntityRelation, entity: u32) -> Option<u32> {
        (relation == EntityRelation::CreatorBody && entity == self.inputs.entity)
            .then_some(self.inputs.creator)
    }
    fn entity_member(&mut self, entity: u32, id: u32) -> Option<Value> {
        self.inputs.frame?.actor_variable(entity, id)
    }
    fn entity_ruleset(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity != self.inputs.creator || self.inputs.hero == 0 {
            return None;
        }
        self.inputs
            .rules
            .mode_ruleset_value(self.inputs.mode, self.inputs.hero, id)
    }
    fn ruleset(&mut self, id: u32) -> Value {
        crate::body_ruleset::get(
            self.inputs.rules,
            self.inputs.mode,
            self.inputs.hero,
            crate::body_ruleset::ActorKind::Player,
            id,
        )
        .unwrap_or(Value::Null)
    }
    fn random_u64(&mut self) -> Option<u64> {
        let draw = crate::script_init::component_random_at(
            self.inputs.entity,
            self.inputs.tick,
            self.sample,
        );
        self.sample += 1;
        Some(draw)
    }
}

impl Values<'_> {
    fn eval(&mut self, field: &Json) -> Result<Value, script_expr::EvalError> {
        let operand = initial_operand(self.graph, field).ok_or(
            script_expr::EvalError::MalformedConfig("unresolved projectile initializer"),
        )?;
        let initial = script_expr::evaluate(operand, self)?;
        let Some(var) = script_expr::lvalue(field)? else {
            return Ok(initial);
        };
        // A creation-time conditional can overwrite the initialized register. Follow
        // its actual guard, including mode overrides of an otherwise finite lifetime.
        let mut writes = self.graph.nodes.iter().filter(|node| {
            node.class == "STUStatescriptActionSetVar"
                && node
                    .field("m_out_Var")
                    .and_then(|v| script_expr::lvalue(v).ok().flatten())
                    == Some(var)
        });
        let Some(write) = writes.next() else {
            return Ok(initial);
        };
        let unknown = || {
            script_expr::EvalError::MalformedConfig("unresolved conditional projectile initializer")
        };
        if writes.next().is_some() {
            return Err(unknown());
        }
        let mut guards = self
            .graph
            .nodes
            .iter()
            .filter(|node| {
                node.class == "STU_3387AB5D"
                    && crate::script_snapshot_wire::runs_at_creation(self.graph, node.pos)
            })
            .flat_map(|node| {
                [("m_truePlug", true), ("m_falsePlug", false)]
                    .into_iter()
                    .filter_map(move |(plug, desired)| {
                        node.links(plug)
                            .any(|(p, input)| p == Some(write.pos) && input == "m_inPlug")
                            .then_some((node, desired))
                    })
            });
        let (gate, branch) = guards.next().ok_or_else(unknown)?;
        if guards.next().is_some() {
            return Err(unknown());
        }
        let condition =
            script_expr::evaluate(gate.field("m_condition").ok_or_else(unknown)?, self)?;
        if condition == Value::Null {
            return Err(unknown());
        }
        if script_expr::truthy(&condition) == branch {
            script_expr::evaluate(write.field("m_value").ok_or_else(unknown)?, self)
        } else {
            Ok(initial)
        }
    }
    fn number(&mut self, field: &Json) -> Option<f64> {
        let value = self.eval(field).ok()?;
        if !matches!(value, Value::Integer(_) | Value::Float(_)) {
            return None;
        }
        let number = script_expr::to_float(&value);
        number.is_finite().then_some(number)
    }
}

fn child<'a>(graph: &'a Graph, node: &Node, path: &str, class: &str) -> Option<&'a Node> {
    let mut connected = node.links(path).filter_map(|(pos, input)| {
        if !matches!(input, "m_inPlug" | "m_beginPlug") {
            return None;
        }
        graph.node(pos?).filter(|n| n.class == class)
    });
    let child = connected.next()?;
    connected.next().is_none().then_some(child)
}

fn seconds_to_ticks(seconds: f64) -> Option<u32> {
    let ticks = (seconds * 1000.0 / crate::script_runtime::FRAME_MS as f64).ceil();
    (ticks.is_finite() && (0.0..=f64::from(u32::MAX)).contains(&ticks)).then_some(ticks as u32)
}

fn after_first_target(trigger: &Node, branch: &Node) -> bool {
    let Some(condition) = branch.field("m_condition") else {
        return false;
    };
    if condition["$"] == "STUConfigVarBool" {
        return condition["m_value"] == true;
    }
    let Some(count) = trigger
        .field("m_1B89EA79")
        .and_then(|v| script_expr::lvalue(v).ok().flatten())
    else {
        return false;
    };
    let program = &condition["m_expression"];
    // LOAD_VAR 0, LOAD_NUMBER 0, GREATER_THAN, END. Both operands belong to the graph.
    program["m_opcodes"] == serde_json::json!([8, 0, 6, 0, 36, 0])
        && program["m_D99EF254"]
            .as_array()
            .is_some_and(|a| a.len() == 1 && a[0].as_f64() == Some(0.0))
        && program["m_dynamicVars"].as_array().is_some_and(|a| {
            a.len() == 1 && script_expr::lvalue(&a[0]).ok().flatten() == Some(count)
        })
}

impl Proximity {
    pub(super) fn from_definition(body: &ProjectileDefinition, inputs: Inputs<'_>) -> Option<Self> {
        if !body.components.contains("STUHealthComponent")
            || !body.components.contains("STUStatescriptComponent")
        {
            return None;
        }
        let mut profile = None;
        for binding in &body.graphs {
            if binding.flags != 0 || binding.sync.as_ref().is_some_and(|rows| !rows.is_empty()) {
                return None;
            }
            let graph = inputs.rules.graph(binding.graph?)?;
            for entry in graph
                .nodes
                .iter()
                .filter(|n| n.class == "STUStatescriptEntryGameMessage")
            {
                let Some(life) = child(graph, entry, "m_outPlug", "STUStatescriptStateWait") else {
                    continue;
                };
                for (_, arm) in life
                    .links("m_subgraphPlug")
                    .filter_map(|(p, input)| Some((input, graph.node(p?)?)))
                    .filter(|(input, n)| {
                        *input == "m_beginPlug" && n.class == "STUStatescriptStateWait"
                    })
                {
                    let Some(gate) = child(
                        graph,
                        arm,
                        "m_onFinishedPlug",
                        "STUStatescriptStateBooleanSwitch",
                    ) else {
                        continue;
                    };
                    let inhibit = crate::script_snapshot_wire::inactive_gate(gate)?;
                    let Some(trigger) = child(
                        graph,
                        gate,
                        "m_falseSubgraphPlug",
                        "STUStatescriptStateTrackTargets",
                    ) else {
                        continue;
                    };
                    let condition = child(graph, trigger, "m_transitionPlug[0]", "STU_3387AB5D")?;
                    if !after_first_target(trigger, condition)
                        || condition.links("m_falsePlug").next().is_some()
                    {
                        return None;
                    }
                    let recipients = child(
                        graph,
                        condition,
                        "m_truePlug",
                        "STUStatescriptStateTrackTargets",
                    )?;
                    let modify = child(
                        graph,
                        recipients,
                        "m_transitionPlug[0]",
                        "STUStatescriptStateModifyHealth",
                    )?;
                    let mut pools = graph.nodes.iter().filter(|n| {
                        n.class == "STUStatescriptStateHealthPool"
                            && n.field("m_FA3ECC43").and_then(|v| v["$"].as_str())
                                == Some("STU_B5A0CAF0")
                    });
                    let pool = pools.next()?;
                    if profile.is_some() || pools.next().is_some() {
                        return None;
                    }
                    let mut values = Values {
                        graph,
                        inputs: &inputs,
                        sample: 0,
                    };
                    let health = values.number(pool.field("m_amount")?)?;
                    let arm_ticks = seconds_to_ticks(values.number(arm.field("m_timeout")?)?)?;
                    let lifetime = seconds_to_ticks(values.number(life.field("m_timeout")?)?)?;
                    let trigger_radius = values.number(trigger.field("m_radius")?)?;
                    let damage_radius = values.number(recipients.field("m_radius")?)?;
                    let hit =
                        crate::game_hitscan::HitDefinition::from_authored(&modify.fields, |v| {
                            values.eval(v)
                        })
                        .ok()??;
                    if health <= 0.0
                        || hit.amount <= 0.0
                        || trigger_radius <= 0.0
                        || damage_radius <= 0.0
                    {
                        return None;
                    }
                    let impulse = crate::script_init::Knockback::from_authored(
                        modify.field("m_modifyHealth")?,
                        &mut |v| values.eval(v),
                    );
                    profile = Some(Self {
                        arm_ticks,
                        inhibit,
                        lifetime,
                        health,
                        trigger_radius,
                        damage_radius,
                        hit,
                        impulse,
                    });
                }
            }
        }
        profile
    }
}

#[cfg(test)]
pub(super) fn fixture(guid: u64) -> Arc<GraphStore> {
    fixture_with_lifetime_override(guid, false)
}

#[cfg(test)]
fn fixture_with_lifetime_override(guid: u64, override_lifetime: bool) -> Arc<GraphStore> {
    use serde_json::json;
    let schema: Json = serde_json::from_str(include_str!(
        "../../../../fixtures/behavior/projectile-impact-spawn-v1.json"
    ))
    .unwrap();
    let f = &schema["proximity"];
    let number = |field: &str| json!({"$":"STUConfigVarFloat","m_value": f[field]});
    let plug =
        |pos: u16, input: &str| json!({"$":"STUStatescriptOutputPlug","links":[[pos,input]]});
    let state = |pos| plug(pos, "m_beginPlug");
    let lifetime = json!({"$":"STUConfigVarDynamic","m_identifier":321,"m_60DB8F99":0});
    let definitions = [
        (
            "STUStatescriptEntryGameMessage",
            json!({"m_outPlug":state(1)}),
        ),
        (
            "STUStatescriptStateWait",
            json!({"m_timeout":lifetime,"m_subgraphPlug":state(2)}),
        ),
        (
            "STUStatescriptStateWait",
            json!({"m_timeout":number("arm_seconds"),"m_onFinishedPlug":state(3)}),
        ),
        (
            "STUStatescriptStateBooleanSwitch",
            json!({"m_condition":{"$":"STUConfigVarBool","m_value":false},"m_falseSubgraphPlug":state(4)}),
        ),
        (
            "STUStatescriptStateTrackTargets",
            json!({"m_radius":number("trigger_radius"),"m_transitionPlug":[plug(5,"m_inPlug")]}),
        ),
        (
            "STU_3387AB5D",
            json!({"m_condition":{"$":"STUConfigVarBool","m_value":true},"m_truePlug":state(6)}),
        ),
        (
            "STUStatescriptStateTrackTargets",
            json!({"m_radius":number("damage_radius"),"m_transitionPlug":[state(7)]}),
        ),
        (
            "STUStatescriptStateModifyHealth",
            json!({"m_modifyHealth":{"m_amount":number("damage")}}),
        ),
        (
            "STUStatescriptStateHealthPool",
            json!({"m_FA3ECC43":{"$":"STU_B5A0CAF0"},"m_amount":number("health")}),
        ),
        (
            "STUStatescriptEntry",
            json!({"m_outPlug":plug(10,"m_inPlug")}),
        ),
        (
            "STU_12F5D52D",
            json!({"m_143D4C5B":[{"m_out_Var":lifetime,"m_value":number("lifetime")}],
                                 "m_outPlug":plug(11,"m_inPlug")}),
        ),
        (
            "STU_3387AB5D",
            json!({"m_condition":{"$":"STUConfigVarBool","m_value":override_lifetime},
                                "m_truePlug":plug(12,"m_inPlug"),"m_falsePlug":state(8)}),
        ),
        (
            "STUStatescriptActionSetVar",
            json!({"m_out_Var":lifetime,
            "m_value":{"$":"STUConfigVarFloat","m_value":8.0},"m_outPlug":state(8)}),
        ),
    ];
    let mut states = Vec::new();
    let nodes: Vec<_> = definitions.into_iter().enumerate().map(|(pos, (class, mut fields))| {
        let is_state = class.starts_with("STUStatescriptState");
        let state = is_state.then_some(states.len());
        if is_state {
            states.push(pos);
        }
        fields["m_2BBEEAB8"] = json!(is_state);
        fields["m_ADEB6E05"] = json!(false);
        json!({"pos":pos,"uid":pos,"rid":pos,"remote":-1,"state":state,"class":class,"fields":fields,"server_only":false,"client_only":false})
    }).collect();
    let graph = json!({"index":30,"items":nodes.len(),"nodes":nodes,"states":states,"entries":[9,0],"remote":[],"sync_vars":[],"unknown_nodes":[],"fields":{},"containers":{},"header":{"m_nodesBitCount":4,"m_statesBitCount":4,"m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}});
    let bundle = json!({"version":1,"bodies":{},"graphs":[graph],"missing":[],"persistent_projectiles":{(format!("0x{guid:016X}")):{"components":["STUHealthComponent","STUTargetTagComponent","STUStatescriptComponent"],"graphs":[{"graph":30,"m_75408640":0}]}}});
    Arc::new(GraphStore::from_json(bundle.to_string().as_bytes()).unwrap())
}

#[cfg(test)]
#[test]
fn unrelated_projectile_uses_separate_trigger_and_damage_volumes() {
    let guid = 0x0400_0000_0000_617a;
    for (enabled, expected_ticks) in [(false, 1125), (true, 500)] {
        let graphs = fixture_with_lifetime_override(guid, enabled);
        let profile = Proximity::from_definition(
            graphs.persistent_projectile(guid).unwrap(),
            Inputs {
                entity: 32,
                creator: 18,
                frame: None,
                rules: &graphs,
                mode: 0,
                hero: 0,
                tick: 37,
            },
        )
        .unwrap();
        assert_eq!(
            (profile.trigger_radius, profile.damage_radius),
            (1.25, 2.75)
        );
        assert_eq!((profile.arm_ticks, profile.lifetime), (94, expected_ticks));
        assert!(profile.impulse.is_none());
    }
}
