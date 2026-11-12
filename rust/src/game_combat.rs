//! Match-authoritative direct damage and training bot lifetimes.
//!
//! The projectile and body scripts supply shot events. The match supplies
//! the command and pose at consumption, and owns identity-gated delivery.
//! Hit selection, damage, and respawn order follow ow174/game/combat.py.

use std::{collections::HashMap, io, time::Duration};

use bevy_ecs::{component::Component, entity::Entity, world::World};
use indexmap::IndexMap;

use crate::{
    collision_index::IndexError,
    commands::Command,
    game_bots::{BOT_ENTITY, BOT_RECORD_DELAY, BOT_RESPAWN_TIME, Bot},
    game_combat_messages::CombatNotice,
    game_heroes::SOLDIER,
    game_hitscan::{HitDefinition, HitscanShot, Impact, TargetHistory, ViewFrame},
    game_projectiles::ProjectileToken,
    game_replication::BodyId,
    match_scene::bot_create,
    projectile_flight::{ProjectileBallistics, ProjectileFlight},
    scene_assets::BotQueries,
    script_init::ShotEvent,
    vitals::{Death, Vitals},
    world::{EntityUpdate, Operation},
};

#[cfg(any(test, feature = "inspector"))]
use crate::game_debug::{Actor, ActorKind};

#[derive(Debug, thiserror::Error)]
pub enum CombatError {
    #[error("bot wire ID {0:08X} is already in use")]
    BotIdInUse(u32),
    #[error("bot wire IDs exhausted")]
    BotIdsExhausted,
    #[error("bot {wire:08X} references missing ECS body {entity:?}")]
    MissingBot { wire: u32, entity: Entity },
    #[error("bot index {wire:08X} references body with wire ID {actual:08X}")]
    BotIdentity { wire: u32, actual: u32 },
    #[error("projectile batch {connection} references missing ECS flight {entity:?}")]
    MissingFlight { connection: u32, entity: Entity },
    #[error(
        "projectile batch {connection} generation {generation} belongs to {expected:08X}, not {actual:08X}"
    )]
    FlightOwnerIdentity {
        connection: u32,
        generation: u64,
        expected: u32,
        actual: u32,
    },
    #[error("bot index slot {0} is unavailable")]
    MissingBotSlot(usize),
    #[error("cannot encode respawn for bot {wire:08X}: {source}")]
    RespawnCreate {
        wire: u32,
        #[source]
        source: io::Error,
    },
    #[error("combat collision query failed: {0}")]
    Collision(#[from] IndexError),
}

pub(crate) fn eye_height(hero: u64, crouched: bool) -> f64 {
    match (crouched, hero) {
        (false, _) => 1.55,
        (true, SOLDIER) => 1.085,
        (true, _) => 1.15,
    }
}

#[derive(Clone, Copy)]
pub(crate) struct PosedHitscan {
    pub source: u32,
    pub origin: [f64; 3],
    pub command: Command,
    pub shot_frame: u32,
    pub reach: f64,
    pub hit: HitDefinition,
}

pub(crate) enum PosedShot {
    Hitscan(PosedHitscan),
    Projectile(PosedProjectile),
}

pub(crate) struct PosedProjectile {
    pub shot: ShotEvent,
    pub ballistics: ProjectileBallistics,
    pub origin: [f64; 3],
    pub direction: [f64; 3],
}

#[derive(Clone, Copy)]
pub(crate) struct ShotContext {
    pub team: u8,
    pub quanta: u32,
    pub tick: u32,
    pub now: Duration,
}

pub(crate) struct BotHit {
    pub notices: [Option<CombatNotice>; 2],
    pub health: EntityUpdate,
}

#[derive(Clone)]
pub(crate) struct BotReplacement {
    pub retired: u32,
    pub created: EntityUpdate,
}

#[derive(Component)]
struct BotState {
    bot: Bot,
    vitals: Vitals,
    history: TargetHistory,
    respawn_at: Option<Duration>,
}

impl BotState {
    fn new(bot: Bot, replacement: Option<u32>) -> Self {
        Self {
            vitals: Vitals::new(bot.spawn.health, 0.0, 0.0),
            bot,
            history: TargetHistory::new(replacement),
            respawn_at: None,
        }
    }
}

#[derive(Component)]
pub(crate) struct AdmittedFlight {
    pub token: ProjectileToken,
    pub flight: ProjectileFlight,
}

pub(crate) enum FlightOutcome {
    Ended {
        token: ProjectileToken,
        travelled: f64,
    },
    Failed(ProjectileToken),
}

struct FlightBatch {
    owner: BodyId,
    order: Vec<Entity>,
}

/// Room-authoritative bot bodies and in-flight projectiles.
pub(crate) struct RoomSimulation {
    world: World,
    // Wire lookup also preserves the authored spawner order across respawns.
    by_wire: IndexMap<u32, Entity>,
    next_bot: u32,
    combat_active: bool,
    // Handle lists preserve launch order within the worker's ordered peer turns.
    projectiles: HashMap<u32, FlightBatch>,
}

