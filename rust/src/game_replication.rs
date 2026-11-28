//! Per-peer world delivery, including the owning player's body lifetime.

use std::{
    borrow::Borrow,
    collections::{BTreeMap, HashMap, HashSet, VecDeque},
    io,
    ops::Range,
    sync::Arc,
    time::Duration,
};

use crate::{
    commands::{Command, CommandQueue},
    game_body::{BodyCreate, BodyView, body_create, owner_pose},
    game_bots::BOT_RECORD_DELAY,
    game_clock::{MAX_LEASE_FRAME, SEND_EVERY, TICK_PERIOD},
    game_combat::{
        AdmittedFlight, BIOTIC_CANISTER, BIOTIC_FIELD_ENTITY, BioticField, BotReplacement,
        CombatError, FlightOutcome, HealingPulse, PlayerBodyState, PosedBioticField, PosedHitscan,
        PosedProjectile, PosedShot, RoomSimulation,
    },
    game_controller::ControllerFrame,
    game_egress::{DeliveryReport, FrameRecords, GameEgress, RecordSource, SendError, SentFrame},
    game_heroes::Hero,
    game_messages::Message,
    game_parameters::{ClientGameMessage, ClientWheelAction, ClientWheelUse, HeroSelectRequest},
    game_projectiles::{OwnerProjectiles, ProjectileDelivery, ProjectileLeases, ProjectileToken},
    match_scene::{
        PlayerAppearance, Possession, bot_controller_create, bot_controller_possession,
        custom_countdown_delta, team_bit,
    },
    mover::Mover,
    mover_correction::{Correction, CorrectionHistory},
    mover_kinematics::Poses,
    mover_modifiers::{Modifier, ModifierKey},
    projectile_flight::ProjectileFlight,
    pvp_mode::MatchPhase,
    scene_assets::PreparedScene,
    script_init::{HostEvent, HostHealthContext, InitFault, RoomMessage, Snapshot},
    script_player::OwnerBodySpawn,
    script_snapshot_wire::CountdownFrame,
    script_snapshot_wire::{ProjectedSnapshot, SnapshotEncoder, View},
    script_stream::{BodyStream, ChangeKey, DeltaKind, ScriptChunk, Stream},
    vitals::{Damage, Death, Vitals},
    world::{
        Component as WorldComponent, Create, EntityUpdate, GAME_MODE_ENTITY, MAX_PAYLOAD, Movement,
        Operation, SkinTheme, frame,
    },
};

