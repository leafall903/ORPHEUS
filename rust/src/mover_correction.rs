//! Frame-aligned owner movement corrections and the command replay window.
//!
//! Follows `ow174/game/correction.py`. A correction describes the state after
//! its command frame. The packet can carry it once that frame has passed and
//! the owner has acknowledged this body's create.

use std::collections::VecDeque;

use crate::world::{EntityUpdate, Movement};

const HISTORY: usize = 64;
// The client retains input from ACK - 1. To replay after F it needs F + 1.
const ACK_LEAD: u32 = 2;
const SERVER_MOTION_TAIL_US: u32 = 250_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct BodyLifetime {
    entity: u32,
    generation: u64,
}

#[derive(Clone, Copy, Debug)]
pub struct Correction {
    body: BodyLifetime,
    frame: u32,
    pose: Movement,
}

impl Correction {
    pub fn frame(&self) -> u32 {
        self.frame
    }

    pub(crate) fn limit_replay_ack(&self, newest: u32) -> u32 {
        newest.min(self.frame.saturating_add(ACK_LEAD))
    }

    pub fn update(&self) -> EntityUpdate {
        EntityUpdate {
            entity: self.body.entity,
            movement: Some(self.pose),
            ..EntityUpdate::default()
        }
    }
}

/// State for a body whose mover and statescript are eligible for correction.
/// Renew the generation when replacing the mover or reusing the entity ID.
pub struct CorrectionHistory {
    body: BodyLifetime,
    poses: VecDeque<Movement>,
    corrected: Option<u32>,
    processed_at_spawn: Option<u32>,
    impulse_correction: Option<(u32, u32)>,
}

impl CorrectionHistory {
    pub fn new(entity: u32, generation: u64) -> Self {
        Self {
            body: BodyLifetime { entity, generation },
            poses: VecDeque::with_capacity(HISTORY),
            corrected: None,
            processed_at_spawn: None,
            impulse_correction: None,
        }
    }

    /// Commands consumed before this body was created do not belong in its replay window.
    pub(crate) fn with_processed_at_spawn(mut self, processed: Option<u32>) -> Self {
        self.processed_at_spawn = processed;
        self
    }

    /// Open or extend the server-only motion correction window through this frame's tail.
    pub fn server_motion(&mut self, frame: u32, frame_us: u32) {
        self.motion_correction_window(frame.saturating_add(1), frame_us, false);
    }

    /// Correct a swept displacement starting with the pose where it actually moved.
    pub(crate) fn swept_motion(&mut self, first_moved_frame: u32, frame_us: u32) {
        self.motion_correction_window(first_moved_frame, frame_us, true);
    }

    fn motion_correction_window(&mut self, first: u32, frame_us: u32, include_earlier_frame: bool) {
        if frame_us == 0 {
            return;
        }
        let through = first.saturating_add((SERVER_MOTION_TAIL_US - 1) / frame_us);
        self.impulse_correction = Some(match self.impulse_correction {
            Some((prev_first, prev_through)) => (
                if include_earlier_frame {
                    prev_first.min(first)
                } else {
                    prev_first
                },
                prev_through.max(through),
            ),
            None => (first, through),
        });
    }

    pub fn impulse_correction(&self) -> Option<(u32, u32)> {
        self.impulse_correction
    }

    /// Save each simulated pose, including recovered poses in a command gap.
    /// Only increasing, explicit command frames belong to this lifetime.
    pub fn record(&mut self, pose: Movement) -> bool {
        let Some(frame) = pose.frame else {
            return false;
        };
        if self.processed_at_spawn.is_some_and(|spawn| frame < spawn)
            || self
                .poses
                .back()
                .and_then(|pose| pose.frame)
                .is_some_and(|last| frame <= last)
        {
            return false;
        }
        if self.poses.len() == HISTORY {
            self.poses.pop_front();
        }
        self.poses.push_back(pose);
        true
    }

    pub fn corrected(&self) -> Option<u32> {
        self.corrected
    }

    fn snapshot_for(&self, tick: u32) -> Option<(u32, Movement)> {
        self.poses
            .iter()
            .rev()
            .find_map(|&pose| {
                let frame = pose.frame?;
                (frame <= tick).then_some((frame, pose))
            })
            .filter(|&(frame, _)| {
                self.corrected.is_none_or(|sent| frame > sent)
                    && self
                        .impulse_correction
                        .is_none_or(|(first, _)| frame >= first)
            })
    }

    pub fn candidate(&self, tick: u32, create_arrived: bool) -> Option<Correction> {
        self.candidate_with_hero(tick, create_arrived, true)
    }

