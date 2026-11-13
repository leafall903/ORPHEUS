//! Shared routing and scheduling. All mutable gameplay belongs to a room.

use super::*;
use crate::game_egress::SendError;

const MAX_RECENT_FAULTS: usize = 64;

impl State {
    pub(super) fn new(schemas: Arc<Schemas>, patrol: PatrolScript) -> Self {
        Self {
            schemas,
            preloaded_patrol: Some(patrol),
            clock: GameClock::default(),
            timing: None,
            links: GameConnections::default(),
            rooms: HashMap::new(),
            routes: HashMap::new(),
            order: IndexMap::new(),
            next_connection: 1,
            next_input_peer: None,
            recent_faults: VecDeque::new(),
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
        let mut room = Room {
            map,
            players: players
                .into_iter()
                .enumerate()
                .map(|(slot, info)| Player {
                    info,
                    entity: player_entity(slot),
                    gone: false,
                })
                .collect(),
            members: IndexMap::new(),
            world: None,
            #[cfg(test)]
            injection: None,
        };
        let preloaded = world.as_ref().and_then(|_| self.preloaded_patrol.take());
        let schemas = Arc::clone(&self.schemas);
        let prepared = guard_room(&mut room, RoomStage::Admission, |room| {
            let initial = room.message(0, true)?;
            let bytes = encode(&schemas, initial.id, &initial.fields)?;
            let payload = frame(u32::MAX, u32::MAX, &[(0, bytes)], &[], &[])?;
            if payload.len() > MAX_PAYLOAD {
                return Err(invalid("match roster exceeds the game datagram budget").into());
            }
            if let Some(WorldStart { scene, card }) = world {
                let patrol = match preloaded {
                    Some(patrol) => patrol,
                    None => PatrolScript::load()?,
                };
                room.world = Some(RoomWorld {
                    scene,
                    card,
                    patrol,
                });
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
                reply,
            } => {
                let exists = self
                    .routes
                    .get(&connection)
                    .is_some_and(|owner| *owner == id);
                if exists {
                    let resolved = self.resolve(connection)?;
                    if release && resolved.is_some() && !self.links.cancel_handoff(connection) {
                        self.send_home(connection, now)?;
                    }
                    let _ = reply.send(resolved.is_some());
                } else {
                    let _ = reply.send(false);
                }
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
                        if let Some(world) = &mut room.world {
                            world.card.skins.insert(hero, skin);
                            if let Some(peer) = room
                                .members
                                .get_mut(&connection)
                                .and_then(|member| member.peer.as_mut())
                            {
                                peer.world.queue_skin(hero, tick, now);
                            }
                        }
                        Ok(())
                    })?;
                }
                let _ = reply.send(());
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
    pub(super) fn debug_snapshot(&mut self) -> Result<game_debug::Snapshot, WorkerError> {
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
                let mut truncated = bots.len() > MAX_DEBUG_ACTORS;
                let mut actors: Vec<_> = bots.take(MAX_DEBUG_ACTORS).collect();
                for member in room.members.values() {
                    let player = room
                        .players
                        .get(member.slot)
                        .ok_or(RoomError::Invariant("debug member has invalid roster slot"))?;
                    if let Some(actor) = member
                        .peer
                        .as_ref()
                        .and_then(|peer| peer.world.debug_actor(player.info.team))
                    {
                        if actors.len() == MAX_DEBUG_ACTORS {
                            truncated = true;
                        } else {
                            actors.push(actor);
                        }
                    }
                }
                actors.sort_unstable_by_key(|actor| actor.entity);
                Ok(Some((
                    game_debug::Room {
                        id: format!("{:016X}{:016X}", id[0], id[1]),
                        map: format!("{:016X}", room.map.map_guid),
                        name: room.map.name.clone(),
                        actors,
                        encounter: world.scene.encounter_debug(),
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
            timing: self.timing.clone(),
            faults,
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
            room.receive(connection, &received, &schemas, tick, now, draining)
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
                room.input(connection, tick, now, budget)
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
                                if let Some(world) = &mut room.world {
                                    world.scene.clear_projectiles();
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
                )
            })
            .collect();
        for (connection, owner) in expired {
            if owner.is_some_and(|id| !self.rooms.contains_key(&id))
                && !self.routes.contains_key(&connection)
            {
                continue; // An earlier fault in this sweep retired the whole room.
            }
            self.retire(connection)?;
        }
        let mut spawned = HashMap::new();
        let mut created = HashMap::new();
        let mut scripted_health = HashMap::new();
        let mut retired_enemies = HashMap::new();
        if !drain {
            // Preserve the existing global phase order: movement, projectiles,
            // respawn/history, then loading and delivery.
            let ids: Vec<_> = self.rooms.keys().copied().collect();
            for id in ids {
                self.room_work(id, RoomStage::Bots, None, |room| {
                    if let Some(world) = &mut room.world {
                        world.scene.step_bots(tick, &world.patrol)?;
                    }
                    Ok(())
                })?;
            }
            let connections: Vec<_> = self.order.keys().copied().collect();
            for connection in connections {
                if !self.order.contains_key(&connection) {
                    continue;
                }
                let Some(id) = self.resolve(connection)? else {
                    continue;
                };
                self.room_work(id, RoomStage::Projectiles, Some(connection), |room| {
                    room.with_peer(connection, |room, peer, slot| {
                        let Some(world) = &mut room.world else {
                            return Ok(());
                        };
                        let player = &room.players[slot];
                        if !player.gone && peer.world.world_started() {
                            world
                                .scene
                                .step_encounter(tick, peer.world.encounter_hero());
                            peer.world.queue_encounter_presentation(&world.scene)?;
                        }
                        peer.world
                            .set_encounter_active(world.scene.encounter_active());
                        let context = ShotContext {
                            team: player.info.team,
                            quanta: player.quanta(),
                            tick,
                            now,
                        };
                        let (bots, queries) = world.scene.bot_simulation();
                        for update in bots.heal_bots(
                            &queries,
                            context.team,
                            &peer.world.advance_fields(tick)?,
                        )? {
                            peer.world.queue_health(update);
                        }
                        peer.world.advance_biotic_views(tick)?;
                        let Some((owner, launches)) = peer.world.take_projectile_launches() else {
                            world.scene.retire_projectiles(connection);
                            return Ok(());
                        };
                        let (simulation, queries) = world.scene.bot_simulation();
                        let (outcomes, hits) = simulation
                            .advance_projectiles(&queries, connection, owner, launches, context)?;
                        peer.world.finish_projectiles(owner, tick, outcomes)?;
                        for hit in hits {
                            peer.bot_hit(hit);
                        }
                        Ok(())
                    })
                })?;
            }
            let ids: Vec<_> = self.rooms.keys().copied().collect();
            for id in ids {
                let result = self.room_work(id, RoomStage::FinishTick, None, |room| {
                    room.finish_simulation(tick, now)
                })?;
                if let Some(result) = result {
                    spawned.insert(id, result.replacements);
                    created.insert(id, result.created);
                    scripted_health.insert(id, result.health);
                    retired_enemies.insert(id, result.retired);
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
            let schemas = Arc::clone(&self.schemas);
            let loaded = self.room_work(id, RoomStage::Loading, Some(connection), |room| {
                room.with_peer(connection, |room, peer, slot| {
                    let mut leave = false;
                    let encounter_finished = peer.world.encounter_screens_delivered()
                        && room
                            .world
                            .as_ref()
                            .is_some_and(|world| world.scene.encounter_screens_complete());
                    if !drain && encounter_finished {
                        peer.clear_world();
                        if let Some(world) = &mut room.world {
                            world.scene.retire_projectiles(connection);
                        }
                        peer.loading = MapReadiness::default();
                        leave = true;
                    } else if !drain {
                        if let Err(error) = peer.advance_loading(&schemas, room, slot, now, tick) {
                            // Missing structural prerequisites are room faults. Other
                            // preparation errors keep the existing peer leave policy.
                            match error {
                                RoomError::Operation(error) => {
                                    eprintln!(
                                        "Game loading for connection {connection} stopped: {error}"
                                    );
                                    peer.clear_world();
                                    if let Some(world) = &mut room.world {
                                        world.scene.retire_projectiles(connection);
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
                    if let Some(records) = scripted_health.get(&id) {
                        for record in records {
                            peer.world.queue_health(record.clone());
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
                        peer.combat_ui.replacements.extend(
                            replacements
                                .iter()
                                .cloned()
                                .map(|bot| (now.saturating_add(SILENCE_TIME), bot)),
                        );
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
            let sent = guard_room(room, RoomStage::Send, |room| {
                room.with_peer(connection, |room, peer, _| {
                    if !peer.open {
                        return Ok(());
                    }
                    if !drain {
                        peer.flush_combat(&schemas, now)?;
                    }
                    let movements = if drain {
                        Vec::new()
                    } else {
                        match &room.world {
                            Some(world) => peer.world.movement(&world.scene, tick)?,
                            None => Vec::new(),
                        }
                    };
                    match peer.world.send_frame(
                        tick,
                        movements,
                        drain,
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
