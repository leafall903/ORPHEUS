use super::*;
use crate::{
    collision_cache::{CollisionCache, Mesh, Triangle},
    commands::SECONDARY,
    contact_mesh::ContactMesh,
    contact_world::{StaticGeometry, StaticShape},
    game_heroes::Hero,
    script_init::{MoveOutcome, State, StateKind},
    script_player::{OwnerBodySpawn, PlayerBodyFactory},
    script_wire::Value,
};
use serde_json::json;

struct Harness {
    scene: PreparedScene,
    delivery: WorldDelivery,
    id: BodyId,
    instance: u16,
    frame: u32,
}

impl Harness {
    fn new(instance: u16, wall: bool, peer_team: Option<u8>) -> Self {
        Self::spawn(
            PreparedScene::fixture_for_bots(vec![]),
            1,
            instance,
            wall,
            peer_team,
            None,
        )
    }

    fn spawn(
        mut scene: PreparedScene,
        generation: u64,
        instance: u16,
        wall: bool,
        peer_team: Option<u8>,
        trace_case: Option<[f32; 2]>,
    ) -> Self {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../fixtures/behavior/native-owner-move-to-v1.json"
        ))
        .unwrap();
        let graph = 771 + u32::from(instance);
        let body_asset = 0x0400_0000_0000_0021_u64;
        let hero = Hero {
            guid: 0x0400_0000_0000_0055,
            name: "Fixture".into(),
            role: "Damage".into(),
            body: body_asset,
        };
        let node = |pos, class: &str, state: Option<u16>, fields| {
            json!({
                "pos":pos,"rid":pos,"uid":pos,"remote":-1,"state":state,"class":class,
                "server_only":true,"client_only":false,"fields":fields
            })
        };
        let destination = json!({"$":"STUConfigVarDynamic","m_identifier":73,"m_60DB8F99":false});
        let mut held = node(
            1,
            "fixture.held",
            Some(0),
            json!({
                "button":29,"held_states":[1],
                "on_press":{"$":"Plug","links":[[5,"m_inPlug"]]}
            }),
        );
        held["state_behavior"] = json!("held_input");
        let next = |pos, raw| {
            let mut next = node(pos, "fixture.next", Some(raw), json!({}));
            next["state_behavior"] = json!("plain");
            next
        };
        let graph_json = |index, nodes: Vec<serde_json::Value>, states, entries| {
            json!({
                "index":index,"items":nodes.len(),"nodes":nodes,"states":states,"entries":entries,
                "remote":[],"sync_vars":[],"containers":{},"unknown_nodes":[],"fields":{},
                "header":{"m_nodesBitCount":4,"m_statesBitCount":4,"m_remoteSyncNodesBitCount":4,"m_syncVarsBitCount":4}
            })
        };
        let empty = graph_json(770, vec![], json!([]), json!([]));
        let mut moving = graph_json(
            graph,
            vec![
                node(
                    0,
                    "STUStatescriptEntry",
                    None,
                    json!({"m_outPlug":{"$":"Plug","links":[[1,"m_beginPlug"]],"m_FF3DAF1E":[]}}),
                ),
                held,
                node(
                    2,
                    "STUStatescriptStateMoveTo",
                    Some(1),
                    json!({
                        "m_2BBEEAB8":true,"m_B060F688":0,
                        "m_865F3D01":destination,
                        "m_speed":fixture["input"]["speed"],
                        "m_onFinishedPlug":{"$":"Plug","links":[[3,"m_beginPlug"]]},
                        "m_onAbortPlug":{"$":"Plug","links":[[4,"m_beginPlug"]]}
                    }),
                ),
                next(3, 2),
                next(4, 3),
                node(
                    5,
                    "STUStatescriptActionSetVar",
                    None,
                    json!({
                        "m_out_Var":destination,
                        "m_value":{"$":"STUConfigVarVec3","m_value":{"X":fixture["input"]["destination_x"],"Y":0,"Z":0}}
                    }),
                ),
            ],
            json!([1, 2, 3, 4]),
            json!([0]),
        );
        if trace_case.is_some() {
            // Authored 104319 forms copied from graph 0x005a (filter mask and
            // codec defaults, team filter, hardpoint, ternary destinations).
            let dyn_var =
                |id| json!({"$":"STUConfigVarDynamic","m_identifier":id,"m_60DB8F99":false});
            let ternary = |ids: [u64; 2]| {
                json!({"$":"STU_919FD47C","m_configVars":null,"m_EE729DCB":0,
                "m_expression":{"$":"STUConfigVarExpressionData","m_fragments":null,
                    "m_opcodes":[8,0,4,6,6,0,0,4,8,1,0],
                    "m_dynamicVars":ids.iter().map(|id| dyn_var(*id)).collect::<Vec<_>>(),
                    "m_D99EF254":[0]}})
            };
            let authored_filter = |cast: serde_json::Value| {
                json!({
                    "$":"STUConfigVarFilter","m_93FA1642":null,"m_84065FD1":1073741823,
                    "m_1E14CCA8":0,"m_4EA686DB":0,"m_27CF71D5":0,"m_66747E7D":0,"m_89831602":0,
                    "m_condition":null,"m_9C7BD8B8":null,"m_B486603E":null,"m_8B2A4DF3":null,
                    "m_F61726FE":null,"m_B40362FC":null,"m_EE729DCB":0,"m_C1903771":cast
                })
            };
            moving["nodes"][1]["fields"]
                .as_object_mut()
                .unwrap()
                .remove("on_press");
            moving["nodes"][1]["fields"]["held_states"] = json!([4]);
            moving["nodes"][1]["fields"]["on_press"] =
                json!({"$":"Plug","links":[[6,"m_beginPlug"]]});
            let mut hit_branch = node(
                5,
                "fixture.branch",
                None,
                json!({
                    "m_condition":dyn_var(435),
                    "m_truePlug":{"$":"Plug","links":[[7,"m_inPlug"]]},"m_falsePlug":{"$":"Plug","links":[]}
                }),
            );
            hit_branch["action_behavior"] = json!("branch");
            moving["nodes"][5] = hit_branch;
            // The authored gate compares feet-to-anchor distance against an
            // authored minimum (opcodes: push owner, push point, distance,
            // push 2.0, greater-equal) instead of an engine constant.
            let mut gate_branch = node(
                7,
                "fixture.branch",
                None,
                json!({
                    "m_condition":{"$":"STU_919FD47C","m_configVars":[{"$":"STU_B5A0CAF0"}],"m_EE729DCB":0,
                        "m_expression":{"$":"STUConfigVarExpressionData","m_fragments":null,
                            "m_opcodes":[14,0,8,0,53,6,0,35,0],"m_dynamicVars":[dyn_var(431)],
                            "m_D99EF254":[fixture["input"]["min_distance"]]}},
                    "m_truePlug":{"$":"Plug","links":[[2,"m_beginPlug"]]},"m_falsePlug":{"$":"Plug","links":[]}
                }),
            );
            gate_branch["action_behavior"] = json!("branch");
            moving["nodes"].as_array_mut().unwrap().extend([
                gate_branch,
                {
                    let mut trace = node(6, "STU_4BBC5D09", Some(4), json!({
                        "m_2BBEEAB8":true,
                        "m_66B658A5":authored_filter(json!({"$":"STU_20DB8AB5","m_resourceKey":null,
                            "m_EE729DCB":0,"m_C1903771":fixture["input"]["collision_profile"]})),                        "m_6F7A45D9":authored_filter(serde_json::Value::Null).as_object_mut()
                            .map(|f| { f.remove("m_C1903771");
                                f.insert("m_8B2A4DF3".into(), json!({"$":"STU_357DABBB","m_resourceKey":null,
                                    "m_EE729DCB":0,"m_7D7C86A1":fixture["input"]["team_profile"]}));
                                f.clone() }).unwrap(),
                        "m_F745334E":{"$":"STUConfigVarHardPoint","m_resourceKey":null,"m_EE729DCB":0,
                            "m_hardPoint":fixture["input"]["launch_hardpoint"]},
                        "m_2AFC083F":{"$":"STUConfigVarInt","m_value":8,"m_EE729DCB":0},
                        "m_speed":{"$":"STUConfigVarInt","m_value":40,"m_EE729DCB":0},
                        "m_9E1A7C58":dyn_var(435),"m_5ED803CF":dyn_var(431),"m_F2F8606D":dyn_var(432),
                        "m_64801077":dyn_var(433),"m_D7A2A03B":dyn_var(434),"m_1FFD7AC0":dyn_var(38),
                        "m_9169D109":dyn_var(1221),"m_58B4C4D7":dyn_var(1220),
                        "m_onFinishedPlug":{"$":"Plug","links":[[5,"m_inPlug"]]}
                    }));
                    trace["server_only"] = json!(false);
                    trace["remote"] = json!(0);
                    trace
                },
            ]);
            // The authored MoveTo selects its destination, entity and bone
            // through ternary expressions whose none branch applies here.
            moving["nodes"][2]["fields"] = json!({
                "m_2BBEEAB8":true,"m_B060F688":0,
                "m_865F3D01":{"$":"STU_919FD47C","m_configVars":null,"m_EE729DCB":0,
                    "m_expression":{"$":"STUConfigVarExpressionData","m_fragments":null,
                        "m_opcodes":[8,0,4,6,8,1,0,4,8,2,0],
                        "m_dynamicVars":[dyn_var(1221),dyn_var(1220),dyn_var(431)],
                        "m_D99EF254":null}},
                "m_destinationEntity":ternary([1221,38]),
                "m_destinationBone":ternary([1221,8695]),
                "m_speed":{"$":"STUConfigVarFloat","m_value":fixture["input"]["speed"],"m_EE729DCB":0},
                "m_onFinishedPlug":{"$":"Plug","links":[[3,"m_beginPlug"]]},
                "m_onAbortPlug":{"$":"Plug","links":[[4,"m_beginPlug"]]}
            });
            moving["items"] = json!(8);
            moving["states"] = json!([1, 2, 3, 4, 6]);
            moving["remote"] = json!([6]);
        }
        let mut bindings = vec![json!({"graph":770,"m_75408640":0}); usize::from(instance - 1)];
        bindings.push(json!({"graph":graph,"m_75408640":0}));
        let graphs = GraphStore::from_json(&serde_json::to_vec(&json!({
            "version":1,"missing":[],"graphs":[empty,moving],
            "bodies":{
                format!("0x{body_asset:016X}"):{"hero":format!("0x{:016X}",hero.guid),"name":"Fixture","graphs":bindings,"manager":null,"weapons":[]}
            }
        })).unwrap()).unwrap();
        let factory = PlayerBodyFactory::from_body(
            Arc::new(graphs),
            Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap()),
            0,
            &hero,
            None,
            false,
        )
        .unwrap();
        let id = avatar(1, generation);
        let frame = 100;
        let shapes = if wall {
            let mesh = Mesh {
                vertices: serde_json::from_value(fixture["input"]["wall_vertices"].clone())
                    .unwrap(),
                triangles: fixture["input"]["wall_triangles"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|corners| {
                        let corners = corners.as_array().unwrap();
                        Triangle {
                            corners: [0, 1, 2].map(|i| corners[i].as_u64().unwrap() as i32),
                            wings: [-1; 3],
                            mask: 0x18,
                            surface: 0,
                        }
                    })
                    .collect(),
            };
            vec![
                StaticShape::new(
                    StaticGeometry::Mesh(Arc::new(
                        ContactMesh::new(mesh, [1.0; 3], u16::MAX).unwrap(),
                    )),
                    None,
                )
                .unwrap(),
            ]
        } else {
            vec![]
        };
        let mover = Mover::new(
            MoverSettings {
                gravity_scale: 0.0,
                ..MoverSettings::default()
            },
            Arc::new(ContactWorld::new(shapes).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        let runtime = factory
            .start(OwnerBodySpawn {
                controller: OWNER,
                entity: id.entity,
                body_filter: crate::match_scene::team_bit(0).unwrap(),
                slot: 0,
                frame,
                pose: mover.pose(),
                eye_position: None,
                quanta: RETAIL_QUANTA,
                skin: 0,
            })
            .unwrap();
        let body = Body {
            id,
            hero: hero.guid,
            skin: SkinTheme::default(),
        };
        let mut delivery = WorldDelivery::new(OWNER);
        delivery.started = Some(frame);
        delivery.input.processed = Some(frame);
        delivery.body = Some(body);
        delivery
            .bodies
            .insert(id.entity, (id.generation, Lifetime::Live));
        delivery.body_script = Some(BodyScript::new(body, frame, Some(frame), &runtime).unwrap());
        delivery.acknowledge(
            outcome(Arc::new(record(id.entity, Delivery::CreateBody(id))), true),
            Duration::ZERO,
        );
        let room = scene.simulation_mut();
        if let Some([face, center]) = trace_case {
            use crate::{collision_cache::CollisionCache, collision_scene::CollisionScene};
            let cache = CollisionCache {
                header: serde_json::from_str(
                    r#"{"meshes":[[4,2]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
                )
                .unwrap(),
                meshes: vec![Mesh {
                    vertices: vec![
                        [center - 2.0, 0.0, face],
                        [center - 2.0, 4.0, face],
                        [center + 2.0, 0.0, face],
                        [center + 2.0, 4.0, face],
                    ],
                    triangles: [[0, 1, 2], [2, 1, 3]]
                        .map(|corners| Triangle {
                            corners,
                            wings: [-1; 3],
                            mask: 0x18,
                            surface: 0,
                        })
                        .to_vec(),
                }],
                hulls: vec![],
                models: vec![],
                placements: vec![],
            };
            room.enable_spatial_queries(
                false,
                Arc::new(CollisionScene::movement_probe(&cache, &Default::default()).unwrap()),
                crate::script_graph::RoomSpatialNeeds::Queries,
            );
        }
        room.spawn_player_body(PlayerBodyState::new(
            id,
            OWNER,
            0,
            0,
            hero.guid,
            mover,
            Some(Box::new(runtime)),
            frame,
            None,
        ))
        .unwrap();
        if let Some(team) = peer_team {
            let peer = BodyId {
                entity: OWNER + 0x101,
                generation: 1,
            };
            room.spawn_player_body(PlayerBodyState::new(
                peer,
                OWNER + 0x100,
                1,
                team,
                hero.guid,
                mover_at([2.0, 0.0, 0.0]),
                None,
                frame,
                None,
            ))
            .unwrap();
            room.enable_movement_contacts(false, Arc::default());
            // The peer participates in owner contact only once its create is visible.
            delivery.created.insert(peer.entity);
        }
        Self {
            scene,
            delivery,
            id,
            instance,
            frame,
        }
    }

    fn step(&mut self, buttons: u32) {
        self.step_at(self.frame + 1, buttons);
    }

    fn step_at(&mut self, frame: u32, buttons: u32) {
        self.scene
            .simulation_mut()
            .prepare_movement_frame(frame)
            .unwrap();
        self.scene
            .simulation_mut()
            .prepare_spatial_frame(frame)
            .unwrap();
        self.delivery.queue_commands(
            frame,
            &[Command {
                frame,
                buttons,
                ..Command::default()
            }],
        );
        let card = PlayerCard::default();
        let viewer = PlayerAppearance {
            entity: OWNER,
            slot: 0,
            team: 0,
            card: &card,
        };
        self.delivery
            .advance_input(&mut self.scene, &viewer, RETAIL_QUANTA, 128)
            .unwrap();
        assert_eq!(self.player().mover.pose().frame, Some(frame));
        assert!(self.delivery.owner_script_fault.is_none());
        self.frame = frame;
    }

    fn player(&self) -> &PlayerBodyState {
        self.scene.simulation().player_body(self.id).unwrap()
    }
    fn state(&self, raw: u16) -> Option<&State> {
        self.player()
            .runtime
            .as_ref()
            .unwrap()
            .snapshot()
            .unwrap()
            .instances[&self.instance]
            .states
            .get(&raw)
    }

    fn active(&self, raw: u16) -> bool {
        self.state(raw).is_some_and(|state| state.active)
    }
}

