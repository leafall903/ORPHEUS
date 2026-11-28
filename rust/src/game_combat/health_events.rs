//! Deferred notifications for room-authoritative health changes.

use std::{collections::VecDeque, sync::Arc};

use bevy_ecs::{entity::Entity, world::World};
use indexmap::IndexMap;

use crate::{
    game_hitscan::Impact,
    script_expr::valid_entity,
    script_init::{
        HealthTargetFacts, HitTags, HostEvent, HostHealthContext, RoomBodyIdentity,
        RoomEntityStatus,
    },
    vitals::NamedPoolLoss,
};

use super::{BotState, PlayerBodyState, RoomSimulation};

const MAX_ROOM_HEALTH_EVENTS: usize = 512;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) struct SampledBody {
    pub(super) wire: u32,
    pub(super) entity: Entity,
}

impl SampledBody {
    fn lookup(
        wire: u32,
        bots: &IndexMap<u32, Entity>,
        players: &IndexMap<u32, Entity>,
    ) -> Option<Self> {
        if !valid_entity(wire) {
            return None;
        }
        let &entity = bots.get(&wire).or_else(|| players.get(&wire))?;
        Some(Self { wire, entity })
    }

    pub(super) fn identity(self) -> RoomBodyIdentity {
        RoomBodyIdentity {
            wire: self.wire,
            generation: self.entity.to_bits(),
        }
    }

    pub(super) fn position(self, world: &World) -> Option<[f64; 3]> {
        let position = if let Some(state) = world.get::<BotState>(self.entity) {
            (state.bot.entity == self.wire).then_some(state.bot.position)?
        } else {
            let state = world.get::<PlayerBodyState>(self.entity)?;
            (state.id.entity == self.wire).then(|| state.mover.pose().position)?
        };
        position.into_iter().all(f64::is_finite).then_some(position)
    }

    fn room_status(self, world: &World, free_for_all: bool) -> Option<RoomEntityStatus> {
        let (team, alive) = if let Some(state) = world.get::<BotState>(self.entity) {
            (state.bot.entity == self.wire).then_some((
                state.bot.spawn.team,
                !state.bot.dead && state.vitals.death().is_none(),
            ))?
        } else {
            let state = world.get::<PlayerBodyState>(self.entity)?;
            (state.id.entity == self.wire).then(|| (state.team, state.alive()))?
        };
        Some(RoomEntityStatus {
            team: (!free_for_all).then_some(team),
            alive,
        })
    }

    pub(super) fn health_target_facts(self, world: &World) -> Option<HealthTargetFacts> {
        if let Some(state) = world.get::<BotState>(self.entity) {
            return (state.bot.entity == self.wire).then(|| state.health_target_facts());
        }
        let state = world.get::<PlayerBodyState>(self.entity)?;
        (state.id.entity == self.wire).then(|| state.health_target_facts())
    }
}

pub(super) struct HealthDamageEvidence<'a> {
    pub(super) impact: Option<(&'a Impact, &'a HealthTargetFacts)>,
    pub(super) named_pool_losses: &'a [NamedPoolLoss],
    pub(super) frame: Option<u32>,
}

pub(super) struct HealthEventContext<'a> {
    pub(super) world: &'a World,
    pub(super) damage: Option<HealthDamageEvidence<'a>>,
}

struct HealthApplied {
    source: Option<SampledBody>,
    target: SampledBody,
    delta: f64,
    killed: bool,
    hit_tags: Option<HitTags>,
    critical_hit: Option<bool>,
    damage_frame: Option<u32>,
    target_facts: Option<Arc<HealthTargetFacts>>,
    named_pool_losses: Option<Arc<[NamedPoolLoss]>>,
}

