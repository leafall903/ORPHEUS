mod control;

use super::{body::*, *};

#[derive(Clone, Copy, PartialEq, Eq)]
enum BodyTransition {
    Create,
    Respawn,
    Transfer,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(super) enum Lifetime {
    Live,
    Retiring,
    ReusableAt(Duration),
}

#[derive(Clone, Copy)]
pub(super) enum Delivery {
    Baseline,
    BotCreate {
        after: Option<u32>,
    },
    Health {
        revision: u64,
    },
    PlayerSeat {
        revision: u64,
    },
    BotDestroy,
    BotControllerCreate,
    BotPossession {
        body: u32,
    },
    RemotePlayerCreate(BodyId),
    RemotePlayerDestroy(BodyId),
    ParticipantDestroy,
    CreateBody(BodyId),
    Possession(BodyId),
    DestroyBody(BodyId),
    BodyScript {
        #[cfg(test)]
        rifle_ammo_written: Option<i64>,
        id: BodyId,
        last: u32,
        delta: bool,
    },
    SceneScript {
        last: u32,
    },
    ObjectiveScript {
        last: u32,
    },
    ObjectivePlacement {
        appearing: bool,
    },
    ObservedProjectile {
        id: u64,
        step: super::observed_projectiles::ProjectileDelivery,
    },
    ProjectileConfirm {
        id: BodyId,
        token: ProjectileToken,
    },
    ProjectileDestroy {
        id: BodyId,
        token: ProjectileToken,
    },
    BioticCreate,
    BioticFrame {
        last: u32,
    },
    BioticDestroy,
    Script {
        last: u32,
        close: bool,
        resend: bool,
    },
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(super) enum BotLifetime {
    Live,
    Retiring,
    RetiringEnemy,
}

fn acknowledged_contact(
    entity: u32,
    created: &HashSet<u32>,
    pending_respawn: &HashSet<u32>,
) -> bool {
    created.contains(&entity) && !pending_respawn.contains(&entity)
}

impl WorldDelivery {
    pub(crate) fn new(owner: u32) -> Self {
        Self {
            owner,
            queued: Vec::new(),
            created: HashSet::new(),
            bots: HashMap::new(),
            pending_respawn_presentation: HashSet::new(),
            bot_controllers: HashMap::new(),
            health_updates: HashMap::new(),
            health_revision: 0,
            entity_notice_ordinals: HashMap::new(),
            started: None,
            presentation_due: false,
            controller: Stream::new(owner),
            controller_pending: None,
            controller_skins: false,
            mode: Stream::new(GAME_MODE_ENTITY),
            custom_mode_view: None,
            payload_mode_view: None,
            objective_views: BTreeMap::new(),
            objective_placements: BTreeMap::new(),
            payload_pose: None,
            payload_cart_phase: None,
            payload_cart_finish_music: false,
            payload_progress_bound: false,
            payload_viewer_team: None,
            payload_refresh_rows: false,
            payload_audio: None,
            payload_cues: Vec::new(),
            control_body_round: None,
            countdown: None,
            encounter_post_match_queued: false,
            encounter_screens: None,
            encounter_notifications: None,
            encounter_notification_turn: true,
            encounter_phases: BTreeMap::new(),
            encounter_result_queued: false,
            encounter_outcome_queued: false,
            select_open: true,
            viewer_team: 0,
            assembly_choice: None,
            assembly_confirmed: false,
            refresh_selection: false,
            team_updates: HashMap::new(),
            body: None,
            bound: None,
            generation: 0,
            bodies: HashMap::new(),
            remote_players: HashMap::new(),
            input: InputQueue::default(),
            body_script: None,
            detached_motion: None,
            pending_owner_correction: None,
            #[cfg(test)]
            owner_script_fault: None,
            projectile_destroys: HashMap::new(),
            biotic_views: BTreeMap::new(),
            scene_views: BTreeMap::new(),
            retiring_nameplate_chunks: HashSet::new(),
            observed_projectiles: Default::default(),
            bot_chunk_waited: false,
            last_sent_remote_movement: None,
            gate: None,
            next_biotic_view: FIRST_BIOTIC_VIEW,
        }
    }

    pub(crate) fn owner(&self) -> u32 {
        self.owner
    }

    pub(crate) fn clear(&mut self) {
        self.queued.clear();
        self.created.clear();
        self.bots.clear();
        self.pending_respawn_presentation.clear();
        self.bot_controllers.clear();
        self.remote_players.clear();
        self.health_updates.clear();
        self.started = None;
        self.controller = Stream::new(self.owner);
        self.controller_pending = None;
        self.controller_skins = false;
        self.select_open = true;
        self.assembly_choice = None;
        self.assembly_confirmed = false;
        self.refresh_selection = false;
        self.team_updates.clear();
        self.presentation_due = false;
        self.custom_mode_view = None;
        self.payload_mode_view = None;
        self.objective_views.clear();
        self.objective_placements.clear();
        self.payload_pose = None;
        self.payload_cart_phase = None;
        self.payload_cart_finish_music = false;
        self.payload_progress_bound = false;
        self.payload_viewer_team = None;
        self.payload_refresh_rows = false;
        self.payload_audio = None;
        self.payload_cues.clear();
        self.control_body_round = None;
        self.countdown = None;
        self.encounter_post_match_queued = false;
        self.encounter_screens = None;
        self.encounter_notifications = None;
        self.encounter_notification_turn = true;
        self.encounter_phases.clear();
        self.encounter_result_queued = false;
        self.encounter_outcome_queued = false;
        self.body_script = None;
        self.detached_motion = None;
        self.pending_owner_correction = None;
        self.projectile_destroys.clear();
        self.biotic_views.clear();
        self.scene_views.clear();
        self.retiring_nameplate_chunks.clear();
        self.observed_projectiles = Default::default();
        self.bot_chunk_waited = false;
        self.last_sent_remote_movement = None;
        self.gate = None;
        self.input.clear();
    }

    pub(crate) fn clear_in_room(&mut self, simulation: &mut RoomSimulation) {
        simulation.retire_player_owner(self.owner);
        self.clear();
    }

    pub(super) fn push(&mut self, record: Record) {
        self.queued.push(Arc::new(record));
    }

    pub(crate) fn world_started(&self) -> bool {
        self.started.is_some()
    }

    pub(crate) fn spawned_body(&self) -> Option<u32> {
        self.body
            .filter(|_| self.world_started())
            .map(|body| body.id.entity)
    }

    pub(crate) fn active_body_id(&self) -> Option<BodyId> {
        self.body
            .filter(|_| self.world_started())
            .map(|body| body.id)
    }

    pub(crate) fn identity_ready(&self, entity: u32) -> bool {
        self.world_started() && self.created.contains(&entity)
    }

    pub(crate) fn actor_contact_ready(&self, entity: u32) -> bool {
        self.world_started()
            && acknowledged_contact(entity, &self.created, &self.pending_respawn_presentation)
    }

    pub(super) fn owner_movement_blockers(
        &self,
        frame: &crate::game_combat::MovementContactFrame,
        source: u32,
        team: u8,
    ) -> Vec<crate::contact_cache::CapsulePose> {
        frame.blockers_where(source, team, |entity| self.actor_contact_ready(entity))
    }

    pub(crate) fn owner_body_ready(&self, id: BodyId) -> bool {
        self.bound == Some(id) && self.identity_ready(id.entity)
    }

    pub(crate) fn track_entity_notice(&mut self, entity: u32, ordinal: u32) {
        self.entity_notice_ordinals.insert(entity, ordinal);
    }

    pub(crate) fn pending_entity_notice(
        &self,
        entity: u32,
        egress: &GameEgress<Arc<Record>>,
    ) -> bool {
        self.entity_notice_ordinals
            .get(&entity)
            .is_some_and(|&ordinal| !egress.delivered_through(ordinal))
    }

    pub(crate) fn mode_ready(&self) -> bool {
        self.mode.delivered() != 0
    }

    pub(crate) fn change_viewer_team(&mut self, team: u8, simulation: &mut RoomSimulation) {
        if self.viewer_team == team {
            return;
        }
        self.viewer_team = team;
        self.select_open = team != crate::room_primitives::SPECTATOR_TEAM;
        self.assembly_choice = None;
        self.assembly_confirmed = false;
        self.refresh_selection = true;
        self.input.work.clear();
        self.cancel_body_script(Some(simulation));
        if let Some(body) = self.body.take() {
            simulation.retire_player_body(body.id);
            self.retire_body(body.id);
        }
        self.bound = None;
        self.remove_stale();
    }

    pub(crate) fn queue_participant(&mut self, update: EntityUpdate) {
        self.push(Record::baseline(update));
    }

    pub(crate) fn retire_participant(&mut self, entity: u32) {
        if !self.world_started() {
            return;
        }
        self.team_updates.remove(&entity);
        self.remove_stale();
        self.push(Record {
            update: EntityUpdate {
                entity,
                operation: Some(Operation::Destroy),
                ..Default::default()
            },
            delivery: Delivery::ParticipantDestroy,
        });
    }

    pub(crate) fn queue_player_seat(&mut self, mut update: EntityUpdate) {
        let entity = update.entity;
        if !self.world_started() {
            return;
        }
        let Some(Operation::Update(components)) = &mut update.operation else {
            return;
        };
        let mut clears_possession = components.iter().any(|component| component.index == 44);
        if !clears_possession
            && self
                .team_updates
                .get(&entity)
                .is_some_and(|&(_, pending)| pending)
        {
            components.extend(crate::match_scene::Possession::CLEARED.components());
            clears_possession = true;
        }
        self.health_revision = self.health_revision.wrapping_add(1);
        let revision = self.health_revision;
        self.team_updates
            .insert(entity, (revision, clears_possession));
        self.remove_stale();
        self.push(Record {
            update,
            delivery: Delivery::PlayerSeat { revision },
        });
    }

    pub(crate) fn take_payload_cues(&mut self) -> Vec<crate::room_primitives::VoiceCue> {
        if self.mode.data_in_flight() {
            return Vec::new();
        }
        std::mem::take(&mut self.payload_cues)
    }

    pub(crate) fn health_pending(&self, entity: u32) -> bool {
        self.health_updates.contains_key(&entity)
    }

    pub(crate) fn tracks_current_bot(&self, entity: u32) -> bool {
        self.bots.get(&entity) == Some(&BotLifetime::Live)
    }

    pub(crate) fn queue_health(&mut self, update: EntityUpdate) {
        let entity = update.entity;
        let is_owner_or_mode = entity == self.owner || entity == GAME_MODE_ENTITY;
        let live_bot = self.tracks_current_bot(entity);
        let live_remote = self
            .remote_players
            .get(&entity)
            .is_some_and(|&(_, life)| life == BotLifetime::Live);
        let live_body = self.body.is_some_and(|body| {
            body.id.entity == entity
                && self.bodies.get(&entity) == Some(&(body.id.generation, Lifetime::Live))
        });
        let placed_movement = entity & crate::room_primitives::MAP_ENTITY_BIT != 0
            && update.operation.is_none()
            && update.chunk.is_none()
            && update.movement.is_some()
            && self.identity_ready(entity);
        if !is_owner_or_mode
            && !live_bot
            && !live_remote
            && !live_body
            && !placed_movement
            && !self.objective_views.contains_key(&entity)
        {
            return;
        }
        self.health_revision = self.health_revision.wrapping_add(1);
        let revision = self.health_revision;
        self.health_updates.insert(entity, revision);
        self.remove_stale();
        self.push(Record {
            update,
            delivery: Delivery::Health { revision },
        });
    }

    pub(crate) fn create_remote_player(&mut self, id: BodyId, update: EntityUpdate) {
        if !self.world_started() || self.remote_players.contains_key(&id.entity) {
            return;
        }
        self.remote_players
            .insert(id.entity, (id, BotLifetime::Live));
        self.push(Record {
            update,
            delivery: Delivery::RemotePlayerCreate(id),
        });
    }

    pub(crate) fn retire_remote_player(&mut self, id: BodyId) {
        if self.remote_players.get(&id.entity) != Some(&(id, BotLifetime::Live)) {
            return;
        }
        let unsent_create = !self.created.contains(&id.entity)
            && self.queued.iter().any(
                |record| matches!(record.delivery, Delivery::RemotePlayerCreate(qid) if qid == id),
            );
        self.health_updates.remove(&id.entity);
        self.scene_views.remove(&id.entity);
        if unsent_create {
            self.remote_players.remove(&id.entity);
            self.remove_stale();
        } else {
            self.remote_players
                .insert(id.entity, (id, BotLifetime::RetiringEnemy));
            self.remove_stale();
            self.push(Record {
                update: EntityUpdate {
                    entity: id.entity,
                    operation: Some(Operation::Destroy),
                    ..Default::default()
                },
                delivery: Delivery::RemotePlayerDestroy(id),
            });
        }
    }

    pub(crate) fn create_bot(&mut self, update: EntityUpdate) {
        if !self.world_started() || self.bots.contains_key(&update.entity) {
            return;
        }
        self.bots.insert(update.entity, BotLifetime::Live);
        self.push(Record {
            update,
            delivery: Delivery::BotCreate { after: None },
        });
    }

    pub(crate) fn replace_bot(&mut self, replacement: BotReplacement) {
        let old = replacement.retired;
        let new = replacement.created.entity;
        if !self.tracks_current_bot(old) || self.bots.contains_key(&new) {
            return;
        }
        #[cfg(feature = "inspector")]
        if let Some(Operation::Create(create)) = &replacement.created.operation {
            crate::owner_trace::playtest(format_args!(
                "bot-respawn event=queued owner={:08x} old={old:08x} new={new:08x} spawn={:?}",
                self.owner, create.position
            ));
        }
        self.bots.insert(old, BotLifetime::Retiring);
        self.bots.insert(new, BotLifetime::Live);
        self.pending_respawn_presentation.remove(&old);
        if replacement.waits_for_presentation {
            self.pending_respawn_presentation.insert(new);
        }
        self.created.remove(&old);
        self.health_updates.remove(&old);
        self.remove_stale();
        self.push(Record {
            update: EntityUpdate {
                entity: old,
                operation: Some(Operation::Destroy),
                ..EntityUpdate::default()
            },
            delivery: Delivery::BotDestroy,
        });
        self.push(Record {
            update: replacement.created,
            delivery: Delivery::BotCreate { after: Some(old) },
        });
    }

    pub(crate) fn retire_enemy(&mut self, entity: u32) {
        if self.bots.get(&entity) != Some(&BotLifetime::Live) {
            return;
        }
        self.bots.insert(entity, BotLifetime::RetiringEnemy);
        self.health_updates.remove(&entity);
        self.scene_views.remove(&entity);
        self.remove_stale();
        self.push(Record {
            update: EntityUpdate {
                entity,
                operation: Some(Operation::Destroy),
                ..Default::default()
            },
            delivery: Delivery::BotDestroy,
        });
    }