const BODY_REUSE_DELAY: Duration = Duration::from_secs(30);
// Resource bound for this worker. The reference keeps 64 predicted states and
// 32 commands. A farther-ahead command can be retried once the clock catches up.
const MAX_INPUT_LEAD: u32 = 96;
// Several retransmit windows fit before a peer exhausts this queue. One final
// leave marker may follow the accepted prefix when the cap is reached.
const MAX_PENDING_INPUT: usize = 256;
// BodyScript.command retains this many intermediate VM frames before its
// requested frame. The first neutral release runs even across a larger gap.
const MAX_SCRIPT_TAIL: u32 = 256;
const FIRST_BIOTIC_VIEW: u32 = 0xa008_0000;
const END_BIOTIC_VIEW: u32 = 0xa00c_0000;
const KEEP_MOVEMENT_ROTATION: u32 = 0x0100_0000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct BodyId {
    pub entity: u32,
    pub generation: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OwnerRoomActivity {
    Regular,
    Encounter { active: bool },
}

impl OwnerRoomActivity {
    pub(crate) fn allows_body_activity(self) -> bool {
        !matches!(self, Self::Encounter { active: false })
    }

    pub(crate) fn encounter_active(self) -> bool {
        matches!(self, Self::Encounter { active: true })
    }
}

#[derive(Clone, Copy)]
struct Body {
    id: BodyId,
    hero: u64,
    skin: SkinTheme,
}

#[derive(Clone, Copy, Default)]
struct BotControllerState {
    created: bool,
    possessed: Option<u32>,
    possessed_acked: Option<u32>,
}

/// A running owner VM and the authored data used to encode its snapshots.
/// A supported modifier projection is required before the mover can correct
/// the owner's prediction, even if the current set of modifiers is empty.
pub(crate) trait OwnerBodyRuntime: Send + Sync {
    fn status_script(
        &mut self,
        _op: crate::game_combat::status_scripts::StatusScriptOp<'_>,
    ) -> Result<Option<u16>, String> {
        Ok(None)
    }
    fn set_room_activity(&mut self, _activity: OwnerRoomActivity) {}
    fn receive_respawn(&mut self) -> Result<usize, InitFault> {
        Ok(0)
    }
    fn set_encounter_active(&mut self, _active: bool) -> io::Result<()> {
        Ok(())
    }
    fn receive_applied_damage(
        &mut self,
        damage: crate::game_combat::AppliedDamage,
    ) -> io::Result<()>;
    fn receive_applied_heal(&mut self, _source: u32, _target: u32, _amount: f64) -> io::Result<()> {
        Ok(())
    }
    fn take_velocity_changes(&mut self) -> Vec<(u32, crate::mover::VelocityChange)> {
        Vec::new()
    }
    fn take_scripted_motion(&mut self) -> Option<(u32, crate::script_init::ScriptedMotion)> {
        None
    }
    /// Begins a state in the owner stream through the production lifecycle
    /// begin — the same entry a subgraph launch edge takes. Test lanes use
    /// it for the authored re-arm that support graphs will call in the
    /// room feed.
    #[cfg(test)]
    fn begin_state(&mut self, instance: u16, raw: u16) -> Result<(), InitFault>;
    fn owner_move(&self) -> Option<(crate::script_init::MoveToKey, [f64; 3], f64)> {
        None
    }
    fn take_script_requests(&mut self) -> Vec<crate::game_combat::RoomScriptRequest> {
        Vec::new()
    }
    fn receive_room_message(
        &mut self,
        sender: u32,
        message: RoomMessage,
    ) -> Result<usize, InitFault>;
    fn set_wheel_cosmetics(&mut self, _cosmetics: crate::match_scene::WheelCosmetics) {}
    fn queue_wheel_selection(
        &mut self,
        _action: ClientWheelAction,
        _command_frame: Option<u32>,
    ) -> Result<usize, InitFault> {
        Ok(0)
    }
    fn queue_wheel_use(
        &mut self,
        _report: ClientWheelUse,
        _sent_instance_start: Option<u64>,
    ) -> Result<usize, InitFault> {
        Ok(0)
    }
    fn wheel_actions_available(&self) -> bool {
        false
    }
    fn receive_host_event(
        &mut self,
        event: HostEvent,
        context: HostHealthContext,
    ) -> Result<usize, InitFault>;
    fn receive_authored_hit_feedback(
        &mut self,
        event: HostEvent,
        context: &HostHealthContext,
        variant: f32,
    ) -> Result<usize, InitFault>;
    fn receive_authored_elimination_feedback(
        &mut self,
        target: Arc<crate::script_init::HealthTargetFacts>,
    ) -> Result<usize, InitFault>;
    fn set_spatial_frame(&mut self, _frame: Arc<crate::game_combat::RoomSpatialFrame>) {}
    fn set_defenders(&mut self, _defenders: Arc<[u32]>) {}
    fn set_team_member_counts(&mut self, _counts: crate::game_instance::TeamMemberCounts) {}
    fn take_sightings(&mut self) -> Vec<crate::pve_encounter::messages::DefenderSighting> {
        Vec::new()
    }
    fn receive_sighting(
        &mut self,
        _message: crate::pve_encounter::messages::DefenderSighting,
    ) -> io::Result<()> {
        Ok(())
    }
    fn receive_defender_message(
        &mut self,
        _message: crate::pve_encounter::messages::DefenderMessage,
    ) -> io::Result<usize> {
        Ok(0)
    }
    fn cinematic_aim(&self) -> Option<[i32; 2]> {
        None
    }

    fn authored_facing(&self) -> Option<[i32; 2]> {
        None
    }

    fn move_request(&self) -> Option<([f64; 3], f64)> {
        None
    }

    fn active_tether(&self) -> Option<crate::script_init::TetherConstraint> {
        None
    }

    fn ball_roll_speed(&self) -> Option<f64> {
        None
    }

    fn mover_override(&self) -> Option<crate::mover_settings::MoverSettings> {
        None
    }

    fn take_body_swap(&mut self) -> Option<u64> {
        None
    }

    fn deflect_state(&self) -> Option<crate::script_init::DeflectState> {
        None
    }

    /// `(outgoing_damage, incoming_damage, outgoing_heal, incoming_heal, cc_immune)`
    fn combat_modifiers(&self) -> (f64, f64, f64, f64, bool) {
        (1.0, 1.0, 1.0, 1.0, false)
    }

    #[cfg_attr(not(feature = "lua-mods"), allow(dead_code))]
    fn set_ult_charge_scale(&mut self, _scale: f64) {}

    #[cfg_attr(not(feature = "lua-mods"), allow(dead_code))]
    fn set_cooldown_scale(&mut self, _scale: f64) {}

    #[cfg_attr(not(feature = "lua-mods"), allow(dead_code))]
    fn ult_charge_percent(&self) -> f64 {
        0.0
    }

    #[cfg_attr(not(feature = "lua-mods"), allow(dead_code))]
    fn add_ult_charge_points(&mut self, _points: f64) -> io::Result<()> {
        Ok(())
    }

    #[cfg_attr(not(feature = "lua-mods"), allow(dead_code))]
    fn set_ult_charge_percent(&mut self, _percent: f64) -> io::Result<()> {
        Ok(())
    }

    #[cfg_attr(not(feature = "lua-mods"), allow(dead_code))]
    fn reset_all_cooldowns(&mut self) -> io::Result<usize> {
        Ok(0)
    }

    #[cfg_attr(not(feature = "lua-mods"), allow(dead_code))]
    fn is_emoting(&self) -> bool {
        false
    }

    fn set_on_fire(&mut self, _active: bool) {}

    fn has_deadeye(&self) -> bool {
        false
    }

    fn set_deadeye_targets(&mut self, _scene: crate::script_player::DeadeyeScene) {}

    #[cfg(test)]
    fn set_deadeye_scene(&mut self, scene: &PreparedScene, team: u8) -> io::Result<()> {
        if self.has_deadeye() {
            self.set_deadeye_targets(scene.deadeye_scene(team).map_err(io::Error::other)?);
        }
        Ok(())
    }

    fn snapshot(&self) -> Option<&Snapshot>;
    #[cfg(test)]
    fn random_checkpoint(&self) -> Option<crate::script_init::RandomCheckpoint> {
        None
    }
    fn encoder(&self) -> SnapshotEncoder<'_>;
    fn advance(&mut self, command: Command, mover: &Mover, step: BodyStep) -> io::Result<()>;
    fn frame_ms(&self) -> u32;

    fn target_tags(&self) -> crate::script_init::TargetTagSnapshot {
        Default::default()
    }
    fn target_filter_flags(&self) -> Option<u64> {
        None
    }

    fn vitals(&self) -> Option<&Vitals> {
        None
    }

    /// Transfer modeled health to the native body when this runtime is retired.
    fn take_vitals(&mut self) -> Option<Vitals>;

    fn heal(&mut self, _amount: f64) -> f64 {
        0.0
    }

    fn receive_damage(
        &mut self,
        _amount: f64,
        _source: Death,
        _tags: &crate::script_init::HitTags,
    ) -> Damage {
        Damage::default()
    }

    fn publish_death(
        &mut self,
        _killer: u32,
        _impact: Option<&crate::game_hitscan::Impact>,
        _source: Option<crate::game_combat::DeathSourceFacts>,
    ) -> Result<(), InitFault> {
        Ok(())
    }

    /// New health revisions supersede older ones, including any slot clears.
    fn take_health_update(&mut self) -> Option<WorldComponent> {
        None
    }

    fn projectile_leases(&self) -> Option<&ProjectileLeases> {
        None
    }

    fn movement_modifiers(&self) -> Option<&BTreeMap<ModifierKey, Modifier>> {
        None
    }

    fn take_shots(&mut self, _command: Option<Command>, _mover: &Mover) -> Vec<PosedShot> {
        Vec::new()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum BodyStep {
    Posed,
    UnposedBoundary,
    UnposedTimer,
}

pub(crate) struct Record {
    update: EntityUpdate,
    delivery: Delivery,
}

impl Record {
    pub(crate) fn baseline(update: EntityUpdate) -> Self {
        Self {
            update,
            delivery: Delivery::Baseline,
        }
    }

    fn script(chunk: ScriptChunk, close: bool) -> Self {
        Self {
            update: chunk.update,
            delivery: Delivery::Script {
                last: chunk.last,
                close,
                resend: chunk.resend,
            },
        }
    }
}

pub(crate) struct Ready<'a> {
    index: usize,
    record: &'a Arc<Record>,
}

impl Ready<'_> {
    pub(crate) fn tracked(&self) -> Arc<Record> {
        Arc::clone(self.record)
    }
}

impl Borrow<EntityUpdate> for Ready<'_> {
    fn borrow(&self) -> &EntityUpdate {
        &self.record.update
    }
}