#[test]
fn owner_static_trace_drives_the_authored_hit_gate() {
    // (instance, wall face z, wall center x, expectation) against the authored
    // 0x005a forms: a hit at or beyond the graph gate drives the MoveTo, a
    // nearer hit and a miss both roll back without one.
    for (instance, [face, center], expect_move) in [
        (2u16, [4.0, 0.0], true),
        (8, [1.0, 0.0], false),
        (8, [4.0, 10.0], false),
        (3, [4.0, 0.0], true),
    ] {
        let mut h = Harness::spawn(
            PreparedScene::fixture_for_bots(vec![]),
            1,
            instance,
            false,
            None,
            Some([face, center]),
        );
        h.step(SECONDARY);
        assert!(h.active(4));
        assert!(!h.active(1));
        assert_eq!(h.player().mover.pose().position, [0.0; 3]);
        let mut moved = false;
        for _ in 0..60 {
            h.step(SECONDARY);
            moved |= h.player().mover.pose().position[2] > 0.0;
        }
        assert!(!h.active(4));
        assert_eq!(moved, expect_move);
        assert_eq!(h.active(2), expect_move);
        assert!(!h.active(3));
    }
}

#[test]
fn owner_static_trace_releases_on_scope_exit_and_recasts() {
    let mut h = Harness::spawn(
        PreparedScene::fixture_for_bots(vec![]),
        1,
        6,
        false,
        None,
        Some([4.0, 0.0]),
    );
    h.step(SECONDARY);
    assert!(h.active(4));
    // Releasing the button while the ray is in flight cancels the trace scope
    // before the next driven step; nothing was handed to physics yet.
    h.step(0);
    assert!(!h.active(4));
    assert_eq!(h.player().mover.pose().position, [0.0; 3]);
    assert!(h.player().mover.owner_move_result(h.frame).is_none());
    // A fresh press re-arms the state machine and the trace runs again.
    h.step(SECONDARY);
    assert!(h.active(4));
    assert!(h.state(4).unwrap().activation > 1);
    let activation = h.state(4).unwrap().activation;
    for _ in 0..60 {
        h.step(SECONDARY);
    }
    assert!(!h.active(4));
    assert_eq!(h.state(4).unwrap().activation, activation);
    assert!(h.active(2));
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_shipped_default_spawns_without_the_support_class_rejection() {
    // Live-shape regression: the shipped factory default (no policy opt-in
    // anywhere) must take her full kit — including the support graph 0x001B
    // whose unmodeled active-state classes rejected a body startup in the
    // field once. The rejection path (`body startup rejected`) fires before
    // the body exists, so an initialized, report-free script here is proof
    // the spawn reaches lifecycle: default policy + the plain-base fallback
    // + the writerless-projection omission all have to hold at once.
    let mut lane = HookLane::full_kit(30.0);
    lane.step(0);
    let player = lane
        .scene
        .simulation()
        .player_body(lane.id)
        .expect("the shipped default spawns the body");
    let runtime = player
        .runtime
        .as_ref()
        .expect("the shipped default policy keeps the body script alive");
    let snapshot = runtime.snapshot().expect("projected baseline");
    assert!(
        snapshot.initialization_complete(),
        "unmodeled support classes may not gate the baseline under the default policy"
    );
    assert!(
        !snapshot
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.site.graph == 0x001B),
        "graph 0x001B reported under the shipped default: {:?}",
        snapshot.diagnostics
    );
    assert!(
        !snapshot.diagnostics.iter().any(|diagnostic| matches!(
            diagnostic.issue,
            crate::script_init::Issue::UnsupportedState(_)
        )),
        "an UnsupportedState report means a stale binary predating the policy fallback"
    );
    // The scene is still the lane's control path: one movement step in, the
    // owner exists with a finite mover pose.
    let position = lane.position();
    assert!(position.iter().all(|axis| axis.is_finite()));
    // The field rejection happened at the owner-view baseline validation,
    // not at init: exercise that exact production leg. A writerless
    // unmodeled active state must be omitted from both baselines under the
    // default policy (reference observer parity), never rejected, so the
    // spawn proceeds (`body startup rejected` is the delivery.rs gate this
    // call guards).
    crate::game_replication::validate_body_baselines(&**runtime, lane.id.entity)
        .expect("body baselines validate under the shipped default policy");
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_under_the_normal_policy_runs_the_pull_to_the_terminal_and_latches_the_press() {
    // The unspliced prepared kit under the reference production policy,
    // walked on the authored clock (one commanded frame per step, like the
    // isolated lane). Measured facts pinned here:
    // * every admission predicate of the hook Ability (slot 36) passes on
    //   authored data: the start button is logical 35 — command bit 0x008
    //   in the dispatch table (no hero constant) — the cooldown variable 61
    //   reads 0.0, the start/continue conditions (the INSTANCE-scoped stack
    //   variable 9570 — the reference dynamic-ref scope bit m_60DB8F99 = 0
    //   is instance, 1 is entity, expr.py:41) resolve true because the
    //   graph's own init-armed Stack (slot 44, position 99) binds it, fed
    //   by the init-armed chain SetVar(33) -> BooleanSwitch(80) ->
    //   Stack(99); the stack gate m_3C0EB4A6 is absent. The press is then
    //   consumed, the ability pushes to the entity ability stack (variable
    //   33, priority key 0x0C40000000000001) and starts.
    // * the init-armed 150 ms gate Wait (slot 27) then begins the trace
    //   (slot 0), the hit on the 4 m panel takes the hit case, and the
    //   MoveTo (slot 1) travels to the panel: the pose reaches z = 4.0 and
    //   the activation completes. (After the terminal the residual glide
    //   carries the pose past the panel under this lane's gravity-off,
    //   frictionless mover settings — mover physics, not script.)
    // * the launch subgraph re-arms the listener (slot 3, activation 2)
    //   during the pull, and the movement terminal retires it again with
    //   its member list — the authored teardown.
    // * the press stays latched while the button is held (no authored hold
    //   button m_A368407D, no early-press window m_246438AD): every frame
    //   sees the same single movement activation — a held button does not
    //   re-start the pull. The release-then-fresh-press cycle (with and
    //   without an authored cooldown) is the renumbered pair contract
    //   `cooldown_var_admission_pair` in `script_init::tests::
    //   abilities_and_weapons`, which keeps the clock out of this kit.
    let mut lane = HookLane::full_kit(4.0);
    lane.step(0x040 | 0x008);
    let first_listener = lane
        .states()
        .into_iter()
        .find(|state| state.raw == 3)
        .map(|state| state.activation)
        .expect("the authored listener materializes with the press");
    let mut first_move = None;
    let mut saw_travel = false;
    let mut at_panel = false;
    for _ in 0..89 {
        lane.step(0x040 | 0x008);
        for state in lane.states().iter().filter(|state| state.move_to) {
            match first_move {
                Some(activation) => assert_eq!(
                    activation, state.activation,
                    "a held button must not re-start the pull (reference stop latch)"
                ),
                None => first_move = Some(state.activation),
            }
        }
        let z = lane.position()[2];
        saw_travel |= z > 0.5;
        at_panel |= (z - 4.0).abs() < 0.35;
    }
    assert!(saw_travel, "the owner must travel on the authored clock");
    assert!(at_panel, "the pull must reach the authored panel");
    assert!(first_move.is_some(), "the hit case must begin the movement");
    let states = lane.states();
    assert!(
        !states.iter().any(|state| state.move_to && state.active),
        "the activation must complete: {states:?}"
    );
    let listener = states
        .iter()
        .find(|state| state.raw == 3)
        .expect("the listener");
    assert!(
        listener.activation > first_listener && !listener.active,
        "the launch re-armed the listener and the terminal retired it again: {states:?}"
    );
    // Next press on the authored clock must not re-launch: measured in the
    // relaunch-gates scenario below, the cause is the running ability
    // itself (stop button zero; the armed 12 s reaches the raw variable but
    // the timer interception leaves the effective cooldown at zero for
    // every authored ability in this pack), so a fresh press never reaches
    // the start gate. Either way the witness stays the same: no second
    // movement activation.
    for _ in 0..30 {
        lane.step(0);
    }
    let before = lane.states();
    for _ in 0..9 {
        lane.step(0x040 | 0x008);
    }
    let after = lane.states();
    assert_eq!(
        after
            .iter()
            .filter(|state| state.move_to)
            .map(|state| state.activation)
            .collect::<Vec<_>>(),
        before
            .iter()
            .filter(|state| state.move_to)
            .map(|state| state.activation)
            .collect::<Vec<_>>(),
        "a fresh press inside the authored cooldown must not re-launch the pull"
    );
    let snapshot = lane
        .scene
        .simulation()
        .player_body(lane.id)
        .unwrap()
        .runtime
        .as_ref()
        .unwrap()
        .snapshot()
        .unwrap();
    assert!(
        !snapshot
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.site.graph == 0x005A),
        "the hook graph stays report-free: {:?}",
        snapshot.diagnostics
    );
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_under_the_normal_policy_records_the_out_of_range_miss() {
    // The same kit walk with the panel beyond the authored 20 m trace
    // length: the hit flag stays false, no movement activation ever
    // appears, and the owner stays in place (the listener still retires
    // with the trace's subgraph at the terminal).
    let mut lane = HookLane::full_kit(30.0);
    for _ in 0..90 {
        lane.step(0x040 | 0x008);
        assert!(
            !lane
                .states()
                .iter()
                .any(|state| state.move_to && state.active),
            "a miss must never begin the movement"
        );
    }
    let position = lane.position();
    assert!(
        position[2].abs() < 0.01,
        "a miss must leave the owner in place, got {position:?}"
    );
    // Bug 3: the reel ChaseVar names the owner's hand bone, which the
    // server has no transform for. An unresolved bone destination must
    // hold the rope endpoint the flight last wrote — never drag it toward
    // the world origin, which is what made the retract "fly away".
    let (variables, _) = kit_hook_view(&lane);
    assert_close(
        pinned_vector(&variables, 432),
        [0.0, 1.55, 20.0],
        1e-3,
        "the reel holds the flight endpoint at full rope",
    );
    assert!(
        pinned_vector(&variables, 432)[2] > 1.0,
        "the rope endpoint must not collapse toward the origin"
    );
    assert!(
        !lane
            .states()
            .iter()
            .any(|s| matches!(s.raw, 2 | 7) && s.active)
    );
    let first_cast = lane.trace_activation().expect("the first miss cast");
    lane.step(0);
    for _ in 0..35 {
        lane.step(0x008);
    }
    assert!(
        lane.trace_activation()
            .is_some_and(|cast| cast > first_cast)
    );
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_ground_jump_and_a_fresh_press_pin_the_relaunch_gates() {
    use crate::script_snapshot_wire::View;

    let mut lane = HookLane::full_kit(4.0);
    let inspect = |lane: &HookLane, attachment_live| {
        let runtime = lane
            .scene
            .simulation()
            .player_body(lane.id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap();
        let snapshot = runtime.snapshot().unwrap();
        let instance = lane.states_with_instance().unwrap();
        for view in [View::Owner, View::Observer] {
            let projected = runtime.encoder().project(snapshot, view).unwrap();
            assert_eq!(
                projected.instances[&instance]
                    .states
                    .get(&7)
                    .is_some_and(|s| s.active),
                attachment_live,
                "line attachment in {view:?}"
            );
        }
        snapshot.instances[&instance].states[&36].kind
    };
    for frame in 0..90 {
        lane.step(if frame < 3 { 0x008 } else { 0 });
        if lane.states().iter().any(|s| s.raw == 30 && s.active) {
            break;
        }
    }
    assert_eq!(lane.projection_var(59), Some(Value::Integer(2)));
    assert!(!lane.states().iter().any(|s| s.move_to && s.active));
    inspect(&lane, true);
    for _ in 0..14 {
        lane.step(0);
    }
    assert!(
        !lane.states().iter().any(|s| s.raw == 30 && s.active),
        "phase-2 timer did not finish"
    );
    assert_eq!(inspect(&lane, false), StateKind::Ability { flags: 0 });

    let hook = lane.states_with_instance().unwrap();
    let armed = lane.hook_cooldown(hook);
    let trace = lane.trace_activation().unwrap();
    assert_eq!(armed.current, 12.0);
    assert_eq!(armed.rate, 1.0);
    // Put the already-released body on a real ground contact before jumping.
    lane.seat_on_test_floor();
    let y = lane.position()[1];
    lane.step(0x040);
    assert!(lane.position()[1] > y, "the ground jump must move the feet");
    lane.step(0);
    lane.step(0x008);
    for _ in 0..12 {
        lane.step(0);
    }
    assert_eq!(lane.trace_activation(), Some(trace));
    let after = lane.hook_cooldown(hook);
    assert_eq!(
        (after.current, after.last_ms),
        (armed.current, armed.last_ms)
    );
    for _ in 0..760 {
        lane.step(0);
    }
    assert_eq!(lane.trace_activation(), Some(trace));
    lane.step(0x008);
    for _ in 0..16 {
        lane.step(0);
    }
    assert_ne!(lane.trace_activation(), Some(trace));
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_authored_relaunch_edges_pin_the_ability_start_path() {
    // Data-level truth of the relaunch path on the unspliced prepared pack:
    // the state-group boxes carry no plug API at all (editor grouping plus
    // the member list the reference `exit_subgraph` teardown consumes), so
    // the relaunch chain's begin edges live on the ability's start plug —
    // both edges present — while the listener, the gate wait, and the
    // launch trace have no plain begin edges: the re-begin entry is the
    // ability's press path (its press ring consumes a press only after the
    // cooldown and the start condition on stack variable 9570 pass), which
    // is exactly what the kit infrastructure starves under the production
    // policy (measured by the kit-gate scenario above) and what the
    // reference answers with its own component driver begin (`grapple.py`)
    // — the same entry the lifecycle begin exposes.
    let (bytes, _group) = prepared_bundle(false);
    let bundle: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    let graph = bundle["graphs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|g| g["index"].as_u64() == Some(90))
        .unwrap();
    let ability = graph["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .find(|node| node["class"] == "STUStatescriptStateAbility")
        .expect("the hook kit's ability state");
    let links = ability["fields"]["m_B969AFC9"]["links"]
        .as_array()
        .expect("the authored start plug");
    assert_eq!(
        links.len(),
        2,
        "the ability start plug must carry its two begin edges"
    );
    for pos in [5u64, 45, 1] {
        let node = graph["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["pos"].as_u64() == Some(pos))
            .unwrap();
        assert!(
            node["fields"]["m_beginPlug"]["links"]
                .as_array()
                .is_none_or(|list| list.is_empty()),
            "state at {pos} has no plain begin edge: the re-begin entry is the ability press path"
        );
    }
    let boxes = bundle["graphs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|g| g["index"].as_u64() == Some(90))
        .and_then(|g| g.get("containers"))
        .expect("graph 90 declares its state-group containers");
    for item in ["79", "94"] {
        let entry = &boxes[item];
        assert!(
            entry.is_string(),
            "container {item} is the bare class tag: no plug API is serialized \
             for state-group boxes in this build, so no begin edge was ever \
             dropped from them"
        );
    }
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn owner_hook_runs_the_prepared_widowmaker_graph() {
    // Production chain over prepared data: the real Widowmaker body graphs and
    // the real codec table. The trace ray category needs no bundle table: the
    // reference resolves it from the owner's mover proxy category, which the
    // movement pipeline carries into the VM.
    use crate::script_snapshot_wire::View;
    use std::{env, fs, path::PathBuf};

    let root = PathBuf::from(env::var_os("ORPHEUS_RUNTIME_DIR").expect("ORPHEUS_RUNTIME_DIR"));
    let packed = fs::read(root.join("data/game_graphs_174.json.gz")).unwrap();
    let bundle: serde_json::Value =
        serde_json::from_reader(flate2::read::GzDecoder::new(packed.as_slice())).unwrap();
    let trace_node = bundle["graphs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|graph| graph["index"].as_u64() == Some(90))
        .and_then(|graph| {
            graph["nodes"]
                .as_array()?
                .iter()
                .find(|node| node["class"] == "STU_4BBC5D09")
        })
        .expect("prepared Widowmaker hook graph");
    // The authored node must carry the shapes this lane accepts.
    assert_eq!(trace_node["client_only"].as_u64(), Some(0));
    assert_eq!(trace_node["server_only"].as_u64(), Some(0));
    assert_eq!(trace_node["remote"].as_i64(), Some(0));
    let store = GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap();
    // The extraction invariant now covers state-group references too: on the
    // full prepared pack every `{"item":N}` names a declared container and
    // every plug link resolves to a node or a declared box.
    assert_eq!(
        store.dangling_links().count(),
        0,
        "prepared pack referential integrity"
    );
    let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
    let hero = prepared_widowmaker_hero();
    let factory =
        PlayerBodyFactory::from_body(Arc::new(store), Arc::new(codecs), 0, &hero, None, false)
            .unwrap()
            .with_strict(true);
    // The report enumeration pins the strict reconstruction view: the
    // production (normal) policy is measured separately by the full-kit
    // lanes; here every remaining production class of her support graphs
    // must be named with its site, and the hook graph must stay clean.
    // Her hook listens on logical button 11 (authored config, not an engine
    // constant): read it from the same prepared bundle the VM runs.
    let listener = bundle["graphs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|graph| graph["index"].as_u64() == Some(90))
        .and_then(|graph| {
            graph["nodes"]
                .as_array()?
                .iter()
                .find(|node| node["class"] == "STUStatescriptStateLogicalButton")
        })
        .expect("prepared hook button listener");
    let hook = trace_button(listener);
    assert_eq!(hook, 11, "prepared hook listener drifted");

    let id = avatar(1, 1);
    let frame = 100;
    let mover = Mover::new(
        MoverSettings {
            gravity_scale: 0.0,
            ..MoverSettings::default()
        },
        Arc::new(ContactWorld::new(vec![]).unwrap()),
        [0.0; 3],
        0,
        0x18,
    )
    .unwrap();
    let runtime = factory
        .start(OwnerBodySpawn {
            controller: OWNER,
            entity: id.entity,
            body_filter: crate::match_scene::team_bit(0).unwrap(),
            slot: 0,
            frame,
            pose: mover.pose(),
            eye_position: None,
            quanta: RETAIL_QUANTA,
            skin: 0,
        })
        .unwrap();
    let mut scene = PreparedScene::fixture_for_bots(vec![]);
    let room = scene.simulation_mut();
    room.spawn_player_body(PlayerBodyState::new(
        id,
        OWNER,
        0,
        0,
        hero.guid,
        mover,
        Some(Box::new(runtime)),
        frame,
        None,
    ))
    .unwrap();

    let player = scene.simulation().player_body(id).unwrap();
    let runtime = player.runtime.as_ref().unwrap();
    let snapshot = runtime.snapshot().unwrap();
    assert!(
        snapshot.instances.values().any(|script| script.graph == 90),
        "the prepared hook graph must reach an instance"
    );
    let hook_states: Vec<_> = snapshot
        .instances
        .values()
        .filter(|script| script.graph == 90)
        .flat_map(|script| script.states.iter())
        .map(|(&raw, state)| (raw, state.active, &state.kind))
        .collect();
    assert!(
        hook_states.iter().any(|(_, active, _)| *active),
        "the hook state machine must be armed at init: {hook_states:?}"
    );
    assert!(
        !hook_states
            .iter()
            .any(|(_, active, kind)| *active && matches!(**kind, StateKind::StaticTrace { .. })),
        "the trace state must stay dormant before any press: {hook_states:?}"
    );

    // The prepared kit boots to a deterministic initialization report. None of
    // the diagnostics touches the hook graph: the trace chain, its listener,
    // the VERIFIED start codec, and the composed profile table are complete.
    // The entries are the remaining production diagnostics in the support
    // graphs of her full kit; each is an authored demand with its site.
    let error = runtime
        .encoder()
        .project(snapshot, View::Owner)
        .expect_err("prepared kit reports its initialization gaps");
    let error_text = error.to_string();
    assert!(
        error_text.starts_with("body A0000101 has 7 initialization diagnostics: "),
        "{error_text}"
    );
    for site in [
        "004B:21:Expression(UnsupportedConfig(\"STU_AC829876\"))",
        "01CF:27:Expression(UnsupportedConfig(\"STU_A77AC2AC\"))",
        "01CF:186:Expression(UnsupportedConfig(\"STU_25ECB55F\"))",
        "0B8B:89:Expression(UnsupportedConfig(\"STU_A3DEF368\"))",
        "01CA:122:Expression(UnsupportedConfig(\"STU_A77AC2AC\"))",
        "05AC:85:Expression(UnsupportedConfig(\"STU_A3DEF368\"))",
        "0B8B:63:Expression(UnsupportedConfig(\"STU_A3DEF368\"))",
    ] {
        assert!(error_text.contains(site), "missing {site} in {error_text}");
    }
    assert_eq!(
        error_text.matches(":Expression(UnsupportedConfig(").count()
            + error_text.matches(":UnsupportedState(").count(),
        7,
        "{error_text}"
    );
    assert!(
        !error_text.contains(":005A:") && !error_text.contains(":90:"),
        "the hook graph itself must stay diagnostic-free: {error_text}"
    );
}

fn trace_button(listener: &serde_json::Value) -> u8 {
    listener["fields"]["m_logicalButton"]["m_logicalButton"]
        .as_u64()
        .expect("authored logical button id") as u8
}

/// Loads the prepared 104319 graph bundle. The grapple ray category needs no
/// table: the reference resolves it from the owner's mover proxy category
/// (`script/driver.py`: `category = mover.category`), which the movement
/// pipeline already carries. Optionally splices in a test-only body binding
/// so a single authored graph can be measured in isolation. Returns the store
/// bytes and the authored trace node's state-group box item, as carried by
/// the real node.
fn prepared_bundle(widow_body: bool) -> (Vec<u8>, u64) {
    let root = std::path::PathBuf::from(
        std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("ORPHEUS_RUNTIME_DIR"),
    );
    let packed = std::fs::read(root.join("data/game_graphs_174.json.gz")).unwrap();
    let mut bundle: serde_json::Value =
        serde_json::from_reader(flate2::read::GzDecoder::new(packed.as_slice())).unwrap();
    let trace_node = bundle["graphs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|graph| graph["index"].as_u64() == Some(90))
        .and_then(|graph| {
            graph["nodes"]
                .as_array()?
                .iter()
                .find(|node| node["class"] == "STU_4BBC5D09")
        })
        .expect("prepared Widowmaker hook graph");
    let item = trace_node["fields"]["m_0B1AA8CA"]["item"]
        .as_u64()
        .expect("authored trace state group");
    if widow_body {
        // Test-only isolation: the same authored nodes, but the body binds
        // only the hook graph, so the stage matrix below measures the hook
        // chain without the support graphs' unrelated gaps. Her controller-
        // side manager is nulled with the rest of the body graph list.
        bundle["bodies"]["0x040000000000001C"] = json!({
            "hero": "0x02E000000000000A", "name": "Widowmaker",
            "graphs": [{"graph": 90, "m_75408640": 0}], "manager": null, "weapons": []
        });
    }
    (serde_json::to_vec(&bundle).unwrap(), item)
}

fn prepared_widowmaker_hero() -> Hero {
    let root = std::path::PathBuf::from(
        std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("ORPHEUS_RUNTIME_DIR"),
    );
    crate::game_heroes::HeroCatalog::load(root.join("data/game_heroes_174.json"))
        .unwrap()
        .get(0x02E0_0000_0000_000A)
        .expect("stock Widowmaker catalog entry")
        .clone()
}

/// The authored trace points at its launch through a state-group box
/// (`m_0B1AA8CA:{item:N}`). In the extracted bundle that item is a declared
/// state-group container serialized as a bare class tag. The runtime's own
/// `.01B` export check (orpheus-runtime, state_groups tests) proves this
/// build's export carries no transition-plug edges for these groups at all,
/// so the tag is faithful and the press must not route through the box; the
/// launch instead rides the source-side wait edge, which the extraction
/// preserves. If a future pack enriches the box, this assertion fires and
/// the routing path takes over.
fn assert_tag_only_state_group(bytes: &[u8], item: u64) {
    let bundle: serde_json::Value = serde_json::from_slice(bytes).unwrap();
    let graph = bundle["graphs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|graph| graph["index"].as_u64() == Some(90))
        .unwrap();
    let positions: Vec<u64> = graph["nodes"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|node| node["pos"].as_u64())
        .collect();
    assert!(
        !positions.contains(&item),
        "state-group item {item} unexpectedly resolves to an extracted node"
    );
    assert!(
        graph["containers"]
            .get(item.to_string())
            .is_some_and(serde_json::Value::is_string),
        "state-group item {item} now carries serialized begin links; \
         the extraction gap has been closed and the press can launch"
    );
}

const PREPARED_SITES: &str = "004B:21:Expression(UnsupportedConfig(\"STU_AC829876\")), \
    01CF:27:Expression(UnsupportedConfig(\"STU_A77AC2AC\")), \
    01CF:186:Expression(UnsupportedConfig(\"STU_25ECB55F\")), \
    0B8B:89:Expression(UnsupportedConfig(\"STU_A3DEF368\")), \
    01CA:122:Expression(UnsupportedConfig(\"STU_A77AC2AC\")), \
    05AC:85:Expression(UnsupportedConfig(\"STU_A3DEF368\")), \
    0B8B:63:Expression(UnsupportedConfig(\"STU_A3DEF368\"))";

/// The full Widowmaker kit (real body roots and all support graphs) driven
/// through the production command path for the given number of commanded
/// steps, under the chosen component policy. The hook's two authored
/// buttons (logical 11 and the ability press window) are the press under
/// test; `commands` counts one-frame press commands on consecutive frames.
fn drive_full_kit(
    strict: bool,
    buttons: u32,
    commands: u32,
    wall_z: Option<f64>,
) -> (PreparedScene, WorldDelivery, BodyId) {
    let (bytes, _trace_group) = prepared_bundle(false);
    let store = GraphStore::from_json(&bytes).unwrap();
    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
    let hero = prepared_widowmaker_hero();
    let factory =
        PlayerBodyFactory::from_body(Arc::new(store), Arc::new(codecs), 0, &hero, None, false)
            .unwrap()
            .with_strict(strict);
    let id = avatar(1, 1);
    let frame = 100;
    let mover = Mover::new(
        MoverSettings {
            gravity_scale: 0.0,
            ..MoverSettings::default()
        },
        Arc::new(ContactWorld::new(vec![]).unwrap()),
        [0.0; 3],
        0,
        0x18,
    )
    .unwrap();
    let runtime = factory
        .start(OwnerBodySpawn {
            controller: OWNER,
            entity: id.entity,
            body_filter: crate::match_scene::team_bit(0).unwrap(),
            slot: 0,
            frame,
            pose: mover.pose(),
            eye_position: None,
            quanta: RETAIL_QUANTA,
            skin: 0,
        })
        .unwrap();
    let body = Body {
        id,
        hero: hero.guid,
        skin: SkinTheme::default(),
    };
    let mut delivery = WorldDelivery::new(OWNER);
    delivery.started = Some(frame);
    delivery.input.processed = Some(frame);
    delivery.body = Some(body);
    delivery
        .bodies
        .insert(id.entity, (id.generation, Lifetime::Live));
    delivery.body_script = Some(BodyScript::new(body, frame, Some(frame), &runtime).unwrap());
    let mut scene = PreparedScene::fixture_for_bots(vec![]);
    if let Some(wall_z) = wall_z {
        // The same authored-shape panel the isolated lane uses: a quad facing
        // the owner's axis, registered as the room's movement probe so both
        // the trace sweep and the capsule travel see it.
        use crate::{collision_cache::CollisionCache, collision_scene::CollisionScene};
        let cache = CollisionCache {
            header: serde_json::from_str(
                r#"{"meshes":[[4,2]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
            )
            .unwrap(),
            meshes: vec![crate::collision_cache::Mesh {
                vertices: vec![
                    [-2.0, 0.0, wall_z as f32],
                    [-2.0, 4.0, wall_z as f32],
                    [2.0, 0.0, wall_z as f32],
                    [2.0, 4.0, wall_z as f32],
                ],
                triangles: [[0, 1, 2], [2, 1, 3]]
                    .map(|corners| crate::collision_cache::Triangle {
                        corners,
                        wings: [-1; 3],
                        mask: 0x18,
                        surface: 0,
                    })
                    .to_vec(),
            }],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        scene.simulation_mut().enable_spatial_queries(
            false,
            Arc::new(CollisionScene::movement_probe(&cache, &Default::default()).unwrap()),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
    }
    scene
        .simulation_mut()
        .spawn_player_body(PlayerBodyState::new(
            id,
            OWNER,
            0,
            0,
            hero.guid,
            mover,
            Some(Box::new(runtime)),
            frame,
            None,
        ))
        .unwrap();
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    delivery.queue_commands(
        frame + 1,
        &(1..=commands)
            .map(|step| Command {
                frame: frame + step,
                buttons,
                ..Command::default()
            })
            .collect::<Vec<_>>(),
    );
    delivery
        .advance_input(&mut scene, &viewer, RETAIL_QUANTA, 128)
        .unwrap();
    (scene, delivery, id)
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_diagnostics_gate_the_hook_under_the_strict_policy() {
    // Strict policy (the reference strict component): the initialization
    // report is relevant, so the very first commanded step stops the body
    // script and the authored hook never sees its press. The gate is the
    // reported set plus the one live entry the first commanded step adds at
    // graph 01CF; the hook graph carries none of them.
    let (bytes, trace_group) = prepared_bundle(false);
    assert_tag_only_state_group(&bytes, trace_group);
    let (scene, delivery, id) = drive_full_kit(true, 0x800, 1, None);

    let fault = delivery
        .owner_script_fault
        .as_ref()
        .expect("the strict policy stops the body on the report");
    let text = fault.error.to_string();
    assert!(
        text.starts_with("body A0000101 has 8 initialization diagnostics: "),
        "{text}"
    );
    assert_eq!(text.matches("STU_").count(), 8, "{text}");
    for site in PREPARED_SITES.split(", ") {
        assert!(text.contains(site), "missing {site} in {text}");
    }
    // The first commanded pose materializes one further support-graph entry;
    // it is reported with the same inventory shape.
    assert!(
        text.contains("01CF:141:Expression(UnsupportedConfig(\"STU_A77AC2AC\"))"),
        "{text}"
    );
    assert!(
        !text.contains(":005A:"),
        "the hook graph must stay diagnostic-free: {text}"
    );
    // The press never reached the hook and the gate tore the owner script
    // down: no body VM remains, and the pose stayed where it was.
    let player = scene.simulation().player_body(id).unwrap();
    assert_eq!(player.mover.pose().position, [0.0; 3]);
    assert!(
        player.runtime.is_none(),
        "the strict policy cancels the whole body script once the report gates it"
    );
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_under_the_normal_policy_reaches_the_authored_hook_chain() {
    // The reference normal policy (runtime.py: non-strict natives answer
    // typed null, unmodeled classes take the plain base lifecycle): the
    // press window must not fault, the hook must receive its press, and
    // the authored wait/trace chain must run to completion — including the
    // arrival query against a room with no spatial frame, which the
    // reference answers as a miss through the same typed-null path. Only
    // the always-loud data-loss reports (missing nodes, broken refs) could
    // remain, and the hook graph carries none.
    let (scene, delivery, id) = drive_full_kit(false, 0x040 | 0x008, 60, None);
    assert!(
        delivery.owner_script_fault.is_none(),
        "the normal policy must not gate the owner stream: {:?}",
        delivery
            .owner_script_fault
            .as_ref()
            .map(|fault| fault.error.to_string())
    );
    let player = scene.simulation().player_body(id).unwrap();
    assert!(
        player.runtime.is_some(),
        "the owner script survives the press window"
    );
    assert_eq!(player.mover.pose().position, [0.0; 3]);
    let runtime = player.runtime.as_deref().unwrap();
    let snapshot = runtime.snapshot().expect("the owner stream projects");
    assert!(
        snapshot.initialization_complete(),
        "the normal policy leaves no relevant report: {:?}",
        snapshot.diagnostics
    );
    assert!(
        !snapshot
            .diagnostics
            .iter()
            .any(|diagnostic| diagnostic.site.graph == 0x005A),
        "no report may come from the hook graph: {:?}",
        snapshot.diagnostics
    );
}

/// The isolated real hook graph driven through the production command path
/// under the reference normal policy, with a test wall panel at `wall_z`
/// on the owner's facing axis. The wall, the authored distance gate and the
/// movement parameters are real data; the room collision mesh is the only
/// synthetic stage element (the practice-range collision scene is not part
/// of the pack). Every step asserts the owner stream stays fault-free.
struct HookLane {
    scene: PreparedScene,
    delivery: WorldDelivery,
    id: BodyId,
    now: u32,
    card: PlayerCard,
}

/// One projected graph-90 state of the lane, flattened to the facts the
/// scenarios observe: liveness, kind, and the activation counter (a fresh
/// launch must change it).
#[derive(Debug)]
struct LaneState {
    raw: u16,
    active: bool,
    trace: bool,
    move_to: bool,
    activation: u16,
}

impl HookLane {
    fn flavors(isolate: bool, wall_z: f64) -> Self {
        Self::flavors_cache(isolate, panel_cache(wall_z))
    }

    fn flavors_cache(isolate: bool, cache: CollisionCache) -> Self {
        Self::flavors_from(isolate, cache, 0.0, [0.0; 3])
    }

    fn flavors_from(
        isolate: bool,
        cache: CollisionCache,
        gravity_scale: f64,
        start: [f64; 3],
    ) -> Self {
        // The authored hook graph alone, under the production policy: the
        // press runs the real listener into the wait, the wait finishes and
        // begins the static trace through the room's collision scene, and
        // the hit branch requests the authored movement on the owner's own
        // mover. The launch needs no state-group routing here: the wait's
        // onEnd edge is extracted with plain source-side links; the group
        // boxes stay tag-only because this build's `.01B` export carries no
        // transition-plug edges for them at all (pinned by the runtime
        // state-group tests; the composer's serializer restores edges only
        // where the export has them).
        let (bytes, trace_group) = prepared_bundle(isolate);
        let store = GraphStore::from_json(&bytes).unwrap();
        assert_tag_only_state_group(&bytes, trace_group);
        let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
        let codecs = CodecTable::load(root.join("data/statescript_codecs_174.json")).unwrap();
        let hero = prepared_widowmaker_hero();
        let factory =
            PlayerBodyFactory::from_body(Arc::new(store), Arc::new(codecs), 0, &hero, None, false)
                .unwrap();
        let id = avatar(1, 1);
        let frame = 100;
        let mover = Mover::new(
            MoverSettings {
                gravity_scale,
                ..MoverSettings::default()
            },
            Arc::new(ContactWorld::new(vec![]).unwrap()),
            start,
            0,
            0x18,
        )
        .unwrap();
        let runtime = factory
            .start(OwnerBodySpawn {
                controller: OWNER,
                entity: id.entity,
                body_filter: crate::match_scene::team_bit(0).unwrap(),
                slot: 0,
                frame,
                pose: mover.pose(),
                eye_position: None,
                quanta: RETAIL_QUANTA,
                skin: 0,
            })
            .unwrap();
        let body = Body {
            id,
            hero: hero.guid,
            skin: SkinTheme::default(),
        };
        let mut delivery = WorldDelivery::new(OWNER);
        delivery.started = Some(frame);
        delivery.input.processed = Some(frame);
        delivery.body = Some(body);
        delivery
            .bodies
            .insert(id.entity, (id.generation, Lifetime::Live));
        delivery.body_script = Some(BodyScript::new(body, frame, Some(frame), &runtime).unwrap());
        let mut scene = PreparedScene::fixture_for_bots(vec![]);
        let room = scene.simulation_mut();
        use crate::collision_scene::CollisionScene;
        room.enable_spatial_queries(
            false,
            Arc::new(CollisionScene::movement_probe(&cache, &Default::default()).unwrap()),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        room.spawn_player_body(PlayerBodyState::new(
            id,
            OWNER,
            0,
            0,
            hero.guid,
            mover,
            Some(Box::new(runtime)),
            frame,
            None,
        ))
        .unwrap();
        Self {
            scene,
            delivery,
            id,
            now: frame,
            card: PlayerCard::default(),
        }
    }

    fn step(&mut self, buttons: u32) {
        self.step_aim(buttons, 0, 0);
    }

    /// One commanded frame with an explicit aim: the trace reads the
    /// owner's facing through the body pose, so the lane aims exactly where
    /// the reference fixture's click pointed.
    fn step_aim(&mut self, buttons: u32, yaw: i32, pitch: i32) {
        self.now += 1;
        self.scene
            .simulation_mut()
            .prepare_movement_frame(self.now)
            .unwrap();
        self.scene
            .simulation_mut()
            .prepare_spatial_frame(self.now)
            .unwrap();
        self.delivery.queue_commands(
            self.now,
            &[Command {
                frame: self.now,
                buttons,
                yaw,
                pitch,
                ..Command::default()
            }],
        );
        let viewer = PlayerAppearance {
            entity: OWNER,
            slot: 0,
            team: 0,
            card: &self.card,
        };
        self.delivery
            .advance_input(&mut self.scene, &viewer, RETAIL_QUANTA, 128)
            .unwrap();
        assert!(
            self.delivery.owner_script_fault.is_none(),
            "the authored hook chain must run without a fault: {:?}",
            self.delivery
                .owner_script_fault
                .as_ref()
                .map(|fault| fault.error.to_string())
        );
    }

    fn new(wall_z: f64) -> Self {
        Self::flavors(true, wall_z)
    }

    /// The unspliced prepared kit (every authored graph of her body) with a
    /// collision panel at `wall_z`, driven frame by frame under the
    /// reference production policy.
    fn full_kit(wall_z: f64) -> Self {
        Self::flavors(false, wall_z)
    }

    /// The T13f rim geometry turned into the z-forward lane, with the face
    /// at z=14 to keep the lip within the unmodified kit's 20 m trace.
    fn full_kit_rim() -> Self {
        Self::flavors_cache(false, rim_cache(&[]))
    }

    /// The same rim with the reference's fin occluder: an unrelated block
    /// standing on the eye-to-anchor line before the lip.
    fn full_kit_rim_fin() -> Self {
        Self::flavors_cache(false, rim_cache(&[(11.2, 11.8, 4.7, 6.8)]))
    }

    fn states(&self) -> Vec<LaneState> {
        let runtime = self
            .scene
            .simulation()
            .player_body(self.id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap();
        runtime
            .snapshot()
            .unwrap()
            .instances
            .iter()
            .filter(|(_, script)| script.graph == 90)
            .flat_map(|(_, script)| script.states.iter())
            .map(|(&raw, state)| LaneState {
                raw,
                active: state.active,
                trace: matches!(state.kind, StateKind::StaticTrace { .. }),
                move_to: matches!(state.kind, StateKind::MoveTo { .. }),
                activation: state.activation,
            })
            .collect()
    }

    /// The body-own instance id of the hook graph (90), when projected.
    fn states_with_instance(&self) -> Option<u16> {
        let runtime = self
            .scene
            .simulation()
            .player_body(self.id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap();
        runtime
            .snapshot()
            .unwrap()
            .instances
            .iter()
            .find(|(_, script)| script.graph == 90)
            .map(|(id, _)| *id)
    }

    fn begin_listener(&mut self, instance: u16, raw: u16) {
        let runtime = self
            .scene
            .simulation_mut()
            .player_body_mut(self.id)
            .unwrap()
            .runtime
            .as_mut()
            .unwrap();
        runtime.begin_state(instance, raw).unwrap();
    }

    /// The projected (linked) value of a hook-graph variable: the effective
    /// binding first (what the HUD-facing encoder writes for slot-0 links),
    /// then the raw instance variable, then the entity table.
    fn hook_cooldown(&self, instance: u16) -> crate::script_init::AbilityCooldown {
        self.scene
            .simulation()
            .player_body(self.id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap()
            .snapshot()
            .expect("the owner stream projects")
            .instances[&instance]
            .states[&36]
            .ability_cooldown
            .expect("the hook ability's cooldown payload")
    }

    fn projection_var(&self, id: u32) -> Option<Value> {
        let runtime = self
            .scene
            .simulation()
            .player_body(self.id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap();
        let snapshot = runtime.snapshot()?;
        snapshot
            .resolved_bindings
            .iter()
            .find(|((_, variable), _)| *variable == id)
            .map(|(_, value)| value.clone())
            .or_else(|| {
                snapshot
                    .instances
                    .values()
                    .filter(|instance| instance.graph == 90)
                    .find_map(|instance| instance.variables.get(&id).cloned())
            })
            .or_else(|| snapshot.variables.get(&id).cloned())
    }

    /// The activation counter of the authored trace state (slot 0), when it
    /// exists: a second trace begins only after the ability admits a press.
    fn trace_activation(&self) -> Option<u16> {
        self.states()
            .into_iter()
            .find(|state| state.trace)
            .map(|state| state.activation)
    }

    fn seat_on_test_floor(&mut self) {
        use crate::{collision_cache::CollisionCache, collision_scene::CollisionScene};
        let cache = CollisionCache {
            header: serde_json::from_str(
                r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
            )
            .unwrap(),
            meshes: vec![Mesh {
                vertices: vec![[-40.0, 0.0, -20.0], [0.0, 0.0, 60.0], [40.0, 0.0, -20.0]],
                triangles: vec![Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                }],
            }],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        let ground = Arc::new(CollisionScene::movement_probe(&cache, &Default::default()).unwrap());
        self.scene.fixture_set_movement_probe(Arc::clone(&ground));
        let room = self.scene.simulation_mut();
        room.enable_spatial_queries(
            false,
            ground,
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
        room.player_body_mut(self.id).unwrap().mover = Mover::new(
            MoverSettings::default(),
            Arc::new(ContactWorld::new(vec![]).unwrap()),
            [0.0; 3],
            0,
            0x18,
        )
        .unwrap();
        self.step(0);
    }

    fn position(&self) -> [f64; 3] {
        self.scene
            .simulation()
            .player_body(self.id)
            .unwrap()
            .mover
            .pose()
            .position
    }

    fn complete(&self) {
        let runtime = self
            .scene
            .simulation()
            .player_body(self.id)
            .unwrap()
            .runtime
            .as_ref()
            .expect("the normal policy keeps the owner stream");
        let snapshot = runtime.snapshot().expect("the owner stream projects");
        assert!(
            snapshot.initialization_complete(),
            "the authored chain leaves no report: {:?}",
            snapshot.diagnostics
        );
    }
}

const HOOK_PRESS: u32 = 0x040 | 0x008;

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_far_hit_runs_the_trace_to_the_terminal() {
    // A wall at 4 m sits inside the authored [2.5, 20] gate: the hit flag
    // is true, the hit branch begins the movement, the production mover
    // walks toward the hit point, and the activation reaches its authored
    // terminal (no active movement remains) without any report.
    let mut lane = HookLane::new(4.0);
    lane.step(HOOK_PRESS);
    let mut saw_trace = false;
    let mut saw_move = false;
    let mut terminal = false;
    for _ in 0..180 {
        lane.step(HOOK_PRESS);
        let states = lane.states();
        saw_trace |= states.iter().any(|state| state.trace);
        saw_move |= states.iter().any(|state| state.move_to);
        terminal |= saw_move && !states.iter().any(|state| state.move_to && state.active);
    }
    assert!(
        saw_trace,
        "the finished wait must begin the authored trace through the room's collision scene"
    );
    assert!(
        saw_move,
        "the hit branch must request the authored movement on the owner"
    );
    let position = lane.position();
    assert!(
        position[2] > 0.0 && position.iter().all(|axis| axis.is_finite()),
        "the production mover must walk toward the authored hit point, got {position:?}"
    );
    assert!(
        terminal,
        "the movement activation must reach its authored terminal"
    );
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_near_hit_trips_the_authored_physics_explosion() {
    // A wall at 1.5 m is closer than the graph's own 2.0 m watcher: the
    // gate Wait's end plug begins the movement and the BooleanSwitch (pos
    // 90) together, and the switch's authored case `distance(point, owner)
    // < 2.0` runs the explosion chain. The authored chain records the hit
    // and requests the pull (the class's 2.5 m parameter has no recoverable
    // consumer in the current inputs, so nothing here vetoes the launch);
    // the production result is that the explosion impulse puts the owner
    // airborne before the pull can cover the gap.
    let mut lane = HookLane::new(1.5);
    lane.step(HOOK_PRESS);
    let mut saw_trace = false;
    let mut saw_move = false;
    let mut airborne = false;
    for _ in 0..180 {
        lane.step(HOOK_PRESS);
        let states = lane.states();
        saw_trace |= states.iter().any(|state| state.trace);
        saw_move |= states.iter().any(|state| state.move_to);
        airborne |= lane.position()[1] > 0.5;
    }
    assert!(saw_trace, "the trace must still run and record the hit");
    assert!(
        saw_move,
        "the authored near-hit chain does request the movement; the class \
         parameter alone does not veto the launch"
    );
    let position = lane.position();
    assert!(
        airborne,
        "the authored near-hit branch must launch the owner via the physics \
         explosion, got {position:?}"
    );
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_hit_beyond_the_authored_range_is_a_miss() {
    // A wall at 30 m is beyond the authored 20 m trace length: the ray
    // never reaches it, the hit flag stays false, and the miss branch
    // leaves the owner in place.
    let mut lane = HookLane::new(30.0);
    lane.step(HOOK_PRESS);
    let mut saw_trace = false;
    for _ in 0..120 {
        lane.step(HOOK_PRESS);
        saw_trace |= lane.states().iter().any(|state| state.trace);
        assert!(
            !lane.states().iter().any(|state| state.move_to),
            "a hit beyond the authored trace length must not request movement"
        );
    }
    assert!(saw_trace, "the trace must still run and record the miss");
    assert_eq!(
        lane.position(),
        [0.0; 3],
        "the out-of-range miss branch must leave the owner in place"
    );
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_falling_hook_then_resolved_miss_has_no_second_drive() {
    let mut lane = HookLane::flavors_from(false, rim_cache(&[]), 1.0, [0.0, 4.0, 0.0]);
    let pose = |lane: &HookLane| {
        lane.scene
            .simulation()
            .player_body(lane.id)
            .unwrap()
            .mover
            .pose()
    };
    lane.step(0);
    assert!(pose(&lane).velocity[1] < 0.0);
    let mut first_drive = None;
    for frame in 0..100 {
        let before = pose(&lane);
        lane.step_aim(if frame < 3 { HOOK_PRESS } else { 0 }, 0, 0);
        let mover = &lane.scene.simulation().player_body(lane.id).unwrap().mover;
        if first_drive.is_none() && mover.scripted_motion_at(lane.now) {
            let after = mover.pose();
            assert!(before.velocity[1] < 0.0, "{before:?}");
            assert!(after.position[2] > before.position[2], "{after:?}");
            first_drive = lane
                .states()
                .into_iter()
                .find(|s| s.move_to)
                .map(|s| s.activation);
        }
    }
    let first_drive = first_drive.expect("the descending body must pull toward the wall");
    assert!(!lane.states().iter().any(|s| s.move_to && s.active));
    // Start the authored trace at its lifecycle boundary. This isolates the
    // recast from input/cooldown admission, which the other kit tests cover.
    lane.step_aim(0, 32_768, 0);
    let instance = lane.states_with_instance().unwrap();
    lane.begin_listener(instance, 0);
    for _ in 0..60 {
        lane.step_aim(0, 32_768, 0);
        assert!(
            lane.states()
                .iter()
                .filter(|s| s.move_to)
                .all(|s| !s.active && s.activation == first_drive)
        );
    }
    assert_eq!(lane.projection_var(435), Some(Value::Bool(false)));
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn prepared_hook_terminal_arms_the_listener_for_the_next_pull() {
    // The listener button (raw slot 3) is a member of the MoveTo's own
    // subgraph (m_subgraphPlug.m_FF3DAF1E lists it), so the authored design
    // retires the whole listener together with the pull when the movement
    // terminal tears the subgraph down. Consecutive inputs observe exactly
    // that: the release retires the in-flight trace while the pull lives;
    // after the terminal the button is gone and fresh press cycles change
    // nothing (no churn, no fault); the production lifecycle begin — the
    // entry the authored box begin edge will take in the room feed —
    // restarts the listener as a fresh activation, while the trace
    // relaunch stays pinned against the exact missing edges. Note the
    // first pull itself rode the init-armed gate Wait (its begin edge is
    // likewise box-internal): the authored press chain's only extracted
    // effect is the SetVar and the presentation trace at the chain head.
    let mut lane = HookLane::new(4.0);
    lane.step(HOOK_PRESS);
    let first_activation = loop {
        lane.step(HOOK_PRESS);
        let activations = lane
            .states()
            .into_iter()
            .filter(|state| state.trace)
            .map(|state| state.activation)
            .collect::<Vec<_>>();
        if !activations.is_empty() {
            break activations[0];
        }
    };
    for _ in 0..8 {
        lane.step(0);
    }
    let first_movement = lane
        .states()
        .into_iter()
        .filter(|state| state.move_to)
        .map(|state| state.activation)
        .collect::<Vec<_>>();
    assert!(
        !first_movement.is_empty(),
        "the authored chain must have requested the movement"
    );
    assert!(
        !lane
            .states()
            .iter()
            .any(|state| state.trace && state.active),
        "the release must retire the in-flight trace"
    );
    // The movement runs to its authored terminal (no active movement left).
    for _ in 0..180 {
        lane.step(0);
        let states = lane.states();
        if states.iter().any(|state| state.move_to)
            && !states.iter().any(|state| state.move_to && state.active)
        {
            break;
        }
    }
    let states = lane.states();
    assert!(
        states.iter().any(|state| state.move_to),
        "the movement must have existed"
    );
    assert!(
        !states.iter().any(|state| state.raw == 3 && state.active),
        "the terminal must retire the listener with the pull's subgraph"
    );
    // Natural re-arm first: with the listener retired, a full fresh press
    // cycle (down, hold, release) restarts nothing on its own. The state-
    // group boxes (the `STUStatescriptSubGraph` at `{item: 79/94}`) carry
    // only the editor grouping fields in this build's export — the member
    // list used by the reference `exit_subgraph` teardown and no plug API
    // at all — so no re-begin edge ever lived in them: the authored
    // re-begin entry is the ability's start check (the graph's
    // `m_680A2CB2` dispatch calls `button()` on the ability slot too),
    // whose press ring consumes a press only after the cooldown and the
    // start condition on stack variable 9570 pass, and whose start plug
    // carries the relaunch chain's begin edges (pinned data-level in the
    // full-kit scenario `full_kit_under_the_normal_policy_second_press_
    // waits_on_the_authored_gates`). The stack variable is fed by her
    // support graphs; in this isolated lane they are absent and under the
    // production policy the reference returns its typed null there too, so
    // the reference production answer is its own component driver
    // (`grapple.py` begins the launch node per press) — the entry the
    // lifecycle begin exposes below. Neither data flow her kit touches
    // (0x0480000000000083 at nodes 27/456/5348, 0x04800000000000C0 at
    // node 48) waits on this path either: their only users are
    // DataFlowMapping providers, strict-only in the reference.
    for _ in 0..8 {
        lane.step(0);
    }
    for _ in 0..16 {
        lane.step(HOOK_PRESS);
    }
    for _ in 0..8 {
        lane.step(0);
    }
    let states = lane.states();
    assert!(
        !states.iter().any(|state| state.raw == 3 && state.active),
        "the press cycle must not re-begin the listener while the ability gate starves: {states:?}"
    );
    assert_eq!(
        states
            .iter()
            .filter(|state| state.move_to)
            .map(|state| state.activation)
            .collect::<Vec<_>>(),
        first_movement,
        "no natural relaunch while the authored start condition is unsatisfied"
    );
    // The production lifecycle begin — the entry the reference component
    // driver takes for exactly this question — re-arms the listener; it is
    // entry evidence, not a substitute recast: the movement activations
    // stay untouched through it.
    let instance = lane
        .states_with_instance()
        .expect("the hook graph instance");
    for _ in 0..8 {
        lane.step(0);
    }
    lane.begin_listener(instance, 3);
    lane.step(0);
    for _ in 0..120 {
        lane.step(HOOK_PRESS);
    }
    let states = lane.states();
    assert!(
        states
            .iter()
            .any(|state| state.raw == 3 && state.activation != first_activation),
        "the re-armed listener must answer the fresh press as a new activation: {states:?}"
    );
    assert_eq!(
        states
            .iter()
            .filter(|state| state.move_to)
            .map(|state| state.activation)
            .collect::<Vec<_>>(),
        first_movement,
        "the direct lifecycle begin is re-arm entry evidence, not a substitute \
         recast: the movement relaunch still waits on the missing box begin edges"
    );
    assert!(
        lane.position().iter().all(|axis| axis.is_finite()),
        "the owner stays simulated through the re-arm window"
    );
    lane.complete();
}

#[test]
fn owner_move_to_retired_body_cannot_drive_replacement_generation() {
    let mut old = Harness::new(5, false, None);
    old.step(SECONDARY);
    old.step(SECONDARY);
    assert!(old.active(1));
    let mut replacement = Harness::spawn(old.scene, 2, 5, false, None, None);
    assert!(replacement.scene.simulation().player_body(old.id).is_none());

    // The same wire and graph slots now belong to a different body. A
    // command still bound to the retired delivery must not reach that body.
    old.delivery.queue_commands(
        103,
        &[Command {
            frame: 103,
            buttons: SECONDARY,
            ..Command::default()
        }],
    );
    let card = PlayerCard::default();
    let viewer = PlayerAppearance {
        entity: OWNER,
        slot: 0,
        team: 0,
        card: &card,
    };
    old.delivery
        .advance_input(&mut replacement.scene, &viewer, RETAIL_QUANTA, 128)
        .unwrap();
    assert_eq!(replacement.player().mover.pose().position, [0.0; 3]);
    assert!(!replacement.active(1));
    assert!(!replacement.active(2));
    assert!(!replacement.active(3));

    replacement.step(SECONDARY);
    for _ in 0..25 {
        replacement.step(SECONDARY);
    }
    assert!(replacement.active(2));
    assert!(!replacement.active(3));
}

#[test]
fn owner_move_to_arrival_and_obstructions_use_the_production_pose() {
    for (instance, wall, peer_team, expected) in [
        (1, false, None, MoveOutcome::Arrived),
        (4, false, Some(1), MoveOutcome::Blocked),
        (6, false, Some(0), MoveOutcome::Arrived),
        (7, true, None, MoveOutcome::Blocked),
    ] {
        let mut h = Harness::new(instance, wall, peer_team);
        h.step(SECONDARY);
        assert_eq!(
            h.state(1).unwrap().move_request(),
            Some(([4.0, 0.0, 0.0], 10.0))
        );
        h.step(SECONDARY);
        let pose = h.player().mover.pose();
        assert!((pose.position[0] - 0.16).abs() < 0.001, "{pose:?}");
        assert!((pose.velocity[0] - 10.0).abs() < 0.001, "{pose:?}");
        // The drive must terminate on the pose grid: a press against the
        // solid obstacle ends Blocked (the plate-only variant of this
        // fixture slowly tunnels — zero-thickness geometry has no fixed
        // point under a sustained drive; the box seats at face minus
        // capsule radius minus solver skin and reports Blocked).
        let mut press_poses = 0usize;
        for poses in 1..=120usize {
            press_poses = poses;
            if !h.active(1) {
                break;
            }
            h.step(SECONDARY);
        }
        assert!(
            press_poses < 120,
            "the drive must terminal within the window, pose {:?}",
            h.player().mover.pose().position
        );
        assert!(!h.active(1));
        assert_eq!(
            h.active(2),
            expected == MoveOutcome::Arrived,
            "instance={instance} pose={:?}",
            h.player().mover.pose()
        );
        assert_eq!(h.active(3), expected == MoveOutcome::Blocked);
        let next = if expected == MoveOutcome::Arrived {
            2
        } else {
            3
        };
        assert_eq!(h.state(next).unwrap().activation, 1);
        assert_eq!(
            h.player().mover.owner_move_result(h.frame).unwrap().1,
            expected
        );
        let pose = h.player().mover.pose();
        if expected == MoveOutcome::Arrived {
            assert!((pose.position[0] - 4.0).abs() < 0.001);
        } else {
            assert!((0.2..2.0).contains(&pose.position[0]), "{pose:?}");
        }
        h.step(SECONDARY);
        assert!(h.player().mover.owner_move_result(h.frame).is_none());
        assert_eq!(h.state(next).unwrap().activation, 1);
    }
}

#[test]
fn owner_move_to_gap_exit_cancels_before_next_pose_and_inherits_velocity() {
    let mut h = Harness::new(7, false, None);
    h.step(SECONDARY);
    h.step(SECONDARY);
    let before = h.player().mover.pose();
    let state = h.state(1).unwrap();
    let old = (state.activation, state.start_ms);
    // A large gap has an unposed release boundary before the next pose.
    h.step_at(h.frame + 70, 0);
    let after = h.player().mover.pose();
    assert!(!h.active(1));
    assert!(!h.active(2));
    assert!(h.active(3));
    assert!(!h.player().mover.scripted_motion_at(h.frame));
    assert!(h.player().mover.owner_move_result(h.frame).is_none());
    assert!(after.velocity[0] > 0.0 && before.velocity[0] > 0.0);
    assert!(after.position[0] < before.position[0] + 0.16);
    h.step(SECONDARY);
    let new = h.state(1).unwrap();
    assert_ne!((new.activation, new.start_ms), old);
    assert!(new.move_request().is_some());
}

#[test]
fn owner_move_to_rejects_real_completion_after_scope_release() {
    let mut h = Harness::new(5, false, None);
    h.step(SECONDARY);
    for _ in 0..24 {
        h.step(SECONDARY);
    }
    assert!(h.state(1).unwrap().move_request().is_some());
    // Physics still owns this pose; the subsequent release invalidates the
    // result before the VM delivers it at the end of the frame.
    h.step(0);
    assert_eq!(
        h.player()
            .mover
            .owner_move_result(h.frame)
            .map(|(_, result)| result),
        Some(MoveOutcome::Arrived)
    );
    assert!(!h.active(1));
    assert!(!h.active(2));
    assert_eq!(h.state(3).unwrap().activation, 1);
    h.step(SECONDARY);
    assert!(h.state(1).unwrap().move_request().is_some());
    assert!(!h.active(2));
    h.step(SECONDARY);
    assert!(!h.active(1));
    assert!(h.active(2));
    assert_eq!(h.state(2).unwrap().activation, 1);
}

/// One closed axis-aligned solid, twelve outward-wound triangles — the
/// reference fixture's `box()` (test_grapple_ledge_anchor.py) verbatim in
/// the z-forward lane orientation.
#[allow(clippy::too_many_arguments)]
fn box_mesh(
    x0: f32,
    x1: f32,
    y0: f32,
    y1: f32,
    z0: f32,
    z1: f32,
    vertices: &mut Vec<[f32; 3]>,
    triangles: &mut Vec<Triangle>,
) {
    let base = vertices.len();
    vertices.extend_from_slice(&[
        [x0, y0, z0],
        [x1, y0, z1],
        [x1, y0, z0],
        [x0, y0, z1],
        [x0, y1, z0],
        [x1, y1, z0],
        [x1, y1, z1],
        [x0, y1, z1],
    ]);
    for corners in [
        [0, 1, 2],
        [0, 3, 1],
        [4, 5, 6],
        [4, 6, 7],
        [0, 2, 5],
        [0, 5, 4],
        [1, 2, 5],
        [1, 5, 6],
        [3, 7, 4],
        [3, 4, 0],
        [7, 6, 1],
        [7, 1, 3],
    ] {
        triangles.push(Triangle {
            corners: corners.map(|corner| (base + corner) as i32),
            wings: [-1; 3],
            mask: 0x18,
            surface: 0,
        });
    }
}

/// The panel scene every earlier kit test pinned: one vertical quad at
/// `wall_z`, no floor (the raw pull's feet target sits at y=0 by design).
fn panel_cache(wall_z: f64) -> CollisionCache {
    use crate::collision_cache::CollisionCache;
    CollisionCache {
        header: serde_json::from_str(
            r#"{"meshes":[[4,2]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
        )
        .unwrap(),
        meshes: vec![Mesh {
            vertices: vec![
                [-2.0, 0.0, wall_z as f32],
                [-2.0, 4.0, wall_z as f32],
                [2.0, 0.0, wall_z as f32],
                [2.0, 4.0, wall_z as f32],
            ],
            triangles: [[0, 1, 2], [2, 1, 3]]
                .map(|corners| Triangle {
                    corners,
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                })
                .to_vec(),
        }],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    }
}

/// The T13f synthetic rim scene, scaled to the kit's authored 20 m rope: a
/// walkable floor slab and a 6 m wall whose walkable top stands 14 m in
/// front of the lane's spawn (the reference fixture with x and z exchanged
/// for the z-forward lane). Each fin is (z0, z1, y0, y1), a box spanning
/// x -1.1..1.1 and z z0..z1.
fn rim_cache(fins: &[(f32, f32, f32, f32)]) -> CollisionCache {
    use crate::collision_cache::CollisionCache;
    let mut vertices = vec![];
    let mut triangles = vec![];
    box_mesh(
        -30.0,
        30.0,
        -1.0,
        0.0,
        -20.0,
        14.0,
        &mut vertices,
        &mut triangles,
    );
    box_mesh(
        -30.0,
        30.0,
        0.0,
        6.0,
        14.0,
        18.0,
        &mut vertices,
        &mut triangles,
    );
    for &(z0, z1, y0, y1) in fins {
        box_mesh(-1.1, 1.1, y0, y1, z0, z1, &mut vertices, &mut triangles);
    }
    let header = format!(
        r#"{{"meshes":[[{},{}]],"hulls":0,"models":0,"placements":0,"identifiers":[]}}"#,
        vertices.len(),
        triangles.len()
    );
    CollisionCache {
        header: serde_json::from_str(&header).unwrap(),
        meshes: vec![Mesh {
            vertices,
            triangles,
        }],
        hulls: vec![],
        models: vec![],
        placements: vec![],
    }
}

/// The kit graph 90 instance's current variables and the destination of
/// the first live MoveTo, read through the same snapshot the projection
/// encoder consumes.
fn kit_hook_view(lane: &HookLane) -> (indexmap::IndexMap<u32, Value>, Option<[f64; 3]>) {
    let snapshot = lane
        .scene
        .simulation()
        .player_body(lane.id)
        .unwrap()
        .runtime
        .as_ref()
        .unwrap()
        .snapshot()
        .unwrap();
    let (_, instance) = snapshot
        .instances
        .iter()
        .find(|(_, instance)| instance.graph == 90)
        .expect("the hook graph");
    let destination = instance.states.values().find_map(|state| match state.kind {
        StateKind::MoveTo {
            movement: Some((destination, _)),
        } => Some(destination),
        _ => None,
    });
    (instance.variables.clone(), destination)
}

fn pinned_vector(variables: &indexmap::IndexMap<u32, Value>, id: u32) -> [f64; 3] {
    match variables.get(&id) {
        Some(Value::Vector(point)) => *point,
        other => panic!("variable {id} must carry a vector, got {other:?}"),
    }
}

fn assert_close(got: [f64; 3], want: [f64; 3], tolerance: f64, what: &str) {
    for axis in 0..3 {
        assert!(
            (got[axis] - want[axis]).abs() <= tolerance,
            "{what}: got {got:?}, want {want:?} +-{tolerance}"
        );
    }
}

/// The launch that meets the face 0.31 m under the 6 m lip at the 14 m
/// plane: the reference case-1 aim, rescaled to the kit rope (0.31 m under
/// the rim is inside the one-radius window the reference pins).
const LIP_AIM_PITCH: i32 = -3000;
/// The T13f case 2 aim: the raw belly hit 1.9 m under the lip, outside the
/// one-radius window.
const MID_WALL_AIM_PITCH: i32 = -1877;

/// Elevation of a lane pitch: Command pitch counts the same 65536 turns,
/// downward negative, so the upward aim is its negation.
fn aim_elevation(pitch: i32) -> f64 {
    f64::from(-pitch) * std::f64::consts::TAU / 65_536.0
}

fn direct_target(pitch: i32, plane_z: f64, eye_y: f64) -> [f64; 3] {
    [0.0, eye_y + plane_z * aim_elevation(pitch).tan(), plane_z]
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_direct_lip_routes_the_movement_to_the_validated_edge() {
    // Exported destination selector, byte-decoded: I1221 ? I1220 : I431.
    // The wall branch reads m_5ED803CF, so a promoted rope must publish its
    // validated transition there for the pull to stand on the lip; the raw
    // sample stays internal to the trace.
    let mut lane = HookLane::full_kit_rim();
    let mut destination = None;
    let mut first_move = None;
    let mut max_flight_y = f64::MIN;
    for _ in 0..320 {
        lane.step_aim(0x040 | 0x008, 0, LIP_AIM_PITCH);
        let (variables, move_to) = kit_hook_view(&lane);
        drop(variables);
        destination = destination.or(move_to);
        let states = lane.states();
        if let Some(state) = states
            .into_iter()
            .find(|state| state.move_to && state.active)
        {
            match first_move {
                Some(activation) => assert_eq!(
                    activation, state.activation,
                    "a promoted walk is one activation (reference stop latch)"
                ),
                None => first_move = Some(state.activation),
            }
            // While the pull owns the pose the feet may not exceed the
            // standing spot's own height — the lift is the lip, never a
            // hop above it (bug 2 is a lift-on-wall defect; a hop above
            // the destination would be a second one).
            max_flight_y = max_flight_y.max(lane.position()[1]);
        }
    }
    assert!(
        max_flight_y <= 6.05,
        "the walk never climbs above its own standing spot: {max_flight_y}"
    );
    let (variables, _) = kit_hook_view(&lane);
    assert_close(
        pinned_vector(&variables, 1220),
        [0.0, 6.0, 14.0],
        1e-2,
        "the authored final attach output carries the lip",
    );
    assert_close(
        pinned_vector(&variables, 431),
        [0.0, 6.0, 15.0],
        1e-2,
        "the movement output publishes the standing point, not the lip line",
    );
    assert_close(
        pinned_vector(&variables, 434),
        [0.0, 0.0, -1.0],
        1e-2,
        "the attach normal is the wall face",
    );
    let destination = destination.expect("the promoted walk begins a movement");
    // grapple.py `request_finish` T-0013: the rope sticks on the lip and
    // the BODY walks to the standing point — a full footprint (2r = 0.7 m
    // on this capsule) behind the face, so the arriving capsule is fully
    // supported on the shelf instead of half-buried in the wall.
    assert_close(
        destination,
        [0.0, 6.0, 15.0],
        1e-2,
        "the static branch walks onto the cap one radius behind the lip (2r inset, r=0.5)",
    );
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack ORPHEUS_RUNTIME_DIR"]
fn full_kit_aim_just_above_the_lip_attaches_to_the_same_edge() {
    let mut lane = HookLane::full_kit_rim();
    let pitch = -3400;
    let elevation = aim_elevation(pitch);
    let scene = crate::collision_scene::CollisionScene::movement_probe(
        &rim_cache(&[]),
        &Default::default(),
    )
    .unwrap();
    assert!(
        scene
            .checked_cast_ray(
                [0.0, 1.55, 0.0],
                [0.0, elevation.sin(), elevation.cos()],
                20.0,
                0x18
            )
            .unwrap()
            .is_none(),
        "the centre ray must sail over the same lip"
    );
    for frame in 0..90 {
        lane.step_aim(if frame < 3 { 0x008 } else { 0 }, 0, pitch);
        let (vars, movement) = kit_hook_view(&lane);
        if let Some(destination) = movement {
            assert_close(
                pinned_vector(&vars, 1220),
                [0.0, 6.0, 14.0],
                1e-2,
                "assisted anchor",
            );
            assert_close(
                destination,
                [0.0, 6.0, 15.0],
                1e-2,
                "without a centre hit, the walk still goes to the standing point on the cap",
            );
            lane.complete();
            return;
        }
    }
    panic!("the just-above aim did not start a pull");
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack ORPHEUS_RUNTIME_DIR"]
fn full_kit_pulls_a_mid_wall_hit_to_the_raw_anchor_without_a_lift() {
    let mut lane = HookLane::full_kit_rim();
    let want = direct_target(MID_WALL_AIM_PITCH, 14.0, 1.55);
    let mut destination = None;
    let mut max_flight_y: Option<f64> = None;
    let mut arrived = false;
    for _ in 0..150 {
        lane.step_aim(0x040 | 0x008, 0, MID_WALL_AIM_PITCH);
        let (variables, move_to) = kit_hook_view(&lane);
        drop(variables);
        destination = destination.or(move_to);
        let in_flight = lane
            .states()
            .into_iter()
            .any(|state| state.move_to && state.active);
        if in_flight {
            let y = lane.position()[1];
            max_flight_y = Some(max_flight_y.map_or(y, |previous| previous.max(y)));
        } else if max_flight_y.is_some() {
            arrived = true;
        }
    }
    let destination = destination.expect("a raw hit still begins the movement");
    assert_close(destination, want, 2e-2, "the direct pull target");
    let max_flight_y = max_flight_y.expect("the raw pull owns at least one movement frame");
    assert!(
        arrived,
        "the activation must complete inside the walk window"
    );
    assert!(
        max_flight_y <= want[1] + 0.1,
        "a raw wall pull may not exceed its own line: rose to {max_flight_y}, line ends at {}",
        want[1]
    );
    assert!(
        max_flight_y < 5.9,
        "a belly hit below the window never stands on the lip: {max_flight_y}"
    );
    let (variables, _) = kit_hook_view(&lane);
    assert_close(
        pinned_vector(&variables, 431),
        want,
        2e-2,
        "the rope stays on the raw face hit",
    );
    lane.complete();
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_keeps_the_direct_pull_behind_an_unrelated_occluder() {
    // The reference fin case: an unrelated slab stands on the eye-to-anchor
    // line below the lip. The sight test rejects the candidate — proximity
    // excuses nothing — and the pull lands on the fin's own face, never a
    // jump through the blocker.
    let mut lane = HookLane::full_kit_rim_fin();
    let want = direct_target(LIP_AIM_PITCH, 11.2, 1.55);
    let mut destination = None;
    for _ in 0..150 {
        lane.step_aim(0x040 | 0x008, 0, LIP_AIM_PITCH);
        let (variables, move_to) = kit_hook_view(&lane);
        drop(variables);
        destination = destination.or(move_to);
    }
    let destination = destination.expect("the fin face hit begins the movement");
    assert_close(destination, want, 2e-2, "the pull stops at the fin face");
    let (variables, _) = kit_hook_view(&lane);
    assert_close(
        pinned_vector(&variables, 431),
        want,
        2e-2,
        "the rope anchors on the fin, not the lip",
    );
    lane.complete();
}

#[test]
fn rim_cache_answers_the_lip_aim_where_the_fixture_pins_it() {
    use crate::collision_scene::CollisionScene;
    let scene = CollisionScene::movement_probe(&rim_cache(&[]), &Default::default()).unwrap();
    let el = aim_elevation(LIP_AIM_PITCH);
    let direction = [0.0, el.sin(), el.cos()];
    let hit = scene
        .checked_cast_ray([0.0, 1.55, 0.0], direction, 40.0, 0x18)
        .expect("both ray endpoints are inside the grid");
    let Some(hit) = hit else {
        panic!("the centre ray must meet the wall face");
    };
    assert_close(hit.point, [0.0, 5.69, 14.0], 0.01, "the fixture hit");
    assert_close(hit.normal, [0.0, 0.0, -1.0], 1e-3, "the wall face normal");
    let down = scene
        .checked_cast_ray([0.0, 7.0, 15.0], [0.0, -1.0, 0.0], 2.0, 0x18)
        .expect("inside the grid")
        .expect("the cap answers the standing probe");
    assert_close(down.point, [0.0, 6.0, 15.0], 1e-3, "the walkable top");
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_direct_floor_hit_keeps_the_raw_attachment() {
    let mut lane = HookLane::full_kit_rim();
    // Downward aim: the floor is hit before the rim and beyond the
    // minimum range. The final attachment must stay on that floor.
    for frame in 0..90 {
        lane.step_aim(if frame < 3 { 0x008 } else { 0 }, 0, 4096);
        if lane.projection_var(435) == Some(Value::Bool(true)) {
            let (vars, _) = kit_hook_view(&lane);
            let hit = pinned_vector(&vars, 431);
            assert_close(
                hit,
                [0.0, 0.0, 1.55 / aim_elevation(-4096).tan()],
                1e-2,
                "the centre ray hits the floor before the lip",
            );
            assert_eq!(pinned_vector(&vars, 1220), hit);
            lane.complete();
            return;
        }
    }
    panic!("the prepared kit did not record the floor hit");
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_translated_oblique_hit_publishes_static_attach() {
    use crate::script_snapshot_wire::View;
    use crate::{collision_cache::read_movement_cache, collision_scene::CollisionScene};

    let root = std::path::PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap());
    let cache =
        read_movement_cache(std::fs::File::open(root.join("collision/0688.col")).unwrap()).unwrap();
    let ids = Default::default();
    let spatial = Arc::new(CollisionScene::movement_probe(&cache, &ids).unwrap());
    let physical = Arc::new(ContactWorld::from_movement_cache(cache, &ids).unwrap());
    let (x, z) = (40.25, 34.5);
    let floor = spatial.triangles().ground(x, z, 20.0, -8.0, 0x18).unwrap();
    let mut lane = HookLane::full_kit(4.0);
    let room = lane.scene.simulation_mut();
    room.enable_spatial_queries(
        false,
        Arc::clone(&spatial),
        crate::script_graph::RoomSpatialNeeds::Queries,
    );
    room.player_body_mut(lane.id).unwrap().mover =
        Mover::new(MoverSettings::default(), physical, [x, floor.y, z], 0, 0x18).unwrap();
    let (yaw, pitch) = (27500, -4100);
    for _ in 0..20 {
        lane.step_aim(0, yaw, pitch);
    }
    let mut expected = None;
    let mut pull_frames = 0;
    for frame in 0..90 {
        lane.step_aim(if frame < 3 { 0x008 } else { 0 }, yaw, pitch);
        let runtime = lane
            .scene
            .simulation()
            .player_body(lane.id)
            .unwrap()
            .runtime
            .as_ref()
            .unwrap();
        let snapshot = runtime.snapshot().unwrap();
        let instance_id = lane.states_with_instance().unwrap();
        let instance = &snapshot.instances[&instance_id];
        if let Some(trace) = instance.states.get(&0).filter(|state| state.active) {
            if let StateKind::StaticTrace {
                origin,
                direction,
                length,
                category,
                ..
            } = trace.kind
            {
                expected = Some(
                    spatial
                        .checked_cast_ray(origin, direction, length, category)
                        .unwrap()
                        .expect("prepared scene has a direct hit")
                        .point,
                );
            }
        }
        if instance.states.get(&1).is_some_and(|state| state.active) {
            let point = expected.expect("the flight was observed before the pull");
            for view in [View::Owner, View::Observer] {
                let projected = runtime.encoder().project(snapshot, view).unwrap();
                let vars = &projected.instances[&instance_id].variables.values;
                for slot in [431, 1220] {
                    assert_close(
                        pinned_vector(vars, slot),
                        point,
                        1e-4,
                        "projected static hit",
                    );
                }
            }
            pull_frames += 1;
        }
    }
    assert!(
        pull_frames >= 2,
        "the attach must remain available during the pull"
    );
    lane.complete();
}

/// Cross product magnitude of the tip's offset from the origin against the
/// segment direction: zero exactly while the tip lies on the line.
fn off_segment(origin: [f64; 3], anchor: [f64; 3], tip: [f64; 3]) -> f64 {
    let a: [f64; 3] = std::array::from_fn(|i| anchor[i] - origin[i]);
    let b: [f64; 3] = std::array::from_fn(|i| tip[i] - origin[i]);
    let cross = [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ];
    let norm = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt();
    let len = (cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2]).sqrt();
    len / norm
}

/// Progress of the tip along the segment, 0 at the origin, 1 at the anchor.
fn along_segment(origin: [f64; 3], anchor: [f64; 3], tip: [f64; 3]) -> f64 {
    let a: [f64; 3] = std::array::from_fn(|i| anchor[i] - origin[i]);
    let b: [f64; 3] = std::array::from_fn(|i| tip[i] - origin[i]);
    (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / (a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_promoted_flight_tip_interpolates_to_the_lip() {
    // The presentation rope tip must run from the launch origin toward the
    // same validated edge the pull will terminate on, not along the raw ray
    // with a snap at arrival. Both promoted aims share the edge [0, 6, 14].
    let origin = [0.0f64, 1.55, 0.0];
    let edge = [0.0f64, 6.0, 14.0];
    for pitch in [LIP_AIM_PITCH, -3400] {
        let mut lane = HookLane::full_kit_rim();
        let mut samples = 0;
        let mut previous = None;
        for frame in 0..120 {
            lane.step_aim(if frame < 3 { 0x040 | 0x008 } else { 0 }, 0, pitch);
            let (variables, movement) = kit_hook_view(&lane);
            if movement.is_some() {
                break;
            }
            if let Some(Value::Vector(tip)) = variables.get(&432) {
                let tip = *tip;
                if frame >= 3 {
                    let off = off_segment(origin, edge, tip);
                    let along = along_segment(origin, edge, tip);
                    assert!(
                        off <= 1e-3 && (0.0..=1.0).contains(&along),
                        "aim {pitch} frame {frame}: tip {tip:?} is {off:.4} off the launch line to the edge (along {along:.4})"
                    );
                    if let Some(before) = previous {
                        assert!(
                            along >= before - 1e-6,
                            "aim {pitch} frame {frame}: the tip fell back"
                        );
                    }
                    previous = Some(along);
                    samples += 1;
                }
            }
        }
        assert!(
            samples >= 5,
            "aim {pitch}: too few in-flight tip samples ({samples})"
        );
        lane.complete();
    }
}

#[test]
#[ignore = "requires an authorized 104319 runtime pack at ORPHEUS_RUNTIME_DIR"]
fn full_kit_unpromoted_flight_tip_stays_on_the_raw_ray() {
    // A mid-wall direct hit and an out-of-range miss keep the historical
    // ray trajectory: the tip's distance to the raw ray line stays at zero
    // while the sample moves away from the origin toward the ray endpoint.
    for (pitch, _hit) in [(
        MID_WALL_AIM_PITCH,
        direct_target(MID_WALL_AIM_PITCH, 14.0, 1.55),
    )] {
        let elevation = aim_elevation(pitch);
        // A point one rope length along the raw centre ray.
        let reference = [
            0.0f64,
            1.55 + 20.0 * elevation.sin(),
            20.0 * elevation.cos(),
        ];
        let origin = [0.0f64, 1.55, 0.0];
        let mut lane = HookLane::full_kit_rim();
        let mut samples = 0;
        let mut previous = 0.0f64;
        for frame in 0..60 {
            lane.step_aim(if frame < 3 { 0x040 | 0x008 } else { 0 }, 0, pitch);
            let (variables, movement) = kit_hook_view(&lane);
            if movement.is_some() {
                break;
            }
            if let Some(Value::Vector(tip)) = variables.get(&432) {
                let tip = *tip;
                if frame >= 3 {
                    // The ray line runs origin -> (hit, or the eye-forward
                    // endpoint for the miss); the tip must be on it.
                    let along = along_segment(origin, reference, tip);
                    let off = off_segment(origin, reference, tip);
                    assert!(
                        off <= 1e-3 && (0.0..=1.0 + 1e-6).contains(&along),
                        "aim {pitch} frame {frame}: unpromoted tip {tip:?} left the ray (along {along:.4})"
                    );
                    assert!(along >= previous - 1e-6, "aim {pitch}: the tip fell back");
                    previous = along;
                    samples += 1;
                }
            }
        }
        assert!(
            samples >= 5,
            "aim {pitch}: too few in-flight samples ({samples})"
        );
        lane.complete();
    }
}