    fn current(&self, record: &Record) -> bool {
        let entity = record.update.entity;
        match record.delivery {
            Delivery::Baseline | Delivery::ParticipantDestroy => true,
            Delivery::Script {
                last,
                close: false,
                resend: false,
            } if entity == self.owner => {
                !self.controller.data_in_flight() && last > self.controller.delivered()
            }
            Delivery::Script { close: false, .. } => true,
            Delivery::BotCreate { .. } => {
                self.bots.get(&entity) == Some(&BotLifetime::Live)
                    || (self.bots.get(&entity) == Some(&BotLifetime::RetiringEnemy)
                        && !self.created.contains(&entity))
            }
            Delivery::BotDestroy => matches!(
                self.bots.get(&entity),
                Some(BotLifetime::Retiring | BotLifetime::RetiringEnemy)
            ),
            Delivery::BotControllerCreate => self
                .bot_controllers
                .get(&entity)
                .is_some_and(|state| !state.created),
            Delivery::BotPossession { body } => {
                self.tracks_current_bot(body)
                    && self
                        .bot_controllers
                        .get(&entity)
                        .is_some_and(|state| state.possessed == Some(body))
            }
            Delivery::RemotePlayerCreate(id) => {
                matches!(
                    self.remote_players.get(&id.entity),
                    Some(&(current, BotLifetime::Live)) if current == id
                ) || (matches!(
                    self.remote_players.get(&id.entity),
                    Some(&(current, BotLifetime::RetiringEnemy)) if current == id
                ) && !self.created.contains(&id.entity))
            }
            Delivery::RemotePlayerDestroy(id) => matches!(
                self.remote_players.get(&id.entity),
                Some(&(current, BotLifetime::RetiringEnemy)) if current == id
            ),
            Delivery::SceneScript { last } => {
                (self.gate == Some(entity)
                    || self.bots.get(&entity) == Some(&BotLifetime::Live)
                    || self
                        .remote_players
                        .get(&entity)
                        .is_some_and(|&(_, life)| life == BotLifetime::Live))
                    && self.scene_views.get(&entity).is_some_and(|stream| {
                        stream.pending().any(|(pending, _, _)| pending == last)
                    })
            }
            Delivery::ObjectiveScript { last } => {
                self.objective_views.get(&entity).is_some_and(|view| {
                    view.stream.latest_data() == last && view.stream.delivered() < last
                })
            }
            Delivery::ObjectivePlacement { appearing } => {
                self.objective_placements.get(&entity) == Some(&appearing)
            }
            Delivery::Health { revision } => self.health_updates.get(&entity) == Some(&revision),
            Delivery::PlayerSeat { revision } => self
                .team_updates
                .get(&entity)
                .is_some_and(|&(pending, _)| pending == revision),
            Delivery::ObservedProjectile { id, step } => {
                self.observed_projectiles.current(id, step)
                    && (step != super::observed_projectiles::ProjectileDelivery::Launch
                        || self.tracks_current_bot(entity)
                        || self
                            .remote_players
                            .get(&entity)
                            .is_some_and(|&(_, life)| life == BotLifetime::Live)
                        || self
                            .body_script
                            .as_ref()
                            .is_some_and(|s| s.id.entity == entity && self.owner_body_ready(s.id)))
            }
            Delivery::Script {
                last, close: true, ..
            } => last == self.controller.latest_data(),
            Delivery::CreateBody(id) => {
                self.bodies.get(&id.entity) == Some(&(id.generation, Lifetime::Live))
            }
            Delivery::DestroyBody(id) => {
                self.bodies.get(&id.entity) == Some(&(id.generation, Lifetime::Retiring))
            }
            Delivery::Possession(id) => self.body.is_some_and(|body| body.id == id),
            Delivery::BodyScript { id, .. } => self
                .body_script
                .as_ref()
                .is_some_and(|script| script.id == id),
            Delivery::ProjectileConfirm { id, token } => self
                .body_script
                .as_ref()
                .filter(|script| script.id == id)
                .and_then(|script| script.projectiles.as_ref())
                .is_some_and(|projectiles| projectiles.confirmation_current(token, entity)),
            Delivery::ProjectileDestroy { id, token } => {
                self.projectile_destroys.get(&entity) == Some(&(id, token))
            }
            Delivery::BioticCreate => self
                .biotic_views
                .get(&entity)
                .is_some_and(|view| !view.retiring && !view.created),
            Delivery::BioticFrame { last } => self.biotic_views.get(&entity).is_some_and(|view| {
                !view.retiring
                    && view.stream.latest_data() == last
                    && view.stream.delivered() < last
            }),
            Delivery::BioticDestroy => self
                .biotic_views
                .get(&entity)
                .is_some_and(|view| view.retiring),
        }
    }

    fn current_owner_correction(&self) -> Option<PendingOwnerCorrection> {
        let pending = self.pending_owner_correction?;
        self.body
            .is_some_and(|body| body.id == pending.id)
            .then_some(pending)
    }

    pub(crate) fn acknowledge(&mut self, report: DeliveryReport<Arc<Record>>, now: Duration) {
        if self.started.is_none() {
            return;
        }
        if let Some((sequence, arrived)) = report.owner_correction {
            if let Some(pending) = self
                .current_owner_correction()
                .filter(|pending| pending.sequence == sequence)
            {
                self.pending_owner_correction = (!arrived).then_some(PendingOwnerCorrection {
                    retry: true,
                    ..pending
                });
            }
        }
        let mut retry = Vec::new();
        for packet in report.packets {
            for record in packet.records {
                if !self.current(&record) {
                    continue;
                }
                #[cfg(feature = "inspector")]
                if crate::owner_trace::playtest_enabled() {
                    self.trace_playtest_delivery(
                        &record,
                        packet.sequence,
                        if packet.arrived { "ack" } else { "lost" },
                    );
                }
                match (packet.arrived, record.delivery) {
                    (arrived, Delivery::SceneScript { last }) => {
                        if arrived {
                            self.retiring_nameplate_chunks
                                .remove(&(record.update.entity, last));
                        }
                        let stream = self
                            .scene_views
                            .get_mut(&record.update.entity)
                            .expect("current scene stream");
                        let delivery = if arrived {
                            stream.arrived(last)
                        } else {
                            stream.lost(last)
                        };
                        if delivery.advanced {
                            let _presented = self
                                .pending_respawn_presentation
                                .remove(&record.update.entity);
                            #[cfg(feature = "inspector")]
                            if _presented {
                                crate::owner_trace::playtest(format_args!(
                                    "bot-respawn event=presentation-ack owner={:08x} packet={} new={:08x}",
                                    self.owner, packet.sequence, record.update.entity
                                ));
                            }
                        }
                        if delivery.retry {
                            retry.push(record);
                        }
                    }
                    (arrived, Delivery::BodyScript { last, .. }) => {
                        let script = self.body_script.as_mut().expect("current body stream");
                        #[cfg(feature = "inspector")]
                        let prior_action = if arrived && crate::owner_trace::playtest_enabled() {
                            script
                                .stream
                                .base()
                                .map(|(_, snapshot)| snapshot.acknowledged_callout())
                        } else {
                            None
                        };
                        let delivery = if arrived {
                            script.stream.arrived(last)
                        } else {
                            script.stream.lost(last)
                        };
                        #[cfg(feature = "inspector")]
                        if let Some(arrived) = delivery
                            .owner_report
                            .filter(|_| crate::owner_trace::enabled())
                        {
                            crate::owner_trace::record(format_args!(
                                "owner-volley delivery body={:08x} chunk={last} arrived={arrived}",
                                script.id.entity
                            ));
                        }
                        #[cfg(feature = "inspector")]
                        if let Some((before, (_, snapshot))) =
                            prior_action.zip(script.stream.base())
                        {
                            let serial = snapshot.acknowledged_callout();
                            if serial > before {
                                crate::owner_trace::playtest(format_args!(
                                    "combat owner action acknowledged body={:08X} generation={} serial={serial} chunk={last}",
                                    script.id.entity, script.id.generation
                                ));
                            }
                        }
                        if let Some((projectiles, arrived)) =
                            script.projectiles.as_mut().zip(delivery.owner_report)
                        {
                            projectiles.owner_arrival(last, arrived);
                        }
                        if delivery.retry {
                            retry.push(record);
                        }
                    }
                    (false, Delivery::Script { resend: false, .. }) => {}
                    (false, _) => retry.push(record),
                    (true, Delivery::Baseline) => {
                        if matches!(
                            record.update.operation,
                            Some(Operation::Create(_) | Operation::Placeable(_))
                        ) {
                            self.created.insert(record.update.entity);
                        }
                    }
                    (true, Delivery::ParticipantDestroy) => {
                        self.created.remove(&record.update.entity);
                    }
                    (true, Delivery::BotCreate { .. }) => {
                        self.created.insert(record.update.entity);
                        if let Some(stream) = self.scene_views.get_mut(&record.update.entity) {
                            stream.create_arrived();
                        }
                    }
                    (true, Delivery::BotDestroy) => {
                        self.bots.remove(&record.update.entity);
                        self.created.remove(&record.update.entity);
                        self.scene_views.remove(&record.update.entity);
                        self.retiring_nameplate_chunks
                            .retain(|&(entity, _)| entity != record.update.entity);
                    }
                    (true, Delivery::BotControllerCreate) => {
                        self.created.insert(record.update.entity);
                        if let Some(state) = self.bot_controllers.get_mut(&record.update.entity) {
                            state.created = true;
                        }
                    }
                    (true, Delivery::BotPossession { body }) => {
                        if let Some(state) = self
                            .bot_controllers
                            .values_mut()
                            .find(|state| state.possessed == Some(body))
                        {
                            state.possessed_acked = Some(body);
                        }
                    }
                    (true, Delivery::RemotePlayerCreate(id)) => {
                        self.created.insert(id.entity);
                        if let Some(stream) = self.scene_views.get_mut(&id.entity) {
                            stream.create_arrived();
                        }
                    }
                    (true, Delivery::RemotePlayerDestroy(id)) => {
                        if self.remote_players.get(&id.entity)
                            == Some(&(id, BotLifetime::RetiringEnemy))
                        {
                            self.remote_players.remove(&id.entity);
                        }
                        self.created.remove(&id.entity);
                        self.scene_views.remove(&id.entity);
                    }
                    (true, Delivery::Health { .. }) => {
                        self.health_updates.remove(&record.update.entity);
                    }
                    (true, Delivery::PlayerSeat { .. }) => {
                        self.team_updates.remove(&record.update.entity);
                    }
                    (true, Delivery::ObservedProjectile { id, step }) => {
                        self.observed_projectiles.arrived(id, step);
                    }
                    (true, Delivery::CreateBody(id)) => {
                        self.created.insert(id.entity);
                        if let Some(script) =
                            self.body_script.as_mut().filter(|script| script.id == id)
                        {
                            script.stream.create_arrived();
                        }
                    }
                    (true, Delivery::Possession(id)) => self.bound = Some(id),
                    (true, Delivery::DestroyBody(id)) => {
                        self.bodies.insert(
                            id.entity,
                            (
                                id.generation,
                                Lifetime::ReusableAt(now.saturating_add(BODY_REUSE_DELAY)),
                            ),
                        );
                    }
                    (true, Delivery::ProjectileConfirm { token, .. }) => {
                        self.body_script
                            .as_mut()
                            .and_then(|script| script.projectiles.as_mut())
                            .expect("current projectile tracker")
                            .create_arrived(token, record.update.entity);
                    }
                    (true, Delivery::ProjectileDestroy { .. }) => {
                        self.projectile_destroys.remove(&record.update.entity);
                    }
                    (true, Delivery::BioticCreate) => {
                        self.biotic_views
                            .get_mut(&record.update.entity)
                            .expect("current field view")
                            .created = true;
                    }
                    (true, Delivery::BioticFrame { last }) => {
                        self.biotic_views
                            .get_mut(&record.update.entity)
                            .expect("current field view")
                            .stream
                            .arrived(last);
                    }
                    (true, Delivery::BioticDestroy) => {
                        self.biotic_views.remove(&record.update.entity);
                    }
                    (true, Delivery::ObjectiveScript { last }) => {
                        if let Some(view) = self.objective_views.get_mut(&record.update.entity) {
                            view.stream.arrived(last);
                        }
                    }
                    (true, Delivery::ObjectivePlacement { appearing }) => {
                        let entity = record.update.entity;
                        self.objective_placements.remove(&entity);
                        if appearing {
                            self.created.insert(entity);
                        } else {
                            self.created.remove(&entity);
                            self.objective_views.remove(&entity);
                        }
                    }
                    (true, Delivery::Script { last, .. }) if record.update.entity == self.owner => {
                        if self.controller.arrived(last) && self.controller_pending.is_some() {
                            self.presentation_due = true;
                        }
                    }
                    (true, Delivery::Script { last, .. }) => {
                        self.mode.arrived(last);
                    }
                }
            }
        }
        if !retry.is_empty() {
            retry.append(&mut self.queued);
            self.queued = retry;
            self.remove_stale();
        }
    }

    #[cfg(feature = "inspector")]
    fn trace_playtest_delivery(&self, record: &Record, sequence: u32, event: &str) {
        let entity = record.update.entity;
        match record.delivery {
            Delivery::BotCreate { after: Some(old) } => {
                crate::owner_trace::playtest(format_args!(
                    "bot-respawn event={event} owner={:08x} packet={sequence} record=create old={old:08x} new={entity:08x}",
                    self.owner
                ));
            }
            Delivery::BotDestroy => crate::owner_trace::playtest(format_args!(
                "bot-respawn event={event} owner={:08x} packet={sequence} record=destroy old={entity:08x}",
                self.owner
            )),
            Delivery::ProjectileConfirm { id, token }
            | Delivery::ProjectileDestroy { id, token } => {
                let operation = if matches!(record.delivery, Delivery::ProjectileConfirm { .. }) {
                    "confirm"
                } else {
                    "destroy"
                };
                crate::owner_trace::playtest(format_args!(
                    "projectile event={event} owner={:08x} packet={sequence} record={operation} token={token:?} entity={entity:08x}",
                    id.entity
                ));
            }
            Delivery::ObservedProjectile { id, step } => {
                crate::owner_trace::playtest(format_args!(
                    "npc-projectile event={event} owner={:08x} packet={sequence} batch={id} step={step:?} entity={entity:08x}",
                    self.owner
                ));
            }
            _ => {}
        }
    }

    pub(super) fn remove_stale(&mut self) {
        let mut queued = std::mem::take(&mut self.queued);
        queued.retain(|record| self.current(record));
        self.queued = queued;
    }