impl HealthApplied {
    fn resolve(&self, room: &RoomSimulation) -> Option<(HostEvent, [Option<SampledBody>; 2])> {
        let target = room.sample_health_body(self.target.wire);
        if target.is_some_and(|body| body != self.target) {
            // A replacement must not inherit a stale notification or its target ID.
            return None;
        }
        let source = self
            .source
            .filter(|&body| room.sample_health_body(body.wire) == Some(body));
        let event = HostEvent::HealthApplied {
            source: source.map(|body| body.wire),
            target: self.target.wire,
            delta: self.delta,
            killed: self.killed,
            hit_tags: self.hit_tags,
        };
        Some((event, [source.filter(|&body| body != self.target), target]))
    }
}

#[derive(Default)]
pub(super) struct RoomHealthEvents {
    enabled: bool,
    authored_hit_variant: Option<f32>,
    authored_elimination_feedback: bool,
    pending: VecDeque<HealthApplied>,
}

impl RoomHealthEvents {
    pub(super) fn stage(
        &mut self,
        source: Option<SampledBody>,
        target: SampledBody,
        delta: f64,
        killed: bool,
        context: HealthEventContext<'_>,
        room_status: Option<RoomEntityStatus>,
    ) {
        if !self.enabled
            || self.pending.len() >= MAX_ROOM_HEALTH_EVENTS
            || !delta.is_finite()
            || delta == 0.0
            || (killed && delta > 0.0)
        {
            return;
        }
        let (impact, mut target_facts) =
            match context.damage.as_ref().and_then(|damage| damage.impact) {
                Some((impact, facts))
                    if impact.entity == target.wire && facts.target == target.wire =>
                {
                    (Some(impact), Some(facts.clone()))
                }
                // A settled change without weapon tags can still authenticate the
                // target facts seen by a distinct source.
                None if source.is_some_and(|source| source != target) => {
                    (None, target.health_target_facts(context.world))
                }
                _ => (None, None),
            };
        if let Some(facts) = &mut target_facts {
            facts.room_status = room_status;
        }
        self.pending.push_back(HealthApplied {
            source,
            target,
            delta,
            killed,
            hit_tags: impact.map(|impact| impact.tags),
            critical_hit: impact.map(|impact| impact.critical),
            damage_frame: context.damage.as_ref().and_then(|damage| damage.frame),
            target_facts: target_facts.map(Arc::new),
            named_pool_losses: context
                .damage
                .map(|damage| Arc::from(damage.named_pool_losses)),
        });
    }
}

impl RoomSimulation {
    pub(crate) fn enable_host_health_events(&mut self) {
        self.health_events.enabled = true;
    }

    pub(crate) fn enable_authored_hit_feedback(&mut self, variant: f32) {
        self.health_events.authored_hit_variant = Some(variant);
        self.enable_host_health_events();
    }

    pub(crate) fn enable_authored_elimination_feedback(&mut self) {
        self.health_events.authored_elimination_feedback = true;
    }

    pub(super) fn authored_elimination_target(&self, wire: u32) -> Option<HealthTargetFacts> {
        if !self.health_events.authored_elimination_feedback {
            return None;
        }
        let target = self.sample_health_body(wire)?;
        let mut facts = target.health_target_facts(&self.world)?;
        let status = target.room_status(&self.world, self.free_for_all)?;
        if status.alive {
            return None;
        }
        facts.room_status = Some(status);
        Some(facts)
    }

    pub(super) fn record_health_applied(
        &mut self,
        source: u32,
        target: u32,
        delta: f64,
        killed: bool,
        damage: Option<HealthDamageEvidence<'_>>,
    ) {
        #[cfg(feature = "inspector")]
        if delta.is_finite() && delta < 0.0 && crate::owner_trace::playtest_enabled() {
            let frame = damage.as_ref().and_then(|evidence| evidence.frame);
            let impact = damage
                .as_ref()
                .and_then(|evidence| evidence.impact)
                .map(|(impact, _)| impact);
            let critical = impact.map(|impact| impact.critical);
            let geometry = impact.and_then(|impact| impact.geometry);
            crate::owner_trace::playtest(format_args!(
                "combat settled frame={frame:?} source={source:08X} target={target:08X} delta={delta} critical={critical:?} killed={killed} health_events={} geometry={geometry:?}",
                self.health_events.enabled,
            ));
        }
        if !self.health_events.enabled {
            return;
        }
        let Some(target) = self.sample_health_body(target) else {
            return;
        };
        let source = self.sample_health_body(source);
        let has_impact = damage
            .as_ref()
            .is_some_and(|damage| damage.impact.is_some());
        let context = HealthEventContext {
            world: &self.world,
            damage,
        };
        let room_status = if has_impact || source.is_some_and(|source| source != target) {
            self.sample_health_target_room_status(target)
        } else {
            None
        };
        self.health_events
            .stage(source, target, delta, killed, context, room_status);
    }

