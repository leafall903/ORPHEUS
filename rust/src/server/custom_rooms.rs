use std::{
    collections::{BTreeMap, HashMap},
    io,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};

use super::{Lobby, match_id};
use crate::{
    game_service::{GameService, MatchPlayer},
    jam_codec::{Fields, Value},
    lobby_custom_room::{
        self, BROWSER, BROWSER_OUT, BROWSER_RESULTS, CustomRoom, Member, PreparedRoom, RoomAction,
        browser_filter,
    },
    lobby_practice,
    lobby_session::{LobbySession, PreparedLogin},
    lobby_transport::LobbyMessage,
    match_handoff::{Handoff, LobbySeat, MATCH_LOBBY, enter_game, general_channel, match_channel},
    room_primitives::SPECTATOR_TEAM,
};

type RoomId = [u64; 2];
type SessionId = u64;

#[derive(Default)]
pub(super) struct CustomRooms {
    rooms: BTreeMap<RoomId, Room>,
    peers: HashMap<SessionId, RoomPeer>,
}

struct Room {
    lobby: CustomRoom,
    game: Option<RoomGame>,
}

struct RoomGame {
    seats: BTreeMap<SessionId, Handoff>,
    spectator_capacity: u8,
    exit: Option<CustomRoomExit>,
    next_check: Instant,
}

enum CustomRoomExit {
    Return,
    Restart(Box<PreparedRoom>),
}

pub(super) enum RoomEvent {
    Messages(Arc<[LobbyMessage]>),
    Enter(LobbySeat, Box<[LobbyMessage; 3]>),
}

pub(super) struct RoomPeer {
    pub login: PreparedLogin,
    pub card: Fields,
    pub can_transfer: bool,
    pub outgoing: mpsc::SyncSender<RoomEvent>,
    pub replaced: Arc<AtomicBool>,
}

impl RoomPeer {
    fn send(&self, event: RoomEvent) {
        if self.outgoing.try_send(event).is_err() {
            // A peer that cannot keep up must reconnect to get a complete snapshot.
            self.replaced.store(true, Ordering::Release);
        }
    }
}

impl CustomRooms {
    pub(super) fn register(&mut self, peer: RoomPeer) {
        self.peers.insert(peer.login.identity.session[0], peer);
    }

    fn room_of(&self, session: SessionId) -> Option<RoomId> {
        self.rooms
            .iter()
            .find_map(|(&id, room)| room.lobby.members.contains_key(&session).then_some(id))
    }

    fn online(&self, session: SessionId) -> bool {
        self.peers
            .get(&session)
            .is_some_and(|peer| !peer.replaced.load(Ordering::Acquire))
    }

    fn send(&self, session: SessionId, messages: Arc<[LobbyMessage]>) {
        if let Some(peer) = self.peers.get(&session) {
            peer.send(RoomEvent::Messages(messages));
        }
    }

    fn publish(&self, id: RoomId, messages: Vec<LobbyMessage>) {
        if messages.is_empty() {
            return;
        }
        let messages: Arc<[_]> = messages.into();
        for &session in self.rooms[&id].lobby.members.keys() {
            self.send(session, Arc::clone(&messages));
        }
    }

    pub(super) fn unregister(&mut self, session: SessionId, game: &GameService) {
        self.peers.remove(&session);
        if let Err(error) = self.leave(session, game) {
            eprintln!("Custom room disconnect: {error}");
        }
    }