    pub(crate) fn ready(&self) -> Vec<Ready<'_>> {
        let body = self.body.map(|body| body.id);
        let mut created = body.is_none_or(|id| self.created.contains(&id.entity));
        let mut bound = body == self.bound;
        let mut destroyed = HashSet::new();
        self.queued
            .iter()
            .enumerate()
            .filter_map(|(index, record)| {
                if matches!(record.update.operation, Some(Operation::Destroy))
                    && self
                        .entity_notice_ordinals
                        .contains_key(&record.update.entity)
                {
                    return None;
                }
                match record.delivery {
                    Delivery::ParticipantDestroy
                        if !self.created.contains(&record.update.entity) =>
                    {
                        return None;
                    }
                    Delivery::SceneScript { last }
                        if !self
                            .scene_views
                            .get(&record.update.entity)
                            .is_some_and(|stream| stream.ready_to_send(last)) =>
                    {
                        return None;
                    }
                    Delivery::BotDestroy => {
                        if self.bots.get(&record.update.entity) == Some(&BotLifetime::RetiringEnemy)
                            && !self.identity_ready(record.update.entity)
                        {
                            return None;
                        }
                        destroyed.insert(record.update.entity);
                    }
                    Delivery::RemotePlayerDestroy(id) => {
                        if self.remote_players.get(&id.entity)
                            == Some(&(id, BotLifetime::RetiringEnemy))
                            && !self.identity_ready(id.entity)
                        {
                            return None;
                        }
                        destroyed.insert(id.entity);
                    }
                    Delivery::BotCreate { after: Some(old) }
                        if self.bots.contains_key(&old) && !destroyed.contains(&old) =>
                    {
                        return None;
                    }
                    Delivery::BotPossession { body }
                        if !self.identity_ready(record.update.entity)
                            || !self.identity_ready(body) =>
                    {
                        return None;
                    }
                    Delivery::Health { revision }
                        if record.update.entity != self.owner
                            && record.update.entity != GAME_MODE_ENTITY
                            && (!self.identity_ready(record.update.entity)
                                || self.body_script.as_ref().is_some_and(|script| {
                                    script.id.entity == record.update.entity
                                        && script.stream.awaiting_owner_startup()
                                        && script.projected_health_revision != Some(revision)
                                })) =>
                    {
                        return None;
                    }
                    Delivery::PlayerSeat { .. } if !self.identity_ready(record.update.entity) => {
                        return None;
                    }
                    Delivery::DestroyBody(_) if self.team_updates.contains_key(&self.owner) => {
                        return None;
                    }
                    Delivery::CreateBody(id) if Some(id) == body => created = true,
                    Delivery::Possession(_) if !created => return None,
                    Delivery::Possession(_) => bound = true,
                    Delivery::DestroyBody(_) | Delivery::Script { close: true, .. } if !bound => {
                        return None;
                    }
                    Delivery::BodyScript {
                        id, last, delta, ..
                    } if (delta && !bound)
                        || !self.body_script.as_ref().is_some_and(|script| {
                            script.id == id && script.stream.ready_to_send(last)
                        }) =>
                    {
                        return None;
                    }
                    Delivery::ProjectileDestroy { .. }
                        if self
                            .biotic_views
                            .values()
                            .any(|view| view.parent == record.update.entity) =>
                    {
                        return None;
                    }
                    Delivery::ProjectileConfirm { id, .. } if !self.owner_body_ready(id) => {
                        // Even a fallback confirmation needs an acknowledged owner lease.
                        return None;
                    }
                    _ => {}
                }
                Some(Ready { index, record })
            })
            .collect()
    }

    /// Retain blocked records and remove only the exact records selected by the
    /// successful send, even when eligibility skipped an earlier queue entry.
    pub(crate) fn sent(&mut self, indexes: &[usize]) {
        if indexes.is_empty() {
            return;
        }
        for &index in indexes {
            let record = &self.queued[index];
            if let Delivery::SceneScript { last } = record.delivery {
                if let Some(stream) = self.scene_views.get_mut(&record.update.entity) {
                    stream.sent(last);
                }
            }
        }
        if let Some(script) = &mut self.body_script {
            for &index in indexes {
                match self.queued[index].delivery {
                    Delivery::BodyScript { id, last, .. } if id == script.id => script.sent(last),
                    _ => {}
                }
            }
        }
        let mut selected = indexes.iter().copied().peekable();
        let mut index = 0;
        self.queued.retain(|_| {
            let keep = selected.next_if_eq(&index).is_none();
            index += 1;
            keep
        });
    }

    pub(crate) fn sent_indices(ready: &[Ready<'_>], sent: &[RecordSource]) -> Vec<usize> {
        sent.iter()
            .filter_map(|source| match *source {
                RecordSource::Queued(index) => Some(ready[index].index),
                RecordSource::Movement(_) => None,
            })
            .collect()
    }

    pub(crate) fn send_frame<E>(
        &mut self,
        tick: u32,
        mut movements: Vec<EntityUpdate>,
        reliable_only: bool,
        simulation: Option<&mut RoomSimulation>,
        egress: &mut GameEgress<Arc<Record>>,
        send: impl FnOnce(Vec<u8>) -> Result<u32, E>,
    ) -> Result<SentFrame, SendError<E>> {
        // Numbered notices can still be retransmitted after an entity starts
        // retiring. Keep its destroy behind the acknowledged reliable prefix.
        let pending = egress.oldest_pending_reliable();
        self.entity_notice_ordinals
            .retain(|_, ordinal| u64::from(*ordinal) >= pending);
        if !reliable_only {
            if let Some(simulation) = simulation {
                self.prepare_body_updates(simulation);
            }
        }
        let script = self.body_script.as_ref();
        let script_history = script.and_then(|script| {
            script
                .corrections
                .as_ref()
                .map(|history| (script.id, history))
        });
        let detached = script_history.is_none();
        let owner_history = script_history.or_else(|| {
            let (id, history) = self.detached_motion.as_ref()?;
            (self.body.is_some_and(|body| body.id == *id)).then_some((*id, history))
        });
        let history = owner_history.map(|(_, history)| history);
        let pending = self.current_owner_correction();
        let protect_correction = pending.is_some()
            || history.is_some_and(|history| history.impulse_correction().is_some());
        let correction = if reliable_only {
            None
        } else {
            owner_history
                .and_then(|(id, history)| {
                    history.candidate(tick, self.created.contains(&id.entity))
                })
                .or_else(|| {
                    pending
                        .filter(|pending| {
                            pending.retry
                                && pending.pose.frame() <= tick
                                && self.created.contains(&pending.id.entity)
                        })
                        .map(|pending| pending.pose)
                })
        };
        if let Some(last) = self.last_sent_remote_movement {
            movements.sort_unstable_by_key(|record| (record.entity <= last, record.entity));
        }
        let owner_index = correction.map(|correction| {
            let index = movements.len();
            movements.push(correction.update());
            index
        });
        let mut queued = self.ready();
        let is_bot_chunk = |item: &Ready<'_>| {
            let record = item.record;
            matches!(
                record.delivery,
                Delivery::SceneScript { last }
                    if record.update.chunk.is_some()
                        && self.bots.contains_key(&record.update.entity)
                        && !self
                            .retiring_nameplate_chunks
                            .contains(&(record.update.entity, last))
            )
        };
        // A mixed packet can be acknowledged even when the receiver drops
        // the NPC chunk. Give each class a send opportunity under sustained
        // traffic and let the ordinary stream handle acknowledgements.
        let contention =
            !reliable_only && egress.has_queued_reliable() && queued.iter().any(is_bot_chunk);
        let bot_turn = contention && self.bot_chunk_waited;
        if contention {
            queued.retain(|item| is_bot_chunk(item) == bot_turn);
        }
        let newest = self.command_ack(tick);
        let sent = egress.send_frame_with_messages(
            FrameRecords {
                tick,
                queued: &queued,
                movements: &movements,
                owner_correction: owner_index.is_some(),
                reliable_only,
            },
            !bot_turn,
            |records| {
                let selected = owner_index
                    .filter(|&index| {
                        records
                            .iter()
                            .any(|record| std::ptr::eq(*record, &movements[index]))
                    })
                    .and(correction.as_ref());
                let ack = history.map_or(newest, |history| {
                    history.command_ack(tick, Some(newest), selected)
                });
                pending.map_or(ack, |pending| {
                    selected.unwrap_or(&pending.pose).limit_replay_ack(ack)
                })
            },
            |source| match source {
                RecordSource::Queued(index) => queued[index].tracked(),
                RecordSource::Movement(_) => unreachable!("movement records are not retried"),
            },
            send,
        )?;
        let indexes = Self::sent_indices(&queued, &sent.records);
        if let Some(entity) = sent.records.iter().rev().find_map(|source| match *source {
            RecordSource::Movement(index) if Some(index) != owner_index => {
                Some(movements[index].entity)
            }
            _ => None,
        }) {
            self.last_sent_remote_movement = Some(entity);
        }
        for &index in &indexes {
            if let Delivery::ObservedProjectile {
                id,
                step: super::observed_projectiles::ProjectileDelivery::Launch,
            } = self.queued[index].delivery
            {
                self.observed_projectiles.launch_sent(id, tick);
            }
        }
        #[cfg(feature = "inspector")]
        if crate::owner_trace::playtest_enabled() {
            for &index in &indexes {
                self.trace_playtest_delivery(&self.queued[index], sent.sequence, "sent");
            }
        }
        self.sent(&indexes);
        if !reliable_only {
            self.bot_chunk_waited = contention && !bot_turn;
        }
        let selected = owner_index
            .zip(correction)
            .filter(|(index, _)| sent.records.contains(&RecordSource::Movement(*index)));
        if let Some((_, correction)) = selected {
            if let Some(body) = self.body.filter(|_| protect_correction) {
                egress.track_owner_correction(sent.sequence);
                self.pending_owner_correction = Some(PendingOwnerCorrection {
                    id: body.id,
                    sequence: sent.sequence,
                    pose: correction,
                    retry: false,
                });
            }
            if detached {
                if let Some((_, history)) = &mut self.detached_motion {
                    if history.sent(&correction) && history.impulse_correction().is_none() {
                        self.detached_motion = None;
                    }
                }
            } else if let Some(script) = &mut self.body_script {
                script.correction_sent(&correction);
            }
        }
        Ok(sent)
    }

    fn prepare_body_updates(&mut self, simulation: &mut RoomSimulation) {
        // A split input gap can update physics history immediately. The owner
        // VM and health publish only after the queued command completes.
        if matches!(
            self.input.work.front(),
            Some(InputWork::Command {
                pending: Some(_),
                ..
            })
        ) {
            return;
        }
        let Some(id) = self.body_script.as_ref().map(|script| script.id) else {
            return;
        };
        let created = self.created.contains(&id.entity);
        let (health, next) = {
            let Some(runtime) = simulation
                .player_body_mut(id)
                .and_then(|state| state.runtime.as_deref_mut())
            else {
                self.cancel_body_script(Some(simulation));
                return;
            };
            let health = runtime.take_health_update();
            let next = self
                .body_script
                .as_mut()
                .expect("checked body script")
                .next_record(runtime, created);
            (health, next)
        };
        self.publish_owner_health(simulation, id, health);
        let (record, replaced) = match next {
            Ok(Some(record)) => record,
            Ok(None) => return,
            Err(error) => {
                eprintln!("body {:08X} owner statescript stopped: {error}", id.entity);
                #[cfg(test)]
                {
                    self.owner_script_fault =
                        self.body_script.as_ref().map(|script| OwnerScriptFault {
                            id,
                            frame: script.frame,
                            step: None,
                            error,
                        });
                }
                self.cancel_body_script(Some(simulation));
                return;
            }
        };
        if let Some(replaced) = replaced {
            self.queued.retain(|queued| {
                !matches!(queued.delivery, Delivery::BodyScript { id: queued_id, last, .. }
                    if queued_id == id && last == replaced)
            });
        }
        // A late copy of an immutable startup record must not put the old
        // HUD on top of newer health pools for this body.
        if let Some(script) = self.body_script.as_mut() {
            script.projected_health_revision = self.health_updates.get(&id.entity).copied();
        }
        self.push(record);
    }

    fn publish_owner_health(
        &mut self,
        simulation: &mut RoomSimulation,
        id: BodyId,
        health: Option<WorldComponent>,
    ) {
        if let Some(health) = health {
            let update = EntityUpdate {
                entity: id.entity,
                operation: Some(Operation::Update(vec![health])),
                ..EntityUpdate::default()
            };
            simulation.record_observer_player_health(self.owner, update.clone());
            self.queue_health(update);
        }
    }

    pub(super) fn cancel_body_script(&mut self, simulation: Option<&mut RoomSimulation>) {
        if let Some(mut script) = self.body_script.take() {
            if self.body.is_some_and(|body| body.id == script.id) {
                self.detached_motion = script
                    .corrections
                    .take()
                    .filter(|history| history.impulse_correction().is_some())
                    .map(|history| (script.id, history));
            }
            self.retire_biotic_views(|_| false);
            script.stream.retire();
            if let Some(sim) = simulation {
                sim.clear_player_runtime(script.id);
                let health = sim
                    .player_body_mut(script.id)
                    .and_then(PlayerBodyState::take_health_update);
                self.publish_owner_health(sim, script.id, health);
            }
            if let Some(projectiles) = &mut script.projectiles {
                self.queue_projectiles(script.id, projectiles.retire())
                    .expect("projectile retirement has no release payload");
            }
            self.remove_stale();
        }
    }

    pub(crate) fn player_script_halted(
        &mut self,
        owner: u32,
        id: BodyId,
        health: Option<WorldComponent>,
    ) {
        if owner == self.owner {
            if self.active_body_id() != Some(id) {
                return;
            }
            if self
                .body_script
                .as_ref()
                .is_some_and(|script| script.id == id)
            {
                // The room already transferred the runtime and its health.
                self.cancel_body_script(None);
            }
        } else {
            if self.remote_players.get(&id.entity) != Some(&(id, BotLifetime::Live)) {
                return;
            }
            self.scene_views.remove(&id.entity);
            self.remove_stale();
        }
        if let Some(health) = health {
            self.queue_health(EntityUpdate {
                entity: id.entity,
                operation: Some(Operation::Update(vec![health])),
                ..EntityUpdate::default()
            });
        }
    }

    pub(crate) fn start(
        &mut self,
        scene: &PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
    ) -> io::Result<()> {
        let records = scene.initial_world(viewer)?;
        self.viewer_team = viewer.team;
        self.select_open = viewer.team != crate::room_primitives::SPECTATOR_TEAM;
        self.gate = scene
            .simulation()
            .junkenstein_gate()
            .map(|gate| gate.entity);
        let mut attached = Vec::new();
        for update in records {
            if scene.simulation().tracks_scene_entity(update.entity) {
                self.bots.insert(update.entity, BotLifetime::Live);
                let has_parent = matches!(
                    update.operation.as_ref(),
                    Some(Operation::Create(create)) if create.movement_parent.is_some()
                );
                let record = Record {
                    update,
                    delivery: Delivery::BotCreate { after: None },
                };
                if has_parent {
                    attached.push(record);
                } else {
                    self.push(record);
                }
            } else {
                self.push(Record::baseline(update));
            }
        }
        for (id, update) in scene.simulation().observer_player_creates(self.owner) {
            self.remote_players
                .insert(id.entity, (id, BotLifetime::Live));
            self.push(Record {
                update,
                delivery: Delivery::RemotePlayerCreate(id),
            });
        }
        for record in attached {
            self.push(record);
        }
        self.started = Some(tick);
        Ok(())
    }

