//! StateScript VM integration tests that exercise root server, vitals, and combat modules.

use std::{path::PathBuf, sync::Arc};

use orpheus_wire::commands::Command;
use serde_json::{Value as Json, json};

use crate::{
    pve_encounter::messages::DefenderMessage,
    pve_encounter::voice::{IntroCue, intro_cue, victory_cue},
    script_expr::{self, EvalError, Scope},
    script_graph::GraphStore,
    script_init::{
        ABORT_PLUG, Component, HUD_MAX_HEALTH, MAX_INIT_WORK, OWNER_HEALTH, Root, Site, Snapshot,
        StateKind, World, player_entity_variables, player_roots,
    },
    script_wire::{Value, Variables},
    vitals::{Death, PoolKind, Vitals},
    world::GAME_MODE_ENTITY,
};

fn node(position: u16, class: &str, state: Option<u16>, mut fields: Json) -> Json {
    fields["m_2BBEEAB8"] = json!(state.is_some());
    fields["m_ADEB6E05"] = json!(
        class.contains("Action")
            || matches!(
                class,
                "STU_12F5D52D" | "STU_22926252" | "STU_DA54F8F7" | "STU_4C3509FB" | "STU_CBE8BDAA"
            )
    );
    json!({
        "pos":position, "class":class, "uid":position, "rid":position, "remote":-1,
        "state":state, "client_only":false, "server_only":false, "fields":fields,
    })
}

fn graph(index: u32, nodes: Vec<Json>, states: Json, entries: Json) -> Json {
    json!({
        "index":index, "items":nodes.len(), "nodes":nodes, "states":states,
        "entries":entries, "remote":[], "sync_vars":[], "containers":{},
        "unknown_nodes":[], "fields":{},
        "header":{
            "m_nodesBitCount":4, "m_statesBitCount":4, "m_remoteSyncNodesBitCount":4,
            "m_syncVarsBitCount":4
        }
    })
}

fn store(graphs: Vec<Json>) -> GraphStore {
    GraphStore::from_json(
        &serde_json::to_vec(&json!({
            "version":1, "graphs":graphs, "bodies":{}, "missing":[]
        }))
        .unwrap(),
    )
    .unwrap()
}

fn plug(links: &[(u16, &str)], states: &[u16]) -> Json {
    json!({"$":"Plug","links":links,"m_FF3DAF1E":states})
}

fn var(id: u32, entity: bool) -> Json {
    json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":entity})
}

fn active(snapshot: &Snapshot, id: u16) -> Vec<u16> {
    snapshot.instances[&id]
        .states
        .iter()
        .filter_map(|(&raw, state)| state.active.then_some(raw))
        .collect()
}

struct RifleWorld(Arc<GraphStore>);

impl World for RifleWorld {
    fn ruleset(&mut self, id: u32) -> Option<Value> {
        match id {
            0x2538 | 0x2819 | 0x32e8 => Some(Value::Float(1.0)),
            0x260f => Some(Value::Bool(true)),
            0x281a | 0x32e0 | 0x32e9 => Some(Value::Bool(false)),
            _ => None,
        }
    }

    fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
        if entity != GAME_MODE_ENTITY {
            return None;
        }
        self.0
            .mode_variables(0x0230_0000_0000_0018)
            .map(|values| values.get(&id).copied().map_or(Value::Null, Value::from))
    }

    fn native(&mut self, site: Site, config: &Json) -> Option<Value> {
        match config.get("$")?.as_str()? {
            "STU_1FF67CA6" => Some(Value::Entity(site.entity)),
            "STU_310D6529" => Some(Value::Vector([0.0; 3])),
            "STU_37F20D24" => Some(Value::Asset(0x0230_0000_0000_0018)),
            "STU_A3DEF368" if self.0.mode_variables(0x0230_0000_0000_0018).is_some() => {
                Some(Value::Entity(GAME_MODE_ENTITY))
            }
            "STU_D3EB8D87" | "STU_A77AC2AC" => Some(Value::Null),
            "STU_4A101787" => Some(Value::Bool(false)),
            _ => None,
        }
    }

    fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
        None
    }
}

mod tests {
    use super::*;
    use crate::script_init::AbilityCooldown;