    pub(super) fn leave(&mut self, session: SessionId, game: &GameService) -> io::Result<()> {
        let Some(id) = self.room_of(session) else {
            return Ok(());
        };
        let room = self.rooms.get_mut(&id).expect("member has a room");
        let closed: Arc<[_]> = Arc::new(room.lobby.close());
        if room.lobby.owner == session {
            let room = self.rooms.remove(&id).expect("owner has a room");
            for &member in room.lobby.members.keys() {
                self.send(member, Arc::clone(&closed));
            }
            if let Some(live) = room.game {
                for seat in live.seats.values() {
                    game.release_seat(seat)?;
                }
            }
        } else {
            room.lobby.members.remove(&session);
            let seat = room
                .game
                .as_mut()
                .and_then(|live| live.seats.remove(&session));
            let roster = room.lobby.roster();
            self.send(session, closed);
            self.publish(id, vec![roster]);
            if let Some(seat) = seat {
                game.release_seat(&seat)?;
            }
        }
        Ok(())
    }

    fn member(&self, session: SessionId, position: lobby_custom_room::Position) -> Member {
        let peer = &self.peers[&session];
        Member {
            card: peer.card.clone(),
            party: peer.login.identity.party_id,
            position,
        }
    }

    fn create(&mut self, session: SessionId, name: &str, game: &GameService) -> io::Result<()> {
        if let Some(id) = self.room_of(session) {
            let room = &self.rooms[&id];
            if room.game.is_some() {
                return Ok(());
            }
            if room.lobby.owner == session {
                self.send(session, room.lobby.messages().into());
                return Ok(());
            }
        }
        let id = match_id()?;
        self.leave(session, game)?;
        let lobby = CustomRoom::new(id, name, session, self.member(session, (0, 0)));
        self.send(session, lobby.messages().into());
        self.rooms.insert(id, Room { lobby, game: None });
        Ok(())
    }

    fn join(&mut self, session: SessionId, id: RoomId, game: &GameService) -> io::Result<()> {
        let Some(room) = self.rooms.get(&id).filter(|room| {
            room.game.is_none() && room.lobby.public() && self.online(room.lobby.owner)
        }) else {
            return Ok(());
        };
        if room.lobby.members.contains_key(&session) {
            self.send(session, room.lobby.messages().into());
            return Ok(());
        }
        if self
            .room_of(session)
            .is_some_and(|old| self.rooms[&old].game.is_some())
        {
            return Ok(());
        }
        let Some(position) = room.lobby.join_position() else {
            return Ok(());
        };
        self.leave(session, game)?;
        let member = self.member(session, position);
        let room = &mut self
            .rooms
            .get_mut(&id)
            .expect("join target was validated")
            .lobby;
        room.members.insert(session, member);
        let mut snapshot = room.messages();
        let roster = snapshot.pop().expect("room snapshot ends in the roster");
        self.send(session, snapshot.into());
        self.publish(id, vec![roster]);
        Ok(())
    }

    fn browser(&self, session: SessionId, kind: u64, names: &[String]) -> LobbyMessage {
        let rows = self
            .rooms
            .values()
            .filter_map(|room| {
                (room.game.is_none()
                    && !room.lobby.members.contains_key(&session)
                    && self.online(room.lobby.owner))
                .then(|| room.lobby.browser_row(names))
                .flatten()
            })
            .collect();
        // The loopback lobby admits at most 32 connections, below one retail page (64).
        (
            BROWSER,
            BROWSER_RESULTS,
            Fields::from([(0x85, Value::Unsigned(kind)), (0x88, Value::Array(rows))]),
        )
    }