    pub(crate) fn present(&mut self, scene: &PreparedScene, tick: u32) -> io::Result<()> {
        if self.controller.latest_data() == 0
            && self.created.contains(&self.owner)
            && !self.team_updates.contains_key(&self.owner)
            && (!scene.map().is_junkenstein() || self.mode_ready())
        {
            self.queue_controller(scene, tick, self.select_open, self.assembly_choice)?;
            self.refresh_selection = false;
        }
        if !self.controller.data_in_flight() && !self.team_updates.contains_key(&self.owner) {
            if let Some(frame) = self.controller_pending {
                let command_frame = self.command_ack(tick);
                if let Some(record) = Self::controller_chunk(
                    &mut self.controller,
                    scene,
                    command_frame,
                    frame,
                    self.controller_skins,
                )? {
                    self.controller_pending = None;
                    self.controller_skins = frame.skins;
                    self.push(record);
                }
            }
        }
        if self.mode.latest_data() == 0
            && self.created.contains(&GAME_MODE_ENTITY)
            && !scene.has_objective_presenter()
        {
            let chunk = self.mode.full(scene.mode_ui()).map_err(invalid)?;
            self.push(Record::script(chunk, false));
            self.custom_mode_view = scene.custom_mode_view();
        }
        self.presentation_due = self.controller.latest_data() == 0 || self.mode.latest_data() == 0;
        Ok(())
    }

    pub(crate) fn queue_custom_mode_presentation(
        &mut self,
        scene: &PreparedScene,
        tick: u32,
        team: u8,
        now: Duration,
    ) -> io::Result<()> {
        if self.team_updates.contains_key(&self.owner) {
            return Ok(());
        }
        if self.refresh_selection && self.created.contains(&self.owner) {
            self.queue_controller(scene, tick, self.select_open, None)?;
            self.refresh_selection = false;
        }
        if let Some((graphs, codecs)) = scene.payload_presentation() {
            return self.queue_payload_presentation(scene, tick, team, now, graphs, codecs);
        }
        if let Some((graphs, codecs)) = scene.objective_presentation() {
            if scene.control_round().is_some() {
                return self.queue_control_presentation(scene, tick, team, now, graphs, codecs);
            }
            self.queue_capture_presentation(scene, tick, team, now, graphs, codecs)?;
            return Ok(());
        }
        let (Some(previous), Some(current)) = (self.custom_mode_view, scene.custom_mode_view())
        else {
            return Ok(());
        };
        if previous == current || self.mode.data_in_flight() {
            return Ok(());
        }
        let phase_changed = previous.0 != current.0;
        let chunk = if phase_changed {
            self.mode.full(scene.mode_ui()).map_err(invalid)?
        } else {
            self.mode
                .reliable_delta(&custom_countdown_delta(current.1)?)
                .map_err(invalid)?
        };
        self.push(Record::script(chunk, false));
        self.custom_mode_view = Some(current);
        self.update_assembly_controller(scene, tick, team, Some(previous.0), current.0)
    }

    fn update_assembly_controller(
        &mut self,
        scene: &PreparedScene,
        tick: u32,
        team: u8,
        previous: Option<MatchPhase>,
        phase: MatchPhase,
    ) -> io::Result<()> {
        let phase_changed = previous != Some(phase);
        if phase_changed
            && team < 2
            && scene.has_objective_presenter()
            && self.controller.latest_data() != 0
        {
            let hero = self.assembly_choice.or(self.body.map(|body| body.hero));
            if phase == MatchPhase::Assemble {
                self.queue_controller(scene, tick, true, hero)?;
            } else if previous == Some(MatchPhase::Assemble) {
                self.assembly_confirmed = false;
                let awaiting_body = self.body.is_none_or(|body| hero != Some(body.hero));
                self.queue_controller(scene, tick, awaiting_body, hero)?;
            }
        }
        Ok(())
    }

    fn queue_objective(
        &mut self,
        encoder: &crate::script_snapshot_wire::SnapshotEncoder<'_>,
        snapshot: ProjectedSnapshot,
        initial_pose: Option<crate::world::Movement>,
    ) -> io::Result<bool> {
        let entity = snapshot.entity();
        if !self.identity_ready(entity) {
            return Ok(false);
        }
        let chunk = if let Some(view) = self.objective_views.get_mut(&entity) {
            if view.stream.data_in_flight() {
                return Ok(false);
            }
            let Some(frame) = encoder
                .mounted_delta(Some(&view.snapshot), &snapshot)
                .map_err(invalid)?
            else {
                return Ok(false);
            };
            let chunk = view.stream.reliable_delta(&frame).map_err(invalid)?;
            view.snapshot = snapshot;
            chunk
        } else {
            let mut stream = Stream::new(entity);
            let mut chunk = stream
                .full(&encoder.full(&snapshot).map_err(invalid)?)
                .map_err(invalid)?;
            chunk.update.movement = initial_pose;
            self.objective_views.insert(
                entity,
                ObjectiveView {
                    stream,
                    snapshot,
                    door_state: None,
                    capture_state: None,
                },
            );
            chunk
        };
        self.push(Record {
            update: chunk.update,
            delivery: Delivery::ObjectiveScript { last: chunk.last },
        });
        Ok(true)
    }

    fn queue_objective_rooms(
        &mut self,
        encoder: &crate::script_snapshot_wire::SnapshotEncoder<'_>,
        entries: &[crate::room_primitives::BarrierEntry],
        now: f64,
        team: u8,
        door_state: impl Fn(
            &crate::room_primitives::BarrierEntry,
            Option<&crate::room_primitives::DoorState>,
        ) -> crate::room_primitives::DoorState,
        room_visual: impl Fn(&crate::room_primitives::BarrierEntry) -> bool,
    ) -> io::Result<()> {
        use crate::{pvp_mode::presentation, room_primitives::BarrierKind};
        let graphs = encoder.graphs;
        for entry in entries {
            if matches!(
                entry.kind,
                BarrierKind::Door | BarrierKind::CheckpointGate | BarrierKind::GateDebris
            ) {
                let previous = self
                    .objective_views
                    .get(&entry.entity)
                    .and_then(|view| view.door_state.as_ref());
                let state = door_state(entry, previous);
                if previous == Some(&state) {
                    continue;
                }
                let seen = previous.is_some();
                let snapshot = presentation::door(graphs, entry, &state, now)?;
                let pose = entry.rotation.filter(|_| !seen).map(|rotation| {
                    let [x, y, z] = crate::room_primitives::rotate_vec(rotation, [0.0, 0.0, 1.0]);
                    let units = 65536.0 / std::f64::consts::TAU;
                    crate::world::Movement {
                        position: entry.position,
                        yaw: i32::from((x.atan2(z) * units).round_ties_even() as i32 as i16),
                        body_pitch: ((-y).atan2(x.hypot(z)) * units).round_ties_even() as i32,
                        ..Default::default()
                    }
                });
                if self.queue_objective(encoder, snapshot, pose)? {
                    self.objective_views
                        .get_mut(&entry.entity)
                        .expect("queued objective view")
                        .door_state = Some(state);
                }
            } else if entry.kind.is_room_visual() {
                let visible = room_visual(entry);
                if self.queue_objective_placement(entry.placeable, visible) && visible {
                    self.queue_objective(
                        encoder,
                        presentation::room_visual(graphs, entry, team)?,
                        None,
                    )?;
                }
            }
        }
        Ok(())
    }

    // True once the viewer has confirmed the desired placement lifetime.
    fn queue_objective_placement(&mut self, placeable: u32, visible: bool) -> bool {
        let entity = crate::room_primitives::MAP_ENTITY_BIT | placeable;
        if self.objective_placements.contains_key(&entity) {
            return false;
        }
        if visible == self.identity_ready(entity) {
            return true;
        }
        if self
            .objective_views
            .get(&entity)
            .is_some_and(|view| view.stream.data_in_flight())
        {
            return false;
        }
        let operation = if visible {
            Operation::Placeable(placeable)
        } else {
            Operation::Destroy
        };
        self.objective_placements.insert(entity, visible);
        self.push(Record {
            update: EntityUpdate {
                entity,
                operation: Some(operation),
                ..Default::default()
            },
            delivery: Delivery::ObjectivePlacement { appearing: visible },
        });
        false
    }

    fn queue_payload_presentation(
        &mut self,
        scene: &PreparedScene,
        tick: u32,
        team: u8,
        now: Duration,
        graphs: &crate::script_graph::GraphStore,
        codecs: &crate::script_codecs::CodecTable,
    ) -> io::Result<()> {
        use crate::{
            pvp_mode::{
                AudioTimeline, ModeActionSnapshot, hud_clock_seconds, match_speech, mode_action,
                presentation,
            },
            room_primitives::MAP_ENTITY_BIT,
        };

        let Some(round) = scene.payload_round() else {
            return Ok(());
        };
        let Some((phase, seconds)) = scene.custom_mode_view() else {
            return Ok(());
        };
        let Some(cart) = round
            .payload_placeable
            .map(|placeable| MAP_ENTITY_BIT | placeable)
        else {
            return Ok(());
        };
        if self.identity_ready(cart) {
            if let Some((_, update)) = scene
                .simulation()
                .pvp_objective()
                .and_then(|pvp| pvp.payload_entity_update())
            {
                if let Some(pose) = update.movement {
                    let changed = self.payload_pose.is_none_or(|previous| {
                        let [x, y, z] = std::array::from_fn(|axis| {
                            pose.position[axis] - previous.position[axis]
                        });
                        x.hypot(y).hypot(z) >= 0.01
                            || pose.yaw != previous.yaw
                            || pose.body_pitch != previous.body_pitch
                            || (pose.velocity == [0.0; 3]) != (previous.velocity == [0.0; 3])
                    });
                    if changed {
                        self.queue_health(update);
                        self.payload_pose = Some(pose);
                    }
                }
            }
        }
        let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(graphs, codecs);
        let mut checkpoints_ready = true;
        for checkpoint in presentation::checkpoints(round) {
            let entity = checkpoint.entity();
            if let Some(view) = self.objective_views.get(&entity) {
                checkpoints_ready &= view.stream.delivered() != 0;
            } else {
                self.queue_objective(&encoder, checkpoint, None)?;
                checkpoints_ready = false;
            }
        }
        if scene.capture_round().is_some() {
            if !checkpoints_ready || !self.identity_ready(cart) {
                return Ok(());
            }
            if !self.queue_capture_presentation(scene, tick, team, now, graphs, codecs)? {
                return Ok(());
            }
        }
        let now = now.as_secs_f64();
        let stage = round.room_stage();
        let handoff = round.stage_offset != 0
            && self
                .payload_audio
                .is_some_and(|old| old.stage < round.stage_offset);
        let team_changed = self
            .payload_viewer_team
            .replace(team)
            .is_some_and(|old| old != team);
        if team_changed && team < 2 && phase == MatchPhase::Active && round.stage > 0 {
            self.payload_refresh_rows = true;
        }
        let cart_confirmed = self
            .objective_views
            .get(&cart)
            .is_some_and(|view| !view.stream.data_in_flight());
        let cart_phase_confirmed = cart_confirmed && self.payload_cart_phase == Some(round.phase);
        let finish_music_confirmed = cart_confirmed && self.payload_cart_finish_music;
        if self.mode_ready() {
            self.queue_objective_rooms(
                &encoder,
                &round.barriers.entries,
                now,
                team,
                |entry, previous| round.door_state(entry.entity, now, previous),
                |entry| round.room_visual_visible(entry, team),
            )?;
            let refresh =
                self.payload_refresh_rows && cart_confirmed && phase == MatchPhase::Active;
            let previous = self.objective_views.get(&cart).map(|view| &view.snapshot);
            if self.queue_objective(
                &encoder,
                presentation::cart(graphs, round, refresh, now, previous)?,
                None,
            )? {
                self.payload_cart_phase = Some(round.phase);
                self.payload_cart_finish_music = round.near_finish();
                self.payload_refresh_rows &= !refresh;
            }
        }
        if !checkpoints_ready
            || !self.identity_ready(cart)
            || !self.identity_ready(GAME_MODE_ENTITY)
            || self.mode.data_in_flight()
        {
            return Ok(());
        }
        let previous_phase = self.custom_mode_view.map(|view| view.0);
        if previous_phase.is_some()
            && (previous_phase != Some(phase) || handoff)
            && matches!(phase, MatchPhase::Active | MatchPhase::Complete)
            && !cart_phase_confirmed
        {
            return Ok(());
        }
        if phase == MatchPhase::Active
            && previous_phase == Some(phase)
            && !handoff
            && cart_phase_confirmed
            && !self.payload_progress_bound
        {
            let frame = presentation::progress_binding(
                graphs,
                scene.map().mode_guid,
                false,
                tick,
                self.mode.latest_data() + 1,
            )?;
            let chunk = self.mode.reliable_delta(&frame).map_err(invalid)?;
            self.push(Record::script(chunk, false));
            self.payload_progress_bound = true;
            return Ok(());
        }
        let remaining = if phase == MatchPhase::Active || phase == MatchPhase::Setup {
            hud_clock_seconds(round.deadline - now)
        } else {
            f64::from(seconds)
        };
        let audio = AudioTimeline {
            phase,
            seconds: remaining.ceil() as i64,
            stage,
            overtime: round.overtime,
            winner: round.winner,
            near_finish: round.near_finish()
                && (finish_music_confirmed
                    || self.payload_audio.is_some_and(|old| old.near_finish)),
            control_wins: None,
            round_winner: None,
        };
        let mut snapshot =
            presentation::mode(graphs, scene.map(), round, phase, seconds, now, team)?;
        let mut actions = Vec::new();
        if let Some(old) = self.payload_audio {
            let before = ModeActionSnapshot {
                phase: old.phase,
                seconds: old.seconds as f64,
                stage: old.stage,
                overtime: old.overtime,
            };
            let current = ModeActionSnapshot {
                phase,
                seconds: remaining,
                stage,
                overtime: round.overtime,
            };
            if !handoff && phase == MatchPhase::Active && old.phase == phase && stage > old.stage {
                actions.push(presentation::checkpoint_sound(graphs, &mut snapshot, team)?);
            }
            if let Some(position) = mode_action(before, current) {
                actions.push((1, position));
            }
        }
        let chunk = if let Some(previous) = &self.payload_mode_view {
            let Some(frame) = encoder
                .mounted_delta_with_actions(
                    Some(previous),
                    &snapshot,
                    self.mode.latest_data() + 1,
                    &actions,
                )
                .map_err(invalid)?
            else {
                self.payload_cues.extend(match_speech(
                    self.payload_audio.as_ref(),
                    &audio,
                    scene.map().mode_guid,
                    team,
                    false,
                ));
                self.payload_audio = Some(audio);
                return Ok(());
            };
            self.mode.reliable_delta(&frame).map_err(invalid)?
        } else {
            self.mode
                .full(&encoder.full(&snapshot).map_err(invalid)?)
                .map_err(invalid)?
        };
        self.push(Record::script(chunk, false));
        self.payload_cues.extend(match_speech(
            self.payload_audio.as_ref(),
            &audio,
            scene.map().mode_guid,
            team,
            false,
        ));
        self.payload_audio = Some(audio);
        self.payload_mode_view = Some(snapshot);
        if handoff {
            self.payload_progress_bound = false;
        }
        self.custom_mode_view = Some((phase, seconds));
        self.update_assembly_controller(scene, tick, team, previous_phase, phase)
    }