    pub(super) fn sample_health_target_room_status(
        &self,
        target: SampledBody,
    ) -> Option<RoomEntityStatus> {
        let queries = self.spatial_queries.as_ref()?;
        target.room_status(&self.world, queries.free_for_all)
    }

    /// Queue each event for the recipients' next VM frames, in settlement order.
    pub(super) fn flush_health_events(&mut self) {
        while let Some(mut change) = self.health_events.pending.pop_front() {
            // Coalesce multi-pellet weapon impacts sharing the same `(source, target, damage_frame)`
            // so shotguns deliver one consolidated `HostEvent::HealthApplied` + hit cue per volley.
            if change.delta < 0.0
                && change.critical_hit.is_some()
                && change.damage_frame.is_some()
                && change.source.is_some_and(|s| s != change.target)
            {
                let mut rest = VecDeque::with_capacity(self.health_events.pending.len());
                while let Some(next) = self.health_events.pending.pop_front() {
                    if next.source == change.source
                        && next.target == change.target
                        && next.delta < 0.0
                        && next.critical_hit.is_some()
                        && next.damage_frame == change.damage_frame
                    {
                        change.delta += next.delta;
                        change.killed |= next.killed;
                        change.critical_hit = Some(
                            change.critical_hit.unwrap_or(false)
                                || next.critical_hit.unwrap_or(false),
                        );
                        if next.killed && next.target_facts.is_some() {
                            change.target_facts = next.target_facts;
                        }
                        if let Some(more_losses) = next.named_pool_losses {
                            let mut combined: Vec<NamedPoolLoss> = change
                                .named_pool_losses
                                .as_deref()
                                .map_or_else(Vec::new, <[NamedPoolLoss]>::to_vec);
                            for loss in more_losses.iter() {
                                if let Some(prev) = combined
                                    .iter_mut()
                                    .find(|l| l.identifier == loss.identifier)
                                {
                                    prev.amount += loss.amount;
                                } else {
                                    combined.push(loss.clone());
                                }
                            }
                            change.named_pool_losses = Some(Arc::from(combined));
                        }
                    } else {
                        rest.push_back(next);
                    }
                }
                self.health_events.pending = rest;
            }
            if let Some((event, receivers)) = change.resolve(self) {
                let HostEvent::HealthApplied { source, .. } = event;
                // Resolve keeps verified source attribution even for a self-change,
                // where the source needs no separate delivery.
                let source_body = change.source.filter(|body| source == Some(body.wire));
                let context = HostHealthContext {
                    source_body: source_body.map(SampledBody::identity),
                    target_body: Some(change.target.identity()),
                    target_facts: change.target_facts,
                    named_pool_losses: change.named_pool_losses,
                    critical_hit: change.critical_hit,
                    damage_frame: change.damage_frame,
                };
                for body in receivers.into_iter().flatten() {
                    self.queue_health_event(body.entity, event, context.clone());
                }
            }
        }
    }

    pub(super) fn sample_health_body(&self, wire: u32) -> Option<SampledBody> {
        SampledBody::lookup(wire, &self.by_wire, &self.players_by_wire)
    }