    pub(super) fn request(
        &mut self,
        lobby: &Lobby,
        login: &PreparedLogin,
        session: &LobbySession<'_>,
        request: &LobbyMessage,
        seat: Option<&LobbySeat>,
        browser_initialized: &mut bool,
    ) -> io::Result<bool> {
        let actor = login.identity.session[0];
        if !self.online(actor) {
            return Ok(true);
        }
        if request.0 == BROWSER_OUT && (40700..=40703).contains(&request.1) {
            let mut messages = Vec::new();
            if request.1 == 40703 {
                let (kind, names) = browser_filter(&request.2);
                messages.push(self.browser(actor, kind, &names));
            } else {
                messages.push((
                    BROWSER,
                    request.1 - 100,
                    Fields::from([(0x88, Value::Array(vec![]))]),
                ));
                // A reconnect can retain the client's pending All request.
                if request.1 == 40700 && !*browser_initialized {
                    messages.push(self.browser(actor, 0, &[]));
                }
            }
            if session.announces(BROWSER) {
                *browser_initialized |= messages.iter().any(|message| message.1 == BROWSER_RESULTS);
                self.send(actor, messages.into());
            }
            return Ok(true);
        }
        let name = lobby_custom_room::create_name(request);
        let target = lobby_custom_room::join_id(request);
        if name.is_some() || target.is_some() {
            if session.announces(lobby_custom_room::CUSTOM_GAMES) && session.announces(MATCH_LOBBY)
            {
                if let Some(seat) = seat {
                    if lobby.game.seat_exists(&seat.handoff)? {
                        return Ok(true);
                    }
                }
                if let Some(name) = name {
                    self.create(actor, name.unwrap_or(&login.account.name), &lobby.game)?;
                } else if let Some(id) = target {
                    self.join(actor, id, &lobby.game)?;
                }
            }
            return Ok(true);
        }
        let Some(id) = self.room_of(actor) else {
            return Ok(false);
        };
        let room = self.rooms.get_mut(&id).expect("current room");
        if room.lobby.is_leave(request) {
            self.leave(actor, &lobby.game)?;
            return Ok(true);
        }
        if room.game.as_ref().is_some_and(|game| game.exit.is_some()) {
            return Ok(true);
        }
        if lobby_custom_room::is_edit(request) {
            let messages = if lobby_custom_room::is_move(request) {
                let Some((target, team, slot)) = room.lobby.seat_request(actor, &request.2) else {
                    return Ok(true);
                };
                let slot = match &room.game {
                    Some(game) => match game.seats.get(&target) {
                        Some(handoff) => lobby.game.move_custom_seat(
                            handoff,
                            team,
                            slot,
                            game.spectator_capacity,
                        )?,
                        None => None,
                    },
                    None => room.lobby.choose_slot(target, team, slot),
                };
                let Some(slot) = slot else { return Ok(true) };
                room.lobby
                    .members
                    .get_mut(&target)
                    .expect("validated target")
                    .position = (team, slot);
                vec![room.lobby.roster()]
            } else if room.lobby.owner == actor {
                room.lobby.edit(request, room.game.is_some())
            } else {
                Vec::new()
            };
            self.publish(id, messages);
            return Ok(true);
        }
        if let Some(action) = RoomAction::read(request) {
            if room.lobby.owner == actor {
                self.action(lobby, id, action)?;
            }
            return Ok(true);
        }
        Ok(false)
    }

    fn action(&mut self, lobby: &Lobby, id: RoomId, action: RoomAction) -> io::Result<()> {
        let room = self.rooms.get_mut(&id).expect("host room");
        let owner = room.lobby.owner;
        let Some(game) = &mut room.game else {
            if action == RoomAction::Load {
                if let Some(prepared) = prepare(&room.lobby, lobby) {
                    self.start(lobby, id, prepared)?;
                }
            }
            return Ok(());
        };
        let handoff = &game.seats[&owner];
        let exit = match action {
            RoomAction::Load => return Ok(()),
            RoomAction::StartMode => {
                lobby.game.start_custom_mode(handoff)?;
                return Ok(());
            }
            RoomAction::Return => CustomRoomExit::Return,
            RoomAction::Restart => {
                let Some(prepared) = prepare(&room.lobby, lobby) else {
                    return Ok(());
                };
                CustomRoomExit::Restart(Box::new(prepared))
            }
        };
        if lobby.game.release_loaded_seat(handoff)? {
            game.exit = Some(exit);
            for (&player, seat) in &game.seats {
                if player != owner {
                    lobby.game.release_seat(seat)?;
                }
            }
            room.lobby.discard_pending_settings();
            self.publish(id, vec![action.leave_notice()]);
        }
        Ok(())
    }