    fn queue_capture_presentation(
        &mut self,
        scene: &PreparedScene,
        tick: u32,
        team: u8,
        now: Duration,
        graphs: &crate::script_graph::GraphStore,
        codecs: &crate::script_codecs::CodecTable,
    ) -> io::Result<bool> {
        use crate::{
            pvp_mode::{
                AudioTimeline, FIRST_ATTACKING_TEAM, ModeActionSnapshot, hud_clock_seconds,
                match_speech, mode_action,
                presentation::{self, CapturePresentation},
                spawn_door_open, stage_room_visual,
            },
            room_primitives::MAP_ENTITY_BIT,
        };

        let Some(round) = scene.capture_round() else {
            return Ok(false);
        };
        let Some((phase, seconds)) = scene.custom_mode_view() else {
            return Ok(false);
        };
        let payload = scene.payload_round();
        let handoff = payload.is_some_and(|route| route.unlocked_objective());
        if let Some(route) = payload.filter(|route| route.unlocked_objective()) {
            if self
                .payload_audio
                .is_some_and(|old| old.stage >= route.stage_offset)
            {
                return Ok(true);
            }
        }
        let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(graphs, codecs);
        let now = now.as_secs_f64();
        let stage = round.stage;
        let mode_id = scene.map().mode_guid;
        let mut descriptors_ready = true;
        for index in 0..round.points.len() {
            let snapshot = presentation::capture_descriptor(round, index);
            let entity = snapshot.entity();
            if let Some(view) = self.objective_views.get(&entity) {
                descriptors_ready &= view.stream.delivered() != 0;
            } else {
                self.queue_objective(&encoder, snapshot, None)?;
                descriptors_ready = false;
            }
        }
        if !descriptors_ready || !self.identity_ready(GAME_MODE_ENTITY) {
            return Ok(false);
        }

        // The mode creates its HUD containers locally. Install point presenters after that frame.
        let previous_phase = self.custom_mode_view.map(|view| view.0);
        if self.mode_ready() {
            if let Some(route) = payload {
                self.queue_objective_rooms(
                    &encoder,
                    &route.barriers.entries,
                    now,
                    team,
                    |entry, previous| route.door_state(entry.entity, now, previous),
                    |entry| route.room_visual_visible(entry, team),
                )?;
                if !handoff {
                    self.queue_objective(
                        &encoder,
                        presentation::cart(graphs, route, false, now, None)?,
                        None,
                    )?;
                }
            } else {
                self.queue_objective_rooms(
                    &encoder,
                    &round.barriers.entries,
                    now,
                    team,
                    |entry, previous| {
                        let open = spawn_door_open(entry, phase, stage);
                        round
                            .barriers
                            .door_state(entry.entity, now, stage, open, previous)
                    },
                    |entry| stage_room_visual(phase, stage, entry, team),
                )?;
            }
            let mut old_waypoint_retired = true;
            for (index, point) in round
                .points
                .iter()
                .enumerate()
                .filter(|(index, _)| *index != stage)
            {
                let view = &self.objective_views[&(MAP_ENTITY_BIT | point.spec.placeable)];
                old_waypoint_retired &= !view.stream.data_in_flight()
                    && view.capture_state.is_none_or(|state| !state.waypoint);
                if let Some(outline) = round.outlines[index] {
                    old_waypoint_retired &= self.queue_objective_placement(outline, false);
                }
            }
            let mut points_ready = true;
            for (index, point) in round.points.iter().enumerate() {
                let entity = MAP_ENTITY_BIT | point.spec.placeable;
                let desired = CapturePresentation::of(round, index, phase);
                let view = &self.objective_views[&entity];
                let acknowledged =
                    view.capture_state == Some(desired) && !view.stream.data_in_flight();
                let newly_visible =
                    desired.waypoint && view.capture_state.is_none_or(|state| !state.waypoint);
                points_ready &= acknowledged;
                if let Some(outline) = round.outlines[index] {
                    let visible = desired.waypoint && acknowledged && old_waypoint_retired;
                    let keep = desired.waypoint && self.identity_ready(MAP_ENTITY_BIT | outline);
                    let settled = self.queue_objective_placement(outline, visible || keep);
                    if handoff {
                        points_ready &= settled;
                    }
                    if settled && visible {
                        self.queue_objective(
                            &encoder,
                            presentation::capture_outline(
                                graphs,
                                MAP_ENTITY_BIT | outline,
                                !desired.active,
                            )?,
                            None,
                        )?;
                    }
                }
                if (!newly_visible || old_waypoint_retired)
                    && self.queue_objective(
                        &encoder,
                        presentation::capture_point(graphs, round, index, phase)?,
                        None,
                    )?
                {
                    self.objective_views
                        .get_mut(&entity)
                        .expect("queued capture presenter")
                        .capture_state = Some(desired);
                }
            }
            let transition = self
                .payload_audio
                .is_some_and(|old| old.phase != phase || old.stage != stage);
            if (transition || handoff) && !points_ready {
                return Ok(false);
            }
            if !handoff
                && points_ready
                && matches!(phase, MatchPhase::Active | MatchPhase::Complete)
                && previous_phase == Some(phase)
                && !self.mode.data_in_flight()
                && !self.payload_progress_bound
            {
                let frame = presentation::progress_binding(
                    graphs,
                    mode_id,
                    true,
                    tick,
                    self.mode.latest_data() + 1,
                )?;
                let chunk = self.mode.reliable_delta(&frame).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.payload_progress_bound = true;
                return Ok(false);
            }
        }
        if self.mode.data_in_flight() {
            return Ok(false);
        }
        if handoff && self.payload_mode_view.is_none() {
            return Ok(true);
        }

        let remaining = match phase {
            MatchPhase::Waiting | MatchPhase::Complete => 0.0,
            MatchPhase::Assemble => f64::from(seconds),
            _ => hud_clock_seconds(round.deadline - now),
        };
        let audio = AudioTimeline {
            phase,
            stage,
            seconds: remaining.ceil() as i64,
            overtime: round.overtime,
            winner: round.winner,
            near_finish: false,
            control_wins: None,
            round_winner: None,
        };
        let snapshot = presentation::capture_mode(
            graphs,
            scene.map(),
            round,
            phase,
            presentation::ObjectiveClock { now, remaining },
            team,
            payload,
        )?;
        let chunk = if let Some(previous) = &self.payload_mode_view {
            let stamp = self.mode.latest_data() + 1;
            let old = self
                .payload_audio
                .expect("capture view has an audio timeline");
            let captured = old.phase == MatchPhase::Active
                && (handoff
                    || stage > old.stage
                    || phase == MatchPhase::Complete && round.winner == Some(FIRST_ATTACKING_TEAM));
            // Keep the checkpoint effect and an overtime exit action in separate reliable frames.
            if captured && !previous.has_instance(presentation::CHECKPOINT_SOUND_INSTANCE) {
                let mut sound = previous.clone();
                let (instance, action) = presentation::checkpoint_sound(graphs, &mut sound, team)?;
                let frame = encoder
                    .mounted_delta_with_action(
                        Some(previous),
                        &sound,
                        Some((stamp, instance, action)),
                    )
                    .map_err(invalid)?
                    .expect("checkpoint action");
                let chunk = self.mode.reliable_delta(&frame).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.payload_mode_view = Some(sound);
                return Ok(false);
            }
            if handoff {
                return Ok(true);
            }
            let action = mode_action(
                ModeActionSnapshot {
                    phase: old.phase,
                    stage: old.stage,
                    seconds: old.seconds as f64,
                    overtime: old.overtime,
                },
                ModeActionSnapshot {
                    phase,
                    stage,
                    seconds: remaining,
                    overtime: round.overtime,
                },
            )
            .map(|action| (stamp, 1, action));
            let Some(frame) = encoder
                .mounted_delta_with_action(Some(previous), &snapshot, action)
                .map_err(invalid)?
            else {
                return Ok(false);
            };
            self.mode.reliable_delta(&frame).map_err(invalid)?
        } else {
            self.mode
                .full(&encoder.full(&snapshot).map_err(invalid)?)
                .map_err(invalid)?
        };
        self.push(Record::script(chunk, false));
        self.payload_cues.extend(match_speech(
            self.payload_audio.as_ref(),
            &audio,
            mode_id,
            team,
            false,
        ));
        self.payload_audio = Some(audio);
        self.payload_mode_view = Some(snapshot);
        self.custom_mode_view = Some((phase, seconds));
        self.update_assembly_controller(scene, tick, team, previous_phase, phase)?;
        Ok(false)
    }

    pub(crate) fn advance_controller_ack(&mut self, now: Duration) -> io::Result<()> {
        let newest = self
            .input
            .processed
            .filter(|&frame| frame > self.controller.command_frame());
        let Some(chunk) = self.controller.owner_ack(now, newest).map_err(invalid)? else {
            return Ok(());
        };
        self.queued.retain(|record| {
            !(record.update.entity == self.owner
                && matches!(
                    record.delivery,
                    Delivery::Script {
                        close: false,
                        resend: false,
                        ..
                    }
                ))
        });
        self.push(Record::script(chunk, false));
        Ok(())
    }

    pub(crate) fn present_scene_scripts(
        &mut self,
        scene: &PreparedScene,
        tick: u32,
    ) -> io::Result<()> {
        if let Some(projection) = scene.simulation().gate_projection() {
            self.present_scene_script(projection)?;
        }
        for projection in scene.enemy_projections().map_err(io::Error::other)? {
            let dead = scene.simulation().is_bot_dead(projection.snapshot.entity);
            self.present_scene_script_with_dead(projection, dead)?;
        }
        for projection in scene.simulation().player_projections(self.owner) {
            self.present_scene_script(projection)?;
        }
        for baseline in scene.simulation().deployable_baselines() {
            self.present_static_scene_baseline(baseline)?;
        }
        self.advance_observed_projectiles(tick)
    }

    pub(super) fn present_static_scene_baseline(
        &mut self,
        baseline: &crate::script_snapshot_wire::StaticSceneBaseline,
    ) -> io::Result<()> {
        let entity = baseline.snapshot.entity();
        if !self.tracks_current_bot(entity) || !self.identity_ready(entity) {
            return Ok(());
        }
        let stream = self
            .scene_views
            .entry(entity)
            .or_insert_with(|| BodyStream::new(entity));
        if stream.base().is_some() || stream.pending().next().is_some() {
            return Ok(());
        }
        stream.create_arrived();
        let chunk = stream
            .full(&baseline.payload, baseline.snapshot.clone())
            .map_err(invalid)?;
        ensure_script_chunk_fits(
            &chunk.update,
            "static scene frame exceeds the datagram budget",
        )?;
        self.push(Record {
            update: chunk.update,
            delivery: Delivery::SceneScript { last: chunk.last },
        });
        Ok(())
    }

    pub(crate) fn present_bot_controllers(&mut self, scene: &PreparedScene) -> io::Result<()> {
        if !self.world_started() {
            return Ok(());
        }
        for bot in scene.simulation().seated_bots() {
            let Some(&state) = self.bot_controllers.get(&bot.controller) else {
                self.bot_controllers
                    .insert(bot.controller, BotControllerState::default());
                self.push(Record {
                    update: bot_controller_create(bot.controller, bot.slot, bot.team)?,
                    delivery: Delivery::BotControllerCreate,
                });
                continue;
            };
            if state.created
                && self.created.contains(&bot.body)
                && self.tracks_current_bot(bot.body)
                && state.possessed != Some(bot.body)
            {
                if let Some(entry) = self.bot_controllers.get_mut(&bot.controller) {
                    entry.possessed = Some(bot.body);
                }
                self.push(Record {
                    update: bot_controller_possession(bot.controller, bot.body, bot.hero),
                    delivery: Delivery::BotPossession { body: bot.body },
                });
            }
        }
        Ok(())
    }

    pub(super) fn present_scene_script(
        &mut self,
        projection: crate::script_snapshot_wire::SceneProjection<'_>,
    ) -> io::Result<()> {
        self.present_scene_script_with_dead(projection, false)
    }

    pub(super) fn present_scene_script_with_dead(
        &mut self,
        projection: crate::script_snapshot_wire::SceneProjection<'_>,
        bot_dead: bool,
    ) -> io::Result<()> {
        let entity = projection.snapshot.entity;
        if self.gate != Some(entity)
            && self.bots.get(&entity) != Some(&BotLifetime::Live)
            && !self
                .remote_players
                .get(&entity)
                .is_some_and(|&(_, life)| life == BotLifetime::Live)
        {
            return Ok(());
        }
        let bot = self.bots.contains_key(&entity);
        let controller_ready = !self
            .bot_controllers
            .values()
            .any(|state| state.possessed == Some(entity))
            || self
                .bot_controllers
                .values()
                .any(|state| state.possessed_acked == Some(entity));
        let can_introduce = self.mode_ready() && !bot_dead && controller_ready;
        let stream = self
            .scene_views
            .entry(entity)
            .or_insert_with(|| BodyStream::new(entity));
        if self.created.contains(&entity) {
            stream.create_arrived();
        }
        let pending: Vec<_> = stream
            .pending()
            .map(|(last, snapshot, _)| (last, snapshot.has_practice_nameplate()))
            .collect();
        if !pending.is_empty() {
            let allow_pipeline =
                bot_dead && stream.base().is_some() && pending.iter().all(|&(_, has_np)| has_np);
            if !allow_pipeline {
                return Ok(());
            }
            for (last, _) in pending {
                if stream.discard_unsent(last) {
                    self.queued.retain(|record| {
                        record.update.entity != entity
                            || !matches!(record.delivery, Delivery::SceneScript { last: queued } if queued == last)
                    });
                }
            }
        }
        let encoder = projection.encoder;
        let revision = projection.snapshot.revision;
        let can_cache_clean = !projection.snapshot.has_time_sensitive_projection()
            && (!bot || !self.observed_projectiles.has_active_volleys_for(entity));
        if can_cache_clean && stream.observer_matches_clean(revision, bot_dead, can_introduce) {
            return Ok(());
        }
        let chunk = if let Some((_, base)) = stream.base() {
            let mut snapshot = encoder
                .project_next(projection.snapshot, base)
                .map_err(invalid)?;
            if bot {
                snapshot.stage_practice_nameplates((!bot_dead).then_some(base), can_introduce);
            }
            self.observed_projectiles.retain_volleys(&mut snapshot);
            let delta = encoder
                .queue_delta(stream, &snapshot, DeltaKind::Observer { resend: true }, 16)
                .map_err(invalid)?;
            if delta.is_none() && can_cache_clean {
                stream.mark_observer_clean(revision, bot_dead, can_introduce);
            }
            delta
        } else {
            let mut snapshot = encoder
                .project(projection.snapshot, View::Observer)
                .map_err(invalid)?;
            if bot {
                snapshot.stage_practice_nameplates(None, false);
            }
            self.observed_projectiles.retain_volleys(&mut snapshot);
            Some(encoder.queue_full(stream, &snapshot).map_err(invalid)?)
        };
        if let Some(chunk) = chunk {
            ensure_script_chunk_fits(
                &chunk.update,
                "scene script chunk exceeds the datagram budget",
            )?;
            if bot && bot_dead {
                self.retiring_nameplate_chunks.insert((entity, chunk.last));
            }
            self.push(Record {
                update: chunk.update,
                delivery: Delivery::SceneScript { last: chunk.last },
            });
        }
        Ok(())
    }