impl RoomSimulation {
    pub(crate) fn new(bots: Vec<Bot>) -> Result<Self, CombatError> {
        let mut world = World::new();
        let mut next_bot = BOT_ENTITY;
        let mut by_wire = IndexMap::with_capacity(bots.len());
        for bot in bots {
            if by_wire.contains_key(&bot.entity) {
                return Err(CombatError::BotIdInUse(bot.entity));
            }
            next_bot = next_bot.max(
                bot.entity
                    .checked_add(1)
                    .ok_or(CombatError::BotIdsExhausted)?,
            );
            by_wire.insert(bot.entity, world.spawn(BotState::new(bot, None)).id());
        }
        Ok(Self {
            world,
            by_wire,
            next_bot,
            combat_active: false,
            projectiles: HashMap::new(),
        })
    }

    fn states(&self) -> impl ExactSizeIterator<Item = Result<&BotState, CombatError>> {
        self.by_wire.iter().map(|(&wire, &entity)| {
            let state = self
                .world
                .get::<BotState>(entity)
                .ok_or(CombatError::MissingBot { wire, entity })?;
            check_bot_identity(wire, state)?;
            Ok(state)
        })
    }

    fn state_mut(&mut self, wire: u32) -> Result<Option<&mut BotState>, CombatError> {
        let Some(&entity) = self.by_wire.get(&wire) else {
            return Ok(None);
        };
        let state = self
            .world
            .get_mut::<BotState>(entity)
            .ok_or(CombatError::MissingBot { wire, entity })?
            .into_inner();
        check_bot_identity(wire, state)?;
        Ok(Some(state))
    }

    pub(crate) fn bodies(&self) -> Result<impl ExactSizeIterator<Item = &Bot>, CombatError> {
        let bodies = self
            .states()
            .map(|state| state.map(|state| &state.bot))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(bodies.into_iter())
    }