    /// Collect already applied VM-local mutations before processing retirement.
    pub(super) fn collect_local_health_events(&mut self) {
        if !self.health_events.enabled {
            return;
        }
        for (&wire, &entity) in &self.by_wire {
            let Some(mut state) = self.world.get_mut::<BotState>(entity) else {
                continue;
            };
            let Some(enemy) = state.enemy.as_mut() else {
                continue;
            };
            let changes = enemy.scripts.take_local_health_changes();
            let target = SampledBody { wire, entity };
            for change in changes {
                let source =
                    SampledBody::lookup(change.source, &self.by_wire, &self.players_by_wire);
                // The VM has finished its local mutation. Read the committed body.
                let room_status = source
                    .filter(|&source| source != target)
                    .and_then(|_| self.sample_health_target_room_status(target));
                self.health_events.stage(
                    source,
                    target,
                    change.delta,
                    change.killed,
                    HealthEventContext {
                        world: &self.world,
                        damage: change
                            .named_pool_losses
                            .as_deref()
                            .map(|named_pool_losses| HealthDamageEvidence {
                                impact: None,
                                named_pool_losses,
                                frame: None,
                            }),
                    },
                    room_status,
                );
            }
        }
    }

    fn queue_health_event(&mut self, entity: Entity, event: HostEvent, context: HostHealthContext) {
        // A full or halted VM only drops its own notification.
        if let Some(mut state) = self.world.get_mut::<PlayerBodyState>(entity) {
            if state.alive() {
                let HostEvent::HealthApplied { target, killed, .. } = event;
                let allow_hit_feedback = match (context.critical_hit, context.damage_frame) {
                    (Some(crit), Some(frame))
                        if context
                            .source_body
                            .is_some_and(|source| source.generation == entity.to_bits()) =>
                    {
                        state.should_emit_hit_feedback(target, frame, crit, killed)
                    }
                    _ => true,
                };
                if let Some(runtime) = &mut state.runtime {
                    if let Some(variant) = self.health_events.authored_hit_variant.filter(|_| {
                        allow_hit_feedback
                            && context.critical_hit.is_some()
                            && context
                                .source_body
                                .is_some_and(|source| source.generation == entity.to_bits())
                    }) {
                        let _ = runtime.receive_authored_hit_feedback(event, &context, variant);
                    }
                    let _ = runtime.receive_host_event(event, context);
                }
            }
        } else if let Some(mut state) = self.world.get_mut::<BotState>(entity) {
            if !state.bot.dead && state.vitals.death().is_none() {
                if let Some(enemy) = state
                    .enemy
                    .as_mut()
                    .filter(|enemy| enemy.fault.is_none() && !enemy.scripts.retirement_requested)
                {
                    let _ = enemy
                        .scripts
                        .body
                        .queue_host_event_with_context(event, context);
                } else if let Some(stock) = state.stock_script.as_mut() {
                    stock.receive_host_event(event, context);
                }
            }
        }
    }
}