    pub(crate) fn queue_encounter_presentation(&mut self, scene: &PreparedScene) -> io::Result<()> {
        if !self.mode_ready() || self.mode.data_in_flight() {
            return Ok(());
        }
        let sim = scene.simulation();
        if !self.encounter_post_match_queued {
            if let Some(payload) = sim.encounter_post_match_wire()? {
                let chunk = self.mode.reliable_delta(&payload).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.encounter_post_match_queued = true;
                return Ok(());
            }
        }
        if !self.encounter_result_queued {
            if let Some(payload) = sim.encounter_result_wire()? {
                let chunk = self.mode.reliable_delta(&payload).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.encounter_result_queued = true;
                return Ok(());
            }
        }
        if !self.encounter_outcome_queued {
            if let Some(stamp) = self.mode.latest_data().checked_add(1) {
                if let Some(payload) = sim.encounter_outcome_wire(stamp)? {
                    let mut chunk = self.mode.reliable_delta(&payload).map_err(invalid)?;
                    if let Some(result) = sim.encounter_match_result() {
                        chunk.update.operation = Some(Operation::Update(vec![result]));
                    }
                    self.push(Record::script(chunk, false));
                    self.encounter_outcome_queued = true;
                    return Ok(());
                }
            }
        }
        if self.encounter_post_match_queued {
            if let Some((snapshot, payload)) =
                sim.encounter_screen_delta(self.encounter_screens.as_ref())?
            {
                let chunk = self.mode.reliable_delta(&payload).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.encounter_screens = Some(snapshot);
                return Ok(());
            }
        }
        if self.encounter_result_queued || self.encounter_outcome_queued {
            return Ok(());
        }
        if self.encounter_notifications.is_some() {
            if let Some((raw, activation, payload)) =
                sim.encounter_phase_wire(&self.encounter_phases)?
            {
                let chunk = self.mode.reliable_delta(&payload).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.encounter_phases.insert(raw, activation);
                return Ok(());
            }
        }
        let countdown = sim
            .encounter_countdown()
            .filter(|value| self.countdown.as_ref() != Some(value));
        // Both projections can change every tick because they read the clock.
        // Alternate when both are pending so either leaves room for the other.
        if !self.encounter_post_match_queued
            && (self.encounter_notification_turn || countdown.is_none())
        {
            if let Some((snapshot, payload)) =
                sim.encounter_notification_delta(self.encounter_notifications.as_ref())?
            {
                let chunk = self.mode.reliable_delta(&payload).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.encounter_notifications = Some(snapshot);
                self.encounter_notification_turn = false;
                return Ok(());
            }
        }
        if let Some(countdown) = countdown {
            if let Some(payload) = sim.encounter_countdown_wire(&countdown)? {
                let chunk = self.mode.reliable_delta(&payload).map_err(invalid)?;
                self.push(Record::script(chunk, false));
                self.countdown = Some(countdown);
                self.encounter_notification_turn = true;
            }
        }
        Ok(())
    }

    pub(crate) fn encounter_screens_delivered(&self) -> bool {
        // After cleanup the manager is the only instance in this projection.
        self.encounter_screens
            .as_ref()
            .is_some_and(|screens| screens.instance_count() == 1)
            && !self.mode.data_in_flight()
    }

    fn can_handle_game_message(&self) -> bool {
        self.started.is_some()
            && self.controller.latest_data() != 0
            && self.created.contains(&self.owner)
            && !self.team_updates.contains_key(&self.owner)
    }

    fn game_message(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        now: Duration,
        quanta: u32,
        message: &Message,
    ) -> io::Result<()> {
        if !self.can_handle_game_message() || viewer.team == crate::room_primitives::SPECTATOR_TEAM
        {
            return Ok(());
        }
        let Some(message) = ClientGameMessage::read(message) else {
            return Ok(());
        };
        if message.entity != self.owner {
            return Ok(());
        }
        enum HeroSelectAction {
            Pick(Hero),
            ReopenAssembly,
            OpenOrClose(Option<bool>),
        }
        let action = match message.hero_select(scene.heroes()) {
            Some(HeroSelectRequest::Pick(Some(hero))) => Some(HeroSelectAction::Pick(hero.clone())),
            Some(HeroSelectRequest::ReopenAssembly) => Some(HeroSelectAction::ReopenAssembly),
            Some(HeroSelectRequest::OpenOrClose(wanted)) => {
                Some(HeroSelectAction::OpenOrClose(wanted))
            }
            _ => None,
        };
        match action {
            Some(HeroSelectAction::Pick(hero)) => {
                if viewer
                    .card
                    .role
                    .is_some_and(|role| role != 0 && hero.role_number() != u8::try_from(role).ok())
                {
                    return Ok(());
                }
                let assembling = scene.selected_phase() == Some(MatchPhase::Assemble);
                if scene.holds_assembly_spawn(viewer.team) {
                    self.assembly_choice = Some(hero.guid);
                    self.assembly_confirmed = assembling;
                    return self.queue_controller(scene, tick, true, Some(hero.guid));
                }
                if assembling && scene.has_objective_presenter() {
                    self.assembly_choice = Some(hero.guid);
                    self.assembly_confirmed = true;
                } else {
                    self.assembly_choice = None;
                    self.assembly_confirmed = false;
                }
                let skin = viewer
                    .card
                    .skins
                    .get(&hero.guid)
                    .copied()
                    .unwrap_or_default();
                if self.body.is_some_and(|body| {
                    (body.hero, body.skin) == (hero.guid, skin)
                        && !scene
                            .simulation()
                            .player_body(body.id)
                            .is_some_and(PlayerBodyState::needs_runtime_restart)
                }) {
                    self.queue_controller(scene, tick, self.assembly_confirmed, Some(hero.guid))?;
                } else if let Some(entity) = self.next_body(now) {
                    self.switch(scene, viewer, tick, quanta, entity, (&hero, skin))?;
                }
            }
            Some(HeroSelectAction::ReopenAssembly)
                if self.assembly_confirmed
                    && scene.selected_phase() == Some(MatchPhase::Assemble) =>
            {
                self.assembly_confirmed = false;
                self.queue_controller(
                    scene,
                    tick,
                    true,
                    self.assembly_choice.or(self.body.map(|body| body.hero)),
                )?;
            }
            Some(HeroSelectAction::OpenOrClose(wanted)) => {
                let open = self.body.is_none()
                    || self.assembly_confirmed
                    || scene.holds_assembly_spawn(viewer.team)
                    || wanted.unwrap_or(!self.select_open);
                let hero = self.assembly_choice.or(self.body.map(|body| body.hero));
                self.queue_controller(scene, tick, open, hero)?;
            }
            _ => {}
        }
        Ok(())
    }

    pub(crate) fn advance_assembly_selection(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        quanta: u32,
        now: Duration,
    ) -> io::Result<()> {
        let Some(guid) = self.assembly_choice else {
            return Ok(());
        };
        if scene.selected_phase() == Some(MatchPhase::Assemble)
            || self.custom_mode_view.is_none_or(|(phase, _)| {
                matches!(phase, MatchPhase::Waiting | MatchPhase::Assemble)
            })
            || self.mode.data_in_flight()
        {
            return Ok(());
        }
        if self.body.is_some_and(|body| body.hero == guid) {
            self.assembly_choice = None;
            self.assembly_confirmed = false;
            return Ok(());
        }
        let Some(entity) = self.next_body(now) else {
            return Ok(());
        };
        let Some(hero) = scene.heroes().get(guid).cloned() else {
            return Ok(());
        };
        let skin = viewer.card.skins.get(&guid).copied().unwrap_or_default();
        self.assembly_choice = None;
        self.assembly_confirmed = false;
        self.switch(scene, viewer, tick, quanta, entity, (&hero, skin))
    }

    pub(crate) fn command_ack(&self, tick: u32) -> u32 {
        self.input.processed.unwrap_or_else(|| {
            self.input
                .first_admitted
                .map_or(tick, |frame| tick.min(frame.saturating_sub(1)))
        })
    }

    pub(crate) fn queue_game_message(&mut self, tick: u32, received: Duration, message: Message) {
        if let Some(report) = ClientGameMessage::read(&message).and_then(ClientWheelUse::read) {
            if let Some(body) = self
                .active_body_id()
                .filter(|&id| id.entity == report.entity && self.owner_body_ready(id))
            {
                self.input.push(InputWork::WheelUse { body, report });
            }
            return;
        }
        if self.can_handle_game_message() {
            self.input.push(InputWork::Message {
                message,
                tick,
                received,
            });
        }
    }

    pub(crate) fn queue_wheel_action(
        &mut self,
        simulation: &RoomSimulation,
        message: &Message,
        command_frame: Option<u32>,
    ) {
        let Some(body) = self
            .active_body_id()
            .filter(|&id| self.owner_body_ready(id))
        else {
            return;
        };
        if !simulation
            .player_body(body)
            .filter(|state| state.owner == self.owner && state.alive())
            .and_then(|state| state.runtime.as_deref())
            .is_some_and(OwnerBodyRuntime::wheel_actions_available)
        {
            return;
        }
        if let Some(action) = ClientWheelAction::read(message) {
            if let Some(target) = action.target() {
                if target == body.entity || !simulation.tracks_living_actor(target) {
                    return;
                }
            }
            self.input.push(InputWork::WheelAction {
                body,
                action,
                command_frame,
            });
        }
    }

    pub(crate) fn queue_skin(&mut self, hero: u64, tick: u32, received: Duration) {
        if self.can_handle_game_message() {
            self.input.push(InputWork::Skin {
                hero,
                tick,
                received,
            });
        }
    }

    fn apply_skin(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        hero: u64,
        tick: u32,
        now: Duration,
        quanta: u32,
    ) -> io::Result<()> {
        let Some(body) = self.body.filter(|body| body.hero == hero) else {
            return Ok(());
        };
        let Some(&skin) = viewer
            .card
            .skins
            .get(&hero)
            .filter(|&&skin| skin != body.skin)
        else {
            return Ok(());
        };
        if let Some(entity) = self.next_body(now) {
            let hero =
                scene.heroes().get(hero).cloned().ok_or_else(|| {
                    invalid("active player hero is absent from the scene catalog")
                })?;
            self.switch(scene, viewer, tick, quanta, entity, (&hero, skin))?;
        }
        Ok(())
    }

    pub(crate) fn queue_commands(&mut self, tick: u32, commands: &[Command]) {
        if self.started.is_some() {
            self.input
                .commands(tick, commands, self.body_script.as_ref().map(|s| s.frame));
        }
    }

    pub(crate) fn has_input_work(&self) -> bool {
        !self.input.work.is_empty()
    }

    pub(crate) fn combat_body_active(&self) -> bool {
        self.body_script.is_some()
    }

    pub(crate) fn take_hitscan(
        &self,
        simulation: &mut RoomSimulation,
    ) -> Vec<(PosedHitscan, crate::game_hitscan::HitTargetPolicy)> {
        let Some(id) = self.active_body_id() else {
            return Vec::new();
        };
        simulation.take_player_hitscan(id)
    }

    pub(crate) fn advance_fields(
        &mut self,
        simulation: &mut RoomSimulation,
        tick: u32,
    ) -> io::Result<Option<(BodyId, Vec<HealingPulse>)>> {
        let Some(id) = self
            .body_script
            .as_ref()
            .filter(|script| script.projectiles.is_some())
            .map(|script| script.id)
        else {
            return Ok(None);
        };
        let Some((pulses, expired_tokens)) = simulation.advance_player_fields(id, tick) else {
            return Ok(None);
        };
        let Some(projectiles) = self
            .body_script
            .as_mut()
            .and_then(|script| script.projectiles.as_mut())
        else {
            return Ok(None);
        };
        let mut deliveries = Vec::new();
        for token in expired_tokens {
            projectiles.cancel(token, &mut deliveries);
        }
        self.queue_projectiles(id, deliveries)?;
        Ok((!pulses.is_empty()).then_some((id, pulses)))
    }

    fn retire_biotic_views(&mut self, keep: impl Fn(&BioticView) -> bool) {
        for (&entity, view) in &mut self.biotic_views {
            if !view.retiring && !keep(view) {
                view.retiring = true;
                self.queued.push(Arc::new(Record {
                    update: EntityUpdate {
                        entity,
                        operation: Some(Operation::Destroy),
                        ..EntityUpdate::default()
                    },
                    delivery: Delivery::BioticDestroy,
                }));
            }
        }
    }

    pub(crate) fn advance_biotic_views(
        &mut self,
        simulation: &RoomSimulation,
        tick: u32,
    ) -> io::Result<()> {
        let Some((body, script)) = self.body.zip(self.body_script.as_ref()) else {
            return Ok(());
        };
        let Some(projectiles) = &script.projectiles else {
            return Ok(());
        };
        let Some(state) = simulation.player_body(script.id) else {
            return Ok(());
        };
        if state.fields.is_empty() && self.biotic_views.is_empty() {
            return Ok(());
        }
        let mut active = Vec::new();
        for (token, field) in &state.fields {
            if let Some(faded) = field.presentation(tick) {
                active.extend(
                    projectiles
                        .confirmed_entities(*token)
                        .map(|parent| (*token, parent, faded)),
                );
            }
        }
        // Retire the attached child before allowing its predicted parent to be destroyed.
        self.retire_biotic_views(|view| {
            active
                .iter()
                .any(|&(token, parent, _)| token == view.token && parent == view.parent)
        });
        for (token, parent, faded) in active {
            let existing = self
                .biotic_views
                .iter_mut()
                .find(|(_, view)| view.token == token && view.parent == parent);
            if let Some((_, view)) = existing {
                if view.created && !view.retiring && view.faded != Some(faded) {
                    let Some(runtime) = &state.runtime else {
                        return Ok(());
                    };
                    let payload = runtime
                        .encoder()
                        .biotic_field_frame(faded)
                        .map_err(invalid)?;
                    let chunk = view.stream.full(&payload).map_err(invalid)?;
                    view.faded = Some(faded);
                    self.push(Record {
                        update: chunk.update,
                        delivery: Delivery::BioticFrame { last: chunk.last },
                    });
                }
                continue;
            }
            if self.next_biotic_view == END_BIOTIC_VIEW {
                continue;
            }
            let entity = self.next_biotic_view;
            self.next_biotic_view += 1;
            let mut create = Create::new(BIOTIC_FIELD_ENTITY, Vec::new());
            create.skin.guid = body.skin.guid;
            create.movement_parent = Some(parent);
            create.movement = Some(Movement {
                flags: KEEP_MOVEMENT_ROTATION,
                ..Movement::default()
            });
            self.biotic_views.insert(
                entity,
                BioticView {
                    token,
                    parent,
                    created: false,
                    faded: None,
                    retiring: false,
                    stream: Stream::new(entity),
                },
            );
            self.push(Record {
                update: EntityUpdate {
                    entity,
                    operation: Some(Operation::Create(Box::new(create))),
                    ..EntityUpdate::default()
                },
                delivery: Delivery::BioticCreate,
            });
        }
        self.remove_stale();
        Ok(())
    }

