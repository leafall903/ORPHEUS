//! Reliable entity and launch records for remote projectiles.

use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    io,
    sync::Arc,
};

use crate::{
    game_combat::{ObservedProjectileEvent, ObservedShot},
    game_projectiles::DELIVERY_GRACE,
    projectile_wire::{self, ShotRecord},
    script_snapshot_wire::ProjectedSnapshot,
    world::{EntityUpdate, Operation},
};

use super::{Delivery, Record, WorldDelivery};

// This wire namespace is separate from player leases and persistent field views.
const FIRST_ENTITY: u32 = 0xa00c_0000;
const END_ENTITY: u32 = 0xa010_0000;
const MAX_ENTITIES: usize = 256;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum ProjectileDelivery {
    Create(u32),
    Launch,
    Destroy(u32),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Phase {
    AwaitingSource,
    Creating,
    Launching { sent_at: Option<u32> },
    Visible { sent_at: u32 },
    Retiring,
}

#[derive(Clone, Copy)]
enum Flight {
    Active,
    Finished {
        frame: u32,
        impact: bool,
        point: Option<[f32; 3]>,
    },
    Retiring,
}

struct ProjectileView {
    shot: Arc<ObservedShot>,
    entities: Vec<u32>,
    pending: HashSet<u32>,
    phase: Phase,
    flights: Vec<Flight>,
    cancelled: bool,
}

impl ProjectileView {
    fn source(&self) -> u32 {
        self.shot.volley.event.entity
    }

    fn track_record(&self, step: ProjectileDelivery) -> bool {
        match (self.phase, step) {
            (Phase::Creating, ProjectileDelivery::Create(entity))
            | (Phase::Visible { .. } | Phase::Retiring, ProjectileDelivery::Destroy(entity)) => {
                self.pending.contains(&entity)
            }
            (Phase::Launching { .. }, ProjectileDelivery::Launch) => true,
            _ => false,
        }
    }

    fn launch(&self) -> io::Result<EntityUpdate> {
        let shot = self.shot.volley.event;
        let payload = projectile_wire::shot_events(&[ShotRecord {
            frame: shot.frame,
            instance: shot.instance,
            state: shot.raw_state,
            activation: shot.activation,
            ordinal: shot.ordinal,
            volleys: self.shot.volley.count,
            origin: self.shot.origin,
            direction: self.shot.direction,
            entities: &self.entities,
        }])?;
        Ok(EntityUpdate {
            entity: shot.entity,
            ch2: Some(payload),
            ..EntityUpdate::default()
        })
    }

    fn completed_at(&self) -> Option<u32> {
        self.flights
            .iter()
            .try_fold(0, |last, flight| match *flight {
                Flight::Finished { frame, .. } => Some(last.max(frame)),
                _ => None,
            })
    }

    fn retire(
        &mut self,
        through: u32,
        real_through: u32,
        all: bool,
        mut queue: impl FnMut(EntityUpdate, ProjectileDelivery),
    ) {
        for (&entity, flight) in self.entities.iter().zip(&mut self.flights) {
            let (due, impact_info) = match *flight {
                Flight::Retiring => (false, None),
                Flight::Active => (all, None),
                Flight::Finished {
                    frame,
                    impact,
                    point,
                } => {
                    let boundary_pad =
                        u32::from(point.is_none() && real_through % super::SEND_EVERY == 0);
                    (
                        all || frame <= through.saturating_add(boundary_pad),
                        impact.then_some((frame, point)),
                    )
                }
            };
            if due {
                *flight = Flight::Retiring;
                self.pending.insert(entity);
                queue(
                    EntityUpdate {
                        entity,
                        operation: Some(Operation::Destroy),
                        ch2: impact_info.and_then(|(frame, point)| match point {
                            Some(pos) => projectile_wire::release_at(frame, pos, 1).ok(),
                            None => projectile_wire::release(frame.saturating_add(4096), 1).ok(),
                        }),
                        ..Default::default()
                    },
                    ProjectileDelivery::Destroy(entity),
                );
            }
        }
        if self
            .flights
            .iter()
            .all(|flight| matches!(flight, Flight::Retiring))
        {
            self.phase = Phase::Retiring;
        }
    }
}

pub(super) struct ObservedProjectiles {
    views: BTreeMap<u64, ProjectileView>,
    reserved: HashSet<u32>,
    next: u32,
}

impl Default for ObservedProjectiles {
    fn default() -> Self {
        Self {
            views: BTreeMap::new(),
            reserved: HashSet::new(),
            next: FIRST_ENTITY,
        }
    }
}

impl ObservedProjectiles {
    pub(super) fn current(&self, id: u64, step: ProjectileDelivery) -> bool {
        self.views
            .get(&id)
            .is_some_and(|view| view.track_record(step))
    }

    pub(super) fn arrived(&mut self, id: u64, step: ProjectileDelivery) {
        let Some(view) = self.views.get_mut(&id) else {
            return;
        };
        match step {
            ProjectileDelivery::Create(entity) | ProjectileDelivery::Destroy(entity) => {
                view.pending.remove(&entity);
            }
            ProjectileDelivery::Launch => {
                if let Phase::Launching {
                    sent_at: Some(sent_at),
                } = view.phase
                {
                    view.phase = Phase::Visible { sent_at };
                }
            }
        }
    }

    pub(super) fn launch_sent(&mut self, id: u64, tick: u32) {
        if let Some(view) = self.views.get_mut(&id) {
            if let Phase::Launching { sent_at } = &mut view.phase {
                *sent_at = Some(tick);
            }
        }
    }

    fn apply(&mut self, event: &ObservedProjectileEvent) {
        match event {
            ObservedProjectileEvent::Launched {
                id,
                presentation,
                count,
            } => {
                if *count == 0
                    || usize::from(*count) > MAX_ENTITIES.saturating_sub(self.reserved.len())
                    || self.views.contains_key(id)
                {
                    return;
                }
                let mut entities = Vec::with_capacity(usize::from(*count));
                while entities.len() < usize::from(*count) {
                    let candidate = self.next;
                    self.next = if candidate + 1 == END_ENTITY {
                        FIRST_ENTITY
                    } else {
                        candidate + 1
                    };
                    if self.reserved.insert(candidate) {
                        entities.push(candidate);
                    }
                }
                self.views.insert(
                    *id,
                    ProjectileView {
                        shot: Arc::clone(presentation),
                        entities,
                        pending: HashSet::new(),
                        phase: Phase::AwaitingSource,
                        flights: vec![Flight::Active; usize::from(*count)],
                        cancelled: false,
                    },
                );
            }
            ObservedProjectileEvent::Completed {
                id,
                ordinal,
                frame,
                impact,
                point,
            } => {
                if let Some(flight @ Flight::Active) = self
                    .views
                    .get_mut(id)
                    .and_then(|view| view.flights.get_mut(*ordinal))
                {
                    *flight = Flight::Finished {
                        frame: *frame,
                        impact: *impact,
                        point: *point,
                    };
                }
            }
        }
    }

    pub(super) fn has_active_volleys_for(&self, source: u32) -> bool {
        self.views.values().any(|view| {
            view.source() == source
                && !view.cancelled
                && matches!(
                    view.phase,
                    Phase::AwaitingSource | Phase::Creating | Phase::Launching { .. }
                )
        })
    }

    pub(super) fn retain_volleys(&self, snapshot: &mut ProjectedSnapshot) {
        let source = snapshot.entity();
        let mut retained = BTreeSet::new();
        let mut presentation = BTreeSet::new();
        for view in self.views.values().filter(|view| {
            view.source() == source
                && !view.cancelled
                && matches!(
                    view.phase,
                    Phase::AwaitingSource | Phase::Creating | Phase::Launching { .. }
                )
        }) {
            let volley = &view.shot.volley;
            if retained.insert((volley.event.instance, volley.event.raw_state))
                && volley
                    .presentation_keys()
                    .all(|key| !presentation.contains(&key))
                && snapshot.retain_volley(volley)
            {
                // Later activations wait for the first shot using the same inputs.
                presentation.extend(volley.presentation_keys());
            }
        }
    }
}

impl WorldDelivery {
    pub(crate) fn observe_projectiles(&mut self, events: &[ObservedProjectileEvent]) {
        if self.world_started() {
            let local_body = self.body.map(|b| b.id.entity);
            for event in events {
                if let ObservedProjectileEvent::Launched { presentation, .. } = event {
                    if presentation.skip_owner
                        && local_body == Some(presentation.volley.event.entity)
                    {
                        continue;
                    }
                }
                self.observed_projectiles.apply(event);
            }
        }
    }

    pub(super) fn advance_observed_projectiles(&mut self, tick: u32) -> io::Result<()> {
        if self.observed_projectiles.views.is_empty() {
            return Ok(());
        }
        let mut records = Vec::new();
        let mut clear = Vec::new();
        let owner_ready_body = self
            .body_script
            .as_ref()
            .filter(|script| self.owner_body_ready(script.id))
            .map(|script| script.id.entity);
        let owner_base = self.body_script.as_ref().and_then(|script| {
            script
                .stream
                .base()
                .map(|(_, base)| (script.id.entity, base))
        });
        let tracker = &mut self.observed_projectiles;
        for (&id, view) in &mut tracker.views {
            let mut queue = |update, step| {
                records.push(Record {
                    update,
                    delivery: Delivery::ObservedProjectile { id, step },
                });
            };
            let source = view.source();
            let live = self.bots.get(&source) == Some(&super::BotLifetime::Live)
                || self
                    .remote_players
                    .get(&source)
                    .is_some_and(|&(_, life)| life == super::BotLifetime::Live)
                || owner_ready_body == Some(source);
            let base = self
                .scene_views
                .get(&source)
                .and_then(|stream| stream.base())
                .map(|(_, base)| base)
                .or_else(|| owner_base.and_then(|(ent, base)| (ent == source).then_some(base)));
            let completed = view.completed_at();
            let deadline = completed
                .unwrap_or_else(|| view.shot.launched.saturating_add(view.shot.lifetime_ticks))
                .saturating_add(DELIVERY_GRACE);
            let expired = tick >= deadline;
            let retire = match view.phase {
                Phase::AwaitingSource => {
                    if !live || expired {
                        clear.push(id);
                    } else if base.is_some() {
                        view.phase = Phase::Creating;
                        view.pending.extend(view.entities.iter().copied());
                        for &entity in &view.entities {
                            queue(
                                projectile_wire::confirmation(entity, view.shot.definition),
                                ProjectileDelivery::Create(entity),
                            );
                        }
                    }
                    false
                }
                Phase::Creating => {
                    view.cancelled |= !live || expired;
                    if !view.pending.is_empty() {
                        false
                    } else if view.cancelled {
                        true
                    } else {
                        if base.is_some_and(|base| base.has_volley(&view.shot.volley)) {
                            let update = view.launch()?;
                            super::delivery::ensure_script_chunk_fits(
                                &update,
                                "remote projectile launch exceeds the datagram budget",
                            )?;
                            queue(update, ProjectileDelivery::Launch);
                            view.phase = Phase::Launching { sent_at: None };
                        }
                        false
                    }
                }
                Phase::Launching { sent_at } => {
                    // A short flight can finish before transport reports a lost
                    // launch. Keep the shot and its entities retryable until the
                    // delivery deadline while its source can still receive it.
                    expired
                        || (!live
                            && (sent_at.is_none()
                                || sent_at.zip(completed).is_some_and(|(sent_at, end)| {
                                    tick >= sent_at
                                        .saturating_add(end.saturating_sub(view.shot.launched))
                                })))
                }
                Phase::Visible { sent_at } => {
                    // The client can start tracing the projectile on receipt,
                    // before this room processes the packet acknowledgement.
                    // Align retirement to SEND_EVERY boundaries so a projectile
                    // that impacts before the next flush stops instead of
                    // visually passing through an ally target.
                    if tick > sent_at {
                        let through = view
                            .shot
                            .launched
                            .saturating_add(tick.saturating_sub(sent_at));
                        view.retire(through, tick, false, &mut queue);
                    }
                    false
                }
                Phase::Retiring => {
                    if view.pending.is_empty() {
                        clear.push(id);
                    }
                    false
                }
            };
            if retire {
                view.retire(tick, tick, true, &mut queue);
            }
        }
        for id in clear {
            if let Some(view) = tracker.views.remove(&id) {
                for entity in view.entities {
                    tracker.reserved.remove(&entity);
                }
            }
        }
        if !records.is_empty() {
            for record in records {
                self.push(record);
            }
            self.remove_stale();
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bits::{BitReader, BitWriter},
        game_egress::{DeliveryReport, PacketDelivery},
        script_expr::{Scope, Variable},
        script_init::ShotEvent,
        script_snapshot_wire::{ProjectedInstance, ProjectedState, View, VolleyActivation},
        script_stream::{BodyStream, DeltaKind},
        script_wire::{Binding, Value},
    };
    use serde::Deserialize;
    use std::time::Duration;

    #[derive(Deserialize)]
    struct Input {
        source: u32,
        graph: u32,
        instance: u16,
        state: u16,
        activation: u16,
        launch: u32,
        flight_ticks: u32,
        early_contact_ticks: u32,
        count: u16,
        presentation_variable: u32,
        presentation_value: bool,
    }

    fn project(input: &Input, activation: u16) -> ProjectedSnapshot {
        let start_ms = u64::from(input.launch) * crate::script_runtime::FRAME_MS;
        ProjectedSnapshot {
            entity: input.source,
            view: View::Observer,
            entity_exclusions: BTreeSet::new(),
            variables: Default::default(),
            instances: BTreeMap::from([(
                input.instance,
                ProjectedInstance {
                    graph: input.graph,
                    start_ms,
                    parent: None,
                    variables: Default::default(),
                    states: BTreeMap::from([(
                        input.state,
                        ProjectedState {
                            active: false,
                            payload: BTreeMap::from([
                                ("counter".into(), Value::Integer(i64::from(activation))),
                                ("volleys".into(), Value::Integer(1)),
                            ]),
                            volley: Some(VolleyActivation {
                                identity: (activation, start_ms),
                                ammo: None,
                            }),
                            link: None,
                        },
                    )]),
                    events: Vec::new(),
                },
            )]),
            callout: 0,
            callouts: Vec::new(),
            emit_events: false,
        }
    }

    fn outgoing(world: &mut WorldDelivery) -> Vec<Arc<Record>> {
        let (indices, records): (Vec<_>, Vec<_>) = world
            .ready()
            .iter()
            .map(|ready| (ready.index, ready.tracked()))
            .unzip();
        world.sent(&indices);
        records
    }

    fn acknowledge(world: &mut WorldDelivery, records: Vec<Arc<Record>>, arrived: bool) {
        world.acknowledge(
            DeliveryReport {
                requeued_reliable: 0,
                owner_correction: None,
                packets: vec![PacketDelivery {
                    sequence: 0,
                    arrived,
                    records,
                }],
            },
            Duration::ZERO,
        );
    }

    fn send_launch(world: &mut WorldDelivery, tick: u32, sequence: u32) -> Arc<Record> {
        let ready = world.ready();
        assert_eq!(ready.len(), 1);
        let record = ready[0].tracked();
        let sent = world
            .send_frame(
                tick,
                Vec::new(),
                false,
                None,
                &mut super::super::GameEgress::default(),
                |_| Ok::<_, ()>(sequence),
            )
            .unwrap();
        assert_eq!(sent.records, [super::super::RecordSource::Queued(0)]);
        record
    }

    #[test]
    fn remote_projectiles_wait_for_creates_and_the_exact_volley_then_retry_launch() {
        check_remote_projectile_delivery(true, false);
    }

    #[test]
    fn completed_remote_volley_retries_late_loss_until_the_delivery_deadline() {
        check_remote_projectile_delivery(false, false);
    }

    #[test]
    fn transmitted_remote_flights_outlive_their_retired_shooter() {
        for ack_launch in [false, true] {
            check_remote_projectile_delivery(ack_launch, true);
        }
    }

    fn check_remote_projectile_delivery(ack_launch: bool, retire_source: bool) {
        #[derive(Deserialize)]
        struct Fixture {
            case_id: String,
            input: Input,
            shooter_retirement_ticks_after_transmission: u32,
            loss_report_ticks_after_visual_end: u32,
        }
        let fixture: Fixture = serde_json::from_str(include_str!(
            "../../../fixtures/behavior/observed-projectile-delivery-v1.json"
        ))
        .unwrap();
        assert_eq!(fixture.case_id, "observed-projectile-delivery-v1");
        let input = fixture.input;
        let id = 1;
        let launch = input.launch;
        let finished = launch + input.flight_ticks;
        let event = ShotEvent {
            entity: input.source,
            instance: input.instance,
            raw_state: input.state,
            graph: input.graph,
            frame: launch,
            time_ms: u64::from(launch) * crate::script_runtime::FRAME_MS,
            activation: input.activation,
            activation_ms: u64::from(launch) * crate::script_runtime::FRAME_MS,
            ordinal: 0,
        };
        let mut original = project(&input, input.activation);
        let mut newer = project(&input, input.activation + 1);
        let variable = Variable {
            scope: Scope::Instance,
            id: input.presentation_variable,
        };
        let firing = Value::Bool(input.presentation_value);
        let volley = original
            .freeze_volley(event, vec![(variable, firing.clone())])
            .unwrap();
        assert!(!original.has_volley(&volley));
        assert!(newer.freeze_volley(event, Vec::new()).is_none());
        original
            .instances
            .get_mut(&input.instance)
            .unwrap()
            .variables
            .values
            .insert(variable.id, firing);
        let binding = Binding {
            instance: input.instance,
            state: input.state,
            state_bits: 4,
            slot: 0,
            priority: 0,
            above: true,
        };
        let attach_binding = |snapshot: &mut ProjectedSnapshot| {
            let variables = &mut snapshot
                .instances
                .get_mut(&input.instance)
                .unwrap()
                .variables;
            variables
                .values
                .entry(variable.id)
                .or_insert(Value::Bool(false));
            variables.bindings.insert(variable.id, vec![binding]);
        };
        let mut bound = original.clone();
        attach_binding(&mut bound);
        assert!(!bound.has_volley(&volley));
        attach_binding(&mut newer);
        let mut world = WorldDelivery::new(103);
        world.started = Some(100);
        world
            .bots
            .insert(input.source, super::super::BotLifetime::Live);
        world.observe_projectiles(&[ObservedProjectileEvent::Launched {
            id,
            presentation: Arc::new(ObservedShot {
                volley,
                definition: 0x0400_0000_0000_00ab,
                origin: [1.0, 2.0, 3.0],
                direction: [0.0, 0.0, 1.0],
                launched: launch,
                lifetime_ticks: 30,
                skip_owner: false,
            }),
            count: input.count,
        }]);
        world.advance_observed_projectiles(launch).unwrap();
        assert!(outgoing(&mut world).is_empty());

        let mut source = BodyStream::new(input.source);
        source.create_arrived();
        let chunk = source.full(&BitWriter::new(), newer.clone()).unwrap();
        assert!(source.arrived(chunk.last).advanced);
        world.scene_views.insert(input.source, source);
        world.advance_observed_projectiles(launch + 1).unwrap();
        let creates = outgoing(&mut world);
        assert_eq!(creates.len(), usize::from(input.count));
        assert!(
            creates
                .iter()
                .all(|record| matches!(record.update.operation, Some(Operation::Create(_))))
        );
        let entities: Vec<_> = creates.iter().map(|record| record.update.entity).collect();
        acknowledge(&mut world, creates, false);
        let mut retry = outgoing(&mut world);
        assert_eq!(
            retry
                .iter()
                .map(|record| record.update.entity)
                .collect::<Vec<_>>(),
            entities
        );
        // A partial acknowledgement must not publish a launch.
        let last = retry.pop().unwrap();
        acknowledge(&mut world, retry, true);
        world.advance_observed_projectiles(launch + 2).unwrap();
        assert!(outgoing(&mut world).is_empty());
        acknowledge(&mut world, vec![last], true);
        world.advance_observed_projectiles(launch + 3).unwrap();
        assert!(outgoing(&mut world).is_empty());

        // A later server activation cannot replace the one referenced by the shot.
        let mut pinned = newer.clone();
        world.observed_projectiles.retain_volleys(&mut pinned);
        assert_eq!(pinned, original);
        let stream = world.scene_views.get_mut(&input.source).unwrap();
        let delta = stream
            .delta(
                &BitWriter::new(),
                pinned,
                BTreeSet::new(),
                DeltaKind::Observer { resend: true },
            )
            .unwrap();
        world.advance_observed_projectiles(launch + 4).unwrap();
        assert!(outgoing(&mut world).is_empty());
        assert!(
            world
                .scene_views
                .get_mut(&input.source)
                .unwrap()
                .arrived(delta.last)
                .advanced
        );
        world.advance_observed_projectiles(launch + 5).unwrap();
        let launched = send_launch(&mut world, launch + 5, 1);
        assert_eq!(launched.update.entity, input.source);
        let bits = launched.update.ch2.as_ref().unwrap();
        let mut reader = BitReader::new(bits.as_bytes());
        assert!(reader.bit().unwrap());
        assert!(reader.bit().unwrap());
        assert_eq!(reader.var_b().unwrap(), launch);
        acknowledge(&mut world, vec![Arc::clone(&launched)], false);
        let retry_tick = launch + 6;
        let replay = send_launch(&mut world, retry_tick, 2);
        assert_eq!(replay.update, launched.update);

        world.observe_projectiles(&[
            ObservedProjectileEvent::Completed {
                id,
                ordinal: 1,
                frame: launch + input.early_contact_ticks,
                impact: true,
                point: None,
            },
            ObservedProjectileEvent::Completed {
                id,
                ordinal: 0,
                frame: finished,
                impact: false,
                point: None,
            },
        ]);
        world.advance_observed_projectiles(finished).unwrap();
        assert!(outgoing(&mut world).is_empty());
        if ack_launch {
            acknowledge(&mut world, vec![Arc::clone(&replay)], true);
        }
        if retire_source {
            world.bots.remove(&input.source);
            if !ack_launch {
                acknowledge(&mut world, vec![Arc::clone(&replay)], false);
            }
            let retirement_tick = retry_tick + fixture.shooter_retirement_ticks_after_transmission;
            assert!(retirement_tick < retry_tick + input.early_contact_ticks);
            world.advance_observed_projectiles(retirement_tick).unwrap();
            assert!(outgoing(&mut world).is_empty());
        }
        if !ack_launch {
            let visual_end = retry_tick + input.flight_ticks;
            let (retire, last_launch) = if retire_source {
                (visual_end, replay)
            } else {
                let report_tick = visual_end + fixture.loss_report_ticks_after_visual_end;
                world.advance_observed_projectiles(report_tick).unwrap();
                assert!(outgoing(&mut world).is_empty());
                acknowledge(&mut world, vec![Arc::clone(&replay)], false);
                let late_retry = send_launch(&mut world, report_tick, 3);
                assert_eq!(late_retry.update, replay.update);
                (finished + DELIVERY_GRACE, late_retry)
            };
            world.advance_observed_projectiles(retire - 1).unwrap();
            assert!(outgoing(&mut world).is_empty());
            world.advance_observed_projectiles(retire).unwrap();
            acknowledge(&mut world, vec![last_launch], false);
            let destroys = outgoing(&mut world);
            assert_eq!(destroys.len(), entities.len());
            for (destroy, entity) in destroys.iter().zip(entities) {
                assert_eq!(destroy.update.entity, entity);
                assert!(matches!(destroy.update.operation, Some(Operation::Destroy)));
            }
            acknowledge(&mut world, destroys, true);
            world.advance_observed_projectiles(retire + 1).unwrap();
            assert!(world.observed_projectiles.views.is_empty());
            assert!(world.observed_projectiles.reserved.is_empty());
            return;
        }
        let mut settled = newer.clone();
        world.observed_projectiles.retain_volleys(&mut settled);
        assert_eq!(settled, newer);
        let arrival = finished + 1;
        let retire = retry_tick + input.flight_ticks;
        world.advance_observed_projectiles(arrival).unwrap();
        assert!(outgoing(&mut world).is_empty());
        world
            .advance_observed_projectiles(retry_tick + input.early_contact_ticks)
            .unwrap();
        let early = outgoing(&mut world);
        assert_eq!(early.len(), 1);
        assert_eq!(early[0].update.entity, entities[1]);
        assert!(matches!(
            early[0].update.operation,
            Some(Operation::Destroy)
        ));
        acknowledge(&mut world, early, false);
        let early_retry = outgoing(&mut world);
        assert_eq!(early_retry.len(), 1);
        assert_eq!(early_retry[0].update.entity, entities[1]);
        acknowledge(&mut world, early_retry, true);
        world.advance_observed_projectiles(retire - 1).unwrap();
        assert!(outgoing(&mut world).is_empty());
        world.advance_observed_projectiles(retire).unwrap();
        let destroys = outgoing(&mut world);
        assert_eq!(
            destroys
                .iter()
                .map(|record| record.update.entity)
                .collect::<Vec<_>>(),
            [entities[0]]
        );
        assert!(
            destroys
                .iter()
                .all(|record| matches!(record.update.operation, Some(Operation::Destroy)))
        );
        acknowledge(&mut world, destroys, true);
        world.advance_observed_projectiles(retire + 1).unwrap();
        assert!(world.observed_projectiles.views.is_empty());
        assert!(world.observed_projectiles.reserved.is_empty());
    }

    #[test]
    fn visible_flight_retires_at_send_every_boundary_before_overshoot() {
        let input = Input {
            source: 0xc000_0001,
            graph: 0x0ba3,
            instance: 1,
            state: 28,
            activation: 1,
            launch: 100,
            flight_ticks: 10,
            early_contact_ticks: 1,
            count: 1,
            presentation_variable: 233,
            presentation_value: true,
        };
        let mut snap = project(&input, 1);
        let event = ShotEvent {
            entity: input.source,
            instance: input.instance,
            raw_state: input.state,
            graph: input.graph,
            frame: input.launch,
            time_ms: u64::from(input.launch) * crate::script_runtime::FRAME_MS,
            activation: input.activation,
            activation_ms: u64::from(input.launch) * crate::script_runtime::FRAME_MS,
            ordinal: 0,
        };
        let var = Variable {
            scope: Scope::Instance,
            id: input.presentation_variable,
        };
        let volley = snap
            .freeze_volley(event, vec![(var, Value::Bool(true))])
            .unwrap();
        snap.instances
            .get_mut(&input.instance)
            .unwrap()
            .variables
            .values
            .insert(var.id, Value::Bool(true));

        let mut world = WorldDelivery::new(103);
        world.started = Some(100);
        world
            .bots
            .insert(input.source, super::super::BotLifetime::Live);
        let mut source = BodyStream::new(input.source);
        source.create_arrived();
        let chunk = source.full(&BitWriter::new(), snap).unwrap();
        assert!(source.arrived(chunk.last).advanced);
        world.scene_views.insert(input.source, source);

        world.observe_projectiles(&[
            ObservedProjectileEvent::Launched {
                id: 7,
                presentation: Arc::new(ObservedShot {
                    volley,
                    definition: 0x0400_0000_0000_00ab,
                    origin: [0.0; 3],
                    direction: [0.0, 0.0, 1.0],
                    launched: 100,
                    lifetime_ticks: 10,
                    skip_owner: false,
                }),
                count: 1,
            },
            // Completes at tick 102 (1 tick before SEND_EVERY=3 tick 102? Wait: 102 % 3 == 0,
            // or completes at 103 when sent_at=101 and 102 % 3 == 0).
            ObservedProjectileEvent::Completed {
                id: 7,
                ordinal: 0,
                frame: 102,
                impact: true,
                point: None,
            },
        ]);
        world.advance_observed_projectiles(100).unwrap();
        let creates = outgoing(&mut world);
        acknowledge(&mut world, creates, true);
        world.advance_observed_projectiles(101).unwrap();
        let launch_rec = send_launch(&mut world, 101, 1);
        acknowledge(&mut world, vec![launch_rec], true);
        // At tick 102 (102 % SEND_EVERY == 0), sent_at == 101, launched + 1 + 2 = 103 >= 102 -> retires!
        world.advance_observed_projectiles(102).unwrap();
        let destroys = outgoing(&mut world);
        assert_eq!(destroys.len(), 1);
        assert!(matches!(
            destroys[0].update.operation,
            Some(Operation::Destroy)
        ));
    }
}