    pub(crate) fn for_each_body_mut(
        &mut self,
        mut step: impl FnMut(&mut Bot),
    ) -> Result<(), CombatError> {
        for (&wire, &entity) in &self.by_wire {
            let mut state = self
                .world
                .get_mut::<BotState>(entity)
                .ok_or(CombatError::MissingBot { wire, entity })?;
            check_bot_identity(wire, &state)?;
            step(&mut state.bot);
            check_bot_identity(wire, &state)?;
        }
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn body_mut(&mut self, wire_id: u32) -> Option<&mut Bot> {
        Some(&mut self.state_mut(wire_id).unwrap()?.bot)
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_actors(&self) -> Result<impl ExactSizeIterator<Item = Actor>, CombatError> {
        let states = self.states().collect::<Result<Vec<_>, _>>()?;
        Ok(states.into_iter().map(|state| {
            let bot = &state.bot;
            Actor {
                entity: bot.entity,
                generation: None,
                kind: ActorKind::Bot,
                team: bot.spawn.team,
                position: bot.position,
                velocity: bot.velocity,
                alive: !bot.dead,
                health: Some(if self.combat_active {
                    state.vitals.total()
                } else {
                    bot.spawn.health
                }),
                script_active: bot.patrol_active(),
                last_contact_clip: bot.last_contact_clip(),
            }
        }))
    }

    pub(crate) fn activate_combat(&mut self) {
        self.combat_active = true;
    }

    fn record_poses(&mut self, queries: &BotQueries<'_>, tick: u32) -> Result<(), CombatError> {
        for (&wire, &entity) in &self.by_wire {
            let state = self
                .world
                .get_mut::<BotState>(entity)
                .ok_or(CombatError::MissingBot { wire, entity })?;
            check_bot_identity(wire, &state)?;
            if !state.bot.dead {
                let state = state.into_inner();
                state.history.record(tick, queries.target(&state.bot));
            }
        }
        Ok(())
    }

    pub(crate) fn hitscan(
        &mut self,
        queries: &BotQueries<'_>,
        shot: PosedHitscan,
        context: ShotContext,
        visible: impl Fn(u32) -> bool,
    ) -> Result<Option<BotHit>, CombatError> {
        let view = ViewFrame::from_command(&shot.command, context.quanta);
        let ray = HitscanShot::from_command(shot.origin, &shot.command, shot.reach, shot.hit);
        // Scene order breaks exact ray ties.
        let mut candidates = Vec::with_capacity(self.by_wire.len());
        for state in self.states() {
            let state = state?;
            let bot = &state.bot;
            if !eligible_bot(bot, shot.source, context.team, queries.free_for_all())
                || !visible(bot.entity)
            {
                continue;
            }
            if let Some(pose) = state.history.at(view, queries.target(bot)) {
                candidates.push((bot.entity, pose));
            }
        }
        let Some(impact) = ray.resolve(candidates, |origin, direction, reach| {
            queries.hitscan_wall(origin, direction, reach)
        })?
        else {
            return Ok(None);
        };
        self.apply_bot_impact(shot.source, shot.shot_frame, context, &impact)
    }

    pub(crate) fn advance_projectiles(
        &mut self,
        queries: &BotQueries<'_>,
        connection: u32,
        owner: BodyId,
        launches: Vec<AdmittedFlight>,
        context: ShotContext,
    ) -> Result<(Vec<FlightOutcome>, Vec<BotHit>), CombatError> {
        if let Some(batch) = self.projectiles.get(&connection) {
            // Body generations are monotonic within a live connection. Late
            // work cannot inspect, advance or retire the replacement's batch.
            if owner.generation < batch.owner.generation {
                return Ok((Vec::new(), Vec::new()));
            }
            if owner.generation == batch.owner.generation && owner.entity != batch.owner.entity {
                return Err(CombatError::FlightOwnerIdentity {
                    connection,
                    generation: owner.generation,
                    expected: batch.owner.entity,
                    actual: owner.entity,
                });
            }
            for &entity in &batch.order {
                if self.world.get::<AdmittedFlight>(entity).is_none() {
                    return Err(CombatError::MissingFlight { connection, entity });
                }
            }
        }
        if self
            .projectiles
            .get(&connection)
            .is_some_and(|batch| batch.owner != owner)
        {
            self.retire_projectiles(connection);
        }
        let mut order = self
            .projectiles
            .remove(&connection)
            .map_or_else(Vec::new, |batch| batch.order);
        order.extend(
            launches
                .into_iter()
                .map(|launch| self.world.spawn(launch).id()),
        );
        let mut outcomes = Vec::new();
        let mut hits = Vec::new();
        if order.is_empty() {
            return Ok((outcomes, hits));
        }

        self.activate_combat();
        let mut continuing = Vec::with_capacity(order.len());
        for entity in order {
            // An earlier flight may have killed a target in this same tick.
            let mut candidates = Vec::with_capacity(self.by_wire.len());
            for state in self.states() {
                let bot = &state?.bot;
                if eligible_bot(bot, owner.entity, context.team, queries.free_for_all()) {
                    candidates.push((bot.entity, queries.target(bot)));
                }
            }
            let (token, result) = {
                let mut active = self
                    .world
                    .get_mut::<AdmittedFlight>(entity)
                    .ok_or(CombatError::MissingFlight { connection, entity })?;
                let result =
                    active
                        .flight
                        .advance(context.tick, &candidates, |origin, direction, reach| {
                            queries.hitscan_wall(origin, direction, reach)
                        });
                (active.token, result)
            };
            match result {
                Ok(None) => {
                    continuing.push(entity);
                    continue;
                }
                Ok(Some(end)) => {
                    for impact in &end.impacts {
                        if let Some(hit) =
                            self.apply_bot_impact(owner.entity, context.tick, context, impact)?
                        {
                            hits.push(hit);
                        }
                    }
                    outcomes.push(FlightOutcome::Ended {
                        token,
                        travelled: end.travelled,
                    });
                }
                // A query exceeding the bounded ray budget cancels only this
                // flight. Broken geometry and ECS ownership are room faults.
                Err(IndexError::RayTraversalLimit) => outcomes.push(FlightOutcome::Failed(token)),
                Err(error) => return Err(error.into()),
            }
            if !self.world.despawn(entity) {
                return Err(CombatError::MissingFlight { connection, entity });
            }
        }
        if !continuing.is_empty() {
            self.projectiles.insert(
                connection,
                FlightBatch {
                    owner,
                    order: continuing,
                },
            );
        }
        Ok((outcomes, hits))
    }

    pub(crate) fn retire_projectiles(&mut self, connection: u32) {
        if let Some(batch) = self.projectiles.remove(&connection) {
            for entity in batch.order {
                // Cleanup must remain usable while retiring a damaged room.
                self.world.despawn(entity);
            }
        }
    }

    pub(crate) fn clear_projectiles(&mut self) {
        for entity in self.projectiles.drain().flat_map(|(_, batch)| batch.order) {
            self.world.despawn(entity);
        }
    }

    fn apply_bot_impact(
        &mut self,
        source: u32,
        shot_frame: u32,
        context: ShotContext,
        impact: &Impact,
    ) -> Result<Option<BotHit>, CombatError> {
        let Some(state) = self.state_mut(impact.entity)? else {
            // An impact carrying a retired wire identity must not damage its
            // replacement body.
            return Ok(None);
        };
        let bot = &mut state.bot;
        if bot.dead {
            return Ok(None);
        }
        let result = state.vitals.damage(
            impact.damage,
            Death {
                time: context.now.as_secs_f64(),
                frame: context.tick,
                killer: source,
            },
        );
        if result.dealt == 0.0 {
            return Ok(None);
        }
        if result.killed {
            bot.died(context.tick);
            state.respawn_at = Some(context.now.saturating_add(BOT_RESPAWN_TIME));
        }
        let health = EntityUpdate {
            entity: bot.entity,
            operation: Some(Operation::Update(vec![state.vitals.component()])),
            ..EntityUpdate::default()
        };
        state.vitals.clear_changed();
        Ok(Some(BotHit {
            notices: [
                Some(CombatNotice::Hit {
                    target: bot.entity,
                    dealt: result.dealt,
                    critical: impact.critical,
                    killed: result.killed,
                    shot_frame,
                }),
                result.killed.then_some(CombatNotice::Elimination {
                    killer: source,
                    victim: bot.entity,
                }),
            ],
            health,
        }))
    }

    pub(crate) fn finish_tick(
        &mut self,
        queries: &BotQueries<'_>,
        tick: u32,
        now: Duration,
    ) -> Result<Vec<BotReplacement>, CombatError> {
        if !self.combat_active {
            self.states().try_for_each(|state| state.map(|_| ()))?;
            return Ok(Vec::new());
        }
        let replacements = self.respawn_due(tick, now)?;
        self.record_poses(queries, tick)?;
        Ok(replacements)
    }

    fn respawn_due(
        &mut self,
        tick: u32,
        now: Duration,
    ) -> Result<Vec<BotReplacement>, CombatError> {
        // Validate the entire batch before retiring any entity handles.
        let mut prepared = Vec::new();
        let mut next_bot = self.next_bot;
        for (slot, (&wire, &previous)) in self.by_wire.iter().enumerate() {
            let state = self
                .world
                .get::<BotState>(previous)
                .ok_or(CombatError::MissingBot {
                    wire,
                    entity: previous,
                })?;
            check_bot_identity(wire, state)?;
            if !state.respawn_at.is_some_and(|deadline| deadline <= now) {
                continue;
            }
            if self.by_wire.contains_key(&next_bot) {
                return Err(CombatError::BotIdInUse(next_bot));
            }
            let fresh = state
                .bot
                .respawned(next_bot, tick.saturating_add(BOT_RECORD_DELAY));
            next_bot = next_bot
                .checked_add(1)
                .ok_or(CombatError::BotIdsExhausted)?;
            let created =
                bot_create(&fresh, None).map_err(|source| CombatError::RespawnCreate {
                    wire: fresh.entity,
                    source,
                })?;
            prepared.push((
                slot,
                previous,
                fresh,
                BotReplacement {
                    retired: state.bot.entity,
                    created,
                },
            ));
        }
        let mut replacements = Vec::with_capacity(prepared.len());
        for (slot, previous, fresh, replacement) in prepared {
            let fresh_wire = fresh.entity;
            if !self.world.despawn(previous) {
                return Err(CombatError::MissingBot {
                    wire: replacement.retired,
                    entity: previous,
                });
            }
            let entity = self.world.spawn(BotState::new(fresh, Some(tick))).id();
            self.by_wire
                .replace_index(slot, fresh_wire)
                .map_err(|_| CombatError::BotIdInUse(fresh_wire))?;
            *self
                .by_wire
                .get_index_mut(slot)
                .ok_or(CombatError::MissingBotSlot(slot))?
                .1 = entity;
            replacements.push(replacement);
        }
        self.next_bot = next_bot;
        Ok(replacements)
    }

    #[cfg(test)]
    pub(crate) fn invalidate_bot_component(&mut self) -> bool {
        let Some(&entity) = self.by_wire.values().next() else {
            return false;
        };
        let Ok(mut entity) = self.world.get_entity_mut(entity) else {
            return false;
        };
        entity.take::<BotState>().is_some()
    }
}

fn check_bot_identity(wire: u32, state: &BotState) -> Result<(), CombatError> {
    if state.bot.entity != wire {
        return Err(CombatError::BotIdentity {
            wire,
            actual: state.bot.entity,
        });
    }
    Ok(())
}

fn eligible_bot(bot: &Bot, source: u32, team: u8, free_for_all: bool) -> bool {
    !bot.dead && bot.entity != source && (free_for_all || bot.spawn.team != team)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{game_content::GameMap, game_instance::RETAIL_QUANTA, scene_assets::SceneAssets};

    fn fixture_bots() -> Vec<Bot> {
        crate::game_bots::BotCatalog::from_json(
            r#"{
            "heroes":{"0x01":{"name":"Test bot","body":"0x02","health":100}},
            "maps":{"0x03":{"hints":{},"spawners":[
                {"hero":"0x01","team":1,"position":[1,0,0],"rotation":[0,0,0,1],"graphs":[]},
                {"hero":"0x01","team":1,"position":[2,0,0],"rotation":[0,0,0,1],"graphs":[]}
            ]}}
        }"#,
        )
        .unwrap()
        .make(3, |_| crate::game_bots::BotWalk::default())
    }

    fn fixture_impact(entity: u32, damage: f64) -> Impact {
        Impact {
            entity,
            distance: 1.0,
            damage,
            critical: false,
            loadout: None,
        }
    }

    const FIXTURE_CONTEXT: ShotContext = ShotContext {
        team: 0,
        quanta: RETAIL_QUANTA,
        tick: 1000,
        now: Duration::from_secs(4),
    };

    fn state(bots: &RoomSimulation, wire_id: u32) -> Option<&BotState> {
        bots.world.get(*bots.by_wire.get(&wire_id)?)
    }

    fn flight_fixture(owner: BodyId, speed: f64) -> AdmittedFlight {
        use crate::game_projectiles::{OwnerProjectiles, ProjectileLeases};

        let frame = FIXTURE_CONTEXT.tick;
        let definition = 0x0400_0000_0000_072c;
        let leases = ProjectileLeases::new(0, frame, [definition]).unwrap();
        let token = OwnerProjectiles::new(owner.entity, leases)
            .admit(
                ShotEvent {
                    entity: owner.entity,
                    instance: 1,
                    graph: 42,
                    raw_state: 0,
                    frame,
                    time_ms: 0,
                    activation: 1,
                    activation_ms: 0,
                    ordinal: 0,
                },
                definition,
                3,
            )
            .unwrap();
        let flight = ProjectileFlight::new(
            ProjectileBallistics {
                speed,
                lifetime: 0.04,
                definition: Some(definition),
                direct: None,
                blast: None,
            },
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            None,
        )
        .unwrap();
        AdmittedFlight { token, flight }
    }

    fn fixture_ground() -> crate::collision_scene::CollisionScene {
        use crate::{
            collision_cache::{CollisionCache, Mesh, Triangle},
            collision_scene::CollisionScene,
        };

        let cache = CollisionCache {
            header: serde_json::from_str(
                r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
            )
            .unwrap(),
            meshes: vec![Mesh {
                vertices: vec![[-1.0, 0.0, 0.0], [-1.0, 2.0, 0.0], [-1.0, 0.0, 1.0]],
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
        CollisionScene::movement_probe(&cache, &Default::default()).unwrap()
    }

    #[test]
    fn projectile_flights_keep_peer_generation_and_report_terminal_outcomes() {
        use crate::mover_settings::MoverCatalog;

        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let mut context = FIXTURE_CONTEXT;
        let owner = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        let other = BodyId {
            entity: 0xa100_0101,
            generation: 1,
        };
        let first = flight_fixture(owner, 1.0);
        let second = flight_fixture(other, 1.0);
        let token = first.token;
        assert_eq!(token, second.token);
        for (connection, owner, flight) in [(7, owner, first), (8, other, second)] {
            let (outcomes, hits) = simulation
                .advance_projectiles(&queries, connection, owner, vec![flight], context)
                .unwrap();
            assert!(outcomes.is_empty() && hits.is_empty());
        }
        let old_handle = simulation.projectiles[&7].order[0];
        let other_handle = simulation.projectiles[&8].order[0];

        context.tick += 3;
        let owner = BodyId {
            generation: 2,
            ..owner
        };
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, owner, Vec::new(), context)
            .unwrap();
        assert!(outcomes.is_empty() && hits.is_empty());
        assert!(simulation.world.get::<AdmittedFlight>(old_handle).is_none());
        assert!(
            simulation
                .world
                .get::<AdmittedFlight>(other_handle)
                .is_some()
        );

        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 8, other, Vec::new(), context)
            .unwrap();
        assert!(hits.is_empty());
        assert!(matches!(
            outcomes.as_slice(),
            [FlightOutcome::Ended { token: observed, travelled }]
                if *observed == token && (*travelled - 0.04).abs() < 1e-12
        ));
        assert!(
            simulation
                .world
                .get::<AdmittedFlight>(other_handle)
                .is_none()
        );

        let too_far = flight_fixture(owner, 10_000_000.0);
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, owner, vec![too_far], context)
            .unwrap();
        assert!(hits.is_empty());
        assert!(
            matches!(outcomes.as_slice(), [FlightOutcome::Failed(observed)] if *observed == token)
        );
        assert!(simulation.projectiles.is_empty());
        assert!(simulation.world.entities().is_empty());
    }

    #[test]
    fn broken_bot_indexes_are_errors_and_do_not_partially_respawn_a_batch() {
        use crate::mover_settings::MoverCatalog;

        let context = FIXTURE_CONTEXT;
        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground);
        let mut simulation = RoomSimulation::new(fixture_bots()).unwrap();
        let first = BOT_ENTITY;
        let second = first + 1;
        let first_handle = simulation.by_wire[&first];
        let broken_handle = simulation.by_wire[&second];
        simulation
            .apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
            .unwrap();
        simulation
            .world
            .entity_mut(broken_handle)
            .remove::<BotState>();
        let missing = |error: CombatError| {
            assert!(matches!(error, CombatError::MissingBot { wire, entity }
                if wire == second && entity == broken_handle));
        };
        missing(simulation.bodies().err().unwrap());
        missing(simulation.debug_actors().err().unwrap());
        missing(simulation.for_each_body_mut(|_| {}).unwrap_err());
        missing(simulation.record_poses(&queries, context.tick).unwrap_err());
        missing(
            simulation
                .apply_bot_impact(7, context.tick, context, &fixture_impact(second, 1.0))
                .err()
                .unwrap(),
        );
        let next_bot = simulation.next_bot;
        missing(
            simulation
                .respawn_due(context.tick + 1, context.now + BOT_RESPAWN_TIME)
                .err()
                .unwrap(),
        );
        assert_eq!(simulation.next_bot, next_bot);
        assert_eq!(simulation.by_wire[&first], first_handle);
        assert!(state(&simulation, first).unwrap().bot.dead);

        // A present component with a different wire identity is also invalid,
        // rather than an empty body list or a different target to damage.
        let mut simulation = RoomSimulation::new(fixture_bots()).unwrap();
        simulation.body_mut(first).unwrap().entity = second;
        assert!(
            matches!(simulation.bodies(), Err(CombatError::BotIdentity { wire, actual })
            if wire == first && actual == second)
        );
    }