    pub(crate) fn take_projectile_launches(
        &self,
        simulation: &mut RoomSimulation,
    ) -> Option<(BodyId, Vec<AdmittedFlight>)> {
        let script = self.body_script.as_ref()?;
        let launches = simulation.take_player_projectile_launches(script.id)?;
        Some((script.id, launches))
    }

    pub(crate) fn finish_projectiles(
        &mut self,
        id: BodyId,
        tick: u32,
        outcomes: Vec<FlightOutcome>,
    ) -> io::Result<()> {
        let Some(projectiles) = self
            .body_script
            .as_mut()
            .filter(|script| script.id == id)
            .and_then(|script| script.projectiles.as_mut())
        else {
            return Ok(());
        };
        let mut deliveries = Vec::new();
        for outcome in outcomes {
            match outcome {
                FlightOutcome::Ended {
                    token,
                    travelled,
                    impact,
                } => {
                    let travelled = (travelled / TICK_PERIOD.as_secs_f64()).ceil() as u32;
                    projectiles.flight_ended(token, travelled, tick, impact);
                }
                FlightOutcome::Failed(token) => projectiles.cancel(token, &mut deliveries),
            }
        }
        deliveries.extend(projectiles.advance(tick));
        self.queue_projectiles(id, deliveries)
    }

    pub(super) fn queue_projectiles(
        &mut self,
        id: BodyId,
        deliveries: Vec<ProjectileDelivery>,
    ) -> io::Result<()> {
        if deliveries.is_empty() {
            return Ok(());
        }
        for delivery in deliveries {
            let update = delivery.update().map_err(invalid)?;
            let delivery = match delivery {
                ProjectileDelivery::Confirm { token, .. } => {
                    self.projectile_destroys.remove(&update.entity);
                    Delivery::ProjectileConfirm { id, token }
                }
                ProjectileDelivery::Destroy { token, .. } => {
                    self.projectile_destroys.insert(update.entity, (id, token));
                    Delivery::ProjectileDestroy { id, token }
                }
            };
            self.push(Record { update, delivery });
        }
        self.remove_stale();
        Ok(())
    }

    /// One unit per physics pose, unposed VM frame, pre-body command, or queued game message.
    /// A fresh frame is committed only after its final pose has completed.
    pub(crate) fn advance_input(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        quanta: u32,
        budget: usize,
    ) -> io::Result<(usize, bool)> {
        let mut remaining = budget;
        while remaining != 0 {
            let Some(work) = self.input.work.pop_front() else {
                break;
            };
            // Let the room settle these shots before a selection despawns
            // the body that owns them, even if its script has stopped.
            if matches!(work, InputWork::Message { .. } | InputWork::Skin { .. })
                && self
                    .active_body_id()
                    .and_then(|id| scene.simulation().player_body(id))
                    .is_some_and(|body| !body.hitscan.is_empty())
            {
                self.input.work.push_front(work);
                break;
            }
            match work {
                InputWork::Message {
                    message,
                    tick,
                    received,
                } => {
                    self.game_message(scene, viewer, tick, received, quanta, &message)?;
                    remaining -= 1;
                }
                InputWork::WheelAction {
                    body,
                    action,
                    command_frame,
                } => {
                    let command_frame = command_frame.filter(|&frame| {
                        self.input
                            .admitted
                            .is_some_and(|admitted| frame <= admitted)
                    });
                    let runtime = self
                        .active_body_id()
                        .filter(|&active| active == body && self.owner_body_ready(active))
                        .and_then(|id| scene.simulation_mut().player_body_mut(id))
                        .filter(|state| state.owner == self.owner && state.alive())
                        .and_then(|state| state.runtime.as_mut())
                        .filter(|runtime| runtime.wheel_actions_available());
                    if let Some(runtime) = runtime {
                        // A full or halted VM queue rejects the request locally.
                        let _ = runtime.queue_wheel_selection(action, command_frame);
                    }
                    remaining -= 1;
                }
                InputWork::WheelUse { body, report } => {
                    let sent_instance_start = self
                        .body_script
                        .as_ref()
                        .filter(|script| script.id == body)
                        .and_then(|script| script.sent_spray_origin(report));
                    if self.active_body_id() == Some(body) && self.owner_body_ready(body) {
                        if let Some(runtime) = scene
                            .simulation_mut()
                            .player_body_mut(body)
                            .filter(|state| state.owner == self.owner && state.alive())
                            .and_then(|state| state.runtime.as_mut())
                        {
                            let _ = runtime.queue_wheel_use(report, sent_instance_start);
                        }
                    }
                    remaining -= 1;
                }
                InputWork::Skin {
                    hero,
                    tick,
                    received,
                } => {
                    self.apply_skin(scene, viewer, hero, tick, received, quanta)?;
                    remaining -= 1;
                }
                InputWork::Command {
                    command,
                    mut pending,
                } => {
                    let (used, complete) =
                        self.advance_command(scene, command, &mut pending, quanta, remaining)?;
                    remaining -= used;
                    if !complete {
                        self.input
                            .work
                            .push_front(InputWork::Command { command, pending });
                        break;
                    }
                    self.input.processed = Some(command.frame);
                }
                InputWork::Leave => return Ok((budget - remaining + 1, true)),
            }
        }
        Ok((budget - remaining, false))
    }

    fn advance_command(
        &mut self,
        scene: &mut PreparedScene,
        command: Command,
        pending: &mut Option<PendingCommand>,
        frame_us: u32,
        budget: usize,
    ) -> io::Result<(usize, bool)> {
        let Some(id) = self.body.map(|body| body.id) else {
            return Ok((1, true));
        };
        let needs_deadeye = scene.simulation().player_body(id).and_then(|state| {
            state
                .runtime
                .as_deref()
                .filter(|runtime| runtime.has_deadeye())
                .map(|_| state.team)
        });
        if let Some(team) = needs_deadeye {
            if let Ok(deadeye) = scene.deadeye_scene(team) {
                if let Some(runtime) = scene
                    .simulation_mut()
                    .player_body_mut(id)
                    .and_then(|state| state.runtime.as_deref_mut())
                {
                    runtime.set_deadeye_targets(deadeye);
                }
            }
        }
        let movement_scale = scene
            .simulation()
            .pvp_objective()
            .map_or(1.0, |mode| mode.movement_scale());
        let (simulation, rays) = scene.simulation_and_rays();
        let motion_frame = simulation.movement_contact_frame();
        let Some(state) = simulation.player_body_mut(id) else {
            return Ok((1, true));
        };
        let alive = state.alive();
        state.mover.set_player_dead(!alive);
        let pending = pending.get_or_insert_with(|| {
            PendingCommand::new(
                state.mover.pending_poses(command),
                self.body_script.as_ref(),
            )
        });
        let mut used = 0;
        let mut fault = None;
        let team = state.team;
        let blockers = motion_frame.as_ref().map_or_else(Vec::new, |frame| {
            self.owner_movement_blockers(frame, id.entity, team)
        });
        let world_started = self.world_started();
        let created = &self.created;
        let pending_respawn = &self.pending_respawn_presentation;
        let can_contact =
            |entity| world_started && acknowledged_contact(entity, created, pending_respawn);
        let previously_processed = self.input.processed;
        let script = &mut self.body_script;
        let detached_motion = &mut self.detached_motion;
        let PlayerBodyState {
            mover,
            runtime,
            frame: body_frame,
            fields,
            launches,
            hitscan,
            ..
        } = state;
        let mut buffers = StepBuffers {
            frame: body_frame,
            fields,
            launches,
            hitscan,
        };
        if let Some((script, runtime)) = script.as_mut().zip(runtime.as_deref_mut()) {
            for (frame, step) in pending.gap.by_ref().take(budget) {
                used += 1;
                let neutral = Command {
                    frame,
                    ..Command::default()
                };
                if let Err(error) = script.advance_step(runtime, mover, &mut buffers, neutral, step)
                {
                    mover.track_modifiers(frame, &BTreeMap::new());
                    mover.clear_pending_script_motion();
                    pending.gap = ScriptGap::default();
                    fault = Some((frame, step, error));
                    break;
                }
            }
        }
        if fault.is_none() && used < budget && pending.poses.len() != 0 {
            if let Some(runtime) = runtime.as_deref_mut() {
                stage_scripted_motion(runtime, mover);
            }
        }
        let mut posed_buttons: Vec<u32> = Vec::new();
        let dt = f64::from(frame_us) * movement_scale / 1_000_000.0;
        let stepped = mover.step_pending(
            rays,
            &mut pending.poses,
            dt,
            budget - used,
            || &blockers,
            |mover, command| {
                if alive {
                    posed_buttons.push(command.buttons);
                    if let Some(frame) = &motion_frame {
                        // Use the resulting capsule on every posed frame, before
                        // saving it for owner correction or running body scripts.
                        let separation = frame.ally_separation_where(
                            id.entity,
                            team,
                            mover.contact_capsule(),
                            dt,
                            can_contact,
                        );
                        mover.apply_ally_separation(separation, &blockers, dt);
                    }
                }
                if script.is_none()
                    && frame_us != 0
                    && mover.needs_owner_correction_at(command.frame)
                {
                    if detached_motion
                        .as_ref()
                        .is_some_and(|(owner, _)| *owner != id)
                    {
                        *detached_motion = None;
                    }
                    let (_, history) = detached_motion.get_or_insert_with(|| {
                        (
                            id,
                            CorrectionHistory::new(id.entity, id.generation)
                                .with_processed_at_spawn(previously_processed),
                        )
                    });
                    history.swept_motion(command.frame, frame_us);
                }
                if let Some((_, history)) =
                    detached_motion.as_mut().filter(|(owner, _)| *owner == id)
                {
                    history.record(mover.pose());
                }
                if let Some(script) = script.as_mut() {
                    // The pose already moved even if the state's next update fails.
                    script.record_authoritative_movement(
                        mover,
                        command.frame,
                        frame_us,
                        previously_processed,
                    );
                    if fault.is_some() {
                        if let Some(history) = script.scripted_motion_history() {
                            history.record(mover.pose());
                        }
                    }
                }
                if let Some((script, runtime)) = script
                    .as_mut()
                    .zip(runtime.as_deref_mut())
                    .filter(|_| fault.is_none())
                {
                    if let Err(error) =
                        script.advance_step(runtime, mover, &mut buffers, command, BodyStep::Posed)
                    {
                        mover.track_modifiers(command.frame, &BTreeMap::new());
                        mover.clear_pending_script_motion();
                        fault = Some((command.frame, BodyStep::Posed, error));
                    }
                }
            },
        );
        let complete = pending.poses.len() == 0;
        if alive && !posed_buttons.is_empty() {
            if let Some(state) = simulation.player_body_mut(id) {
                for buttons in posed_buttons {
                    let pressed = buttons & !state.last_buttons;
                    let released = state.last_buttons & !buttons;
                    state.last_buttons = buttons;
                    state.pending_pressed_buttons |= pressed;
                    state.pending_released_buttons |= released;
                }
            }
        }
        if let Some((frame, step, error)) = fault {
            eprintln!("body owner statescript stopped at frame {frame} during {step:?}: {error}");
            #[cfg(test)]
            {
                self.owner_script_fault = Some(OwnerScriptFault {
                    id,
                    frame,
                    step: Some(step),
                    error,
                });
            }
            self.cancel_body_script(Some(simulation));
        }
        used += stepped.map_err(invalid)?;
        Ok((used, complete))
    }

    pub(crate) fn advance_player_respawn(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        quanta: u32,
        now: Duration,
    ) -> io::Result<()> {
        let Some(body) = self.body else {
            return Ok(());
        };
        if !scene.simulation().is_player_respawn_due(body.id, now)
            || self.input.work.iter().any(|work| {
                // A partial command encodes work for the old mover. Ordered
                // messages and skin choices get a chance to replace that body.
                !matches!(work, InputWork::Command { pending: None, .. })
            })
        {
            return Ok(());
        }
        let Some(entity) = self.next_body(now) else {
            return Ok(());
        };
        // A startup attempt is transactional. A rejected body leaves the
        // corpse and its selection in place without repeating startup.
        scene.simulation_mut().clear_player_respawn(body.id);
        let hero = scene
            .heroes()
            .get(body.hero)
            .cloned()
            .ok_or_else(|| invalid("active player hero is absent from the scene catalog"))?;
        self.switch_at(
            scene,
            viewer,
            tick,
            quanta,
            entity,
            (&hero, body.skin),
            None,
            BodyTransition::Respawn,
        )
    }

    pub(super) fn next_body(&self, now: Duration) -> Option<u32> {
        let suffix = self.body.map_or(0, |body| body.id.entity & 0xff);
        (0..255)
            .map(|offset| self.owner | ((suffix + offset) % 255 + 1))
            .find(|&entity| {
                self.bodies.get(&entity).is_none_or(
                    |(_, lifetime)| matches!(lifetime, Lifetime::ReusableAt(at) if now >= *at),
                )
            })
    }