    fn start(&mut self, lobby: &Lobby, id: RoomId, mut prepared: PreparedRoom) -> io::Result<()> {
        let room = &self.rooms[&id].lobby;
        prepared
            .positions
            .retain(|session, _| room.members.contains_key(session));
        let sessions: Vec<_> = std::iter::once(room.owner)
            .chain(
                room.members
                    .keys()
                    .copied()
                    .filter(|&session| session != room.owner),
            )
            .collect();
        if sessions
            .iter()
            .any(|&session| !self.online(session) || !self.peers[&session].can_transfer)
        {
            return Ok(());
        }
        let mut players = Vec::with_capacity(sessions.len());
        for session in &sessions {
            let login = &self.peers[session].login;
            let (team, slot) = prepared.positions[session];
            let player = MatchPlayer {
                name: login.account.name.clone(),
                team: lobby_custom_room::match_team(team),
                tournament: login.tournament,
                ranked: false,
            };
            let card = lobby_practice::player_card(login, &lobby.assets, &lobby.collection)?;
            players.push((player, card, Some(slot as i8)));
        }
        let match_id = match_id()?;
        let tickets = match lobby.game.open_prepared(match_id, prepared.scene, players) {
            Ok(tickets) => tickets,
            Err(error) => {
                eprintln!("Custom game could not start: {error}");
                return Ok(());
            }
        };
        let room = self.rooms.get_mut(&id).expect("room reserved for match");
        let changed = prepared
            .positions
            .iter()
            .any(|(session, &position)| position != room.lobby.members[session].position);
        room.lobby.apply_positions(prepared.positions);
        room.game = Some(RoomGame {
            seats: sessions.into_iter().zip(tickets).collect(),
            spectator_capacity: prepared.capacity[SPECTATOR_TEAM as usize] as u8,
            exit: None,
            next_check: Instant::now(),
        });
        if changed {
            self.publish(id, vec![self.rooms[&id].lobby.roster()]);
        }
        for (&session, handoff) in &self.rooms[&id].game.as_ref().expect("new room game").seats {
            let messages = enter_game(
                general_channel(),
                match_channel(match_id),
                handoff,
                "127.0.0.1",
                lobby.game.local_addr().port(),
            )?;
            let peer = &self.peers[&session];
            peer.send(RoomEvent::Enter(
                LobbySeat {
                    account_lo: peer.login.account.account_lo,
                    lobby_session: session,
                    handoff: handoff.clone(),
                },
                Box::new(messages),
            ));
        }
        Ok(())
    }

    pub(super) fn poll(
        &mut self,
        lobby: &Lobby,
        session: SessionId,
        can_transfer: bool,
        now: Instant,
    ) -> io::Result<()> {
        self.peers
            .get_mut(&session)
            .expect("logged in")
            .can_transfer = can_transfer;
        let Some(id) = self.room_of(session) else {
            return Ok(());
        };
        let room = self.rooms.get_mut(&id).expect("room member");
        let Some(game) = &mut room.game else {
            return Ok(());
        };
        if now < game.next_check {
            return Ok(());
        }
        game.next_check = now + Duration::from_millis(150);
        for seat in game.seats.values() {
            if lobby.game.seat_exists(seat)? {
                return Ok(());
            }
        }
        let exit = room.game.take().and_then(|game| game.exit);
        room.lobby.fit_seats();
        let messages = room.lobby.messages();
        self.publish(id, messages);
        if let Some(CustomRoomExit::Restart(prepared)) = exit {
            self.start(lobby, id, *prepared)?;
        }
        Ok(())
    }
}

fn prepare(room: &CustomRoom, lobby: &Lobby) -> Option<PreparedRoom> {
    match room.prepare_match(&lobby.maps, &lobby.assets) {
        Ok(prepared) => prepared,
        Err(error) => {
            eprintln!("Custom game could not prepare map: {error}");
            None
        }
    }
}