    #[test]
    fn missing_flight_component_is_explicit_and_retirement_is_idempotent() {
        use crate::mover_settings::MoverCatalog;

        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let owner = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        simulation
            .advance_projectiles(
                &queries,
                7,
                owner,
                vec![flight_fixture(owner, 1.0)],
                FIXTURE_CONTEXT,
            )
            .unwrap();
        let entity = simulation.projectiles[&7].order[0];
        simulation
            .world
            .entity_mut(entity)
            .remove::<AdmittedFlight>();
        assert!(matches!(
            simulation.advance_projectiles(&queries, 7, owner, Vec::new(), FIXTURE_CONTEXT),
            Err(CombatError::MissingFlight { connection: 7, entity: missing }) if missing == entity
        ));
        simulation.retire_projectiles(7);
        simulation.retire_projectiles(7);
        simulation.clear_projectiles();
        simulation.clear_projectiles();
        assert!(simulation.world.entities().is_empty());
        assert!(simulation.projectiles.is_empty());
    }

    #[test]
    fn stale_generation_does_not_retire_or_advance_the_replacement_flight() {
        use crate::mover_settings::MoverCatalog;

        let ground = fixture_ground();
        let movers = MoverCatalog::default();
        let queries = BotQueries::new(false, &movers, &ground);
        let mut simulation = RoomSimulation::new(Vec::new()).unwrap();
        let owner = BodyId {
            entity: 0xa000_0102,
            generation: 2,
        };
        let old = BodyId {
            entity: 0xa000_0101,
            generation: 1,
        };
        simulation
            .advance_projectiles(
                &queries,
                7,
                owner,
                vec![flight_fixture(owner, 1.0)],
                FIXTURE_CONTEXT,
            )
            .unwrap();
        let handle = simulation.projectiles[&7].order[0];
        let later = ShotContext {
            tick: FIXTURE_CONTEXT.tick + 3,
            ..FIXTURE_CONTEXT
        };
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, old, vec![flight_fixture(old, 1.0)], later)
            .unwrap();
        assert!(outcomes.is_empty() && hits.is_empty());
        assert_eq!(simulation.projectiles[&7].order, [handle]);
        assert_eq!(simulation.world.entities().len(), 1);
        assert!(matches!(
            simulation.advance_projectiles(
                &queries,
                7,
                BodyId {
                    entity: old.entity,
                    ..owner
                },
                Vec::new(),
                later
            ),
            Err(CombatError::FlightOwnerIdentity { .. })
        ));
        let (outcomes, hits) = simulation
            .advance_projectiles(&queries, 7, owner, Vec::new(), later)
            .unwrap();
        assert!(hits.is_empty());
        assert!(
            matches!(outcomes.as_slice(), [FlightOutcome::Ended { travelled, .. }]
            if (*travelled - 0.04).abs() < 1e-12)
        );
        assert!(simulation.world.entities().is_empty());
    }

    #[test]
    fn duplicate_or_exhausted_bot_identities_are_rejected_at_admission() {
        let mut bots = fixture_bots();
        bots[1].entity = bots[0].entity;
        assert!(matches!(
            RoomSimulation::new(bots),
            Err(CombatError::BotIdInUse(BOT_ENTITY))
        ));
        let mut bots = fixture_bots();
        bots[1].entity = u32::MAX;
        assert!(matches!(
            RoomSimulation::new(bots),
            Err(CombatError::BotIdsExhausted)
        ));
    }

    #[test]
    fn bot_damage_history_and_respawn_follow_identity_after_scene_reorder() {
        use crate::game_hitscan::TargetPose;

        let mut bots = RoomSimulation::new(fixture_bots()).unwrap();
        bots.activate_combat();
        let first = BOT_ENTITY;
        let second = BOT_ENTITY + 1;
        let first_handle = bots.by_wire[&first];
        let second_handle = bots.by_wire[&second];
        let context = FIXTURE_CONTEXT;
        let pose = TargetPose {
            feet: state(&bots, first).unwrap().bot.position,
            height: 1.8,
            radius: 0.5,
            eye: 1.6,
        };
        let second_pose = TargetPose {
            feet: state(&bots, second).unwrap().bot.position,
            ..pose
        };
        bots.state_mut(first)
            .unwrap()
            .unwrap()
            .history
            .record(context.tick, pose);
        bots.state_mut(second)
            .unwrap()
            .unwrap()
            .history
            .record(context.tick, second_pose);

        bots.by_wire.swap_indices(0, 1);
        bots.body_mut(first).unwrap().position = [7.0, 0.0, 0.0];
        let result = bots
            .apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
            .unwrap()
            .unwrap();
        let due = context.now + BOT_RESPAWN_TIME;
        let view = ViewFrame::from_command(
            &Command {
                frame: context.tick,
                ..Command::default()
            },
            context.quanta,
        );
        assert_eq!(result.health.entity, first);
        assert_eq!(
            result.notices[1],
            Some(CombatNotice::Elimination {
                killer: 7,
                victim: first
            })
        );
        let actors = bots.debug_actors().unwrap().collect::<Vec<_>>();
        assert_eq!(actors[0].entity, second);
        assert!(actors[0].alive);
        assert_eq!(actors[0].health, Some(100.0));
        assert_eq!(actors[1].entity, first);
        assert_eq!(actors[1].position, [7.0, 0.0, 0.0]);
        assert!(!actors[1].alive);
        assert_eq!(actors[1].health, Some(0.0));
        let target = state(&bots, first).unwrap();
        assert_eq!(target.respawn_at, Some(due));
        assert_eq!(target.history.at(view, second_pose), Some(pose));
        let other = state(&bots, second).unwrap();
        assert_eq!(other.respawn_at, None);
        assert_eq!(other.history.at(view, pose), Some(second_pose));

        let replacements = bots.respawn_due(context.tick + 1, due).unwrap();
        let [replacement] = replacements.as_slice() else {
            panic!("expected one due replacement");
        };
        let fresh = replacement.created.entity;
        assert_eq!(replacement.retired, first);
        assert_eq!(fresh, BOT_ENTITY + 2);
        assert_eq!(
            bots.by_wire.keys().copied().collect::<Vec<_>>(),
            [second, fresh]
        );
        assert!(state(&bots, first).is_none());
        assert!(bots.world.get::<BotState>(first_handle).is_none());
        assert_eq!(bots.by_wire[&second], second_handle);
        let target = state(&bots, fresh).unwrap();
        assert!(!target.bot.dead);
        assert_eq!(target.bot.position, pose.feet);
        assert_eq!(target.vitals.total(), 100.0);
        assert_eq!(target.respawn_at, None);
        assert_eq!(target.history.at(view, pose), None);
        assert_eq!(
            state(&bots, second).unwrap().history.at(view, pose),
            Some(second_pose)
        );
        assert!(
            bots.apply_bot_impact(7, context.tick, context, &fixture_impact(first, 100.0))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn failed_respawn_batch_preserves_all_due_bodies() {
        let mut bots = RoomSimulation::new(fixture_bots()).unwrap();
        let identities = [BOT_ENTITY, BOT_ENTITY + 1];
        let handles = [bots.by_wire[&identities[0]], bots.by_wire[&identities[1]]];
        let context = FIXTURE_CONTEXT;
        for identity in identities {
            bots.apply_bot_impact(7, context.tick, context, &fixture_impact(identity, 100.0))
                .unwrap();
        }
        let due = context.now + BOT_RESPAWN_TIME;
        let first_free = u32::MAX - 1;
        bots.next_bot = first_free;

        assert!(bots.respawn_due(context.tick + 1, due).is_err());
        assert_eq!(bots.next_bot, first_free);
        assert_eq!(bots.by_wire.keys().copied().collect::<Vec<_>>(), identities);
        for (identity, handle) in identities.into_iter().zip(handles) {
            assert_eq!(bots.by_wire[&identity], handle);
            let target = state(&bots, identity).unwrap();
            assert!(target.bot.dead);
            assert_eq!(target.vitals.total(), 0.0);
            assert_eq!(target.respawn_at, Some(due));
        }
    }

    #[test]
    fn respawn_encoding_failure_preserves_its_source_and_the_entire_batch() {
        let mut bots = fixture_bots();
        std::sync::Arc::get_mut(&mut bots[1].spawn).unwrap().team = u8::MAX;
        let mut simulation = RoomSimulation::new(bots).unwrap();
        let before = simulation.by_wire.clone();
        let next_bot = simulation.next_bot;
        let context = FIXTURE_CONTEXT;
        for identity in [BOT_ENTITY, BOT_ENTITY + 1] {
            simulation
                .apply_bot_impact(7, context.tick, context, &fixture_impact(identity, 100.0))
                .unwrap();
        }
        let error = simulation
            .respawn_due(context.tick + 1, context.now + BOT_RESPAWN_TIME)
            .err()
            .unwrap();
        assert!(matches!(&error, CombatError::RespawnCreate { wire, .. } if *wire == next_bot + 1));
        assert!(std::error::Error::source(&error).unwrap().is::<io::Error>());
        assert_eq!(simulation.by_wire, before);
        assert_eq!(simulation.next_bot, next_bot);
        assert!(simulation.bodies().unwrap().all(|bot| bot.dead));
    }

    #[test]
    #[ignore = "requires a prepared 104319 Practice Range scene"]
    fn repeated_rifle_hits_retire_and_respawn_only_the_hit_bot() {
        let runtime = std::env::var_os("ORPHEUS_RUNTIME_DIR").unwrap();
        let mut scene = SceneAssets::load(runtime)
            .unwrap()
            .prepare(GameMap::practice_range())
            .unwrap();
        let (bots, queries) = scene.bot_simulation();
        let slot = bots
            .bodies()
            .unwrap()
            .position(|bot| bot.spawn.team == 1)
            .unwrap();
        let bot = bots.bodies().unwrap().nth(slot).unwrap();
        let entity = bot.entity;
        let [x, y, z] = bot.position;
        let count = bots.bodies().unwrap().len();
        let mut context = ShotContext {
            team: 0,
            quanta: RETAIL_QUANTA,
            tick: 1000,
            now: Duration::from_secs(4),
        };
        // Before combat activation, moving bodies must not seed rewind history.
        let cold_frame = context.tick - 1;
        let mut fallback = queries.target(&state(bots, entity).unwrap().bot);
        fallback.feet[0] += 1.0;
        let view = ViewFrame::from_command(
            &Command {
                frame: cold_frame,
                ..Command::default()
            },
            context.quanta,
        );
        assert!(
            bots.finish_tick(&queries, cold_frame, context.now)
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            state(bots, entity).unwrap().history.at(view, fallback),
            Some(fallback)
        );
        bots.activate_combat();
        bots.finish_tick(&queries, context.tick, context.now)
            .unwrap();
        let mut shot = PosedHitscan {
            source: 0xa000_0101,
            origin: [x - 8.0, y + eye_height(SOLDIER, false), z],
            command: Command {
                frame: context.tick,
                yaw: 16384,
                ..Command::default()
            },
            shot_frame: 999,
            reach: 180.0,
            hit: HitDefinition {
                amount: 20.0,
                loadout: None,
                critical: 2.0,
                falloff: None,
            },
        };
        assert!(
            bots.hitscan(&queries, shot, context, |_| false)
                .unwrap()
                .is_none()
        );
        assert!(
            bots.hitscan(&queries, shot, ShotContext { team: 1, ..context }, |_| {
                true
            })
            .unwrap()
            .is_none()
        );
        for index in 0..5 {
            let result = bots
                .hitscan(&queries, shot, context, |_| true)
                .unwrap()
                .unwrap();
            let terminal = index == 4;
            assert_eq!(result.health.entity, entity);
            assert_eq!(
                result.notices[0],
                Some(CombatNotice::Hit {
                    target: entity,
                    dealt: 40.0,
                    critical: true,
                    killed: terminal,
                    shot_frame: shot.shot_frame,
                })
            );
            assert_eq!(
                result.notices[1],
                terminal.then_some(CombatNotice::Elimination {
                    killer: shot.source,
                    victim: entity,
                })
            );
            assert_eq!(
                state(bots, entity).unwrap().vitals.total(),
                160.0 - f64::from(index) * 40.0
            );
            assert_eq!(bots.bodies().unwrap().nth(slot).unwrap().dead, terminal);
        }
        assert!(
            bots.hitscan(&queries, shot, context, |_| true)
                .unwrap()
                .is_none()
        );
        let due = context.now + BOT_RESPAWN_TIME;
        assert!(
            bots.finish_tick(&queries, 1187, due - Duration::from_millis(1))
                .unwrap()
                .is_empty()
        );
        let [replacement] = bots
            .finish_tick(&queries, 1188, due)
            .unwrap()
            .try_into()
            .ok()
            .unwrap();
        assert_eq!(replacement.retired, entity);
        let next = BOT_ENTITY + count as u32;
        assert_eq!(replacement.created.entity, next);
        assert!(matches!(
            replacement.created.operation,
            Some(Operation::Create(_))
        ));
        assert!(!bots.bodies().unwrap().nth(slot).unwrap().dead);
        assert_eq!(state(bots, next).unwrap().vitals.total(), 200.0);
        context.tick = 1188;
        context.now = due;
        assert!(
            bots.hitscan(&queries, shot, context, |_| true)
                .unwrap()
                .is_none()
        );
        shot.command.frame = context.tick;
        assert!(
            bots.hitscan(&queries, shot, context, |visible| visible == next)
                .unwrap()
                .is_some()
        );
        assert_eq!(state(bots, next).unwrap().vitals.total(), 160.0);
    }
}