#[cfg(test)]
struct OwnerScriptFault {
    id: BodyId,
    frame: u32,
    // Owner egress can fail without attempting a script step.
    step: Option<BodyStep>,
    error: io::Error,
}

#[derive(Clone, Copy)]
struct PendingOwnerCorrection {
    id: BodyId,
    sequence: u32,
    pose: Correction,
    retry: bool,
}

pub(crate) struct WorldDelivery {
    owner: u32,
    queued: Vec<Arc<Record>>,
    created: HashSet<u32>,
    bots: HashMap<u32, BotLifetime>,
    pending_respawn_presentation: HashSet<u32>,
    bot_controllers: HashMap<u32, BotControllerState>,
    health_updates: HashMap<u32, u64>,
    health_revision: u64,
    // These barriers belong to the peer's reliable stream and survive world clears.
    entity_notice_ordinals: HashMap<u32, u32>,
    started: Option<u32>,
    pub(crate) presentation_due: bool,
    controller: Stream,
    controller_pending: Option<ControllerFrame<'static>>,
    controller_skins: bool,
    mode: Stream,
    custom_mode_view: Option<(MatchPhase, u32)>,
    payload_mode_view: Option<ProjectedSnapshot>,
    objective_views: BTreeMap<u32, ObjectiveView>,
    objective_placements: BTreeMap<u32, bool>,
    payload_pose: Option<crate::world::Movement>,
    payload_cart_phase: Option<MatchPhase>,
    payload_cart_finish_music: bool,
    payload_progress_bound: bool,
    payload_viewer_team: Option<u8>,
    payload_refresh_rows: bool,
    payload_audio: Option<crate::pvp_mode::AudioTimeline>,
    payload_cues: Vec<crate::room_primitives::VoiceCue>,
    control_body_round: Option<usize>,
    countdown: Option<CountdownFrame>,
    encounter_post_match_queued: bool,
    encounter_screens: Option<ProjectedSnapshot>,
    encounter_notifications: Option<ProjectedSnapshot>,
    encounter_notification_turn: bool,
    encounter_phases: BTreeMap<u16, u16>,
    encounter_result_queued: bool,
    encounter_outcome_queued: bool,
    select_open: bool,
    viewer_team: u8,
    assembly_choice: Option<u64>,
    assembly_confirmed: bool,
    refresh_selection: bool,
    team_updates: HashMap<u32, (u64, bool)>,
    body: Option<Body>,
    bound: Option<BodyId>,
    generation: u64,
    bodies: HashMap<u32, (u64, Lifetime)>,
    remote_players: HashMap<u32, (BodyId, BotLifetime)>,
    input: InputQueue,
    body_script: Option<BodyScript>,
    detached_motion: Option<(BodyId, CorrectionHistory)>,
    pending_owner_correction: Option<PendingOwnerCorrection>,
    #[cfg(test)]
    owner_script_fault: Option<OwnerScriptFault>,
    projectile_destroys: HashMap<u32, (BodyId, ProjectileToken)>,
    biotic_views: BTreeMap<u32, BioticView>,
    scene_views: BTreeMap<u32, BodyStream<ProjectedSnapshot>>,
    retiring_nameplate_chunks: HashSet<(u32, u32)>,
    observed_projectiles: observed_projectiles::ObservedProjectiles,
    bot_chunk_waited: bool,
    last_sent_remote_movement: Option<u32>,
    gate: Option<u32>,
    next_biotic_view: u32,
}

struct ObjectiveView {
    stream: Stream,
    snapshot: ProjectedSnapshot,
    door_state: Option<crate::room_primitives::DoorState>,
    capture_state: Option<crate::pvp_mode::presentation::CapturePresentation>,
}

mod body;
mod delivery;
mod observed_projectiles;

pub(crate) use body::validate_body_baselines;
use body::{BioticView, BodyScript, InputQueue};
use delivery::{BotLifetime, Delivery, Lifetime};

#[cfg(test)]
pub(crate) mod tests;