    /// Return a correction candidate only when `create_arrived` and either the hero
    /// is always corrected or a server-only `impulse_correction` window is open.
    pub fn candidate_with_hero(
        &self,
        tick: u32,
        create_arrived: bool,
        hero_eligible: bool,
    ) -> Option<Correction> {
        if !create_arrived || (!hero_eligible && self.impulse_correction.is_none()) {
            return None;
        }
        let (frame, pose) = self.snapshot_for(tick)?;
        Some(Correction {
            body: self.body,
            frame,
            pose,
        })
    }

    /// Use the correction only if this exact candidate is in the packet under
    /// consideration. Until the first send, a usable pose already caps the ACK
    /// even when the body create or packet budget holds that pose back.
    pub fn command_ack(
        &self,
        tick: u32,
        latest_input: Option<u32>,
        selected: Option<&Correction>,
    ) -> u32 {
        let newest = latest_input.unwrap_or(tick);
        let replay_from = selected
            .filter(|candidate| candidate.body == self.body)
            .map(Correction::frame)
            .max(self.corrected)
            .or_else(|| self.snapshot_for(tick).map(|(frame, _)| frame))
            .unwrap_or(tick);
        // A fresh body can have no usable pose yet while the room clock trails
        // the input stream. Its initial pose already replaces the earlier history.
        let replay_from = replay_from.max(self.processed_at_spawn.unwrap_or_default());
        newest.min(replay_from.saturating_add(ACK_LEAD))
    }

    /// Commit after the selected datagram has been sent successfully.
    pub fn sent(&mut self, correction: &Correction) -> bool {
        if correction.body != self.body
            || self.corrected.is_some_and(|sent| correction.frame <= sent)
        {
            return false;
        }
        self.corrected = Some(correction.frame);
        if self
            .impulse_correction
            .is_some_and(|(_, through)| correction.frame >= through)
        {
            self.impulse_correction = None;
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bits::BitReader,
        game_egress::{FrameRecords, GameEgress, RecordSource, SendError},
        world::{MAX_ENTITY_RECORDS, NO_INPUT, Operation, movement_record},
    };
    use sha2::{Digest, Sha256};

    const ENTITY: u32 = 0xa000_0101;

    fn pose(frame: u32) -> Movement {
        Movement {
            position: [f64::from(frame) / 1024.0, 1.0, -2.0],
            frame: Some(frame),
            ..Movement::default()
        }
    }

    #[test]
    fn selects_one_newest_eligible_pose_after_create_ack() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        for frame in 7000..7010 {
            assert!(history.record(pose(frame)));
        }
        assert!(history.candidate(7005, false).is_none());
        assert!(history.candidate(6999, true).is_none());
        let first = history.candidate(7005, true).unwrap();
        assert_eq!(first.update().movement, Some(pose(7005)));
        assert!(history.sent(&first));
        assert!(!history.sent(&first));
        assert!(history.candidate(7005, true).is_none());
        let next = history.candidate(7006, true).unwrap();
        assert_eq!(next.frame(), 7006);
        assert!(history.sent(&next));
        let latest = history.candidate(7020, true).unwrap();
        assert_eq!(latest.frame(), 7009);
        assert!(history.sent(&latest));
        assert!(history.candidate(7021, true).is_none());
    }

    #[test]
    fn preserves_the_python_correction_bytes_for_all_input_frame_encodings() {
        // `correction.movement(Snapshot(...))`, then `world.movement_record(..., 7010)`.
        for (input_frame, length, expected) in [
            (
                Some(NO_INPUT),
                448,
                "93062ff28740128476ab865a0845be78031fe954fe6643382270370eb6da1d42",
            ),
            (
                Some(7001),
                481,
                "608555899c859da761a3861c111bc78a572fc464e9432c659838b93a34220248",
            ),
            (
                None,
                429,
                "c906c01d81bc869fc93a6fbb87b64779fced1bcdb463b621050ac4aa90b2ce26",
            ),
        ] {
            let mut history = CorrectionHistory::new(ENTITY, 1);
            assert!(history.record(Movement {
                position: [3.125, 0.5, -3.75],
                velocity: [0.25, 0.75, -2.125],
                yaw: 12_000,
                body_pitch: 0,
                pitch: -3000,
                flags: 0x0400_0002,
                air_ticks: 11,
                gravity: 0.875,
                throttles: [3, -127],
                input_frame,
                input_throttles: [3, -127],
                frame: Some(7007),
                crouch_frame: Some(6999),
                spring: [-13, 27],
                spring_speed: 0.25,
                fall: -0.125,
            }));
            let correction = history.candidate(7010, true).unwrap().update();
            assert_eq!(correction.entity, ENTITY);
            let record = movement_record(&correction.movement.unwrap(), Some(7010)).unwrap();
            assert_eq!(record.bit_len(), length);
            assert_eq!(format!("{:x}", Sha256::digest(record.as_bytes())), expected);
        }
    }