#[cfg(test)]
#[path = "health_events/tests.rs"]
mod settled_hit_tests;

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::{
        game_combat::{HealthEffect, tests::fixture_bots},
        vitals::Death,
    };

    #[test]
    fn records_actual_settled_damage_and_healing() {
        let bots = fixture_bots();
        let source = bots[0].entity;
        let target = bots[1].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        let now = Duration::from_millis(48);
        let dealer = Death {
            killer: source,
            frame: 3,
            time: now.as_secs_f64(),
        };
        room.settle_health(target, HealthEffect::Damage(1.0), dealer, now)
            .unwrap();
        assert!(room.health_events.pending.is_empty());
        room.enable_host_health_events();

        for (effect, expected) in [
            (HealthEffect::Damage(24.0), Some((-24.0, false))),
            (HealthEffect::Heal(50.0), Some((25.0, false))),
            (HealthEffect::Heal(1.0), None),
            (HealthEffect::Damage(200.0), Some((-100.0, true))),
            (HealthEffect::Damage(1.0), None),
            (HealthEffect::Heal(1.0), None),
        ] {
            assert!(room.settle_health(target, effect, dealer, now).unwrap());
            let actual = room.health_events.pending.pop_front().map(|entry| {
                assert_eq!(entry.source.map(|body| body.wire), Some(source));
                assert_eq!(entry.target.wire, target);
                assert_eq!(entry.hit_tags, None);
                assert!(entry.target_facts.is_some());
                (entry.delta, entry.killed)
            });
            assert_eq!(actual, expected);
            assert!(room.health_events.pending.is_empty());
        }
    }

    #[test]
    fn local_npc_healing_publishes_committed_target_facts() {
        use crate::{
            game_content::JUNKENSTEIN_REVENGE,
            game_hitscan::TargetPose,
            game_pools::HealthPools,
            npc_catalog::NpcDefinition,
            script_codecs::CodecTable,
            script_enemy::{EnemySpawn, EnemyTarget, WitchStaffSource},
            script_graph::GraphStore,
            script_init::{CreationKind, EntityCreation, OWNER_HEALTH},
            script_wire::Value,
        };
        use serde_json::{Value as Json, json};

        let fixture: Json = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/script-local-health-outcomes-v1.json"
        ))
        .unwrap();
        let input = &fixture["input"]["local_heal"];
        let expected = &fixture["expected"]["local_heal"];
        let effect = json!({
            "index":121,"items":2,"fields":{},"containers":{},"unknown_nodes":[],
            "header":{"m_nodesBitCount":2,"m_statesBitCount":2,
                "m_remoteSyncNodesBitCount":2,"m_syncVarsBitCount":2},
            "states":[0],"entries":[1],"remote":[],"sync_vars":[],
            "nodes":[
                {"pos":0,"rid":0,"uid":0,"remote":-1,"state":0,"client_only":false,
                    "server_only":true,"class":"STUStatescriptStateModifyHealth",
                    "fields":{"m_2BBEEAB8":1,"m_ADEB6E05":0,
                        "m_modifyHealth":{"m_amount":input["requested_per_second"]}}},
                {"pos":1,"rid":1,"uid":1,"remote":-1,"state":null,"client_only":false,
                    "server_only":true,"class":"STUStatescriptEntry",
                    "fields":{"m_outPlug":{"$":"Plug","links":[[0,"m_beginPlug"]]}}}
            ]
        });
        let bundle = json!({
            "version":1,"graphs":[fixture["input"]["ai"]["listener_graph"],effect],
            "bodies":{},"missing":[],
            "junkenstein":{
                "pairs":{"0x02E0000000000901":{"body":"0x0400000000000902",
                    "controller":"0x0400000000000903"}},
                "entities":{
                    "0x0400000000000902":{"hero":"0x02E0000000000901","name":"Target",
                        "graphs":[{"graph":908,"m_75408640":0}],"weapons":[],
                        "target_tags":[input["target_tag"]]},
                    "0x0400000000000903":{"name":"Control","weapons":[],
                        "graphs":[{"graph":908,"m_75408640":0}]}
                }
            }
        });
        let graphs =
            Arc::new(GraphStore::from_json(&serde_json::to_vec(&bundle).unwrap()).unwrap());
        let definition = NpcDefinition {
            hero: 0x02e0_0000_0000_0901,
            body: 0x0400_0000_0000_0902,
            name: "Target".into(),
            pools: HealthPools {
                health: 100.0,
                armour: 0.0,
                shields: 0.0,
            },
        };
        let bots = fixture_bots();
        let source = bots[0].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        room.enable_host_health_events();
        let target = room
            .create_enemy(EnemySpawn {
                graphs: Arc::clone(&graphs),
                codecs: Arc::new(CodecTable::from_json(r#"{"classes":{}}"#).unwrap()),
                definition: &definition,
                request: &EntityCreation {
                    position: [0.0; 3],
                    kind: CreationKind::Hero {
                        hero: definition.hero,
                        team: 1,
                        yaw: 0.0,
                        flags: [Value::Bool(false), Value::Bool(false)],
                        controller: None,
                        skin: None,
                    },
                },
                mode: JUNKENSTEIN_REVENGE,
                mode_variables: Arc::default(),
                tick: 3,
                walk: Default::default(),
                gate: None,
            })
            .unwrap();

        let state = room.state_mut(target).unwrap().unwrap();
        state.vitals.damage(
            input["wound"].as_f64().unwrap(),
            Death {
                killer: target,
                frame: 3,
                time: 0.048,
            },
        );
        let enemy = state.enemy.as_mut().unwrap();
        enemy.binding.roster = Some(Arc::new(vec![EnemyTarget {
            hero: None,
            filter_flags: None,
            tags: Default::default(),
            health: None,
            velocity: None,
            script_state: Default::default(),
            entity: source,
            team: 1,
            pose: TargetPose {
                feet: [0.0; 3],
                height: 1.0,
                radius: 0.25,
                eye: 0.75,
            },
            alive: true,
        }]));
        enemy.scripts.record_witch_staff_source(
            source,
            WitchStaffSource {
                controller: source,
                name: "Source".into(),
                health: [100.0, 0.0],
                capacity: [100.0, 0.0],
            },
        );
        enemy
            .scripts
            .sync_witch_staff(
                &graphs,
                &enemy.binding,
                &mut state.vitals,
                None,
                Some((source, 8.0)),
            )
            .unwrap();
        enemy
            .scripts
            .advance(&graphs, &enemy.binding, &mut state.vitals, 4)
            .unwrap();
        let committed_health = expected["committed_health"].as_f64().unwrap();
        assert_eq!(state.vitals.total(), committed_health);

        room.collect_local_health_events();
        let change = room.health_events.pending.pop_front().unwrap();
        let (event, _) = change.resolve(&room).unwrap();
        assert_eq!(
            event,
            HostEvent::HealthApplied {
                source: Some(source),
                target,
                delta: expected["actual_delta"].as_f64().unwrap(),
                killed: false,
                hit_tags: None,
            }
        );
        let facts = change.target_facts.unwrap();
        let tag = crate::accounts::parse_guid(input["target_tag"].as_str().unwrap()).unwrap();
        assert_eq!(facts.target, target);
        assert_eq!(facts.filter_flags, expected["target_filter_flags"].as_u64());
        assert_eq!(facts.tags.presence(tag), Some(true));
        assert_eq!(
            facts.script_state.read(OWNER_HEALTH),
            Some(Value::Float(committed_health))
        );
        room.collect_local_health_events();
        assert!(room.health_events.pending.is_empty());
    }

    #[test]
    fn field_pulses_record_one_capped_change_with_checked_source_per_body() {
        use crate::{
            contact_world::ContactWorld,
            game_combat::{BioticField, BotQueries, FIELD_DELAY, tests::fixture_ground},
            game_heroes::SOLDIER,
            game_replication::BodyId,
            mover::Mover,
            mover_settings::{MoverCatalog, MoverSettings},
            script_player::fixture_body_factory,
            world::Operation,
        };
        use std::sync::Arc;

        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/script-local-health-outcomes-v1.json"
        ))
        .unwrap();
        assert_eq!(
            fixture["case_id"],
            "script-local-health-outcomes.synthetic.v1"
        );
        let field = &fixture["input"]["field"];
        let wounds: [f64; 2] =
            serde_json::from_value(field["initial_two_actor_wounds"].clone()).unwrap();
        let deltas: [f64; 2] =
            serde_json::from_value(fixture["expected"]["field"]["actual_deltas"].clone()).unwrap();
        let elapsed = u32::try_from(field["pulse_ticks_elapsed"].as_u64().unwrap()).unwrap();
        let dirty_full_ally_wound = field["dirty_full_ally_wound"].as_f64().unwrap();

        let mut bots = fixture_bots();
        bots.truncate(1);
        let (ally_factory, mut ally_spawn) = fixture_body_factory();
        ally_spawn.pose.position = [0.0; 3];
        ally_spawn.body_filter = crate::match_scene::team_bit(1).unwrap();
        ally_spawn.slot = 1;
        let targets = [bots[0].entity, ally_spawn.entity];
        let mut room = RoomSimulation::new(bots).unwrap();
        let owner = BodyId {
            entity: 702,
            generation: 1,
        };
        let ally = BodyId {
            entity: ally_spawn.entity,
            generation: 1,
        };
        let mover = || {
            Mover::new(
                MoverSettings::default(),
                Arc::new(ContactWorld::new(Vec::new()).unwrap()),
                [0.0; 3],
                0,
                0x08,
            )
            .unwrap()
        };
        let owner_body = |id| PlayerBodyState::new(id, 701, 0, 1, 703, mover(), None, 100, None);
        let ally_body = |id| {
            PlayerBodyState::new(
                id,
                ally_spawn.controller,
                ally_spawn.slot,
                1,
                SOLDIER,
                mover(),
                Some(Box::new(ally_factory.start(ally_spawn).unwrap())),
                ally_spawn.frame,
                None,
            )
        };
        room.spawn_player_body(owner_body(owner)).unwrap();
        room.spawn_player_body(ally_body(ally)).unwrap();
        room.enable_host_health_events();
        let mut field = BioticField::new([0.0; 3]);
        assert!(field.advance(100).0.is_none());
        let pulse = field.advance(100 + FIELD_DELAY + elapsed).0.unwrap();
        assert!(pulse.amount < wounds[1]);
        let pulses = [pulse, pulse];
        let ground = Arc::new(fixture_ground());
        room.enable_spatial_queries(
            false,
            Arc::clone(&ground),
            crate::script_graph::RoomSpatialNeeds::EntityFacts,
        );
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground, None);

        let dealer = Death {
            killer: owner.entity,
            frame: 100,
            time: 1.6,
        };
        let wound_bot = |room: &mut RoomSimulation| {
            room.state_mut(targets[0])
                .unwrap()
                .unwrap()
                .vitals
                .damage(wounds[0], dealer);
        };
        wound_bot(&mut room);
        room.player_body_mut(ally)
            .unwrap()
            .runtime
            .as_mut()
            .unwrap()
            .receive_damage(wounds[1], dealer, &crate::script_init::HitTags::EMPTY);
        room.heal_allies(&queries, 1, Some(owner.entity), Some(owner), &pulses)
            .unwrap();
        let updates = room.take_scripted_health();
        assert_eq!(
            updates
                .iter()
                .map(|update| update.entity)
                .collect::<Vec<_>>(),
            targets
        );
        let bot_health = room
            .state_mut(targets[0])
            .unwrap()
            .unwrap()
            .vitals
            .component();
        let ally_health = room
            .player_body(ally)
            .unwrap()
            .vitals()
            .unwrap()
            .cumulative_component();
        assert_eq!(
            updates[0].operation,
            Some(Operation::Update(vec![bot_health]))
        );
        assert_eq!(
            updates[1].operation,
            Some(Operation::Update(vec![ally_health.clone()]))
        );
        for (wire, delta) in targets.into_iter().zip(deltas) {
            let event = room.health_events.pending.pop_front().unwrap();
            assert_eq!(event.source.map(|body| body.wire), Some(owner.entity));
            assert_eq!(
                (event.target.wire, event.delta, event.killed),
                (wire, delta, false)
            );
            assert_eq!(event.hit_tags, None);
            let facts = event.target_facts.as_ref().unwrap();
            assert_eq!(facts.target, wire);
            assert_eq!(
                facts.room_status,
                Some(RoomEntityStatus {
                    team: Some(1),
                    alive: true,
                })
            );
            assert_eq!(
                facts.filter_flags,
                Some(1 | crate::match_scene::team_bit(1).unwrap())
            );
        }
        assert!(room.health_events.pending.is_empty());

        // This body returned to full health with a separate pending update.
        let ally_state = room.player_body_mut(ally).unwrap();
        assert!(ally_state.take_health_update().is_none());
        let ally_runtime = ally_state.runtime.as_mut().unwrap();
        assert_eq!(
            ally_runtime
                .receive_damage(
                    dirty_full_ally_wound,
                    dealer,
                    &crate::script_init::HitTags::EMPTY
                )
                .dealt,
            dirty_full_ally_wound
        );
        assert_eq!(
            ally_runtime.heal(dirty_full_ally_wound),
            dirty_full_ally_wound
        );
        room.heal_allies(&queries, 1, Some(owner.entity), Some(owner), &pulses)
            .unwrap();
        assert!(room.take_scripted_health().is_empty());
        assert!(room.health_events.pending.is_empty());
        assert_eq!(
            room.player_body_mut(ally).unwrap().take_health_update(),
            Some(ally_health)
        );

        room.player_body_mut(ally)
            .unwrap()
            .runtime
            .as_mut()
            .unwrap()
            .receive_damage(
                dirty_full_ally_wound,
                dealer,
                &crate::script_init::HitTags::EMPTY,
            );
        room.heal_allies(&queries, 1, Some(owner.entity), Some(owner), &pulses)
            .unwrap();
        let before_replacement = room.scripted_health.last().unwrap().clone();
        assert_eq!(before_replacement.entity, ally.entity);
        room.record_observer_player_health(ally_spawn.controller, before_replacement);
        room.spawn_player_body(ally_body(BodyId {
            generation: 2,
            ..ally
        }))
        .unwrap();
        assert!(room.take_scripted_health().is_empty());
        assert!(room.take_observer_player_health().is_empty());
        let original_ally_event = room.health_events.pending.pop_front().unwrap();
        assert!(original_ally_event.resolve(&room).is_none());

        room.spawn_player_body(owner_body(BodyId {
            generation: 2,
            ..owner
        }))
        .unwrap();
        assert!(
            room.advance_player_fields(owner, 100 + FIELD_DELAY + elapsed)
                .is_none()
        );
        wound_bot(&mut room);
        room.heal_allies(&queries, 1, Some(owner.entity), Some(owner), &pulses)
            .unwrap();
        let event = room.health_events.pending.pop_front().unwrap();
        assert!(event.source.is_none());
        assert!(event.target_facts.is_none());
        assert_eq!((event.target.wire, event.delta), (targets[0], deltas[0]));
        assert!(room.health_events.pending.is_empty());
    }

    #[test]
    fn bounds_pending_events_and_resolves_each_body_generation() {
        let bots = fixture_bots();
        let source = bots[0].entity;
        let target = bots[1].entity;
        let mut room = RoomSimulation::new(bots).unwrap();
        room.enable_host_health_events();

        for (delta, killed) in [
            (f64::NAN, false),
            (f64::INFINITY, false),
            (0.0, false),
            (-0.0, false),
            (1.0, true),
        ] {
            room.record_health_applied(source, target, delta, killed, None);
        }
        room.record_health_applied(source, 0, -1.0, false, None);
        assert!(room.health_events.pending.is_empty());

        room.record_health_applied(source, source, -1.0, false, None);
        let self_damage = room.health_events.pending.pop_front().unwrap();
        let (_, receivers) = self_damage.resolve(&room).unwrap();
        assert_eq!(receivers.into_iter().flatten().count(), 1);

        for amount in 1..=MAX_ROOM_HEALTH_EVENTS + 1 {
            room.record_health_applied(source, target, -(amount as f64), false, None);
        }
        assert_eq!(room.health_events.pending.len(), MAX_ROOM_HEALTH_EVENTS);
        let retired = room.by_wire.shift_remove(&target).unwrap();
        assert!(room.world.despawn(retired));
        let first = room.health_events.pending.pop_front().unwrap();
        let (event, receivers) = first.resolve(&room).unwrap();
        assert_eq!(
            event,
            HostEvent::HealthApplied {
                source: Some(source),
                target,
                delta: -1.0,
                killed: false,
                hit_tags: None,
            }
        );
        assert_eq!(
            receivers.map(|body| body.map(|body| body.wire)),
            [Some(source), None]
        );

        let replacement = room.world.spawn_empty().id();
        assert_ne!(retired, replacement);
        room.by_wire.insert(target, replacement);
        let last = room.health_events.pending.pop_back().unwrap();
        assert_eq!(last.delta, -(MAX_ROOM_HEALTH_EVENTS as f64));
        assert!(last.resolve(&room).is_none());
        room.flush_health_events();
        assert!(room.health_events.pending.is_empty());
    }
}
