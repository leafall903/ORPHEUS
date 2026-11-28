use super::*;
use crate::{
    pvp_mode::{
        AudioTimeline, ControlRound, match_speech,
        presentation::{self, control},
        stage_room_visual,
    },
    room_primitives::MAP_ENTITY_BIT,
    script_wire::Value,
};

impl WorldDelivery {
    pub(super) fn queue_control_presentation(
        &mut self,
        scene: &PreparedScene,
        tick: u32,
        team: u8,
        now: Duration,
        graphs: &crate::script_graph::GraphStore,
        codecs: &crate::script_codecs::CodecTable,
    ) -> io::Result<()> {
        let Some(round) = scene.control_round() else {
            return Ok(());
        };
        let Some((_, seconds)) = scene.custom_mode_view() else {
            return Ok(());
        };
        let encoder = crate::script_snapshot_wire::SnapshotEncoder::new(graphs, codecs);
        let now = now.as_secs_f64();
        let stage = round.stage();
        let phase = round.phase;
        let mode_ready = self.mode_ready();
        self.queue_objective_rooms(
            &encoder,
            &round.barriers.entries,
            now,
            team,
            |entry, previous| {
                round.barriers.door_state(
                    entry.entity,
                    now,
                    stage,
                    ControlRound::door_open(entry, phase, stage),
                    previous,
                )
            },
            |entry| {
                mode_ready
                    && matches!(
                        phase,
                        MatchPhase::Waiting
                            | MatchPhase::Assemble
                            | MatchPhase::Setup
                            | MatchPhase::Unlock
                            | MatchPhase::Active
                    )
                    && stage_room_visual(phase, stage, entry, team)
            },
        )?;
        let mut descriptors_ready = true;
        for index in 0..round.stages.len() {
            let first = control::point_descriptor(round, scene.map(), index);
            if let Some(view) = self.objective_views.get(&first.entity()) {
                descriptors_ready &= view.stream.delivered() != 0;
            } else {
                self.queue_objective(&encoder, first, None)?;
                descriptors_ready = false;
            }
        }
        if !descriptors_ready || !self.identity_ready(GAME_MODE_ENTITY) {
            return Ok(());
        }
        let old_phase = self.custom_mode_view.map(|view| view.0);
        let changing_room = round.round_index > 0
            && self.custom_mode_view.is_some()
            && matches!(
                phase,
                MatchPhase::Setup | MatchPhase::Unlock | MatchPhase::Active
            )
            && self.body.is_some_and(|body| {
                self.control_body_round != Some(round.round_index)
                    || !self.owner_body_ready(body.id)
            });
        let mut point_ready = false;
        if mode_ready {
            // Retire the old presenter and its outlines before opening a new one.
            let order = (0..round.stages.len())
                .filter(|&index| index != stage)
                .chain([stage]);
            let mut retired = true;
            for index in order {
                let point = &round.stages[index].point;
                let entity = MAP_ENTITY_BIT | point.placeable;
                let state = control::point_status(round, index);
                let view = &self.objective_views[&entity];
                let settled = !view.stream.data_in_flight();
                let acknowledged = view.capture_state == Some(state) && settled;
                let owner = view.snapshot.entity_variables().get(&0x0063);
                if index == stage {
                    point_ready =
                        acknowledged && owner == Some(&Value::Integer(i64::from(round.owner)));
                }
                if (!state.waypoint || (retired && !changing_room))
                    && self.queue_objective(
                        &encoder,
                        control::point(graphs, round, scene.map(), index)?,
                        None,
                    )?
                {
                    self.objective_views
                        .get_mut(&entity)
                        .expect("queued control presenter")
                        .capture_state = Some(state);
                }
                for &outline in &scene.map().objectives[index].outlines {
                    let visible = state.waypoint && retired && !changing_room;
                    let keep = visible && self.identity_ready(MAP_ENTITY_BIT | outline);
                    let settled =
                        self.queue_objective_placement(outline, visible && (acknowledged || keep));
                    if visible && acknowledged && settled {
                        let snapshot = presentation::outline(
                            graphs,
                            MAP_ENTITY_BIT | outline,
                            !state.active,
                            i64::from(round.owner),
                        )?;
                        self.queue_objective(&encoder, snapshot, None)?;
                    }
                    if !visible {
                        retired &= settled;
                    }
                }
                if index != stage {
                    retired &= acknowledged;
                }
            }
            if changing_room
                || (phase == MatchPhase::Setup && !point_ready)
                || (old_phase == Some(MatchPhase::Unlock)
                    && phase == MatchPhase::Active
                    && !point_ready)
            {
                return Ok(());
            }
        }
        if self.mode.data_in_flight() {
            return Ok(());
        }
        let remaining = control::clock(round, seconds, now);
        let audio = AudioTimeline {
            phase,
            stage: round.round_index,
            seconds: remaining.ceil() as i64,
            overtime: round.overtime,
            winner: round.winner,
            near_finish: false,
            control_wins: Some(round.round_wins.map(|wins| wins as u8)),
            round_winner: round.round_winner,
        };
        let previous = self.payload_mode_view.as_ref();
        let old_audio = self.payload_audio.as_ref();
        let owner_changed = previous
            .and_then(|view| view.instance_variables(1))
            .and_then(|vars| vars.get(&0x1332))
            != Some(&Value::Integer(i64::from(round.owner)));
        if owner_changed
            && old_phase == Some(MatchPhase::Active)
            && phase == MatchPhase::Active
            && !point_ready
        {
            return Ok(());
        }
        let mut snapshot = control::mode(
            graphs,
            scene.map(),
            round,
            presentation::ObjectiveClock { now, remaining },
            team,
            (previous, old_audio),
            self.command_ack(tick),
        )?;
        let chunk = if let (Some(previous), Some(old_audio)) = (previous, old_audio) {
            let old_clock = previous
                .instance_variables(1)
                .and_then(|vars| vars.get(&0x0517))
                .and_then(|value| match value {
                    Value::Float(clock) => Some(*clock),
                    _ => None,
                })
                .unwrap_or_default();
            let mut actions =
                control::actions(old_audio, &audio, previous, round, remaining, old_clock);
            actions.extend(control::setup_music(
                graphs,
                &mut snapshot,
                previous,
                &audio,
                old_audio,
                remaining,
                old_clock,
            )?);
            let Some(frame) = encoder
                .mounted_delta_with_actions(
                    Some(previous),
                    &snapshot,
                    self.mode.latest_data() + 1,
                    &actions,
                )
                .map_err(invalid)?
            else {
                return Ok(());
            };
            self.mode.reliable_delta(&frame).map_err(invalid)?
        } else {
            self.mode
                .full(&encoder.full(&snapshot).map_err(invalid)?)
                .map_err(invalid)?
        };
        let cues = match_speech(old_audio, &audio, scene.map().mode_guid, team, false);
        self.push(Record::script(chunk, false));
        self.payload_cues.extend(cues);
        self.payload_mode_view = Some(snapshot);
        self.payload_audio = Some(audio);
        self.custom_mode_view = Some((phase, seconds));
        self.update_assembly_controller(scene, tick, team, old_phase, phase)
    }

    pub(crate) fn advance_control_round_spawn(
        &mut self,
        scene: &mut PreparedScene,
        viewer: &PlayerAppearance<'_>,
        tick: u32,
        quanta: u32,
        now: Duration,
    ) -> io::Result<()> {
        let Some(round) = scene.control_round() else {
            return Ok(());
        };
        let Some(body) = self.body else { return Ok(()) };
        if round.round_index == 0
            || self.control_body_round == Some(round.round_index)
            || viewer.team >= 2
        {
            return Ok(());
        }
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
        let hero = scene
            .heroes()
            .get(body.hero)
            .cloned()
            .ok_or_else(|| invalid("active player hero is absent from the scene catalog"))?;
        self.switch(scene, viewer, tick, quanta, entity, (&hero, body.skin))
    }
}
