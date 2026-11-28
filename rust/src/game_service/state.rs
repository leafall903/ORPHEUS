//! Shared routing and scheduling. All mutable gameplay belongs to a room.

use super::*;
#[cfg(feature = "lua-debug")]
use crate::ai_script::AiScriptError;
use crate::{game_egress::SendError, patrol_script};
#[cfg(feature = "lua-debug")]
use sha2::{Digest, Sha256};

const MAX_RECENT_FAULTS: usize = 64;

pub(super) struct State {
    pub(super) schemas: Arc<Schemas>,
    pub(super) preloaded_patrol: Option<PatrolScript>,
    #[cfg(feature = "lua-debug")]
    pub(super) active_patrol_source: Option<Arc<str>>,
    pub(super) patrol_policy_digest: [u8; 32],
    pub(super) patrol_reload_generation: u64,
    pub(super) clock: GameClock,
    pub(super) timing: Option<WorkerTiming>,
    pub(super) links: GameConnections,
    pub(super) rooms: HashMap<MatchId, Room>,
    pub(super) routes: HashMap<u32, MatchId>,
    pub(super) order: IndexMap<u32, ()>,
    pub(super) next_connection: u32,
    pub(super) next_input_peer: Option<u32>,
    pub(super) recent_faults: VecDeque<FaultRecord>,
    pub(super) career_recorder: Option<CareerRecorder>,
    #[cfg(feature = "lua-mods")]
    pub(super) mod_host: Option<Arc<std::sync::Mutex<crate::mod_script::ModHost>>>,
    #[cfg(any(test, feature = "inspector"))]
    pub(super) debug_control_enabled: bool,
}

#[cfg(any(test, feature = "inspector"))]
type TickPlans = HashMap<MatchId, room::RoomTickPlan>;
#[cfg(not(any(test, feature = "inspector")))]
type TickPlans = ();

fn room_tick_plan(
    plans: &TickPlans,
    id: MatchId,
    tick: u32,
    now: Duration,
    drain: bool,
    send_frames: bool,
) -> (bool, u32, Duration, bool) {
    if drain {
        return (false, tick, now, false);
    }
    #[cfg(any(test, feature = "inspector"))]
    {
        if let Some(plan) = plans.get(&id) {
            return (plan.simulate, plan.tick, plan.now, plan.send_world);
        }
    }
    #[cfg(not(any(test, feature = "inspector")))]
    let _ = (plans, id);
    (true, tick, now, send_frames)
}

impl State {
    pub(super) fn new(schemas: Arc<Schemas>, patrol: PatrolScript) -> Self {
        let patrol_policy_digest = patrol.digest();
        Self {
            schemas,
            preloaded_patrol: Some(patrol),
            #[cfg(feature = "lua-debug")]
            active_patrol_source: None,
            patrol_policy_digest,
            patrol_reload_generation: 0,
            clock: GameClock::default(),
            timing: None,
            links: GameConnections::default(),
            rooms: HashMap::new(),
            routes: HashMap::new(),
            order: IndexMap::new(),
            next_connection: 1,
            next_input_peer: None,
            recent_faults: VecDeque::new(),
            career_recorder: None,
            #[cfg(feature = "lua-mods")]
            mod_host: None,
            #[cfg(any(test, feature = "inspector"))]
            debug_control_enabled: false,
        }
    }