    #[test]
    fn shared_charge_contract() {
        fn guid(value: &Json) -> u64 {
            u64::from_str_radix(value.as_str().unwrap().trim_start_matches("0x"), 16).unwrap()
        }
        fn decode(value: &Json) -> Value {
            if let Some(asset) = value.get("asset") {
                Value::Asset(guid(asset))
            } else if let Some(entity) = value.get("entity").and_then(Json::as_u64) {
                Value::Entity(entity.try_into().unwrap())
            } else if let Some(flag) = value.as_bool() {
                Value::Bool(flag)
            } else if let Some(number) = value.as_f64() {
                Value::Float(number)
            } else {
                assert!(value.is_null());
                Value::Null
            }
        }
        struct Host<'a> {
            owner: u32,
            target: u32,
            case: &'a Json,
        }
        impl World for Host<'_> {
            fn position(&mut self, _: u32) -> Option<[f64; 3]> {
                None
            }
            fn entities_hostile(
                &self,
                _: Site,
                source: u32,
                target: u32,
                option: bool,
            ) -> Option<bool> {
                if source != self.owner
                    || target != self.target
                    || option
                    || self.case["context"] == false
                {
                    return None;
                }
                crate::pve_encounter::host::ordinary_hostility(
                    self.case["source_flags"].as_u64().unwrap_or(0x800001),
                    self.case["target_flags"].as_u64().unwrap_or(0x1000001),
                )
            }
            fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
                (entity == self.target).then(|| {
                    if id == 602 {
                        Value::Bool(self.case["no_charge"].as_bool().unwrap_or(false))
                    } else {
                        Value::Null
                    }
                })
            }
            fn has_target_tag(&self, _: Site, entity: u32, tag: u64) -> Option<bool> {
                (entity == self.target).then(|| {
                    self.case["target_tags"]
                        .as_array()
                        .is_some_and(|rows| rows.iter().any(|row| guid(row) == tag))
                })
            }
        }
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-charge-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture["schema_version"], 1);
        assert_eq!(fixture["case_id"], "statescript.charge.v1");
        assert_eq!(fixture["target_build"], 104319);
        let cases = fixture["input"]["cases"].as_array().unwrap();
        let results = fixture["expected"]["results"].as_array().unwrap();
        assert_eq!(cases.len(), results.len());
        for (case, expected) in cases.iter().zip(results) {
            assert_eq!(case["name"], expected["name"]);
            if case["kind"] == "corpse" {
                let actual = crate::pve_encounter::host::common_corpse_due(
                    guid(&case["hero"]),
                    case["death_frame"]
                        .as_u64()
                        .map(|value| u32::try_from(value).unwrap()),
                    u32::try_from(case["frame"].as_u64().unwrap()).unwrap(),
                );
                assert_eq!(
                    json!({"retire":actual}),
                    expected["output"],
                    "{}",
                    case["name"]
                );
                continue;
            }
            let owner = fixture["input"]["owner"].as_u64().unwrap() as u32;
            let target = fixture["input"]["target"].as_u64().unwrap() as u32;
            let mut world = Host {
                owner,
                target,
                case,
            };
            let graphs = Arc::new(store(vec![fixture["input"]["graph"].clone()]));
            let mut variables = Variables::from([
                (338, Value::Float(200.0)),
                (342, Value::Float(0.0)),
                (2426, Value::Bool(false)),
                (842, Value::Bool(false)),
            ]);
            if let Some(initial) = case["initial"].as_object() {
                for (key, value) in initial {
                    variables.insert(key.parse().unwrap(), decode(value));
                }
            }
            let mut root = Root::new(1, 0x0b8b);
            root.variables.insert(393, Value::Entity(target));
            root.variables.insert(1, decode(&case["variable"]));
            let mut component = Component::start(
                graphs,
                owner,
                variables,
                vec![root],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let actual = match case["kind"].as_str().unwrap() {
                "query" => {
                    match component.evaluate_state_config(1, 0, &case["query"], &mut world) {
                        Ok(Value::Bool(value)) => json!({"hostile":value}),
                        Err(EvalError::UnsupportedConfig(class)) => json!({"error":class}),
                        result => panic!("{}: {result:?}", case["name"]),
                    }
                }
                "tags" => {
                    use crate::{
                        game_hitscan::{DamageTags, HitDefinition, HitscanShot, TargetPose},
                        projectile_flight::{Blast, ProjectileBallistics, ProjectileFlight},
                    };
                    let hit = HitDefinition::from_authored(&case["hit"], |cfg| {
                        component.evaluate_state_config(1, 0, cfg, &mut world)
                    })
                    .unwrap()
                    .unwrap();
                    if case["name"] == "damage-tags-budget-sixteen" {
                        let mut oversized = case["hit"].clone();
                        let rows = oversized["m_modifyHealth"]["m_148F3152"]
                            .as_array_mut()
                            .unwrap();
                        rows.push(rows[0].clone());
                        assert!(
                            HitDefinition::from_authored(&oversized, |cfg| component
                                .evaluate_state_config(1, 0, cfg, &mut world))
                            .unwrap()
                            .is_none(),
                            "over-budget tags must reject the payload, never truncate it"
                        );
                    }
                    let expected_tags = expected["output"]["tags"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(guid)
                        .collect::<Vec<_>>();
                    assert_eq!(hit.tags.as_slice(), expected_tags, "{}", case["name"]);
                    let pose = TargetPose {
                        feet: [0.0, 0.0, 3.0],
                        height: 2.0,
                        radius: 0.5,
                        eye: 1.55,
                    };
                    let shot =
                        HitscanShot::with_direction([0.0, 1.0, 0.0], [0.0, 0.0, 1.0], 100.0, hit);
                    let impact = shot
                        .resolve([(target, pose)], |_, _, _| Ok(None))
                        .unwrap()
                        .unwrap();
                    assert_eq!(impact.tags, hit.tags, "hitscan must retain tags");
                    let mut flight = ProjectileFlight::new(
                        ProjectileBallistics {
                            speed: 50.0,
                            lifetime: 2.0,
                            max_distance: None,
                            terrain_rebound: None,
                            terrain_radius: 0.0,
                            radius: 0.0,
                            definition: None,
                            direct: Some(hit),
                            blast: Some(Blast {
                                hit: HitDefinition {
                                    tags: DamageTags::EMPTY,
                                    ..hit
                                },
                                rings: vec![(1.0, 1.0)],
                                self_damage_scale: 0.0,
                                self_knockback_scale: 0.0,
                                knockback: None,
                                detonate_on_expiry: false,
                            }),
                        },
                        [0.0, 1.0, 0.0],
                        [0.0, 0.0, 1.0],
                        None,
                    )
                    .unwrap();
                    assert!(
                        flight
                            .advance(0, &[(target, pose)], |_, _, _| Ok(None))
                            .unwrap()
                            .is_none()
                    );
                    let end = flight
                        .advance(100, &[(target, pose)], |_, _, _| Ok(None))
                        .unwrap()
                        .unwrap();
                    assert_eq!(
                        end.impacts[0].tags, hit.tags,
                        "direct projectile must retain its payload tags"
                    );
                    assert!(end.impacts.len() > 1);
                    assert!(
                        end.impacts[1..]
                            .iter()
                            .all(|impact| impact.tags == DamageTags::EMPTY),
                        "blast must retain its own tags"
                    );
                    json!({"tags":hit.tags.as_slice().iter().map(|guid|format!("0x{guid:016X}")).collect::<Vec<_>>()})
                }
                "flow" => {
                    let mut observed = Vec::new();
                    let mut last_frame = 0;
                    for step in case["steps"].as_array().unwrap() {
                        match step["op"].as_str().unwrap() {
                            "set" => {
                                for (key, value) in step["values"].as_object().unwrap() {
                                    component
                                        .set_entity_variable(
                                            key.parse().unwrap(),
                                            decode(value),
                                            &mut world,
                                        )
                                        .unwrap();
                                }
                                component.run_now(&mut world).unwrap();
                            }
                            "advance" => {
                                let frame = step["frame"].as_u64().unwrap() as u32;
                                for selected in last_frame + 1..=frame {
                                    component.advance_frame(selected, &mut world).unwrap();
                                }
                                assert!(frame > last_frame);
                                last_frame = frame;
                            }
                            "damage" => {
                                component
                                    .queue_message(
                                        1,
                                        0x0240_0000_0000_0012,
                                        Variables::from([
                                            (0x13, Value::Float(step["amount"].as_f64().unwrap())),
                                            (0x15, Value::Entity(target)),
                                        ]),
                                    )
                                    .unwrap();
                                component.run_now(&mut world).unwrap();
                            }
                            "poll" => component.run_now(&mut world).unwrap(),
                            "observe" => (),
                            op => panic!("unknown operation {op}"),
                        }
                        let snapshot = component.snapshot().unwrap();
                        assert!(
                            snapshot.initialization_complete(),
                            "{}: {:?}",
                            case["name"],
                            snapshot.diagnostics
                        );
                        observed.push(json!({"charge_bits":format!("{:016x}",script_expr::to_float(&snapshot.variables[&342]).to_bits()),"waiting":snapshot.instances[&1].states.get(&4).is_some_and(|state|state.active)}));
                    }
                    json!({"steps":observed})
                }
                kind => panic!("unknown case kind {kind}"),
            };
            assert_eq!(actual, expected["output"], "{}", case["name"]);
        }
    }

    #[test]
    fn shared_victory_view_route_contract() {
        #[derive(Default)]
        struct RouteWorld(Vec<DefenderMessage>);
        impl World for RouteWorld {
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn route_game_message(&mut self, site: Site, message: u64, params: &Variables) -> bool {
                let Some(request) = DefenderMessage::victory_view(site, message, params) else {
                    return false;
                };
                self.0.push(request);
                true
            }
        }
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/statescript-victory-view-route-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["case_id"], "statescript.victory-view-route.v1");
        assert_eq!(contract["target_build"], 104319);
        for case in contract["input"]["cases"].as_array().unwrap() {
            let position = u16::try_from(case["position"].as_u64().unwrap()).unwrap();
            let mut action = node(
                position,
                case["class"].as_str().unwrap(),
                None,
                case["fields"].clone(),
            );
            action["fields"]["m_ADEB6E05"] = json!(true);
            action["fields"]["m_outPlug"] = json!({"$":"Plug", "links":[[5,"m_inPlug"]]});
            let mut model = graph(
                0x0cec,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":{"$":"Plug","links":[[position,"m_inPlug"]]}}),
                    ),
                    action,
                    node(
                        5,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({
                            "m_out_Var":{"$":"STUConfigVarDynamic","m_60DB8F99":1,"m_identifier":9},
                            "m_value":{"$":"STUConfigVarBool","m_value":true}
                        }),
                    ),
                ],
                json!([]),
                json!([0]),
            );
            model["items"] = json!(position.max(5) + 1);
            let graphs = Arc::new(store(vec![model]));
            let mut root = Root::new(1, 0x0cec);
            root.variables.insert(
                8602,
                Value::Entity(
                    u32::try_from(contract["input"]["subject"].as_u64().unwrap()).unwrap(),
                ),
            );
            let mut world = RouteWorld::default();
            let component = Component::start(
                graphs,
                GAME_MODE_ENTITY,
                Variables::new(),
                vec![root],
                0,
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
                snapshot.variables.get(&9),
                Some(&Value::Bool(
                    case["expected"]["forwarded"].as_bool().unwrap()
                ))
            );
            if case["expected"]["parameters"].is_null() {
                assert!(world.0.is_empty());
                continue;
            }
            assert_eq!(world.0.len(), 1);
            let request = &world.0[0];
            assert_eq!(request.source, GAME_MODE_ENTITY);
            assert_eq!(
                request.message,
                crate::pve_encounter::messages::VICTORY_VIEW
            );
            let params: serde_json::Map<String, Json> = request
                .parameters
                .iter()
                .map(|(&key, value)| {
                    (
                        key.to_string(),
                        match value {
                            Value::Bool(value) => json!(value),
                            Value::Entity(value) => json!({"entity":value}),
                            other => panic!("unexpected parameter {other:?}"),
                        },
                    )
                })
                .collect();
            assert_eq!(Json::Object(params), case["expected"]["parameters"]);
        }
    }

    struct PoolWorld(Vitals);
    impl World for PoolWorld {
        fn body_health(&self, _: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
            Some(self.0.amount(kind, maximum))
        }

        fn add_health_pool(
            &mut self,
            _: Site,
            kind: PoolKind,
            maximum: f64,
            current: f64,
        ) -> Option<u32> {
            self.0.add_pool(kind, maximum, current)
        }

        fn add_temporary_shield(
            &mut self,
            _: Site,
            identifier: u64,
            amount: f64,
            stacked: bool,
        ) -> Option<u32> {
            self.0.add_temporary_shield(identifier, amount, stacked)
        }

        fn health_pool_current(&self, _: Site, pool: u32) -> Option<f64> {
            self.0.pool_current(pool)
        }

        fn remove_health_pool(&mut self, _: Site, pool: u32) -> Option<f64> {
            self.0.remove_pool(pool)
        }
        fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
            None
        }
        fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
            None
        }
    }

    #[test]
    fn nonstacked_shield_is_removed_only_after_its_last_state_exits() {
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/script-shared-named-shield-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["evidence"], "synthetic-rust");
        let input = &contract["input"];
        let pool = input["pool_identifier"].as_str().unwrap();
        let identifier = crate::accounts::parse_guid(pool).unwrap();
        let amount = input["amount"].as_f64().unwrap();
        let base_health = input["base_health"].as_f64().unwrap();
        let drain = input["drain"].as_f64().unwrap();
        let instances: [u16; 2] = serde_json::from_value(input["instances"].clone()).unwrap();
        let owner = json!({"$":"STU_B5A0CAF0"});
        let graph_index = 0x7111;
        let graphs = Arc::new(store(vec![graph(
            graph_index,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":plug(&[(1, "m_beginPlug")], &[])}),
                ),
                node(
                    1,
                    "STUStatescriptStateHealthPool",
                    Some(0),
                    json!({
                        "m_FA3ECC43":owner,
                        "m_69A20070":{"$":"STU_FEDD683C", "m_entity":owner},
                        "m_7E8B666C":2,
                        "m_7BE02F89":{"$":"STUConfigVarIdentifier", "m_identifier":pool, "m_resourceKey":null},
                        "m_64839023":{"$":"STUConfigVarBool","m_value":0},
                        "m_0E8A5C8E":{"$":"STUConfigVarBool","m_value":1},
                        "m_amount":amount
                    }),
                ),
            ],
            json!([1]),
            json!([0]),
        )]));
        let remaining = amount - drain;
        let start = || {
            let mut world = PoolWorld(Vitals::new(base_health, 0.0, 0.0));
            let component = Component::start(
                Arc::clone(&graphs),
                777,
                Variables::new(),
                instances.map(|id| Root::new(id, graph_index)).to_vec(),
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            assert_eq!(world.0.total(), base_health + amount);
            assert_eq!(world.0.maximum(), base_health + amount);
            let snapshot = component.snapshot().unwrap();
            assert!(snapshot.diagnostics.is_empty());
            let handles = instances.map(|id| {
                let state = &snapshot.instances[&id].states[&0];
                assert!(state.active);
                let StateKind::HealthPool { pool: Some(handle) } = state.kind else {
                    panic!("expected registered health pool");
                };
                handle
            });
            assert_ne!(handles[0], handles[1]);
            assert_eq!(world.0.drain_temporary_shield(identifier, drain), remaining);
            assert_eq!(
                handles.map(|id| world.0.pool_current(id)),
                [Some(remaining); 2]
            );
            (component, world, handles)
        };

        for [first, last] in [[0, 1], [1, 0]] {
            let (mut component, mut world, handles) = start();
            component
                .debug_end_state(instances[first], 0, &mut world)
                .unwrap();
            assert_eq!(world.0.temporary_shield_current(identifier), remaining);
            assert_eq!(world.0.maximum(), base_health + amount);
            assert_eq!(world.0.pool_current(handles[first]), None);
            assert_eq!(world.0.pool_current(handles[last]), Some(remaining));

            component
                .debug_end_state(instances[last], 0, &mut world)
                .unwrap();
            assert_eq!(world.0.temporary_shield_current(identifier), 0.0);
            assert_eq!(world.0.total(), base_health);
            assert_eq!(world.0.maximum(), base_health);
            assert_eq!(handles.map(|id| world.0.pool_current(id)), [None; 2]);
        }

        let (mut component, mut world, handles) = start();
        component.teardown(&mut world).unwrap();
        assert_eq!(world.0.temporary_shield_current(identifier), 0.0);
        assert_eq!(world.0.total(), base_health);
        assert_eq!(world.0.maximum(), base_health);
        assert_eq!(handles.map(|id| world.0.pool_current(id)), [None; 2]);
    }

    #[test]
    fn ordinary_health_pool_dispatches_by_shape_at_any_placement() {
        let contract: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/script-base-health-pool-placement-v1.json"
        ))
        .unwrap();
        assert_eq!(contract["schema_version"], 1);
        assert_eq!(contract["evidence"], "synthetic-rust");
        const REMAINING: u32 = 711;
        const BEFORE_EXIT: u32 = 712;
        const ABORT_MESSAGE: u64 = 0xF020_0000_0000_0011;

        for case in contract["input"]["cases"].as_array().unwrap() {
            let graph_index = u32::try_from(case["graph"].as_u64().unwrap()).unwrap();
            let position = u16::try_from(case["state_node"].as_u64().unwrap()).unwrap();
            let exit = if position == 1 { 2 } else { 1 };
            let initial = case["current"].as_f64().unwrap();
            let capacity = case["capacity"].as_f64().unwrap();
            let flag = |value| json!({"$":"STUConfigVarBool","m_value":value});
            let owner = json!({"$":"STU_B5A0CAF0"});
            let fixture = graph(
                graph_index,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug": plug(&[(position,"m_beginPlug")], &[])}),
                    ),
                    node(
                        position,
                        "STUStatescriptStateHealthPool",
                        Some(0),
                        json!({
                            "m_FA3ECC43": owner, "m_7E8B666C": 0,
                            "m_amount": capacity, "m_BCFDD2E9": initial,
                            "m_69A20070": {"$":"STU_FEDD683C", "m_entity":owner},
                            "m_4895B8B6": flag(case["base_flag"].as_bool().unwrap()),
                            "m_64839023": flag(false), "m_0E8A5C8E": flag(false),
                            "m_CEAB19E8": var(REMAINING, true),
                            "m_onEndPlug": plug(&[(exit,"m_inPlug")], &[])
                        }),
                    ),
                    node(
                        exit,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var": var(BEFORE_EXIT,true), "m_value":var(REMAINING,true)}),
                    ),
                    node(
                        3,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                            "m_gameMessage": {"m_gameMessage":format!("0x{ABORT_MESSAGE:016X}")},
                            "m_outPlug": plug(&[(position,ABORT_PLUG)], &[]),
                        }),
                    ),
                ],
                json!([position]),
                json!([0, 3]),
            );
            let mut world = PoolWorld(Vitals::empty());
            let mut component = Component::start(
                Arc::new(store(vec![fixture])),
                839,
                Variables::new(),
                vec![Root::new(1, graph_index)],
                0,
                &mut world,
                MAX_INIT_WORK,
            )
            .unwrap();
            let snapshot = component.snapshot().unwrap();
            let label = case["name"].as_str().unwrap();
            if case["admitted"] == true {
                assert!(
                    snapshot.diagnostics.is_empty(),
                    "{label}: {:?}",
                    snapshot.diagnostics
                );
                assert!(snapshot.instances[&1].states[&0].active, "{label}");
                assert_eq!(
                    (world.0.total(), world.0.maximum()),
                    (initial, capacity),
                    "{label}"
                );
                assert_eq!(
                    snapshot.variables[&REMAINING],
                    Value::Float(initial),
                    "{label}"
                );
                assert!(!snapshot.variables.contains_key(&BEFORE_EXIT), "{label}");

                assert_eq!(
                    component
                        .queue_message(1, ABORT_MESSAGE, Variables::new())
                        .unwrap(),
                    1
                );
                component.run_now(&mut world).unwrap();
                let ended = component.snapshot().unwrap();
                assert!(!ended.instances[&1].states[&0].active, "{label}");
                assert_eq!(
                    ended.variables[&BEFORE_EXIT],
                    Value::Float(initial),
                    "{label}"
                );
                assert_eq!((world.0.total(), world.0.maximum()), (0.0, 0.0), "{label}");
            } else {
                assert!(
                    matches!(
                        snapshot.diagnostics.as_slice(),
                        [crate::script_init::Diagnostic {
                            issue: crate::script_init::Issue::UnsupportedState(class),
                            ..
                        }] if class == "STUStatescriptStateHealthPool"
                    ),
                    "{label}: {:?}",
                    snapshot.diagnostics
                );
                assert!(!snapshot.variables.contains_key(&REMAINING), "{label}");
                assert_eq!(world.0.maximum(), 0.0, "{label}");
            }
        }
    }

    #[test]
    fn base_health_pool_keeps_remaining_amount_before_on_end_and_restarts_fresh() {
        let entry = |position, class, message, input| {
            node(
                position,
                class,
                None,
                json!({
                    "m_gameMessage": {"m_gameMessage":message},
                    "m_outPlug": plug(&[(1, input)], &[])
                }),
            )
        };
        let flag = |value| json!({"$":"STUConfigVarBool","m_value":value});
        let owner = json!({"$":"STU_B5A0CAF0"});
        let fixture = graph(
            42,
            vec![
                entry(0, "STUStatescriptEntry", "", "m_beginPlug"),
                node(
                    1,
                    "STUStatescriptStateHealthPool",
                    Some(0),
                    json!({
                        "m_FA3ECC43":owner, "m_7E8B666C":0,
                        "m_amount":var(100,true), "m_BCFDD2E9":var(101,true),
                        "m_69A20070":{"$":"STU_FEDD683C","m_entity":owner},
                        "m_4895B8B6":flag(true), "m_64839023":flag(false), "m_0E8A5C8E":flag(false),
                        "m_CEAB19E8":var(102,true),
                        "m_onEndPlug":plug(&[(4,"m_inPlug")], &[])
                    }),
                ),
                entry(
                    2,
                    "STUStatescriptEntryGameMessage",
                    "0x0240000000000002",
                    ABORT_PLUG,
                ),
                entry(
                    3,
                    "STUStatescriptEntryGameMessage",
                    "0x0240000000000003",
                    "m_beginPlug",
                ),
                node(
                    4,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":var(101,true), "m_value":var(102,true)
                    }),
                ),
            ],
            json!([1]),
            json!([0, 2, 3]),
        );
        let mut world = PoolWorld(Vitals::empty());
        let mut without_output = fixture.clone();
        without_output["nodes"][1]["fields"]["m_CEAB19E8"] = Json::Null;
        let mut component = Component::start(
            Arc::new(store(vec![fixture])),
            42,
            Variables::from([(100, Value::Float(200.0)), (101, Value::Float(175.0))]),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
        assert_eq!((world.0.total(), world.0.maximum()), (175.0, 200.0));
        world.0.damage(
            25.0,
            Death {
                time: 0.0,
                frame: 0,
                killer: 43,
            },
        );
        component
            .queue_message(1, 0x0240_0000_0000_0002, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(snapshot.variables[&101], Value::Float(150.0));
        assert_eq!(snapshot.variables[&OWNER_HEALTH], Value::Float(0.0));
        assert!(!snapshot.instances[&1].states[&0].active);
        assert_eq!(world.0.total(), 0.0);
        component
            .queue_message(1, 0x0240_0000_0000_0003, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(
            snapshot.instances[&1].states[&0].kind,
            StateKind::HealthPool { pool: Some(2) }
        );
        assert_eq!(snapshot.variables[&OWNER_HEALTH], Value::Float(150.0));
        assert_eq!(world.0.pool_current(1), None);
        assert_eq!(world.0.pool_current(2), Some(150.0));
        component
            .set_entity_variable(100, Value::Float(225.0), &mut world)
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(world.0.maximum(), 200.0);
        assert!(
            component
                .snapshot()
                .unwrap()
                .diagnostics
                .iter()
                .any(|diagnostic| {
                    diagnostic.issue
                        == crate::script_init::Issue::UnsupportedField(
                            "changing health pool capacity",
                        )
                })
        );
        let mut world = PoolWorld(Vitals::empty());
        let mut component = Component::start(
            Arc::new(store(vec![without_output])),
            42,
            Variables::from([(100, Value::Float(200.0)), (101, Value::Float(175.0))]),
            vec![Root::new(1, 42)],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(component.snapshot().unwrap().initialization_complete());
        assert_eq!(
            component.snapshot().unwrap().variables[&OWNER_HEALTH],
            Value::Float(175.0)
        );
        world.0.damage(
            25.0,
            Death {
                time: 0.0,
                frame: 0,
                killer: 43,
            },
        );
        component.advance_frame(1, &mut world).unwrap();
        assert_eq!(
            component.snapshot().unwrap().variables[&OWNER_HEALTH],
            Value::Float(150.0)
        );
        component
            .queue_message(1, 0x0240_0000_0000_0002, Variables::new())
            .unwrap();
        component.run_now(&mut world).unwrap();
        assert_eq!(world.0.total(), 0.0);
        assert_eq!(
            component.snapshot().unwrap().variables[&OWNER_HEALTH],
            Value::Float(0.0)
        );
    }

    #[test]
    #[ignore = "requires a local 1.74 graph pack"]
    fn authored_helix_uses_its_six_second_cooldown_before_the_next_volley() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let body = &graphs.bodies()[&0x0400_0000_0000_03cf];
        let mut world = RifleWorld(Arc::clone(&graphs));
        let mut component = Component::start(
            Arc::clone(&graphs),
            0xa000_0101,
            player_entity_variables(body, 200.0, 200.0),
            player_roots(body, &graphs).unwrap(),
            1000,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.advance_frame(1000, &mut world).unwrap();
        let mut shots = Vec::new();
        let cooldown = crate::script_expr::Variable {
            scope: Scope::Instance,
            id: 61,
        };
        for frame in 1001..=1411 {
            let buttons = if matches!(frame, 1030 | 1031 | 1040 | 1041 | 1410 | 1411) {
                2
            } else {
                0
            };
            component
                .advance_command(
                    Command {
                        frame,
                        buttons,
                        ..Command::default()
                    },
                    &mut world,
                )
                .unwrap();
            shots.extend(component.take_shots().into_iter().flatten());
            let (flags, last_ms, remaining, due) = match frame {
                1029 => (0, 16000, 0.0, None),
                1030 => (5, 16480, 5.985000133514404, Some(22480)),
                1040 => (1, 16480, 5.824999809265137, Some(22480)),
                1404 => (1, 16480, 0.0009999275207519531, Some(22480)),
                1405 => (1, 16480, 0.0, None),
                1410 => (1, 22560, 5.985000133514404, Some(28560)),
                _ => continue,
            };
            let snapshot = component.snapshot().unwrap();
            let ability = &snapshot.instances[&11].states[&31];
            assert_eq!(ability.kind, StateKind::Ability { flags }, "frame {frame}");
            assert_eq!(
                ability.ability_cooldown,
                Some(AbilityCooldown {
                    current: if frame == 1029 { 0.0 } else { 6.0 },
                    rate: 1.0,
                    last_ms,
                }),
                "frame {frame}"
            );
            assert_eq!(
                component.resolved_variable(11, cooldown),
                Some(Value::Float(remaining)),
                "frame {frame}"
            );
            assert_eq!(
                snapshot
                    .pending_timers
                    .get(&11)
                    .into_iter()
                    .flatten()
                    .find(|timer| timer.raw_state == 31 && timer.parameter == 2)
                    .map(|timer| timer.due_ms),
                due,
                "frame {frame}"
            );
        }
        assert_eq!(
            shots
                .iter()
                .map(|shot| (
                    shot.instance,
                    shot.raw_state,
                    shot.time_ms,
                    shot.activation,
                    shot.ordinal
                ))
                .collect::<Vec<_>>(),
            [(11, 16, 16480, 1, 0), (11, 16, 22560, 2, 0)]
        );
        let shot = &shots[0];
        let node = component.shot_node(shot).unwrap();
        let rocket =
            crate::projectile_flight::ProjectileBallistics::from_authored(node, |config| {
                component.evaluate_shot_config(shot, config, &mut world)
            })
            .unwrap()
            .unwrap();
        assert_eq!(rocket.definition, Some(0x0400_0000_0000_072c));
        assert_eq!((rocket.speed, rocket.lifetime), (50.0, 2.0));
        let direct = rocket.direct.unwrap();
        assert_eq!(
            (direct.amount, direct.loadout),
            (40.0, Some(0x0b90_0000_0000_008e))
        );
        let blast = rocket.blast.unwrap();
        assert_eq!(
            (blast.hit.amount, blast.hit.loadout),
            (80.0, Some(0x0b90_0000_0000_008e))
        );
        assert_eq!(blast.rings, [(1.0, 1.0), (3.0, 0.5)]);
    }

    #[test]
    #[ignore = "requires a local 1.74 graph pack"]
    fn authored_soldier_rifle_holds_releases_and_reloads_on_the_reference_clock() {
        let runtime = PathBuf::from(
            std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("set ORPHEUS_RUNTIME_DIR"),
        );
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let body = &graphs.bodies()[&0x0400_0000_0000_03cf];
        let mut world = RifleWorld(Arc::clone(&graphs));
        let mut component = Component::start(
            Arc::clone(&graphs),
            0xa000_0101,
            player_entity_variables(body, 200.0, 200.0),
            player_roots(body, &graphs).unwrap(),
            999,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.advance_frame(999, &mut world).unwrap();
        let mut shots = Vec::new();
        let ammo = crate::script_expr::Variable {
            scope: Scope::Instance,
            id: 53,
        };
        let reload = crate::script_expr::Variable {
            scope: Scope::Instance,
            id: 51,
        };
        for frame in 1000..=1201 {
            let buttons = match frame {
                1030..=1073 | 1200 => 1,
                1095 => 0x400,
                _ => 0,
            };
            component
                .advance_command(
                    Command {
                        frame,
                        buttons,
                        ..Command::default()
                    },
                    &mut world,
                )
                .unwrap();
            shots.extend(component.take_shots().into_iter().flatten());
            let expected = match frame {
                1029 => Some(30),
                1030 | 1036 => Some(29),
                1074 | 1095 | 1141 => Some(23),
                1142 | 1188 => Some(30),
                1201 => Some(29),
                _ => None,
            };
            if let Some(expected) = expected {
                assert_eq!(
                    component.resolved_variable(11, ammo),
                    Some(Value::Integer(expected)),
                    "frame {frame}, rifle states {:?}, diagnostics {:?}",
                    active(component.snapshot().unwrap(), 11),
                    component
                        .snapshot()
                        .unwrap()
                        .diagnostics
                        .iter()
                        .filter(|d| d.site.graph == 0x0254)
                        .collect::<Vec<_>>()
                );
            }
            if frame == 1142 {
                assert_eq!(
                    component.resolved_variable(11, reload),
                    Some(Value::Bool(true))
                );
            }
            if frame == 1188 {
                assert_eq!(
                    component.resolved_variable(11, reload),
                    Some(Value::Bool(false))
                );
            }
        }
        assert_eq!(
            shots
                .iter()
                .map(|shot| (shot.time_ms, shot.activation, shot.ordinal))
                .collect::<Vec<_>>(),
            [
                (16480, 1, 0),
                (16592, 1, 1),
                (16702, 1, 2),
                (16813, 1, 3),
                (16924, 1, 4),
                (17036, 1, 5),
                (17147, 1, 6),
                (19200, 2, 0),
            ]
        );
        assert!(
            shots
                .iter()
                .all(|shot| shot.instance == 11 && shot.raw_state == 10)
        );
        let shot = &shots[0];
        let node = component.shot_node(shot).unwrap();
        let mut evaluate = |config: &Json| component.evaluate_shot_config(shot, config, &mut world);
        let hit = crate::game_hitscan::HitDefinition::from_authored(
            node.field("m_6396149F").unwrap(),
            &mut evaluate,
        )
        .unwrap()
        .unwrap();
        assert_eq!(hit.amount, 20.0);
        assert_eq!(hit.loadout, Some(0x0b90_0000_0000_008d));
        assert_eq!(hit.critical, 2.0);
        assert_eq!(
            hit.falloff,
            Some(crate::game_hitscan::DamageFalloff {
                start: 30.0,
                end: 50.0,
                floor: f64::from(0.3_f32),
            })
        );
        assert_eq!(
            evaluate(node.field("m_FEC435C6").unwrap()),
            Ok(Value::Integer(180))
        );
        assert!(component.take_shots().is_none());
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein Boss intro graph"]
    fn authored_boss_body_voice_waits_and_cancels_with_the_instance() {
        #[derive(Default)]
        struct Voice(Vec<(Site, IntroCue)>);
        impl World for Voice {
            fn boss_intro_cue(&self, node: &crate::script_graph::Node) -> Option<IntroCue> {
                intro_cue(node)
            }
            fn play_boss_intro_voice(&mut self, site: Site, cue: IntroCue) -> Option<u64> {
                self.0.push((site, cue));
                Some(3591)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = Voice::default();
        let mut component = Component::start(
            graphs,
            0xc000_1000,
            Variables::new(),
            vec![
                Root::listener(1, 0x0ce0, Some(49)),
                Root::listener(2, 0x0ce0, Some(49)),
            ],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.advance_frame(11, &mut world).unwrap();
        assert!(world.0.is_empty());
        component.debug_exit_instance(2, &mut world).unwrap();
        component.advance_frame(12, &mut world).unwrap();
        let &[(site, cue)] = world.0.as_slice() else {
            panic!("expected one Boss voice submission")
        };
        assert_eq!(site.instance, 1);
        assert_eq!(site.node, Some(77));
        assert_eq!(cue.stimulus, 0x0ee0_0000_0000_06bd);
        assert!(!cue.narrator);
        let snapshot = component.snapshot().unwrap();
        assert!(snapshot.instances[&1].states[&49].active);
        let codecs = crate::script_codecs::CodecTable::load(
            runtime.join("data/statescript_codecs_174.json"),
        )
        .unwrap();
        let encoder =
            crate::script_snapshot_wire::SnapshotEncoder::new(component.graph_store(), &codecs);
        for view in [
            crate::script_snapshot_wire::View::Owner,
            crate::script_snapshot_wire::View::Observer,
        ] {
            encoder
                .full(&encoder.project(snapshot, view).unwrap())
                .unwrap();
        }
        component.advance_frame(237, &mut world).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert_eq!(world.0.len(), 1);
        assert!(!snapshot.instances[&1].states[&49].active);
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein wave health graph"]
    fn authored_boss_wave_health_scales_with_difficulty_and_retires() {
        struct Health {
            vitals: Vitals,
            difficulty: i64,
        }
        impl World for Health {
            fn local_combat_context_available(&self, _: Site) -> Option<bool> {
                Some(false)
            }
            fn entity_variable(&mut self, entity: u32, id: u32) -> Option<Value> {
                (entity == GAME_MODE_ENTITY).then_some({
                    if id == 9948 {
                        Value::Integer(self.difficulty)
                    } else {
                        Value::Null
                    }
                })
            }
            fn body_health(&self, _: Site, kind: Option<PoolKind>, maximum: bool) -> Option<f64> {
                Some(self.vitals.amount(kind, maximum))
            }
            fn add_wave_health(&mut self, _: Site, amount: f64) -> Option<Option<u32>> {
                self.vitals.add_wave_health(amount).map(Some)
            }
            fn remove_health_pool(&mut self, _: Site, pool: u32) -> Option<f64> {
                self.vitals.remove_pool(pool)
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let tables: [(u16, [f64; 4]); 6] = [
            (0, [750.0, 1000.0, 1350.0, 1750.0]),
            (1, [3200.0, 3600.0, 3800.0, 4000.0]),
            (2, [2000.0, 2500.0, 2800.0, 3000.0]),
            (8, [1400.0, 1500.0, 1900.0, 2300.0]),
            (13, [0.0, 100.0, 100.0, 100.0]),
            (47, [1400.0, 1600.0, 1900.0, 2200.0]),
        ];
        let maximum = graphs
            .graph(0x0cbc)
            .unwrap()
            .node(53)
            .unwrap()
            .field("m_condition.m_configVars[1]")
            .unwrap();
        for (raw, table) in tables {
            for (difficulty, bonus) in table.into_iter().enumerate() {
                let mut world = Health {
                    vitals: Vitals::new(250.0, 0.0, 0.0),
                    difficulty: difficulty as i64,
                };
                let mut root = Root::listener(1, 0x0cbc, None);
                root.variables
                    .insert(9948, Value::Integer(difficulty as i64));
                let mut component = Component::start(
                    Arc::clone(&graphs),
                    0xc000_1000,
                    Variables::from([(HUD_MAX_HEALTH, Value::Float(250.0))]),
                    vec![root],
                    0,
                    &mut world,
                    MAX_INIT_WORK,
                )
                .unwrap();
                component
                    .debug_begin_isolated_wave_health_pool(1, raw, &mut world)
                    .unwrap();
                let snapshot = component.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                assert_eq!(world.vitals.maximum(), 250.0 + bonus);
                assert_eq!(world.vitals.total(), 250.0 + bonus);
                assert_eq!(
                    component.evaluate_state_config(1, raw, maximum, &mut world),
                    Ok(Value::Float(250.0 + bonus))
                );
                assert_eq!(
                    snapshot.variables[&HUD_MAX_HEALTH],
                    Value::Float(250.0 + bonus)
                );
                component.debug_end_state(1, raw, &mut world).unwrap();
                assert_eq!(world.vitals.maximum(), 250.0);
                assert_eq!(world.vitals.total(), 250.0);
            }
        }
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein mode graphs"]
    fn authored_victory_voice_releases_the_client_outcome_action() {
        #[derive(Default)]
        struct VictoryWorld {
            actors: Vec<Value>,
            actions: Vec<Site>,
        }

        impl World for VictoryWorld {
            fn is_victory_voice(&self, node: &crate::script_graph::Node) -> bool {
                victory_cue(node)
            }
            fn play_victory_voice(&mut self, site: Site, actor: &Value) -> Option<u64> {
                assert_eq!(site.node, Some(221));
                self.actors.push(actor.clone());
                Some(13_138)
            }
            fn queue_mode_action(&mut self, site: Site) -> bool {
                self.actions.push(site);
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }

        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let raw = graphs
            .graph(0x0c8c)
            .unwrap()
            .node(221)
            .unwrap()
            .state
            .unwrap();
        let actor = Value::Entity(0xc000_1010);
        let mut root = Root::listener(2, 0x0c8c, Some(raw));
        root.variables.insert(8504, Value::Float(4.5));
        let mut world = VictoryWorld::default();
        let mut component = Component::start(
            graphs,
            GAME_MODE_ENTITY,
            Variables::from([(8602, actor.clone())]),
            vec![root],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        assert!(world.actions.is_empty());
        assert_eq!(world.actors, [actor]);
        for frame in 1..=300 {
            component.advance_frame(frame, &mut world).unwrap();
        }
        assert_eq!(world.actions.len(), 1);
        assert_eq!(world.actions[0].node, Some(265));
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
    }

    #[test]
    #[ignore = "requires the external 104319 Junkenstein mode graphs"]
    fn authored_victory_view_child_finishes_the_mode_phase() {
        #[derive(Default)]
        struct EndWorld(Vec<DefenderMessage>);
        impl World for EndWorld {
            fn is_victory_voice(&self, node: &crate::script_graph::Node) -> bool {
                victory_cue(node)
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn play_victory_voice(&mut self, _site: Site, _actor: &Value) -> Option<u64> {
                Some(100)
            }
            fn queue_mode_action(&mut self, _site: Site) -> bool {
                true
            }
            fn route_game_message(&mut self, site: Site, message: u64, params: &Variables) -> bool {
                let Some(request) = DefenderMessage::victory_view(site, message, params) else {
                    return false;
                };
                self.0.push(request);
                true
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let raw = graphs
            .graph(0x0c8c)
            .unwrap()
            .node(73)
            .unwrap()
            .state
            .unwrap();
        let mut root = Root::listener(2, 0x0c8c, Some(raw));
        root.variables.insert(8504, Value::Float(4.5));
        let mut world = EndWorld::default();
        let mut component = Component::start(
            graphs,
            GAME_MODE_ENTITY,
            Variables::from([(8602, Value::Entity(0xa000_0101))]),
            vec![root],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        for frame in 1..=80 {
            component.advance_frame(frame, &mut world).unwrap();
        }
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        let mode = &snapshot.instances[&2];
        assert_eq!(mode.variables.get(&59), Some(&Value::Integer(5)));
        assert!(!mode.states[&raw].active);
        assert!(
            !snapshot
                .instances
                .values()
                .any(|instance| instance.graph == 0x0cec)
        );
        assert_eq!(
            world
                .0
                .iter()
                .map(|message| message.parameters[&0x390].clone())
                .collect::<Vec<_>>(),
            [Value::Bool(true), Value::Bool(false)]
        );
    }

    #[test]
    fn shared_flashbang_base_state_contract() {
        let fixture: Json = serde_json::from_str(include_str!(
            "../../fixtures/behavior/flashbang-base-state-v1.json"
        ))
        .unwrap();
        for (index, supported) in [(0x224e, true), (42, false)] {
            let mut data = graph(
                index,
                vec![
                    node(
                        0,
                        "STUStatescriptEntry",
                        None,
                        json!({"m_outPlug":plug(&[(25,"m_beginPlug")],&[])}),
                    ),
                    node(1, "STUStatescriptState", Some(1), json!({})),
                    node(
                        2,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(100,true),"m_value":true}),
                    ),
                    node(
                        3,
                        "STUStatescriptActionSetVar",
                        None,
                        json!({"m_out_Var":var(101,true),"m_value":true}),
                    ),
                    node(
                        25,
                        "STU_0DB9E66B",
                        Some(0),
                        json!({
                            "m_targets":{"$":"unsupported-if-evaluated"},
                            "m_loadout":{"$":"unsupported-if-evaluated"},
                            "m_43B917C6":{"$":"unsupported-if-evaluated"},
                            "m_onBeginPlug":plug(&[(2,"m_inPlug")],&[]),
                            "m_onEndPlug":plug(&[(3,"m_inPlug")],&[]),
                            "m_subgraphPlug":plug(&[(1,"m_beginPlug")],&[1]),
                        }),
                    ),
                    node(
                        26,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"m_gameMessage":"0x0240000000000002"},
                    "m_outPlug":plug(&[(25,ABORT_PLUG)],&[])}),
                    ),
                    node(
                        27,
                        "STUStatescriptEntryGameMessage",
                        None,
                        json!({
                    "m_gameMessage":{"m_gameMessage":"0x0240000000000003"},
                    "m_outPlug":plug(&[(25,"m_beginPlug")],&[])}),
                    ),
                ],
                json!([25, 1]),
                json!([0, 26, 27]),
            );
            data["items"] = json!(28);
            data["remote"] = json!([25]);
            data["nodes"][4]["remote"] = json!(0);
            let graphs = Arc::new(store(vec![data]));
            let codecs = crate::script_codecs::CodecTable::from_json(
                r#"{"classes":{"0DB9E66B":{"name":"STU_0DB9E66B","codec":"none","stec":false,"status":"VERIFIED","layout":[]}}}"#,
            )
            .unwrap();
            let mut component = Component::start(
                Arc::clone(&graphs),
                42,
                Variables::new(),
                vec![Root::new(1, index)],
                0,
                &mut crate::script_init::EmptyWorld,
                MAX_INIT_WORK,
            )
            .unwrap();
            if !supported {
                assert!(
                    component
                        .snapshot()
                        .unwrap()
                        .diagnostics
                        .iter()
                        .any(|d| d.issue
                            == crate::script_init::Issue::UnsupportedState("STU_0DB9E66B".into()))
                );
                continue;
            }
            for step in fixture["steps"].as_array().unwrap() {
                if let Some(message) = step["message"].as_u64() {
                    component
                        .queue_message(1, message, Variables::new())
                        .unwrap();
                    component
                        .run_now(&mut crate::script_init::EmptyWorld)
                        .unwrap();
                }
                let snapshot = component.snapshot().unwrap();
                assert!(
                    snapshot.initialization_complete(),
                    "{:?}",
                    snapshot.diagnostics
                );
                let states = &snapshot.instances[&1].states;
                assert_eq!(states[&0].kind, StateKind::Plain);
                for raw in [0, 1] {
                    assert_eq!(states[&raw].active, step["active"].as_bool().unwrap());
                }
                assert_eq!(
                    states[&0].activation,
                    u16::try_from(step["activation"].as_u64().unwrap()).unwrap()
                );
                assert_eq!(snapshot.variables.get(&100), Some(&Value::Bool(true)));
                if step["ended"] == true {
                    assert_eq!(snapshot.variables.get(&101), Some(&Value::Bool(true)));
                }
                let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(&graphs, &codecs);
                let projection = encoder
                    .project(snapshot, crate::script_snapshot_wire::View::Observer)
                    .unwrap();
                encoder.full(&projection).unwrap();
            }
        }
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein Boss intro graphs"]
    fn authored_boss_pause_keeps_the_receiver_until_the_last_sender_retires() {
        #[derive(Default)]
        struct Aim(Vec<(Site, bool)>);
        impl World for Aim {
            fn set_cinematic_aim_lock(&mut self, site: Site, active: bool) -> bool {
                self.0.push((site, active));
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = Aim::default();
        let mut component = Component::start(
            graphs,
            0xc000_1000,
            Variables::new(),
            vec![
                Root::listener(1, 0x0ce0, Some(8)),
                Root::listener(2, 0x0cbc, Some(56)),
            ],
            0,
            &mut world,
            MAX_INIT_WORK,
        )
        .unwrap();
        component.refresh_boss_pause(&mut world, false).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(
            snapshot.initialization_complete(),
            "{:?}",
            snapshot.diagnostics
        );
        assert!(snapshot.boss_pause_active());
        assert!(snapshot.instances[&2].states[&57].active);
        assert_eq!(world.0.len(), 1);
        assert_eq!(world.0[0].0.node, Some(45));
        assert!(world.0[0].1);
        let codecs = crate::script_codecs::CodecTable::load(
            runtime.join("data/statescript_codecs_174.json"),
        )
        .unwrap();
        let encoder =
            crate::script_snapshot_wire::SnapshotEncoder::new(component.graph_store(), &codecs);
        encoder
            .full(
                &encoder
                    .project(snapshot, crate::script_snapshot_wire::View::Observer)
                    .unwrap(),
            )
            .unwrap();

        component.debug_exit_instance(1, &mut world).unwrap();
        component.refresh_boss_pause(&mut world, true).unwrap();
        let snapshot = component.snapshot().unwrap();
        assert!(!snapshot.boss_pause_active());
        assert!(snapshot.instances[&2].states[&57].active);
        assert_eq!(snapshot.instances[&2].states[&57].activation, 1);
        component.refresh_boss_pause(&mut world, false).unwrap();
        assert!(!component.snapshot().unwrap().instances[&2].states[&57].active);
        assert_eq!(world.0, [(world.0[0].0, true), (world.0[0].0, false)]);
    }

    #[test]
    #[ignore = "requires the prepared 104319 Junkenstein boss intro graphs"]
    fn authored_boss_control_links_active_instances_and_releases_the_model() {
        struct Model(Vec<bool>);
        impl World for Model {
            fn supports_static_model_protection(&self, site: Site) -> bool {
                site.entity == 0xc000_1000
            }

            fn set_encounter_model_enabled(&mut self, _: Site, enabled: bool) -> bool {
                self.0.push(enabled);
                true
            }
            fn position(&mut self, _entity: u32) -> Option<[f64; 3]> {
                None
            }
            fn entity_variable(&mut self, _entity: u32, _id: u32) -> Option<Value> {
                None
            }
        }
        let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let graphs =
            Arc::new(GraphStore::load(runtime.join("data/game_graphs_174.json.gz")).unwrap());
        let mut world = Model(Vec::new());
        let mut component = Component::start(
            Arc::clone(&graphs),
            0xc000_1000,
            Variables::new(),
            vec![
                Root::listener(1, 0x0ce0, Some(10)),
                Root::listener(2, 0x0d03, Some(1)),
            ],
            0,
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
        assert_eq!(world.0, [false, false]);
        for (source, successor) in [(10, 1), (20, 4), (21, 3), (27, 0)] {
            assert_eq!(
                snapshot.instances[&1].states[&source].kind,
                StateKind::Registered {
                    next: Some((2, successor))
                }
            );
            assert!(snapshot.instances[&2].states[&successor].active);
        }
        assert!(snapshot.instances[&1].states[&31].active);
        assert!(snapshot.instances[&2].states[&12].active);
        let codecs = crate::script_codecs::CodecTable::load(
            runtime.join("data/statescript_codecs_174.json"),
        )
        .unwrap();
        let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(&graphs, &codecs);
        for view in [
            crate::script_snapshot_wire::View::Owner,
            crate::script_snapshot_wire::View::Observer,
        ] {
            let projected = encoder.project(snapshot, view).unwrap();
            encoder.full(&projected).unwrap();
        }
        component.debug_exit_instance(2, &mut world).unwrap();
        assert_eq!(world.0, [false, false]);
        for source in [10, 20, 21, 27] {
            assert_eq!(
                component.snapshot().unwrap().instances[&1].states[&source].kind,
                StateKind::Registered { next: None }
            );
        }
        component.debug_end_state(1, 10, &mut world).unwrap();
        assert_eq!(world.0, [false, false, true]);
        assert!(active(component.snapshot().unwrap(), 1).is_empty());
    }
}
