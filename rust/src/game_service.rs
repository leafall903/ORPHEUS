//! The native UDP match worker. It owns seats, connections and frame timing.
//!
//! A prepared solo Practice Range scene can advance from loading into its
//! initial world. Maps that need the unported bot VM retain that requirement.

use std::{
    collections::{HashMap, VecDeque},
    fs, io,
    net::SocketAddr,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use indexmap::IndexMap;
use mio::{Events, Interest, Poll, Token, Waker, net::UdpSocket};

use crate::{
    game_clock::{FIRST_TICK, GameClock, Step, next_tick_after},
    game_combat::{BotHit, BotReplacement, ShotContext},
    game_combat_messages::CombatNotice,
    game_connection::{GameConnections, MAX_CONNECTION_ID, Outgoing, SILENCE_TIME},
    game_content::{GameMap, PRACTICE_RANGE_MODE},
    game_controller::ControllerFrame,
    game_egress::GameEgress,
    game_ingress::{GameIngress, MapReadiness, MapReadyStep, MessageKind, WorldReadiness, pong},
    game_instance::{
        Instance, Member as InstanceMember, RETAIL_QUANTA, TOURNAMENT_QUANTA, ownership,
    },
    game_link::{ACK, KIND_FRAME, Packet},
    game_messages::{Message, encode, locate},
    game_replication::{Record, WorldDelivery},
    game_timing::WorkerTiming,
    jam_codec::{Fields, Schemas, Value},
    match_handoff::Handoff,
    match_scene::{PlayerAppearance, PlayerCard},
    patrol_script::PatrolScript,
    scene_assets::PreparedScene,
    script_wire::chunk,
    world::{EntityUpdate, GAME_MODE_ENTITY, MAX_PAYLOAD, SkinTheme, frame},
};

mod peer;
mod room;
mod state;
mod worker;

#[cfg(test)]
mod tests;

pub(crate) use peer::player_entity;
use peer::{Peer, initial_controller, script_record};
use room::{
    FaultRecord, Player, Room, RoomError, RoomMember, RoomStage, RoomWorld, WorkerError,
    WorldStart, guard_room,
};
use state::State;
use worker::{
    GAME_SOCKET, INPUT_PER_PEER, INPUT_PER_TURN, RECEIVE_BATCH, REQUESTS, WorkerOptions,
    leave_message, run, send, wall_nanoseconds,
};

#[cfg(any(test, feature = "inspector"))]
use std::collections::BTreeMap;

#[cfg(any(test, feature = "inspector"))]
use crate::game_debug;

#[cfg(any(test, feature = "inspector"))]
const DEBUG_WAIT: Duration = Duration::from_millis(250);
#[cfg(any(test, feature = "inspector"))]
const MAX_DEBUG_ROOMS: usize = 16;
#[cfg(any(test, feature = "inspector"))]
const MAX_DEBUG_ACTORS: usize = 64;

#[derive(Debug)]
pub struct MatchPlayer {
    pub name: String,
    pub team: u8,
    pub tournament: bool,
    pub ranked: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AiReloadReport {
    pub generation: u64,
    pub abi_version: u32,
    pub digest: String,
    pub active_rooms: usize,
    pub changed: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AiPolicyStatus {
    pub generation: u64,
    pub abi_version: u32,
    pub digest: String,
    pub active_rooms: usize,
}

pub struct GameService {
    address: SocketAddr,
    requests: mpsc::SyncSender<Request>,
    wake: Waker,
    stopping: Arc<AtomicBool>,
    worker: Option<JoinHandle<io::Result<()>>>,
}

type MatchId = [u64; 2];

enum Request {
    Open(Box<OpenRequest>),
    Pending {
        connection: u32,
        reply: mpsc::SyncSender<bool>,
    },
    Cancel {
        connection: u32,
        reply: mpsc::SyncSender<bool>,
    },
    Seat {
        connection: u32,
        id: MatchId,
        release: bool,
        reply: mpsc::SyncSender<bool>,
    },
    Skin {
        connection: u32,
        id: MatchId,
        hero: u64,
        skin: SkinTheme,
        reply: mpsc::SyncSender<()>,
    },
    ReloadAiScript {
        source: String,
        reply: mpsc::SyncSender<io::Result<AiReloadReport>>,
    },
    AiPolicyStatus {
        reply: mpsc::SyncSender<AiPolicyStatus>,
    },
    #[cfg(any(test, feature = "inspector"))]
    Debug {
        reply: mpsc::SyncSender<io::Result<game_debug::Snapshot>>,
    },
    #[cfg(any(test, feature = "inspector"))]
    DebugControl {
        id: MatchId,
        command: game_debug::RoomControlCommand,
        reply: mpsc::SyncSender<io::Result<game_debug::RoomControlStatus>>,
    },
    #[cfg(test)]
    Inject {
        id: MatchId,
        stage: RoomStage,
        fault: room::InjectedFault,
        reply: mpsc::SyncSender<bool>,
    },
    #[cfg(test)]
    Faults {
        reply: mpsc::SyncSender<Vec<FaultRecord>>,
    },
}

struct OpenRequest {
    id: MatchId,
    map: GameMap,
    players: Vec<MatchPlayer>,
    world: Option<WorldStart>,
    reply: mpsc::SyncSender<io::Result<Vec<Handoff>>>,
}

impl GameService {
    pub fn bind(address: SocketAddr, schemas: Arc<Schemas>) -> io::Result<Self> {
        Self::bind_with_timing(address, schemas, false)
    }

    pub(crate) fn bind_with_timing(
        address: SocketAddr,
        schemas: Arc<Schemas>,
        collect_timing: bool,
    ) -> io::Result<Self> {
        Self::bind_worker(
            address,
            schemas,
            collect_timing,
            #[cfg(any(test, feature = "inspector"))]
            false,
        )
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn bind_with_options(
        address: SocketAddr,
        schemas: Arc<Schemas>,
        collect_timing: bool,
        debug_control: bool,
    ) -> io::Result<Self> {
        Self::bind_worker(address, schemas, collect_timing, debug_control)
    }

    fn bind_worker(
        address: SocketAddr,
        schemas: Arc<Schemas>,
        collect_timing: bool,
        #[cfg(any(test, feature = "inspector"))] debug_control: bool,
    ) -> io::Result<Self> {
        for id in [20300, 20301, 20302, 20304, 20306, 21601, 21602, 21610] {
            let (_, crc, _) = locate(id).map_err(invalid)?;
            schemas.fields(crc, id).map_err(invalid)?;
        }

        let mut socket = UdpSocket::bind(address)?;
        let address = socket.local_addr()?;
        let poll = Poll::new()?;
        poll.registry()
            .register(&mut socket, GAME_SOCKET, Interest::READABLE)?;
        let wake = Waker::new(poll.registry(), REQUESTS)?;
        let (requests, receiver) = mpsc::sync_channel(RECEIVE_BATCH);
        let (ready, initialized) = mpsc::sync_channel(1);
        let stopping = Arc::new(AtomicBool::new(false));
        let worker_stopping = Arc::clone(&stopping);
        let worker = thread::Builder::new()
            .name("orpheus-game".into())
            .spawn(move || {
                run(
                    socket,
                    poll,
                    receiver,
                    worker_stopping,
                    schemas,
                    ready,
                    WorkerOptions {
                        collect_timing,
                        #[cfg(any(test, feature = "inspector"))]
                        debug_control,
                    },
                )
            })?;
        initialized.recv().map_err(|_| stopped())??;
        Ok(Self {
            address,
            requests,
            wake,
            stopping,
            worker: Some(worker),
        })
    }

    pub fn local_addr(&self) -> SocketAddr {
        self.address
    }

    pub fn open_match(
        &self,
        id: [u64; 2],
        map: GameMap,
        players: Vec<MatchPlayer>,
    ) -> io::Result<Vec<Handoff>> {
        self.request(|reply| {
            Request::Open(Box::new(OpenRequest {
                id,
                map,
                players,
                world: None,
                reply,
            }))
        })?
    }

    /// Open the currently ported world bootstrap. The graph-backed NPC
    /// behavior from the enriched bundle is a separate simulation prerequisite.
    pub fn open_prepared_practice(
        &self,
        id: [u64; 2],
        scene: PreparedScene,
        player: MatchPlayer,
        card: PlayerCard,
    ) -> io::Result<Handoff> {
        if scene.map().mode_guid != PRACTICE_RANGE_MODE {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "the native world bootstrap requires the solo Practice Range without bot VM graphs",
            ));
        }
        self.open_prepared_solo(id, scene, player, card)
    }

    pub(crate) fn open_prepared_solo(
        &self,
        id: [u64; 2],
        scene: PreparedScene,
        player: MatchPlayer,
        card: PlayerCard,
    ) -> io::Result<Handoff> {
        if !scene.movement_ready() {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "this map cannot start in the native solo worker",
            ));
        }
        if scene.map().is_junkenstein() && scene.encounter_snapshot().is_none() {
            return Err(invalid("Junkenstein world has no initialized encounter"));
        }
        let viewer = PlayerAppearance {
            entity: player_entity(0),
            slot: 0,
            team: player.team,
            card: &card,
        };
        let creates = scene.initial_world(&viewer)?;
        let controller = initial_controller(&scene, FIRST_TICK)?;
        let scripts = [
            script_record(viewer.entity, &controller)?,
            script_record(GAME_MODE_ENTITY, scene.mode_ui())?,
        ];
        for record in creates.iter().chain(&scripts) {
            let packet = frame(u32::MAX, u32::MAX, &[], &[], &[record]).map_err(invalid)?;
            if packet.len() > MAX_PAYLOAD {
                return Err(invalid("a world baseline exceeds the game datagram budget"));
            }
        }
        let map = scene.map().clone();
        let mut handoffs = self.request(|reply| {
            Request::Open(Box::new(OpenRequest {
                id,
                map,
                players: vec![player],
                world: Some(WorldStart {
                    scene: Box::new(scene),
                    card,
                }),
                reply,
            }))
        })??;
        handoffs
            .pop()
            .ok_or_else(|| invalid("a solo world requires a seat"))
    }

    pub fn handoff_pending(&self, connection: u32) -> io::Result<bool> {
        self.request(|reply| Request::Pending { connection, reply })
    }

    pub fn cancel_handoff(&self, connection: u32) -> io::Result<bool> {
        self.request(|reply| Request::Cancel { connection, reply })
    }

    pub fn seat_exists(&self, handoff: &Handoff) -> io::Result<bool> {
        self.seat(handoff, false)
    }

    pub fn release_seat(&self, handoff: &Handoff) -> io::Result<bool> {
        self.seat(handoff, true)
    }

    pub(crate) fn update_skin(
        &self,
        handoff: &Handoff,
        hero: u64,
        skin: SkinTheme,
    ) -> io::Result<()> {
        self.request(|reply| Request::Skin {
            connection: handoff.conn,
            id: handoff.match_id,
            hero,
            skin,
            reply,
        })
    }

    pub fn reload_ai_script(&self, source: impl Into<String>) -> io::Result<AiReloadReport> {
        let source = source.into();
        crate::ai_script::validate_ai_source_bounds(&source).map_err(io::Error::from)?;
        self.request(|reply| Request::ReloadAiScript { source, reply })?
    }

    pub fn reload_ai_script_file(&self, path: &Path) -> io::Result<AiReloadReport> {
        let metadata = fs::metadata(path)?;
        let max_bytes = crate::ai_script::AI_MAX_SOURCE_BYTES;
        if metadata.len() > max_bytes as u64 {
            let bytes = usize::try_from(metadata.len()).unwrap_or(usize::MAX);
            return Err(crate::ai_script::AiScriptError::SourceTooLarge {
                bytes,
                max: max_bytes,
            }
            .into());
        }
        let source = fs::read_to_string(path)?;
        self.reload_ai_script(source)
    }

    pub fn ai_policy_status(&self) -> io::Result<AiPolicyStatus> {
        self.request(|reply| Request::AiPolicyStatus { reply })
    }

    fn seat(&self, handoff: &Handoff, release: bool) -> io::Result<bool> {
        self.request(|reply| Request::Seat {
            connection: handoff.conn,
            id: handoff.match_id,
            release,
            reply,
        })
    }

    fn request<T>(&self, request: impl FnOnce(mpsc::SyncSender<T>) -> Request) -> io::Result<T> {
        let (reply, receive) = mpsc::sync_channel(1);
        self.enqueue(request(reply))?;
        receive.recv().map_err(|_| stopped())
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_snapshot(&self) -> io::Result<game_debug::Snapshot> {
        let (reply, receive) = mpsc::sync_channel(1);
        self.enqueue(Request::Debug { reply })?;
        receive
            .recv_timeout(DEBUG_WAIT)
            .map_err(|error| match error {
                mpsc::RecvTimeoutError::Timeout => io::Error::new(
                    io::ErrorKind::TimedOut,
                    "game worker did not return a debug snapshot",
                ),
                mpsc::RecvTimeoutError::Disconnected => stopped(),
            })?
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_room_control(
        &self,
        id: MatchId,
        command: game_debug::RoomControlCommand,
    ) -> io::Result<game_debug::RoomControlStatus> {
        let (reply, receive) = mpsc::sync_channel(1);
        self.enqueue(Request::DebugControl { id, command, reply })?;
        receive
            .recv_timeout(DEBUG_WAIT)
            .map_err(|error| match error {
                mpsc::RecvTimeoutError::Timeout => io::Error::new(
                    io::ErrorKind::TimedOut,
                    "game worker did not process room control command",
                ),
                mpsc::RecvTimeoutError::Disconnected => stopped(),
            })?
    }

    fn enqueue(&self, request: Request) -> io::Result<()> {
        self.requests
            .try_send(request)
            .map_err(|error| match error {
                mpsc::TrySendError::Full(_) => io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "game worker request queue is full",
                ),
                mpsc::TrySendError::Disconnected(_) => stopped(),
            })?;
        self.wake.wake()
    }

    pub fn stop(&mut self) -> io::Result<()> {
        if let Some(worker) = self.worker.take() {
            self.stopping.store(true, Ordering::Release);
            let _ = self.wake.wake();
            worker
                .join()
                .map_err(|_| io::Error::other("game worker panicked"))??;
        }
        Ok(())
    }
}

impl Drop for GameService {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}

fn stopped() -> io::Error {
    io::Error::new(io::ErrorKind::BrokenPipe, "game worker has stopped")
}