    pub(crate) fn advance_body_swap(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        quanta: u32,
        now: Duration,
    ) -> io::Result<()> {
        let Some(body) = self.body else {
            return Ok(());
        };
        if self.input.work.iter().any(|work| {
            matches!(
                work,
                InputWork::Command {
                    pending: Some(_),
                    ..
                }
            )
        }) {
            return Ok(());
        }
        let Some(entity) = self.next_body(now) else {
            return Ok(());
        };
        let dva = crate::hero_ability_rules::HeroAbilityRules::defaults().dva_mech;
        let swap = {
            let duplicate_revert = scene
                .simulation_mut()
                .take_pending_duplicate_revert(self.owner);
            let Some(state) = scene.simulation_mut().player_body_mut(body.id) else {
                return Ok(());
            };
            let Some(new_body) = duplicate_revert.or_else(|| {
                state
                    .runtime
                    .as_mut()
                    .and_then(|runtime| runtime.take_body_swap())
            }) else {
                return Ok(());
            };
            if new_body == state.body_template {
                return Ok(());
            }
            let pose = state.mover.pose();
            let old_body = state.body_template;
            (new_body, old_body, pose.position, pose.velocity, pose.yaw)
        };
        // D.Va Call Mech crush (Pilot -> MEKA): AoE damage + knockback from HeroAbilityRules.
        if swap.1 == dva.pilot_body && swap.0 == dva.meka_body {
            scene.simulation_mut().apply_call_mech_crush(
                body.id.entity,
                viewer.team,
                swap.2,
                tick,
                now,
            );
        }
        // D.Va Self-Destruct eject (MEKA -> Pilot while moving forward): glide & detonate.
        if swap.1 == dva.meka_body
            && swap.0 == dva.pilot_body
            && swap.3[0].hypot(swap.3[2]) > dva.self_destruct_min_speed
        {
            scene.simulation_mut().spawn_self_destruct_bomb(
                body.id.entity,
                viewer.team,
                swap.2,
                swap.3,
                tick,
            );
        }
        let mut hero = scene
            .heroes()
            .get(body.hero)
            .cloned()
            .ok_or_else(|| invalid("active player hero is absent from the scene catalog"))?;
        hero.body = swap.0;
        self.switch_at(
            scene,
            viewer,
            tick,
            quanta,
            entity,
            (&hero, body.skin),
            Some((swap.2, swap.4)),
            BodyTransition::Transfer,
        )
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn advance_mod_hero_switch(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        quanta: u32,
        now: Duration,
    ) -> io::Result<()> {
        let Some(target_hero) = scene.simulation_mut().take_mod_hero_switch(self.owner) else {
            return Ok(());
        };
        let Some(hero) = scene.heroes().get(target_hero).cloned() else {
            return Ok(());
        };
        let Some(entity) = self.next_body(now) else {
            return Ok(());
        };
        let skin = viewer
            .card
            .skins
            .get(&target_hero)
            .copied()
            .unwrap_or_default();
        let current_pose = self
            .body
            .and_then(|body| scene.simulation().player_body(body.id))
            .map(|state| state.mover.pose())
            .filter(|pose| pose.position.into_iter().all(f64::is_finite))
            .map(|pose| (pose.position, pose.yaw));
        // Clear pending wheel/emote inputs targeting the retiring body before switching heroes.
        self.input.work.retain(|item| {
            !matches!(
                item,
                InputWork::WheelAction { .. } | InputWork::WheelUse { .. }
            )
        });
        self.switch_at(
            scene,
            viewer,
            tick,
            quanta,
            entity,
            (&hero, skin),
            current_pose,
            BodyTransition::Create,
        )
    }

    pub(super) fn switch(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        quanta: u32,
        entity: u32,
        selection: (&Hero, SkinTheme),
    ) -> io::Result<()> {
        self.switch_at(
            scene,
            viewer,
            tick,
            quanta,
            entity,
            selection,
            None,
            BodyTransition::Create,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn switch_at(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        quanta: u32,
        entity: u32,
        selection: (&Hero, SkinTheme),
        override_pose: Option<([f64; 3], i32)>,
        transition: BodyTransition,
    ) -> io::Result<()> {
        let (initial_hero, skin) = selection;
        let mut effective_hero = initial_hero.clone();
        let make_mover = |body_def: u64| -> io::Result<Mover> {
            match override_pose {
                Some((position, yaw)) => {
                    scene.player_spawn_mover_at(body_def, viewer.team, position, yaw)
                }
                None if scene.custom_mode_view().is_some() => scene.player_spawn_mover_for_slot(
                    body_def,
                    viewer.team,
                    viewer.slot.max(0) as usize,
                ),
                None => scene.player_spawn_mover(body_def, viewer.team),
            }
        };
        let mut mover = make_mover(effective_hero.body)?;
        let mut movement = mover.pose();
        let generation = scene
            .simulation()
            .next_player_generation(self.owner, self.generation)?;
        let id = BodyId { entity, generation };
        let body = Body {
            id,
            hero: effective_hero.guid,
            skin,
        };
        let body_filter = crate::match_scene::HERO_BIT | team_bit(viewer.team)?;
        let start_frame = self.input.processed.unwrap_or(tick);
        let slot = u8::try_from(viewer.slot).map_err(invalid)?;
        let start_candidate = |hero_def: &Hero, current_mover: &Mover, current_pose: Movement| {
            scene
                .owner_body_runtime(
                    hero_def,
                    OwnerBodySpawn {
                        slot,
                        controller: self.owner,
                        entity,
                        body_filter,
                        frame: start_frame,
                        pose: current_pose,
                        eye_position: crate::game_combat::script_eye_position(
                            hero_def.guid,
                            current_mover,
                        ),
                        skin: skin.guid,
                        quanta,
                    },
                )
                .and_then(|mut runtime| {
                    if let Some(runtime) = &mut runtime {
                        runtime.set_wheel_cosmetics(
                            viewer
                                .card
                                .wheel_cosmetics
                                .get(&hero_def.guid)
                                .copied()
                                .unwrap_or_default(),
                        );
                    }
                    let script = runtime
                        .as_deref()
                        .map(|runtime| {
                            BodyScript::new(body, start_frame, self.input.processed, runtime)
                        })
                        .transpose()?;
                    Ok((runtime, script))
                })
        };
        let candidate = start_candidate(&effective_hero, &mover, movement);
        let (mut runtime, mut script) = match candidate {
            Ok(candidate) => candidate,
            Err(error) => {
                eprintln!(
                    "body startup rejected for controller {:08X}: {error}",
                    self.owner
                );
                return Ok(());
            }
        };
        // If startup statescript immediately requested possession of an authored alternate
        // hero body (e.g. STU_B6E3C77D + STU_CA861E44), swap directly before creating on wire.
        // A mid-life possession transfer already selected its alternate body.
        if transition != BodyTransition::Transfer {
            if let Some(swap_body) = runtime
                .as_mut()
                .and_then(|rt| rt.take_body_swap())
                .filter(|&b| b != effective_hero.body)
            {
                let mut swapped_hero = effective_hero.clone();
                swapped_hero.body = swap_body;
                match make_mover(swapped_hero.body) {
                    Ok(swapped_mover) => {
                        let swapped_movement = swapped_mover.pose();
                        match start_candidate(&swapped_hero, &swapped_mover, swapped_movement) {
                            Ok((Some(swapped_rt), swapped_script)) => {
                                effective_hero = swapped_hero;
                                mover = swapped_mover;
                                movement = swapped_movement;
                                runtime = Some(swapped_rt);
                                script = swapped_script;
                            }
                            Ok((None, _)) => {
                                eprintln!(
                                    "body swap startup returned None for {:016X} -> {:016X}",
                                    effective_hero.body, swap_body
                                );
                            }
                            Err(error) => {
                                eprintln!(
                                    "body swap startup rejected for {:016X} -> {:016X}: {error}",
                                    effective_hero.body, swap_body
                                );
                            }
                        }
                    }
                    Err(error) => {
                        eprintln!(
                            "body swap mover rejected for {:016X} -> {:016X}: {error}",
                            effective_hero.body, swap_body
                        );
                    }
                }
            }
        }
        // Queue on the final startup candidate, before the corpse is replaced.
        // Skin refreshes, hero changes and possession/transport retries carry no respawn.
        if let Some(runtime) = runtime
            .as_mut()
            .filter(|_| transition == BodyTransition::Respawn)
        {
            if let Err(error) = runtime.receive_respawn() {
                eprintln!(
                    "body respawn rejected for controller {:08X}: {error}",
                    self.owner
                );
                return Ok(());
            }
        }
        let pose = owner_pose(movement);
        let hero = &effective_hero;
        let body_create_input = BodyCreate {
            entity,
            hero,
            team_bit: body_filter,
            position: pose.position,
            yaw: pose.yaw,
            skin,
            vitals: runtime.as_deref().and_then(OwnerBodyRuntime::vitals),
        };
        let create = body_create(
            BodyCreate {
                entity,
                hero,
                team_bit: body_filter,
                position: pose.position,
                yaw: pose.yaw,
                skin,
                vitals: body_create_input.vitals,
            },
            BodyView::Owner {
                pose: Some(pose),
                leases: runtime
                    .as_deref()
                    .and_then(OwnerBodyRuntime::projectile_leases)
                    .map_or(&[], ProjectileLeases::blocks),
            },
            scene.pools(),
        );
        let observer_create = body_create(body_create_input, BodyView::Observer, scene.pools());
        let possession = Possession {
            body: entity,
            hero: hero.guid,
            skin: (skin.guid != 0).then_some(skin.guid),
        };
        let mut controller = self.controller.clone();
        let open = self.assembly_confirmed;
        let frame = self.selection_frame(scene, open, Some(hero.guid));
        let close = Self::controller_chunk(
            &mut controller,
            scene,
            self.command_ack(tick),
            frame,
            self.controller_skins,
        )?;

        let state = PlayerBodyState::new(
            id,
            self.owner,
            slot,
            viewer.team,
            hero.guid,
            mover,
            runtime,
            start_frame,
            Some(observer_create),
        )
        .with_body_template(hero.body);
        if transition == BodyTransition::Transfer {
            scene.simulation_mut().swap_player_body(state)?;
        } else {
            scene.simulation_mut().spawn_player_body(state)?;
        }
        self.controller = controller;
        self.generation = generation;
        self.bodies.insert(entity, (generation, Lifetime::Live));
        self.created.remove(&entity);
        self.control_body_round = scene.control_round().map(|round| round.round_index);
        let old = self.body.replace(body);
        self.cancel_body_script(None);
        self.body_script = script;
        self.bound = None;
        self.select_open = open;
        self.push(Record {
            update: create,
            delivery: Delivery::CreateBody(id),
        });
        self.push(Record {
            update: EntityUpdate {
                entity: self.owner,
                operation: Some(Operation::Update(
                    viewer.card.possession_components(possession),
                )),
                ..EntityUpdate::default()
            },
            delivery: Delivery::Possession(id),
        });
        if let Some(old) = old {
            self.retire_body(old.id);
        }
        self.controller_pending = close.is_none().then_some(frame);
        if let Some(close) = close {
            self.controller_skins = frame.skins;
            self.push(close);
        }
        self.remove_stale();
        Ok(())
    }

    pub(super) fn retire_body(&mut self, id: BodyId) {
        if self
            .pending_owner_correction
            .is_some_and(|pending| pending.id == id)
        {
            self.pending_owner_correction = None;
        }
        if self
            .detached_motion
            .as_ref()
            .is_some_and(|(owner, _)| *owner == id)
        {
            self.detached_motion = None;
        }
        self.created.remove(&id.entity);
        self.health_updates.remove(&id.entity);
        self.bodies
            .insert(id.entity, (id.generation, Lifetime::Retiring));
        self.push(Record {
            update: EntityUpdate {
                entity: id.entity,
                operation: Some(Operation::Destroy),
                ..EntityUpdate::default()
            },
            delivery: Delivery::DestroyBody(id),
        });
    }

    fn controller_chunk(
        controller: &mut Stream,
        scene: &PreparedScene,
        command_frame: u32,
        frame: ControllerFrame<'_>,
        previous_skins: bool,
    ) -> io::Result<Option<Record>> {
        if controller.data_in_flight() {
            return Ok(None);
        }
        let chunk = if controller.latest_data() == 0 {
            controller.full_owner(command_frame, |cmfd| {
                scene.controller().controller_frame(cmfd, frame)
            })
        } else {
            controller.reliable_owner_delta(command_frame, |cmfd| {
                scene
                    .controller()
                    .selection_delta(cmfd, frame, frame.skins != previous_skins)
            })
        }
        .map_err(invalid)?;
        Ok(Some(Record::script(
            chunk,
            frame.hero.is_some() && !frame.select_open,
        )))
    }

    fn selection_frame(
        &self,
        scene: &PreparedScene,
        open: bool,
        hero: Option<u64>,
    ) -> ControllerFrame<'static> {
        ControllerFrame {
            select_open: open,
            // Keep the original map-entry transition, then leave the assembly
            // input steady while the player opens and closes the picker.
            skins: if self.controller.latest_data() == 0 {
                open && !scene.map().is_junkenstein()
            } else {
                self.controller_skins
                    && (scene.map().mode_guid == crate::game_content::PRACTICE_RANGE_MODE
                        || matches!(
                            scene.selected_phase(),
                            Some(MatchPhase::Waiting | MatchPhase::Assemble)
                        ))
            },
            hide_assemble: scene.hide_assemble(),
            hero,
            confirmed_hero: self.assembly_choice.filter(|_| self.assembly_confirmed),
            send_selection: self.refresh_selection || scene.has_objective_presenter(),
            objective: scene.hero_select_objective(self.viewer_team),
            ..ControllerFrame::default()
        }
    }

    fn queue_controller(
        &mut self,
        scene: &PreparedScene,
        tick: u32,
        open: bool,
        hero: Option<u64>,
    ) -> io::Result<()> {
        let command_frame = self.command_ack(tick);
        let frame = self.selection_frame(scene, open, hero);
        let record = Self::controller_chunk(
            &mut self.controller,
            scene,
            command_frame,
            frame,
            self.controller_skins,
        )?;
        self.select_open = open;
        self.controller_pending = record.is_none().then_some(frame);
        if let Some(record) = record {
            self.controller_skins = frame.skins;
            self.push(record);
        }
        self.remove_stale();
        Ok(())
    }

    pub(crate) fn movement(
        &self,
        scene: &PreparedScene,
        tick: u32,
    ) -> Result<Vec<EntityUpdate>, CombatError> {
        if !self
            .started
            .is_some_and(|started| tick.saturating_sub(started) >= BOT_RECORD_DELAY)
        {
            return Ok(Vec::new());
        }
        let mut updates: Vec<_> = scene
            .bots()?
            .filter(|bot| self.created.contains(&bot.entity) && bot.record_due(tick, SEND_EVERY))
            .map(|bot| EntityUpdate {
                entity: bot.entity,
                movement: Some(bot.movement(tick)),
                ..EntityUpdate::default()
            })
            .collect();
        // The room already selected this world send, which can fall between
        // exact cadence multiples when the clock skips simulation ticks.
        for (entity, movement) in scene
            .simulation()
            .observer_player_movements(self.owner, tick)
        {
            if self.created.contains(&entity) {
                updates.push(EntityUpdate {
                    entity,
                    movement: Some(movement),
                    ..EntityUpdate::default()
                });
            }
        }
        updates.extend(
            scene
                .simulation()
                .published_pet_movements()
                .filter(|update| {
                    self.tracks_current_bot(update.entity) && self.created.contains(&update.entity)
                }),
        );
        Ok(updates)
    }
}

pub(super) fn command_prefix(
    anchor: Option<u32>,
    tick: u32,
    commands: &[Command],
) -> (Vec<Command>, Option<u32>) {
    // Classify the lease boundary independently of the resource window so a
    // fresh unencodable frame still closes its old link.
    let (mut fresh, boundary) = CommandQueue::new().before_limit(commands, MAX_LEASE_FRAME, anchor);
    let limit = tick.saturating_add(MAX_INPUT_LEAD);
    fresh.truncate(fresh.partition_point(|command| command.frame <= limit));
    (fresh, boundary)
}

pub(super) fn ensure_script_chunk_fits(update: &EntityUpdate, error: &str) -> io::Result<()> {
    let packet = frame(u32::MAX, u32::MAX, &[], &[], &[update]).map_err(invalid)?;
    if packet.len() > MAX_PAYLOAD {
        return Err(invalid(error));
    }
    Ok(())
}

pub(super) fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}
