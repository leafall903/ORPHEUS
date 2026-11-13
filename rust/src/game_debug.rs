//! Read-only simulation facts copied by the game worker for local diagnostics.

use serde::Serialize;

#[cfg(any(test, feature = "inspector"))]
use crate::game_timing::WorkerTiming;

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Snapshot {
    pub tick: u32,
    pub rooms: Vec<Room>,
    pub truncated: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timing: Option<WorkerTiming>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub faults: Vec<RoomFault>,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct RoomFault {
    pub room: String,
    pub stage: &'static str,
    pub connection: Option<u32>,
    pub tick: u32,
    pub reason: &'static str,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Room {
    pub id: String,
    pub map: String,
    pub name: String,
    pub actors: Vec<Actor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub encounter: Option<Encounter>,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Encounter {
    pub elapsed_ms: u64,
    pub countdown_started: bool,
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
pub(crate) struct Actor {
    pub entity: u32,
    pub generation: Option<u64>,
    pub kind: ActorKind,
    pub team: u8,
    pub position: [f64; 3],
    pub velocity: [f64; 3],
    pub alive: bool,
    pub health: Option<f64>,
    pub script_active: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_contact_clip: Option<ContactClip>,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub(crate) struct ContactClip {
    pub tick: u32,
    pub proxy_id: u32,
    pub category: u16,
    pub fraction: f32,
    pub before: [f64; 3],
    pub intended: [f64; 3],
    pub after: [f64; 3],
}

#[cfg(any(test, feature = "inspector"))]
#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ActorKind {
    Player,
    Bot,
}