    pub(super) fn open_match(
        &mut self,
        id: MatchId,
        map: GameMap,
        players: Vec<MatchPlayer>,
        world: Option<WorldStart>,
        now: Duration,
    ) -> io::Result<Vec<Handoff>> {
        if self.clock.draining() {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "game clock is draining",
            ));
        }
        if self.rooms.contains_key(&id) {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "match already exists",
            ));
        }
        let count = players.len();
        if count == 0 || count > usize::from(u8::MAX) {
            return Err(invalid("invalid match roster size"));
        }
        if world
            .as_ref()
            .is_some_and(|start| start.cards.len() != count || start.slots.len() != count)
        {
            return Err(invalid("prepared match roster and world players disagree"));
        }
        let mut room = Room {
            map,
            players: players
                .into_iter()
                .enumerate()
                .map(|(slot, info)| Player {
                    info,
                    entity: player_entity(slot),
                    gone: false,
                    room_slot: world.as_ref().and_then(|start| start.slots[slot]),
                })
                .collect(),
            members: IndexMap::new(),
            world: None,
            #[cfg(any(test, feature = "inspector"))]
            debug_control: room::RoomDebugControl::new(self.clock.frame(), now),
            #[cfg(test)]
            injection: None,
        };
        let preloaded = world.as_ref().and_then(|_| self.preloaded_patrol.take());
        #[cfg(feature = "lua-debug")]
        let active_source = self.active_patrol_source.clone();
        #[cfg(any(test, feature = "inspector", feature = "lua-debug"))]
        let patrol_generation = self.patrol_reload_generation;
        #[cfg(feature = "lua-mods")]
        let mod_host = self.mod_host.clone();
        let schemas = Arc::clone(&self.schemas);
        let prepared = guard_room(&mut room, RoomStage::Admission, |room| {
            if let Some(WorldStart {
                mut scene, cards, ..
            }) = world
            {
                let patrol = preloaded.map_or_else(
                    || {
                        #[cfg(feature = "lua-debug")]
                        if let Some(source) = &active_source {
                            return PatrolScript::from_source_checked(source)
                                .map_err(io::Error::from);
                        }
                        PatrolScript::load()
                    },
                    Ok,
                )?;
                scene
                    .simulation_mut()
                    .set_team_member_counts(room.team_member_counts());
                scene.simulation_mut().set_player_names(
                    room.players
                        .iter()
                        .map(|p| (p.entity, p.info.name.clone()))
                        .collect(),
                );
                scene
                    .simulation_mut()
                    .seat_bots(&room.map, room.players.len());
                #[cfg(feature = "lua-mods")]
                {
                    let heroes = scene.heroes_arc();
                    let rays = scene.combat_rays_arc();
                    scene.simulation_mut().set_mod_world_assets(heroes, rays);
                    if let Some(host) = mod_host {
                        scene.simulation_mut().attach_mod_host(
                            host,
                            room.map.map_guid,
                            room.map.mode_guid,
                        );
                    }
                }
                room.world = Some(RoomWorld {
                    scene,
                    cards,
                    patrol,
                    #[cfg(any(test, feature = "inspector", feature = "lua-debug"))]
                    patrol_generation,
                });
            }
            for initial in std::iter::once(true).chain(room.world.is_some().then_some(false)) {
                let roster = room.message(0, initial)?;
                let bytes = encode(&schemas, roster.id, &roster.fields)?;
                let payload = frame(u32::MAX, u32::MAX, &[(0, bytes)], &[], &[])?;
                if payload.len() > MAX_PAYLOAD {
                    return Err(invalid("match roster exceeds the game datagram budget").into());
                }
            }
            Ok(())
        });
        if let Err(error) = prepared {
            return Err(io::Error::other(error));
        }
        // Prepare all fallible ticket work before publishing any route.
        let mut next = self.next_connection;
        let mut tickets = Vec::with_capacity(count);
        for _ in 0..count {
            let connection = next;
            next = next % MAX_CONNECTION_ID + 1;
            if self.routes.contains_key(&connection) || self.order.contains_key(&connection) {
                return Err(io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "game seat ID is still in use",
                ));
            }
            tickets.push(Handoff::new(connection, id)?);
        }
        // A failed reservation rolls back only this attempt's new reservations;
        // an existing colliding link is never revoked.
        for (index, ticket) in tickets.iter().enumerate() {
            if !self
                .links
                .reserve_handoff(ticket, u64::from(ticket.conn), now)
            {
                for reserved in &tickets[..index] {
                    self.links.revoke(reserved.conn);
                }
                return Err(io::Error::other(WorkerError::Routing(
                    "seat and connection tables disagree during admission",
                )));
            }
        }
        for (slot, ticket) in tickets.iter().enumerate() {
            room.members
                .insert(ticket.conn, RoomMember { slot, peer: None });
            self.routes.insert(ticket.conn, id);
        }
        self.next_connection = next;
        self.rooms.insert(id, room);
        Ok(tickets)
    }

    fn remove_order(&mut self, connection: u32) {
        if self.next_input_peer == Some(connection) {
            self.next_input_peer = self.order.get_index_of(&connection).and_then(|index| {
                (1..self.order.len()).find_map(|offset| {
                    self.order
                        .get_index((index + offset) % self.order.len())
                        .map(|(&id, _)| id)
                })
            });
        }
        self.order.shift_remove(&connection);
    }

    /// Remove a failed ownership unit, even after a route/member has vanished.
    /// Conflicting ownership is global damage and cannot be repaired by guessing.
    pub(super) fn retire_room(&mut self, id: MatchId) -> Result<(), WorkerError> {
        let mut connections = self
            .rooms
            .get(&id)
            .map(|room| room.members.keys().copied().collect::<Vec<_>>())
            .unwrap_or_default();
        connections.extend(
            self.routes
                .iter()
                .filter_map(|(&connection, &owner)| (owner == id).then_some(connection)),
        );
        connections.sort_unstable();
        connections.dedup();
        for &connection in &connections {
            if self
                .routes
                .get(&connection)
                .is_some_and(|owner| *owner != id)
                || self
                    .rooms
                    .iter()
                    .any(|(&other, room)| other != id && room.members.contains_key(&connection))
            {
                return Err(WorkerError::Routing(
                    "connection belongs to conflicting rooms",
                ));
            }
        }
        // Detach first. Cleanup never calls back into the damaged simulation.
        let retired = self.rooms.remove(&id);
        for connection in connections {
            self.links.revoke(connection);
            self.routes.remove(&connection);
            self.remove_order(connection);
        }
        drop(retired);
        Ok(())
    }

    pub(super) fn finish<T>(
        &mut self,
        id: MatchId,
        stage: RoomStage,
        connection: Option<u32>,
        result: Result<T, RoomError>,
    ) -> Result<Option<T>, WorkerError> {
        match result {
            Ok(value) => Ok(Some(value)),
            Err(RoomError::Transport(error)) => Err(WorkerError::Transport(error)),
            Err(error) => {
                let record = FaultRecord {
                    id,
                    stage,
                    connection,
                    tick: self.clock.frame(),
                    reason: error.category(),
                };
                eprintln!(
                    "Room {:016x}{:016x} retired at {} frame {} connection {:?}: {} ({error})",
                    record.id[0],
                    record.id[1],
                    record.stage.name(),
                    record.tick,
                    record.connection,
                    record.reason
                );
                if self.recent_faults.len() == MAX_RECENT_FAULTS {
                    self.recent_faults.pop_front();
                }
                self.recent_faults.push_back(record);
                self.retire_room(id)?;
                Ok(None)
            }
        }
    }

    fn room_work<T>(
        &mut self,
        id: MatchId,
        stage: RoomStage,
        connection: Option<u32>,
        operation: impl FnOnce(&mut Room) -> Result<T, RoomError>,
    ) -> Result<Option<T>, WorkerError> {
        let Some(room) = self.rooms.get_mut(&id) else {
            return Ok(None);
        };
        let result = guard_room(room, stage, operation);
        self.finish(id, stage, connection, result)
    }

    fn resolve(&mut self, connection: u32) -> Result<Option<MatchId>, WorkerError> {
        if let Some(&id) = self.routes.get(&connection) {
            if self
                .rooms
                .get(&id)
                .is_some_and(|room| room.members.contains_key(&connection))
            {
                return Ok(Some(id));
            }
            self.finish::<()>(
                id,
                RoomStage::Routing,
                Some(connection),
                Err(RoomError::Invariant("route has no room member")),
            )?;
            return Ok(None);
        }
        // This slow path runs only for a broken index, not ordinary datagrams.
        let owners: Vec<_> = self
            .rooms
            .iter()
            .filter_map(|(&id, room)| room.members.contains_key(&connection).then_some(id))
            .collect();
        match owners.as_slice() {
            [id] => {
                self.finish::<()>(
                    *id,
                    RoomStage::Routing,
                    Some(connection),
                    Err(RoomError::Invariant("member has no route")),
                )?;
                Ok(None)
            }
            _ => Err(WorkerError::Routing(
                "active connection has no unambiguous room",
            )),
        }
    }

    pub(super) fn handle_request(
        &mut self,
        request: Request,
        now: Duration,
    ) -> Result<(), WorkerError> {
        match request {
            Request::Open(request) => {
                let OpenRequest {
                    id,
                    map,
                    players,
                    world,
                    reply,
                } = *request;
                let result = self.open_match(id, map, players, world, now);
                let fatal = result.as_ref().err().is_some_and(|error| {
                    error
                        .get_ref()
                        .is_some_and(|source| source.is::<WorkerError>())
                });
                if let Err(mpsc::SendError(Ok(_))) = reply.send(result) {
                    self.retire_room(id)?;
                }
                if fatal {
                    return Err(WorkerError::Routing(
                        "admission found inconsistent link ownership",
                    ));
                }
            }
            Request::Pending { connection, reply } => {
                let _ = reply.send(self.links.handoff_pending(connection, now));
            }
            Request::Cancel { connection, reply } => {
                let _ = reply.send(self.links.cancel_handoff(connection));
            }
            Request::Seat {
                connection,
                id,
                release,
                loaded_only,
                reply,
            } => {
                let exists = self
                    .routes
                    .get(&connection)
                    .is_some_and(|owner| *owner == id);
                let loaded = !loaded_only
                    || self
                        .rooms
                        .get(&id)
                        .is_some_and(|room| room.member_loaded(connection));
                if exists && loaded {
                    let resolved = self.resolve(connection)?;
                    if release && resolved.is_some() {
                        if self.links.cancel_handoff(connection) {
                            self.retire(connection)?;
                        } else {
                            self.send_home(connection, now)?;
                        }
                    }
                    let _ = reply.send(resolved.is_some());
                } else {
                    let _ = reply.send(false);
                }
            }
            Request::StartCustomMode {
                connection,
                id,
                reply,
            } => {
                let mut started = false;
                if self.routes.get(&connection) == Some(&id) && self.resolve(connection)?.is_some()
                {
                    self.room_work(id, RoomStage::Loading, Some(connection), |room| {
                        let loaded = room.member_loaded(connection);
                        if let Some(world) = room.world.as_mut().filter(|_| loaded) {
                            started = world.scene.request_custom_mode_start();
                        }
                        Ok(())
                    })?;
                }
                let _ = reply.send(started);
            }
            Request::MoveCustomSeat {
                connection,
                id,
                lobby_team,
                slot,
                spectator_capacity,
                reply,
            } => {
                let mut moved = None;
                if self.routes.get(&connection) == Some(&id) && self.resolve(connection)?.is_some()
                {
                    let schemas = Arc::clone(&self.schemas);
                    self.room_work(id, RoomStage::Loading, Some(connection), |room| {
                        moved = room.move_custom_seat(
                            connection,
                            lobby_team,
                            slot,
                            spectator_capacity,
                            &schemas,
                        )?;
                        Ok(())
                    })?;
                }
                let _ = reply.send(moved);
            }
            Request::Skin {
                connection,
                id,
                hero,
                skin,
                reply,
            } => {
                if self.routes.get(&connection) == Some(&id) && self.resolve(connection)?.is_some()
                {
                    let tick = self.clock.frame();
                    self.room_work(id, RoomStage::Skin, Some(connection), |room| {
                        let room_tick = room.sim_tick(tick);
                        if let (Some(world), Some(member)) =
                            (&mut room.world, room.members.get(&connection))
                        {
                            world.cards[member.slot].skins.insert(hero, skin);
                            if let Some(peer) = room
                                .members
                                .get_mut(&connection)
                                .and_then(|member| member.peer.as_mut())
                            {
                                peer.world.queue_skin(hero, room_tick, now);
                            }
                        }
                        Ok(())
                    })?;
                }
                let _ = reply.send(());
            }
            #[cfg(feature = "lua-debug")]
            Request::ReloadAiScript { source, reply } => {
                let result = self.reload_patrol(&source);
                let _ = reply.send(result);
            }
            Request::AiPolicyStatus { reply } => {
                let _ = reply.send(self.ai_policy_status());
            }
            Request::SetCareerRecorder { recorder, reply } => {
                self.career_recorder = Some(recorder);
                let _ = reply.send(());
            }
            #[cfg(feature = "lua-mods")]
            Request::SetModHost { host, reply } => {
                self.mod_host = Some(Arc::clone(&host));
                for room in self.rooms.values_mut() {
                    if let Some(world) = &mut room.world {
                        let heroes = world.scene.heroes_arc();
                        let rays = world.scene.combat_rays_arc();
                        world
                            .scene
                            .simulation_mut()
                            .set_mod_world_assets(heroes, rays);
                        world.scene.simulation_mut().attach_mod_host(
                            Arc::clone(&host),
                            room.map.map_guid,
                            room.map.mode_guid,
                        );
                    }
                }
                let _ = reply.send(());
            }
            #[cfg(feature = "lua-mods")]
            Request::ModChat {
                id,
                connection,
                sender_name,
                text,
                reply,
            } => {
                let tick = self.clock.frame();
                let replies = if let Some(room) = self.rooms.get_mut(&id) {
                    let room_tick = room.sim_tick(tick);
                    let room_now = room.sim_now(now);
                    let sender_owner = room
                        .members
                        .get(&connection)
                        .and_then(|m| room.players.get(m.slot))
                        .map(|p| p.entity);
                    if let Some(world) = &mut room.world {
                        world.scene.simulation_mut().mod_dispatch_chat_from(
                            sender_owner,
                            &sender_name,
                            &text,
                            room_tick,
                            room_now,
                        )
                    } else {
                        Vec::new()
                    }
                } else {
                    Vec::new()
                };
                let _ = reply.send(replies);
            }
            #[cfg(feature = "lua-mods")]
            Request::DrainModChat { id, reply } => {
                let replies = self
                    .rooms
                    .get_mut(&id)
                    .and_then(|room| room.world.as_mut())
                    .map(|world| world.scene.simulation_mut().drain_mod_chat_replies())
                    .unwrap_or_default();
                let _ = reply.send(replies);
            }
            #[cfg(any(test, feature = "inspector"))]
            Request::Debug { reply } => {
                let result = self.debug_snapshot();
                match result {
                    Ok(snapshot) => {
                        let _ = reply.try_send(Ok(snapshot));
                    }
                    Err(error) => {
                        let _ = reply.try_send(Err(io::Error::other("game worker routing failed")));
                        return Err(error);
                    }
                }
            }
            #[cfg(any(test, feature = "inspector"))]
            Request::DebugControl { id, command, reply } => {
                let result = self.handle_debug_control(id, command);
                let _ = reply.try_send(result);
            }
            #[cfg(test)]
            Request::Inject {
                id,
                stage,
                fault,
                reply,
            } => {
                let found = self
                    .rooms
                    .get_mut(&id)
                    .map(|room| {
                        room.injection = Some((stage, fault));
                    })
                    .is_some();
                let _ = reply.send(found);
            }
            #[cfg(test)]
            Request::Faults { reply } => {
                let _ = reply.send(self.recent_faults.iter().cloned().collect());
            }
        }
        Ok(())
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(super) fn handle_debug_control(
        &mut self,
        id: MatchId,
        command: game_debug::RoomControlCommand,
    ) -> io::Result<game_debug::RoomControlStatus> {
        if !self.debug_control_enabled {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "room debug control is disabled",
            ));
        }
        if self.clock.draining() {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "game clock is draining",
            ));
        }
        let Some(room) = self.rooms.get_mut(&id).filter(|room| room.world.is_some()) else {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "active room not found",
            ));
        };
        room.debug_control.apply(command)
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(super) fn debug_snapshot(&mut self) -> Result<game_debug::Snapshot, WorkerError> {
        let control_enabled = self.debug_control_enabled;
        let ids: Vec<_> = self.rooms.keys().copied().collect();
        let mut rooms = BTreeMap::new();
        let mut truncated = false;
        for id in ids {
            if rooms.len() == MAX_DEBUG_ROOMS {
                truncated = true;
                break;
            }
            let result = self.room_work(id, RoomStage::Debug, None, |room| {
                let Some(world) = &room.world else {
                    return Ok(None);
                };
                let bots = world.scene.bot_actors()?;
                let truncated = bots.len() > MAX_DEBUG_ACTORS;
                let mut actors: Vec<_> = bots.take(MAX_DEBUG_ACTORS).collect();
                actors.sort_unstable_by_key(|actor| actor.entity);
                let simulation = world.scene.simulation();
                let physics = simulation.debug_physics()?;
                let navigation = simulation.debug_navigation()?;
                let (scripts, scripts_truncated) = simulation.debug_scripts()?;
                Ok(Some((
                    game_debug::Room {
                        id: format!("{:016X}{:016X}", id[0], id[1]),
                        map: format!("{:016X}", room.map.map_guid),
                        name: room.map.name.clone(),
                        actors,
                        encounter: simulation.encounter_debug(),
                        ai_generation: Some(world.patrol_generation),
                        control: control_enabled.then(|| room.debug_control.status()),
                        physics,
                        navigation,
                        scripts,
                        scripts_truncated,
                    },
                    truncated,
                )))
            })?;
            if let Some(Some((room, clipped))) = result {
                rooms.insert(id, room);
                truncated |= clipped;
            }
        }
        let faults = self
            .recent_faults
            .iter()
            .map(|fault| game_debug::RoomFault {
                room: format!("{:016X}{:016X}", fault.id[0], fault.id[1]),
                stage: fault.stage.name(),
                connection: fault.connection,
                tick: fault.tick,
                reason: fault.reason,
            })
            .collect();
        Ok(game_debug::Snapshot {
            tick: self.clock.frame(),
            rooms: rooms.into_values().collect(),
            truncated,
            control_enabled,
            timing: self.timing.clone(),
            ai_policy: Some(game_debug::AiPolicy {
                generation: self.patrol_reload_generation,
                abi_version: self.patrol_policy_abi(),
                digest: patrol_script::format_digest(&self.patrol_policy_digest),
            }),
            faults,
        })
    }

    pub(super) fn ai_policy_status(&self) -> AiPolicyStatus {
        let active_rooms = self
            .rooms
            .values()
            .filter(|room| room.world.is_some())
            .count();
        AiPolicyStatus {
            generation: self.patrol_reload_generation,
            abi_version: self.patrol_policy_abi(),
            digest: patrol_script::format_digest(&self.patrol_policy_digest),
            active_rooms,
        }
    }

    fn patrol_policy_abi(&self) -> u32 {
        #[cfg(feature = "lua-debug")]
        if self.active_patrol_source.is_some() {
            return crate::ai_script::AI_ABI_VERSION;
        }
        patrol_script::STATESCRIPT_ABI
    }

    #[cfg(feature = "lua-debug")]
    pub(super) fn reload_patrol(&mut self, source: &str) -> io::Result<AiReloadReport> {
        self.reload_patrol_with(source, |_, candidate| {
            PatrolScript::from_source_checked(candidate)
        })
    }

    #[cfg(feature = "lua-debug")]
    pub(super) fn reload_patrol_with(
        &mut self,
        source: &str,
        mut compile: impl FnMut(usize, &str) -> Result<PatrolScript, AiScriptError>,
    ) -> io::Result<AiReloadReport> {
        // Phase 1: pre-flight source checks, digest calculation, and idempotency.
        crate::ai_script::validate_ai_source_bounds(source).map_err(io::Error::from)?;
        let digest: [u8; 32] = Sha256::digest(source.as_bytes()).into();
        let mut active_ids: Vec<MatchId> = self
            .rooms
            .iter()
            .filter_map(|(&id, room)| room.world.as_ref().map(|_| id))
            .collect();
        active_ids.sort_unstable();
        if digest == self.patrol_policy_digest {
            return Ok(AiReloadReport {
                generation: self.patrol_reload_generation,
                abi_version: crate::ai_script::AI_ABI_VERSION,
                digest: patrol_script::format_digest(&digest),
                active_rooms: active_ids.len(),
                changed: false,
            });
        }

        // Phase 2: disposable VM smoke validation (keeps staged VMs at initial Lua state).
        let smoke = PatrolScript::from_source_checked(source).map_err(io::Error::from)?;
        smoke.smoke_check().map_err(io::Error::from)?;
        drop(smoke);

        // Phase 3: stage preloaded + fresh per-room VMs before touching any live state.
        let next_preloaded = compile(0, source).map_err(io::Error::from)?;
        let mut staged = Vec::with_capacity(active_ids.len());
        for (offset, &id) in active_ids.iter().enumerate() {
            let script = compile(offset + 1, source).map_err(io::Error::from)?;
            staged.push((id, script));
        }

        // Phase 4: infallible atomic swap.
        let active_source: Arc<str> = Arc::from(source);
        let next_generation = self.patrol_reload_generation + 1;
        self.active_patrol_source = Some(active_source);
        self.preloaded_patrol = Some(next_preloaded);
        self.patrol_policy_digest = digest;
        self.patrol_reload_generation = next_generation;
        let mut swapped = 0usize;
        for (id, script) in staged {
            if let Some(world) = self.rooms.get_mut(&id).and_then(|room| room.world.as_mut()) {
                world.patrol = script;
                world.patrol_generation = next_generation;
                swapped += 1;
            }
        }
        Ok(AiReloadReport {
            generation: next_generation,
            abi_version: crate::ai_script::AI_ABI_VERSION,
            digest: patrol_script::format_digest(&digest),
            active_rooms: swapped,
            changed: true,
        })
    }

    pub(super) fn retire(&mut self, connection: u32) -> Result<(), WorkerError> {
        let Some(id) = self.resolve(connection)? else {
            return Ok(());
        };
        if self
            .rooms
            .iter()
            .any(|(&other, room)| other != id && room.members.contains_key(&connection))
        {
            return Err(WorkerError::Routing(
                "retired connection belongs to conflicting rooms",
            ));
        }
        let result = self.room_work(id, RoomStage::Retire, Some(connection), |room| {
            room.retire_member(connection)
        })?;
        if result.is_some() {
            self.links.revoke(connection);
            self.routes.remove(&connection);
            self.remove_order(connection);
            if self
                .rooms
                .get(&id)
                .is_some_and(|room| room.members.is_empty())
            {
                self.rooms.remove(&id);
            }
        }
        Ok(())
    }

    pub(super) fn receive(
        &mut self,
        socket: &UdpSocket,
        bytes: &[u8],
        address: SocketAddr,
        now: Duration,
    ) -> Result<(), WorkerError> {
        let Ok(received) = self.links.receive(bytes, address, now) else {
            return Ok(());
        };
        let connection = received.connection;
        let Some(id) = self.resolve(connection)? else {
            return Ok(());
        };
        let schemas = Arc::clone(&self.schemas);
        let tick = self.clock.frame();
        let draining = self.clock.draining();
        let result = self.room_work(id, RoomStage::Receive, Some(connection), |room| {
            let room_tick = room.sim_tick(tick);
            room.receive(connection, &received, &schemas, room_tick, now, draining)
        })?;
        if result.is_none() {
            return Ok(());
        }
        if let Some(control) = received.control {
            match control {
                Ok(control) => {
                    if let Err(error) = send(socket, control) {
                        if !matches!(
                            error.kind(),
                            io::ErrorKind::WouldBlock
                                | io::ErrorKind::Interrupted
                                | io::ErrorKind::ConnectionReset
                                | io::ErrorKind::ConnectionRefused
                        ) {
                            return Err(WorkerError::Transport(error));
                        }
                    }
                }
                Err(error) => {
                    self.finish::<()>(
                        id,
                        RoomStage::Receive,
                        Some(connection),
                        Err(RoomError::Link(error)),
                    )?;
                    return Ok(());
                }
            }
        }
        if received.closed {
            eprintln!("game peer {connection:08X} closed by client");
            return self.retire(connection);
        }
        if received.admitted {
            self.order.insert(connection, ());
        }
        if draining && received.admitted {
            self.send_home(connection, now)?;
        }
        Ok(())
    }

    pub(super) fn advance_input(&mut self, now: Duration) -> Result<bool, WorkerError> {
        let ordered: Vec<_> = self.order.keys().copied().collect();
        if ordered.is_empty() || self.clock.draining() {
            return Ok(false);
        }
        let start = self
            .next_input_peer
            .and_then(|next| ordered.iter().position(|&id| id == next))
            .unwrap_or(0);
        let tick = self.clock.frame();
        let mut remaining = INPUT_PER_TURN;
        let mut peer_limit_hits = 0;
        for offset in 0..ordered.len() {
            let connection = ordered[(start + offset) % ordered.len()];
            if !self.order.contains_key(&connection) {
                continue;
            }
            let Some(id) = self.resolve(connection)? else {
                continue;
            };
            let has_work = self
                .rooms
                .get(&id)
                .filter(|room| !room.is_paused())
                .and_then(|room| room.members.get(&connection))
                .and_then(|member| member.peer.as_ref())
                .is_some_and(|peer| peer.world.has_input_work());
            if !has_work {
                continue;
            }
            if let Some(index) = self.order.get_index_of(&connection) {
                self.next_input_peer = self
                    .order
                    .get_index((index + 1) % self.order.len())
                    .map(|(&id, _)| id);
            }
            let budget = remaining.min(INPUT_PER_PEER);
            let result = self.room_work(id, RoomStage::Input, Some(connection), |room| {
                let room_tick = room.sim_tick(tick);
                let room_now = room.sim_now(now);
                room.input(connection, room_tick, room_now, budget)
            })?;
            let Some((used, leave)) = result else {
                // A failed turn still consumes its reservation; repeated faults
                // cannot evade the worker's global work bound.
                remaining -= budget;
                if remaining == 0 {
                    break;
                }
                continue;
            };
            remaining -= used;
            if self.timing.is_some() && used == INPUT_PER_PEER {
                peer_limit_hits += 1;
            }
            if leave {
                self.send_home(connection, now)?;
            }
            if remaining == 0 {
                break;
            }
        }
        let backlog = self
            .rooms
            .values()
            .filter(|room| !room.is_paused())
            .flat_map(|room| room.members.values())
            .filter_map(|member| member.peer.as_ref())
            .any(|peer| peer.world.has_input_work());
        if let Some(timing) = &mut self.timing {
            timing.record_input(
                INPUT_PER_TURN - remaining,
                peer_limit_hits,
                remaining == 0,
                backlog,
            );
        }
        Ok(backlog)
    }

    fn send_home(&mut self, connection: u32, now: Duration) -> Result<(), WorkerError> {
        let Some(id) = self.resolve(connection)? else {
            return Ok(());
        };
        if self.links.begin_leaving(connection, now) {
            let schemas = Arc::clone(&self.schemas);
            self.room_work(id, RoomStage::Loading, Some(connection), |room| {
                room.with_peer(connection, |_, peer, _| {
                    peer.reliable(&schemas, leave_message())?;
                    Ok(())
                })
            })?;
        }
        Ok(())
    }

    pub(super) fn world_started(&self) -> bool {
        self.rooms
            .values()
            .flat_map(|room| room.members.values())
            .filter_map(|member| member.peer.as_ref())
            .any(|peer| peer.world.world_started())
    }

    pub(super) fn tick(
        &mut self,
        socket: &UdpSocket,
        now: Duration,
        missed: u64,
    ) -> Result<(), WorkerError> {
        let (tick, send_frames, drain, force) =
            match self.clock.step(missed, !self.routes.is_empty()) {
                Step::Simulate { frame, send_world } => (frame, send_world, false, false),
                Step::Drain {
                    frame,
                    entered,
                    force,
                    send_reliable,
                } => {
                    if entered {
                        let ids: Vec<_> = self.rooms.keys().copied().collect();
                        for id in ids {
                            self.room_work(id, RoomStage::Drain, None, |room| {
                                #[cfg(any(test, feature = "inspector"))]
                                room.debug_control.reset_on_drain();
                                if let Some(world) = &mut room.world {
                                    world.scene.clear_projectiles();
                                    world.scene.simulation_mut().clear_players();
                                }
                                for peer in room
                                    .members
                                    .values_mut()
                                    .filter_map(|member| member.peer.as_mut())
                                {
                                    peer.clear_world();
                                }
                                Ok(())
                            })?;
                        }
                        let connections: Vec<_> = self.order.keys().copied().collect();
                        for connection in connections {
                            self.send_home(connection, now)?;
                        }
                    }
                    (frame, send_reliable, true, force)
                }
            };
        let expired: Vec<_> = self
            .links
            .expire(now, force)
            .into_iter()
            .map(|expired| {
                (
                    expired.connection,
                    self.routes.get(&expired.connection).copied(),
                    expired.reason,
                )
            })
            .collect();
        for (connection, owner, reason) in expired {
            if owner.is_some_and(|id| !self.rooms.contains_key(&id))
                && !self.routes.contains_key(&connection)
            {
                continue; // An earlier fault in this sweep retired the whole room.
            }
            if !force {
                eprintln!("game peer {connection:08X} expired: {reason:?}");
            }
            self.retire(connection)?;
        }
        #[cfg(any(test, feature = "inspector"))]
        let room_plans: TickPlans = if !drain {
            self.rooms
                .iter_mut()
                .map(|(&id, room)| (id, room.debug_control.plan_tick(tick, now, send_frames)))
                .collect()
        } else {
            HashMap::new()
        };
        #[cfg(not(any(test, feature = "inspector")))]
        #[allow(clippy::let_unit_value)]
        let room_plans: TickPlans = ();
        let mut spawned = HashMap::new();
        let mut created = HashMap::new();
        let mut scripted_health = HashMap::new();
        let mut retired_enemies = HashMap::new();
        let mut player_events = HashMap::new();
        let mut observer_player_health = HashMap::new();
        if !drain {
            #[cfg(any(test, feature = "inspector"))]
            if room_plans.values().any(|plan| plan.stepped) {
                let connections: Vec<_> = self.order.keys().copied().collect();
                for connection in connections {
                    if !self.order.contains_key(&connection) {
                        continue;
                    }
                    let Some(id) = self.resolve(connection)? else {
                        continue;
                    };
                    let Some(&plan) = room_plans.get(&id).filter(|plan| plan.stepped) else {
                        continue;
                    };
                    let has_work = self
                        .rooms
                        .get(&id)
                        .and_then(|room| room.members.get(&connection))
                        .and_then(|member| member.peer.as_ref())
                        .is_some_and(|peer| peer.world.has_input_work());
                    if !has_work {
                        continue;
                    }
                    let result =
                        self.room_work(id, RoomStage::Input, Some(connection), |room| {
                            room.input(connection, plan.tick, plan.now, INPUT_PER_PEER)
                        })?;
                    if matches!(result, Some((_, true))) {
                        self.send_home(connection, now)?;
                    }
                }
            }
            // Preserve the existing global phase order: movement, projectiles,
            // respawn/history, then loading and delivery.
            let ids: Vec<_> = self.rooms.keys().copied().collect();
            for id in ids {
                let (simulate, room_tick, room_now, _) =
                    room_tick_plan(&room_plans, id, tick, now, drain, send_frames);
                if !simulate {
                    continue;
                }
                self.room_work(id, RoomStage::Bots, None, |room| {
                    room.advance_custom_mode(room_now)?;
                    if let Some(world) = &mut room.world {
                        world.scene.step_bots(room_tick, &world.patrol)?;
                    }
                    Ok(())
                })?;
            }
            let connections: Vec<_> = self.order.keys().copied().collect();
            let schemas = Arc::clone(&self.schemas);
            for connection in connections {
                if !self.order.contains_key(&connection) {
                    continue;
                }
                let Some(id) = self.resolve(connection)? else {
                    continue;
                };
                let (simulate, room_tick, room_now, _) =
                    room_tick_plan(&room_plans, id, tick, now, drain, send_frames);
                if !simulate {
                    continue;
                }
                self.room_work(id, RoomStage::Projectiles, Some(connection), |room| {
                    room.with_peer(connection, |room, peer, slot| {
                        let Some(world) = &mut room.world else {
                            return Ok(());
                        };
                        let player = &room.players[slot];
                        if !player.gone && peer.world.world_started() {
                            let encounter_hero = world
                                .scene
                                .simulation()
                                .player_body_by_owner(peer.world.owner())
                                .map(|state| state.hero);
                            world.scene.try_step_encounter(room_tick, encounter_hero)?;
                            peer.world.queue_encounter_presentation(&world.scene)?;
                            peer.advance_payload_voice(&schemas, player.info.team, room_now)?;
                            peer.world.queue_custom_mode_presentation(
                                &world.scene,
                                room_tick,
                                player.info.team,
                                room_now,
                            )?;
                            peer.world.advance_controller_ack(room_now)?;
                        }
                        let activity = world.scene.owner_room_activity();
                        if let Some(body_id) = peer.world.active_body_id() {
                            world
                                .scene
                                .simulation_mut()
                                .set_player_activity(body_id, activity);
                        }
                        let context = ShotContext {
                            team: player.info.team,
                            quanta: player.quanta(),
                            tick: room_tick,
                            now: room_now,
                        };
                        if let Some((source, pulses)) = peer
                            .world
                            .advance_fields(world.scene.simulation_mut(), room_tick)?
                        {
                            let (simulation, queries) = world.scene.bot_simulation();
                            simulation.heal_allies(
                                &queries,
                                context.team,
                                Some(source.entity),
                                Some(source),
                                &pulses,
                            )?;
                        }
                        peer.world
                            .advance_biotic_views(world.scene.simulation(), room_tick)?;
                        let Some((owner, launches)) = peer
                            .world
                            .take_projectile_launches(world.scene.simulation_mut())
                        else {
                            world.scene.retire_projectiles(connection);
                            return Ok(());
                        };
                        let (simulation, queries) = world.scene.bot_simulation();
                        let (outcomes, hits) = simulation.advance_projectiles(
                            &queries,
                            connection,
                            owner,
                            launches,
                            context,
                            |entity| peer.world.actor_contact_ready(entity),
                        )?;
                        peer.world.finish_projectiles(owner, room_tick, outcomes)?;
                        simulation.deliver_coalesced_hits(peer.world.owner(), &hits);
                        for hit in hits {
                            peer.bot_hit(hit);
                        }
                        Ok(())
                    })
                })?;
            }
            let ids: Vec<_> = self.rooms.keys().copied().collect();
            for id in ids {
                let (simulate, room_tick, room_now, _) =
                    room_tick_plan(&room_plans, id, tick, now, drain, send_frames);
                if !simulate {
                    continue;
                }
                let result = self.room_work(id, RoomStage::FinishTick, None, |room| {
                    room.finish_simulation(room_tick, room_now)
                })?;
                if let Some(recorder) = &self.career_recorder {
                    if let Some(room) = self.rooms.get_mut(&id) {
                        if let Some(world) = &mut room.world {
                            let records =
                                world.scene.simulation_mut().take_completed_career_records();
                            for (owner, record) in records {
                                if let Some(player) =
                                    room.players.iter().find(|p| p.entity == owner)
                                {
                                    recorder(&player.info.name, &record);
                                }
                            }
                        }
                    }
                }
                if let Some(result) = result {
                    spawned.insert(id, result.replacements);
                    created.insert(id, result.created);
                    scripted_health.insert(id, result.health);
                    retired_enemies.insert(id, result.retired);
                    player_events.insert(id, result.player_events);
                    observer_player_health.insert(id, result.observer_player_health);
                }
            }
        }
        let connections: Vec<_> = self.order.keys().copied().collect();
        for connection in connections {
            if !self.order.contains_key(&connection) {
                continue;
            }
            let Some(id) = self.resolve(connection)? else {
                continue;
            };
            let (simulate, room_tick, room_now, send_world) =
                room_tick_plan(&room_plans, id, tick, now, drain, send_frames);
            let schemas = Arc::clone(&self.schemas);
            let loaded = self.room_work(id, RoomStage::Loading, Some(connection), |room| {
                room.with_peer(connection, |room, peer, slot| {
                    let mut leave = false;
                    let encounter_finished = peer.world.encounter_screens_delivered()
                        && room.world.as_ref().is_some_and(|world| {
                            world.scene.simulation().encounter_screens_complete()
                        });
                    if simulate && encounter_finished {
                        if let Some(world) = &mut room.world {
                            peer.clear_world_in_room(world.scene.simulation_mut());
                            world.scene.retire_projectiles(connection);
                        } else {
                            peer.clear_world();
                        }
                        peer.loading = MapReadiness::default();
                        leave = true;
                    } else if simulate {
                        if let Err(error) =
                            peer.advance_loading(&schemas, room, slot, now, room_tick)
                        {
                            // Missing structural prerequisites are room faults. Other
                            // preparation errors keep the existing peer leave policy.
                            match error {
                                RoomError::Operation(error) => {
                                    eprintln!(
                                        "Game loading for connection {connection} stopped: {error}"
                                    );
                                    if let Some(world) = &mut room.world {
                                        peer.clear_world_in_room(world.scene.simulation_mut());
                                        world.scene.retire_projectiles(connection);
                                    } else {
                                        peer.clear_world();
                                    }
                                    peer.loading = MapReadiness::default();
                                    leave = true;
                                }
                                error => return Err(error),
                            }
                        }
                    }
                    if let Some(records) = created.get(&id) {
                        for record in records {
                            peer.world.create_bot(record.clone());
                        }
                    }
                    if let Some(events) = player_events.get(&id) {
                        for event in events {
                            match event {
                                crate::game_combat::PlayerBodyEvent::Created {
                                    owner,
                                    id: body_id,
                                    update,
                                } if *owner != peer.world.owner() => {
                                    peer.world
                                        .create_remote_player(*body_id, update.as_ref().clone());
                                }
                                crate::game_combat::PlayerBodyEvent::Retired {
                                    owner,
                                    id: body_id,
                                } if *owner != peer.world.owner() => {
                                    peer.world.retire_remote_player(*body_id);
                                }
                                _ => {}
                            }
                        }
                    }
                    if let Some(records) = observer_player_health.get(&id) {
                        for (owner, record) in records {
                            if *owner != peer.world.owner() {
                                peer.world.queue_health(record.clone());
                            }
                        }
                    }
                    // Settled room health takes precedence over earlier owner snapshots.
                    if let Some(records) = scripted_health.get(&id) {
                        for record in records {
                            peer.world.queue_health(record.clone());
                        }
                    }
                    // A halt only carries health still pending at the room's
                    // event drain, after earlier combat health records.
                    if let Some(events) = player_events.get(&id) {
                        for event in events {
                            if let crate::game_combat::PlayerBodyEvent::ScriptHalted {
                                owner,
                                id,
                                health,
                            } = event
                            {
                                peer.world.player_script_halted(*owner, *id, health.clone());
                            }
                        }
                    }
                    if let Some(records) = retired_enemies.get(&id) {
                        for &entity in records {
                            peer.world.retire_enemy(entity);
                        }
                    }
                    if let Some(replacements) =
                        spawned.get(&id).filter(|_| peer.world.world_started())
                    {
                        let expires = room_now.saturating_add(SILENCE_TIME);
                        for replacement in replacements {
                            peer.queue_bot_replacement(replacement.clone(), expires);
                        }
                    }
                    Ok(leave)
                })
            })?;
            let Some(leave) = loaded else { continue };
            if leave {
                self.send_home(connection, now)?;
            }
            if !send_frames || !self.rooms.contains_key(&id) {
                continue;
            }
            // Sender borrows only this connection, never the routing map. It is
            // discarded and revoked along with the room on a caught panic.
            let Some(sender) = self.links.sender(connection) else {
                return Err(WorkerError::Routing("scheduled peer has no active link"));
            };
            let Some(room) = self.rooms.get_mut(&id) else {
                continue;
            };
            let mut sender = sender;
            let reliable_only = drain || !send_world;
            let frame_tick = if drain { tick } else { room_tick };
            let sent = guard_room(room, RoomStage::Send, |room| {
                room.with_peer(connection, |room, peer, _| {
                    if !peer.open {
                        return Ok(());
                    }
                    if !reliable_only {
                        let simulation = room.world.as_ref().map(|world| world.scene.simulation());
                        peer.flush_combat(&schemas, room_now, simulation)?;
                    }
                    let movements = if reliable_only {
                        Vec::new()
                    } else {
                        match &room.world {
                            Some(world) => peer.world.movement(&world.scene, frame_tick)?,
                            None => Vec::new(),
                        }
                    };
                    let simulation = if reliable_only {
                        None
                    } else {
                        room.world
                            .as_mut()
                            .map(|world| world.scene.simulation_mut())
                    };
                    match peer.world.send_frame(
                        frame_tick,
                        movements,
                        reliable_only,
                        simulation,
                        &mut peer.egress,
                        |payload| {
                            let packet = Packet {
                                conn: connection,
                                kind: KIND_FRAME,
                                flags: ACK,
                                payload,
                                ..Packet::default()
                            };
                            let outgoing = sender.transmit(packet).map_err(RoomError::Link)?;
                            send(socket, outgoing).map_err(RoomError::Transport)
                        },
                    ) {
                        Ok(_) => Ok(()),
                        Err(SendError::Select(error)) => Err(RoomError::Frame(error)),
                        Err(SendError::Transport(RoomError::Transport(error)))
                            if matches!(
                                error.kind(),
                                io::ErrorKind::WouldBlock
                                    | io::ErrorKind::Interrupted
                                    | io::ErrorKind::ConnectionReset
                                    | io::ErrorKind::ConnectionRefused
                            ) =>
                        {
                            Ok(())
                        }
                        Err(SendError::Transport(error)) => Err(error),
                    }
                })
            });
            self.finish(id, RoomStage::Send, Some(connection), sent)?;
        }
        self.clock.renew_if_idle(!self.routes.is_empty(), false);
        Ok(())
    }
}