    #[test]
    fn retains_the_last_64_explicit_command_poses() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        assert!(!history.record(Movement::default()));
        for frame in 1000..1070 {
            assert!(history.record(pose(frame)));
        }
        assert!(!history.record(pose(1068)));
        assert!(!history.record(pose(1069)));
        assert!(history.candidate(1005, true).is_none());
        assert_eq!(history.candidate(1006, true).unwrap().frame(), 1006);
        assert_eq!(history.candidate(1080, true).unwrap().frame(), 1069);
    }

    #[test]
    fn caps_ack_while_waiting_for_the_first_pose_or_create() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        assert_eq!(history.command_ack(100, None, None), 100);
        history.record(pose(113));
        history.record(pose(179));
        assert!(history.candidate(140, false).is_none());
        assert_eq!(history.command_ack(107, Some(179), None), 109);
        assert_eq!(history.command_ack(140, Some(179), None), 115);
        let last = history.candidate(180, true).unwrap();
        assert_eq!(history.command_ack(180, Some(179), Some(&last)), 179);
        assert_eq!(history.corrected(), None);
    }

    #[test]
    fn stale_tokens_do_not_commit_or_change_the_ack() {
        let mut old = CorrectionHistory::new(ENTITY, 1);
        old.record(pose(125));
        let stale = old.candidate(130, true).unwrap();
        for (entity, generation) in [(ENTITY, 2), (ENTITY + 1, 1)] {
            let mut current = CorrectionHistory::new(entity, generation);
            current.record(pose(110));
            let valid = current.candidate(130, true).unwrap();
            assert!(current.sent(&valid));
            assert!(!current.sent(&stale));
            assert_eq!(current.corrected(), Some(110));
            assert_eq!(current.command_ack(130, Some(129), Some(&stale)), 112);
        }
    }

    #[test]
    fn selected_send_advances_ack_only_after_budget_and_transport_succeed() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        history.record(pose(100));
        assert!(history.sent(&history.candidate(100, true).unwrap()));
        for frame in 101..114 {
            history.record(pose(frame));
        }
        let own = history.candidate(107, true).unwrap();
        let movement = [own.update()];
        let queued: Vec<_> = (0..MAX_ENTITY_RECORDS)
            .map(|index| EntityUpdate {
                entity: 0x8000_0001 + index as u32,
                operation: Some(Operation::Destroy),
                ..EntityUpdate::default()
            })
            .collect();
        fn candidates<'a>(
            queued: &'a [EntityUpdate],
            movements: &'a [EntityUpdate],
        ) -> FrameRecords<'a> {
            FrameRecords {
                tick: 107,
                queued,
                movements,
                owner_correction: true,
                reliable_only: false,
            }
        }
        let ack = |records: &[&EntityUpdate]| {
            let selected = records
                .iter()
                .any(|record| std::ptr::eq(*record, &movement[0]));
            history.command_ack(107, Some(113), selected.then_some(&own))
        };
        let decode_ack = |bytes: Vec<u8>| {
            let mut reader = BitReader::new(&bytes);
            assert_eq!(reader.var_a().unwrap(), 107);
            assert!(!reader.bit().unwrap()); // empty JAM channel
            reader.var_a().unwrap()
        };
        let mut egress = GameEgress::default();
        let crowded = egress
            .send_frame(
                candidates(&queued, &movement),
                ack,
                |_| (),
                |bytes| {
                    assert_eq!(decode_ack(bytes), 102);
                    Ok::<_, ()>(0)
                },
            )
            .unwrap();
        assert_eq!(crowded.records.len(), MAX_ENTITY_RECORDS);
        assert!(!crowded.records.contains(&RecordSource::Movement(0)));
        assert_eq!(history.corrected(), Some(100));

        let failed = egress.send_frame(
            candidates(&[], &movement),
            ack,
            |_| (),
            |bytes| {
                assert_eq!(decode_ack(bytes), 109);
                Err(())
            },
        );
        assert!(matches!(failed, Err(SendError::Transport(()))));
        assert_eq!(history.corrected(), Some(100));
        let sent = egress
            .send_frame(
                candidates(&[], &movement),
                ack,
                |_| (),
                |bytes| {
                    assert_eq!(decode_ack(bytes), 109);
                    Ok::<_, ()>(2)
                },
            )
            .unwrap();
        assert_eq!(sent.records, [RecordSource::Movement(0)]);
        assert!(history.sent(&own));
        assert_eq!(history.corrected(), Some(107));
        assert_eq!(history.command_ack(108, Some(113), None), 109);
    }

    #[test]
    fn candidate_defaults_hero_eligible_and_candidate_with_hero_gates_on_window() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        assert!(history.record(pose(10)));
        assert_eq!(history.candidate(10, true).map(|c| c.frame), Some(10));
        assert!(history.candidate_with_hero(10, true, false).is_none());

        history.server_motion(10, 160_000);
        assert!(history.record(pose(11)));
        assert_eq!(
            history
                .candidate_with_hero(11, true, false)
                .map(|c| c.frame),
            Some(11)
        );
    }

    #[test]
    fn server_motion_ignores_zero_frame_us_and_saturates_frame_bounds() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        history.server_motion(10, 0);
        assert_eq!(history.impulse_correction(), None);

        history.server_motion(u32::MAX, 16_000);
        assert_eq!(history.impulse_correction(), Some((u32::MAX, u32::MAX)));
    }

    #[test]
    fn swept_motion_includes_first_moved_pose_without_repeating_old_frames() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        assert!(history.record(pose(9)));
        history.swept_motion(10, 100_000);
        assert_eq!(history.impulse_correction(), Some((10, 12)));
        assert!(history.candidate_with_hero(9, true, false).is_none());

        assert!(history.record(pose(10)));
        assert!(history.candidate_with_hero(10, false, false).is_none());
        let moved = history.candidate_with_hero(10, true, false).unwrap();
        assert_eq!(moved.frame(), 10);
        assert!(history.sent(&moved));
        assert!(!history.sent(&moved));
        assert!(history.candidate_with_hero(9, true, false).is_none());
        assert!(history.candidate_with_hero(10, true, false).is_none());
        assert!(!history.record(pose(9)));
        assert!(!history.record(pose(10)));
        assert_eq!(history.impulse_correction(), Some((10, 12)));
    }

    #[test]
    fn swept_motion_window_expires_only_after_sending_its_bounded_tail() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        history.swept_motion(10, 100_000);
        for frame in 10..=13 {
            assert!(history.record(pose(frame)));
        }

        let before_tail = history.candidate_with_hero(11, true, false).unwrap();
        assert!(history.sent(&before_tail));
        assert_eq!(history.impulse_correction(), Some((10, 12)));

        let tail = history.candidate_with_hero(12, true, false).unwrap();
        assert_eq!(tail.frame(), 12);
        // Selecting a packet held back by budget or transport does not commit it.
        assert_eq!(history.corrected(), Some(11));
        assert_eq!(history.impulse_correction(), Some((10, 12)));
        assert_eq!(
            history
                .candidate_with_hero(12, true, false)
                .unwrap()
                .frame(),
            12
        );

        assert!(history.sent(&tail));
        assert_eq!(history.impulse_correction(), None);
        assert!(!history.sent(&tail));
        assert!(!history.sent(&before_tail));
        assert!(history.candidate_with_hero(13, true, false).is_none());
        assert_eq!(history.candidate(13, true).unwrap().frame(), 13);
    }

    #[test]
    fn swept_motion_preserves_zero_duration_saturation_and_existing_window_extension() {
        let mut history = CorrectionHistory::new(ENTITY, 1);
        history.swept_motion(10, 0);
        assert_eq!(history.impulse_correction(), None);

        history.server_motion(9, 100_000);
        history.swept_motion(12, 100_000);
        assert_eq!(history.impulse_correction(), Some((10, 14)));
        history.server_motion(14, 100_000);
        assert_eq!(history.impulse_correction(), Some((10, 17)));

        let mut overlapping = CorrectionHistory::new(ENTITY, 1);
        overlapping.server_motion(10, 100_000);
        assert_eq!(overlapping.impulse_correction(), Some((11, 13)));
        overlapping.server_motion(8, 100_000);
        assert_eq!(overlapping.impulse_correction(), Some((11, 13)));
        overlapping.swept_motion(10, 100_000);
        assert_eq!(overlapping.impulse_correction(), Some((10, 13)));
        assert!(overlapping.record(pose(10)));
        assert_eq!(
            overlapping
                .candidate_with_hero(10, true, false)
                .unwrap()
                .frame(),
            10
        );

        let mut saturated = CorrectionHistory::new(ENTITY, 1);
        saturated.swept_motion(u32::MAX, 16_000);
        assert_eq!(saturated.impulse_correction(), Some((u32::MAX, u32::MAX)));
    }
}
