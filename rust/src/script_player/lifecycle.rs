use std::{collections::BTreeMap, io, sync::Arc};

use serde_json::Value as Json;

use super::{
    unsupported,
    world::{BodyWorld, DeadeyeScene},
};
use crate::{
    commands::Command,
    game_combat::{
        BIOTIC_CANISTER, BIOTIC_FIELD_ENTITY, PosedBioticField, PosedHitscan, PosedProjectile,
        PosedShot, Stun, script_eye_position,
    },
    game_content::is_junkenstein_mode,
    game_hitscan::{HitDefinition, aim_direction},
    game_instance::TeamMemberCounts,
    game_parameters::{ClientWheelAction, ClientWheelUse},
    game_projectiles::{
        ImpactSpawn, ProjectileLeases, authored_number, local_prediction, predicted_sync,
    },
    game_replication::{BodyStep, OwnerBodyRuntime, OwnerRoomActivity},
    mover::Mover,
    mover_modifiers::{Modifier, ModifierKey},
    projectile_flight::{ProjectileBallistics, authored_gravity, posed_projectile_direction},
    pve_encounter::messages::{DefenderMessage, DefenderSighting, SIGHTING},
    script_codecs::CodecTable,
    script_control::LEGACY_PROJECTILE_CONTROL,
    script_expr::to_float,
    script_init::{
        Component, HostEvent, HostHealthContext, InitFault, MoveToKey, ReportedShot, RoomMessage,
        ScriptedMotion, Snapshot,
    },
    script_runtime::FRAME_MS,
    script_snapshot_wire::{ProjectedSnapshot, SnapshotEncoder, View},
    script_wire::Value,
    vitals::{Damage, Death, Vitals},
    weapon_spread::for_each_authored_hitscan_direction,
    world::Component as EntityComponent,
};

pub(crate) struct PlayerBodyRuntime {
    pub(super) component: Component,
    pub(super) world: BodyWorld,
    pub(super) codecs: Arc<CodecTable>,
    pub(super) modifiers: Option<BTreeMap<ModifierKey, Modifier>>,
    pub(super) leases: ProjectileLeases,
    pub(super) frame: u32,
    pub(super) wheel_delivery_frame: u32,
    pub(super) charge_binding: Option<super::charge::ChargeBinding>,
    pub(super) ult_charge_scale: f64,
}

impl PlayerBodyRuntime {
    /// Freeze the observer effect independently of the room's projectile flight.
    fn observer_projectile(
        &self,
        cached: &mut Option<Option<ProjectedSnapshot>>,
        report: ReportedShot,
        ballistics: &ProjectileBallistics,
        origin: [f64; 3],
        direction: [f64; 3],
        skip_owner: bool,
    ) -> Option<Arc<crate::game_combat::ObservedShot>> {
        let snapshot = cached
            .get_or_insert_with(|| {
                self.encoder()
                    .project(self.component.snapshot()?, View::Observer)
                    .ok()
            })
            .as_ref()?;
        let shot = report.event;
        let volley = snapshot.freeze_volley_with_states(
            shot,
            report.presentation,
            &report.presentation_states,
        )?;
        Some(Arc::new(
            crate::game_combat::ObservedShot::new(
                volley,
                ballistics.definition?,
                origin,
                direction,
                shot.frame,
                ballistics.lifetime,
            )
            .with_skip_owner(skip_owner),
        ))
    }

    fn sync_body_health_logged(&mut self) {
        if self.component.snapshot().is_some() {
            if let Err(error) = self.component.sync_body_health(&mut self.world) {
                eprintln!("Owner health synchronization failed: {error}");
            }
        }
    }
}

impl OwnerBodyRuntime for PlayerBodyRuntime {
    fn set_room_activity(&mut self, activity: OwnerRoomActivity) {
        self.world.room_activity = Some(activity);
    }

    fn set_encounter_active(&mut self, active: bool) -> io::Result<()> {
        if self
            .charge_binding
            .is_some_and(|binding| binding.admits_regular_room())
            && self.world.room_activity == Some(OwnerRoomActivity::Regular)
        {
            // Regular rooms report no encounter and still admit configured/common charge.
            return self.sync_charge_admission(true);
        }
        // The room normally supplies this phase before synchronizing charge.
        // Keep direct calls usable by supplying it here when necessary.
        if self.charge_binding.is_some()
            && self
                .world
                .room_activity
                .map(OwnerRoomActivity::encounter_active)
                != Some(active)
        {
            self.world.room_activity = Some(OwnerRoomActivity::Encounter { active });
        }
        self.sync_charge_admission(active)
    }

    fn receive_applied_damage(
        &mut self,
        damage: crate::game_combat::AppliedDamage,
    ) -> io::Result<()> {
        self.credit_applied_damage(damage)
    }

    fn receive_applied_heal(&mut self, source: u32, target: u32, amount: f64) -> io::Result<()> {
        self.credit_applied_heal(source, target, amount)
    }

    fn target_tags(&self) -> crate::script_init::TargetTagSnapshot {
        self.world
            .target_tag_effects
            .snapshot(self.world.target_tags.as_ref())
    }

    fn target_filter_flags(&self) -> Option<u64> {
        Some(self.world.own_filter_flags())
    }

    fn has_deadeye(&self) -> bool {
        self.world.has_deadeye()
    }

    fn set_deadeye_targets(&mut self, scene: crate::script_player::DeadeyeScene) {
        self.world.deadeye_scene = scene;
    }

    fn take_script_requests(&mut self) -> Vec<crate::game_combat::RoomScriptRequest> {
        self.world.script_requests.take()
    }

    fn receive_respawn(&mut self) -> Result<usize, InitFault> {
        // The body reset message carries the respawn flag in native parameter C6.
        // Subscribed graphs own the mode gates, delay, stimulus and audience.
        self.component.queue_entity_message(
            0x0240_0000_0000_0013,
            [(0xc6, Value::Bool(true))].into_iter().collect(),
        )
    }

    fn receive_room_message(
        &mut self,
        sender: u32,
        message: RoomMessage,
    ) -> Result<usize, InitFault> {
        self.component.queue_room_message_from(sender, message)
    }

    fn set_wheel_cosmetics(&mut self, cosmetics: crate::match_scene::WheelCosmetics) {
        self.world.wheel_cosmetics = cosmetics;
    }

    fn wheel_actions_available(&self) -> bool {
        self.world.graphs.has_asset_category_data() && self.component.snapshot().is_some()
    }

    fn queue_wheel_selection(
        &mut self,
        action: ClientWheelAction,
        command_frame: Option<u32>,
    ) -> Result<usize, InitFault> {
        if !self.world.graphs.has_asset_category_entry(action.asset()) {
            return Ok(0);
        }
        let view_frame = command_frame.filter(|_| action.confirmed());
        let earliest = self
            .wheel_delivery_frame
            .max(view_frame.unwrap_or(self.frame));
        let queued = self.component.queue_command_message(
            action.into_local_message(),
            view_frame,
            earliest,
        )?;
        if queued != 0 {
            self.wheel_delivery_frame = earliest;
            // A reliable selection can arrive on its own. Dispatch it at the
            // current VM time once its command frame has been simulated.
            if earliest <= self.frame {
                self.component.run_now(&mut self.world)?;
            }
        }
        Ok(queued)
    }

    fn queue_wheel_use(
        &mut self,
        report: ClientWheelUse,
        sent_instance_start: Option<u64>,
    ) -> Result<usize, InitFault> {
        use crate::script_init::StateKind;
        if report.entity != self.world.entity {
            return Ok(0);
        }
        let Some(instance) = self
            .component
            .snapshot()
            .and_then(|snapshot| snapshot.instances.get(&report.instance))
        else {
            return Ok(0);
        };
        let states = || instance.states.values().filter(|state| state.active);
        let current = states().any(|state| matches!(state.kind, StateKind::SprayPlacement(Some((unlock, _))) if unlock == report.unlock))
            && states().any(|state| matches!(state.kind, StateKind::ClientPulse { .. }));
        if !current && sent_instance_start != Some(instance.started_at_ms()) {
            return Ok(0);
        }
        let queued = self
            .component
            .queue_local_instance_message(report.instance, report.into_local_message())?;
        if queued != 0 {
            self.component.run_now(&mut self.world)?;
        }
        Ok(queued)
    }

    fn set_spatial_frame(&mut self, frame: Arc<crate::game_combat::RoomSpatialFrame>) {
        self.world
            .pet_positions
            .retain(|&pet_id, _| !frame.is_retired(pet_id));
        self.world.spatial_frame = Some(frame);
    }

    fn set_team_member_counts(&mut self, counts: TeamMemberCounts) {
        self.world.team_member_counts = Some(counts);
    }

    fn set_defenders(&mut self, defenders: Arc<[u32]>) {
        self.world.defenders = defenders;
    }

    fn take_sightings(&mut self) -> Vec<DefenderSighting> {
        std::mem::take(&mut self.world.sightings)
    }

    fn receive_sighting(&mut self, message: DefenderSighting) -> io::Result<()> {
        if !is_junkenstein_mode(self.world.mode)
            || message.target != self.world.entity
            || self.world.vitals.death().is_some()
        {
            return Ok(());
        }
        self.component
            .queue_entity_message_from(message.source.entity, SIGHTING, message.parameters)
            .map_err(unsupported)?;
        self.world.sighting_source = Some(message.source);
        let result = self.component.run_now(&mut self.world).map_err(unsupported);
        self.world.sighting_source = None;
        result
    }

    fn receive_defender_message(&mut self, message: DefenderMessage) -> io::Result<usize> {
        if !is_junkenstein_mode(self.world.mode) || !message.accepts_body(self.world.entity) {
            return Ok(0);
        }
        let queued = self
            .component
            .queue_entity_message_from(message.source, message.message, message.parameters)
            .map_err(unsupported)?;
        self.component
            .run_now(&mut self.world)
            .map_err(unsupported)?;
        Ok(queued)
    }

    fn cinematic_aim(&self) -> Option<[i32; 2]> {
        self.world.cinematic_aim.last().map(|(_, aim)| *aim)
    }

    fn authored_facing(&self) -> Option<[i32; 2]> {
        if self.world.vitals.death().is_some() {
            return None;
        }
        let (_, record) = crate::facing::winner(self.world.facing_claims.iter())?;
        crate::facing::angles(record.direction)
    }

    fn active_tether(&self) -> Option<crate::script_init::TetherConstraint> {
        self.component.active_tether()
    }

    fn ball_roll_speed(&self) -> Option<f64> {
        if self.world.vitals.death().is_some() {
            return None;
        }
        self.world.ball_roll_speed
    }

    fn mover_override(&self) -> Option<crate::mover_settings::MoverSettings> {
        if self.world.vitals.death().is_some() {
            return None;
        }
        self.component
            .active_mover_override()
            .map(crate::mover_settings::MoverSettings::from)
    }

    fn take_body_swap(&mut self) -> Option<u64> {
        self.world.pending_body_swap.take()
    }

    fn deflect_state(&self) -> Option<crate::script_init::DeflectState> {
        if self.world.vitals.death().is_some() {
            return None;
        }
        self.world.deflect.values().last().copied()
    }

    fn combat_modifiers(&self) -> (f64, f64, f64, f64, bool) {
        const OUT_DMG: u64 = 0x0d80_0000_0000_0317;
        const IN_DMG: u64 = 0x0d80_0000_0000_0318;
        const OUT_HEAL: u64 = 0x0d80_0000_0000_0319;
        const IN_HEAL: u64 = 0x0d80_0000_0000_031a;
        let (mut out_d, mut in_d, mut out_h, mut in_h, mut cc_immune) =
            (1.0_f64, 1.0_f64, 1.0_f64, 1.0_f64, false);
        for entry in self.world.combat_mods.values() {
            cc_immune |= entry.cc_immune;
            let apply = |slot: &mut f64| {
                if entry.multiplicative {
                    *slot *= entry.value.max(0.0);
                } else {
                    *slot = (*slot + entry.value).max(0.0);
                }
            };
            match entry.stat {
                OUT_DMG => apply(&mut out_d),
                IN_DMG => apply(&mut in_d),
                OUT_HEAL => apply(&mut out_h),
                IN_HEAL => apply(&mut in_h),
                _ => {}
            }
        }
        (
            out_d.clamp(0.0, 5.0),
            in_d.clamp(0.0, 5.0),
            out_h.clamp(0.0, 5.0),
            in_h.clamp(0.0, 5.0),
            cc_immune,
        )
    }

    fn set_ult_charge_scale(&mut self, scale: f64) {
        if scale.is_finite() && scale >= 0.0 {
            self.ult_charge_scale = scale.clamp(0.0, 10.0);
        }
    }

    fn set_cooldown_scale(&mut self, scale: f64) {
        self.component.set_cooldown_scale(scale);
    }

    fn ult_charge_percent(&self) -> f64 {
        PlayerBodyRuntime::ult_charge_percent(self)
    }

    fn add_ult_charge_points(&mut self, points: f64) -> io::Result<()> {
        PlayerBodyRuntime::add_ult_charge_points(self, points)
    }

    fn set_ult_charge_percent(&mut self, percent: f64) -> io::Result<()> {
        PlayerBodyRuntime::set_ult_charge_percent(self, percent)
    }

    fn reset_all_cooldowns(&mut self) -> io::Result<usize> {
        self.component
            .reset_all_cooldowns(&mut self.world)
            .map_err(unsupported)
    }

    fn is_emoting(&self) -> bool {
        self.component
            .snapshot()
            .is_some_and(|snapshot| snapshot.instances.values().any(|inst| inst.graph == 0x023a))
    }

    fn set_on_fire(&mut self, active: bool) {
        self.world.controller_on_fire = active;
        // Enable Practice Range On-Fire HUD bar (`0x0D80000000006868` in `0x01CF:74` & `0x20E4:12`).
        if self.world.mode == crate::game_content::PRACTICE_RANGE_MODE
            && self
                .component
                .snapshot()
                .is_some_and(|s| s.variables.get(&34841) != Some(&Value::Bool(true)))
        {
            let _ = self
                .component
                .set_entity_variable(34841, Value::Bool(true), &mut self.world);
        }
    }

    fn vitals(&self) -> Option<&Vitals> {
        Some(&self.world.vitals)
    }

    fn take_vitals(&mut self) -> Option<Vitals> {
        Some(std::mem::replace(&mut self.world.vitals, Vitals::empty()))
    }

    fn receive_host_event(
        &mut self,
        event: HostEvent,
        context: HostHealthContext,
    ) -> Result<usize, InitFault> {
        self.component.queue_host_event_with_context(event, context)
    }

    fn receive_authored_hit_feedback(
        &mut self,
        event: HostEvent,
        context: &HostHealthContext,
        variant: f32,
    ) -> Result<usize, InitFault> {
        let result = self
            .component
            .queue_authored_hit_feedback(event, context, variant);
        #[cfg(feature = "inspector")]
        if crate::owner_trace::playtest_enabled() && !matches!(result, Ok(0)) {
            let HostEvent::HealthApplied { target, .. } = event;
            crate::owner_trace::playtest(format_args!(
                "combat hit presentation body={:08X} target={target:08X} game_frame={:?} vm_frame={} critical={:?} queued={result:?}",
                self.world.entity, context.damage_frame, self.frame, context.critical_hit
            ));
        }
        result
    }

    fn receive_authored_elimination_feedback(
        &mut self,
        target: Arc<crate::script_init::HealthTargetFacts>,
    ) -> Result<usize, InitFault> {
        #[cfg(feature = "inspector")]
        let victim = target.target;
        let result = self
            .component
            .queue_authored_elimination_feedback(target, true);
        let result = match result {
            Ok(queued) if queued > 0 => {
                self.component.run_now(&mut self.world)?;
                Ok(queued)
            }
            other => other,
        };
        #[cfg(feature = "inspector")]
        if crate::owner_trace::playtest_enabled() {
            crate::owner_trace::playtest(format_args!(
                "combat elimination presentation body={:08X} target={victim:08X} vm_frame={} queued={result:?}",
                self.world.entity, self.frame
            ));
        }
        result
    }

    fn heal(&mut self, amount: f64) -> f64 {
        let healed = self.world.vitals.heal(amount);
        if healed > 0.0 {
            Self::sync_body_health_logged(self);
        }
        healed
    }

    fn receive_damage(
        &mut self,
        amount: f64,
        source: Death,
        tags: &crate::script_init::HitTags,
    ) -> Damage {
        let result = self.world.vitals.damage(amount, source);
        if result.killed {
            self.world.scripted_motion = None;
            self.world.velocity_changes.clear();
            self.component.set_owner_dead(true);
            if let Err(error) = self.sync_charge_admission(false) {
                eprintln!("Owner death charge cleanup failed: {error}");
            }
        } else if result.dealt > 0.0
            && self.world.spatial_frame.is_some()
            && crate::script_expr::valid_entity(source.killer)
        {
            use crate::game_combat_messages::{DAMAGE_TAKEN_MESSAGE, damage_taken_parameters};
            let hit_dir = self
                .world
                .spatial_frame
                .as_deref()
                .and_then(|frame| frame.actor_eye_position(source.killer))
                .map(|origin| {
                    let dx = self.world.position[0] - origin[0];
                    let dz = self.world.position[2] - origin[2];
                    let len = (dx * dx + dz * dz).sqrt();
                    if len > 1e-4 {
                        [dx / len, 0.0, dz / len]
                    } else {
                        [0.0, 0.0, -1.0]
                    }
                })
                .unwrap_or([0.0, 0.0, -1.0]);
            let params = damage_taken_parameters(result.dealt, hit_dir, source.killer, tags);
            let _ = self.component.queue_entity_message_from(
                source.killer,
                DAMAGE_TAKEN_MESSAGE,
                params,
            );
        }
        Self::sync_body_health_logged(self);
        result
    }

    fn publish_death(
        &mut self,
        killer: u32,
        impact: Option<&crate::game_hitscan::Impact>,
        source: Option<crate::game_combat::DeathSourceFacts>,
    ) -> Result<(), InitFault> {
        // A dead owner may stop sending commands. Execute the event at the
        // current VM clock so the observer projection can publish it immediately.
        if crate::game_hitscan::queue_script_death(&mut self.component, killer, impact, None)? == 0
        {
            return Ok(());
        }
        self.world.death_source = source;
        let result = self.component.run_now(&mut self.world);
        self.world.death_source = None;
        result
    }

    fn status_script(
        &mut self,
        op: crate::game_combat::status_scripts::StatusScriptOp<'_>,
    ) -> Result<Option<u16>, String> {
        let mut world = self.world.clone();
        let outcome = op
            .run(&mut self.component, &mut world)
            .map_err(|error| error.to_string())?;
        self.world = world;
        Ok(outcome)
    }

    fn take_health_update(&mut self) -> Option<EntityComponent> {
        let update = self.world.vitals.take_cumulative_update()?;
        Self::sync_body_health_logged(self);
        Some(update)
    }

    fn snapshot(&self) -> Option<&Snapshot> {
        self.component.snapshot()
    }

    #[cfg(test)]
    fn begin_state(&mut self, instance: u16, raw: u16) -> Result<(), InitFault> {
        self.component.begin_state(instance, raw, &mut self.world)
    }

    #[cfg(test)]
    fn random_checkpoint(&self) -> Option<crate::script_init::RandomCheckpoint> {
        self.component
            .snapshot()
            .map(|_| self.component.random_checkpoint())
    }

    fn encoder(&self) -> SnapshotEncoder<'_> {
        SnapshotEncoder::new(&self.world.graphs, &self.codecs)
    }

    fn advance(&mut self, command: Command, mover: &Mover, step: BodyStep) -> io::Result<()> {
        if command.frame <= self.frame
            || (step != BodyStep::UnposedTimer && command.frame != self.frame.saturating_add(1))
        {
            return Err(unsupported(
                "the owner VM frame is outside the command sequence",
            ));
        }
        #[cfg(feature = "inspector")]
        if step != BodyStep::UnposedTimer && crate::owner_trace::enabled() {
            let buttons = self.component.command_buttons();
            let throttle = [command.right, command.forward];
            if step == BodyStep::UnposedBoundary
                || command.buttons != buttons
                || command.action != 0
                || (command.buttons != 0 && throttle != self.world.throttles)
            {
                crate::owner_trace::record(format_args!(
                    "owner-volley input body={:08x} previous={} frame={} step={step:?} buttons={buttons:#x}->{:#x} action={} right={} forward={}",
                    self.world.entity,
                    self.frame,
                    command.frame,
                    command.buttons,
                    command.action,
                    command.right,
                    command.forward
                ));
            }
        }
        let pose = mover.pose();
        #[cfg(feature = "inspector")]
        let before_effects = crate::owner_trace::playtest_enabled().then(|| {
            self.component
                .snapshot()
                .and_then(|snapshot| snapshot.frame_actions.back())
                .map_or(0, |action| action.callout.serial)
        });
        self.world.position = pose.position;
        self.world.eye_position = script_eye_position(self.world.hero, mover);
        // Command gaps and timer advances retain the last movement record.
        if step == BodyStep::Posed {
            self.world.movement = pose;
            self.world.collision_category = mover.collision_category();
            self.world.record_command_view(command.frame);
        }
        if step == BodyStep::Posed {
            self.world.throttles = [command.right, command.forward];
            self.report_move(command.frame, mover)
                .map_err(unsupported)?;
            self.component
                .advance_command(command, &mut self.world)
                .map_err(unsupported)?;
        } else {
            let boundary = step == BodyStep::UnposedBoundary;
            if boundary {
                self.world.throttles = [0; 2];
            }
            self.component
                .advance_unposed(command.frame, boundary, &mut self.world)
                .map_err(unsupported)?;
        }
        self.component
            .poll_voice(&mut self.world)
            .map_err(unsupported)?;
        if step != BodyStep::UnposedTimer {
            self.modifiers = self
                .component
                .movement_modifiers(&mut self.world)
                .map_err(unsupported)?;
        }
        self.frame = command.frame;
        #[cfg(feature = "inspector")]
        if let Some((before, snapshot)) = before_effects.zip(self.component.snapshot()) {
            for action in snapshot
                .frame_actions
                .iter()
                .rev()
                .take_while(|action| action.callout.serial > before)
            {
                let callout = action.callout;
                let Some(instance) = snapshot.instances.get(&callout.instance) else {
                    continue;
                };
                let effect = self
                    .world
                    .graphs
                    .graph(instance.graph)
                    .and_then(|graph| graph.node(callout.position))
                    .filter(|node| node.class == "STUStatescriptActionEffect")
                    .and_then(|node| {
                        crate::script_graph::asset_guid(&node.fields["m_effect"]["m_effect"])
                    });
                if let Some(effect) = effect {
                    crate::owner_trace::playtest(format_args!(
                        "combat effect emitted body={:08X} vm_frame={} serial={} effect={effect:016X}",
                        snapshot.entity, self.frame, callout.serial
                    ));
                }
            }
        }
        Ok(())
    }

    fn frame_ms(&self) -> u32 {
        FRAME_MS as u32
    }

    fn take_velocity_changes(&mut self) -> Vec<(u32, crate::mover::VelocityChange)> {
        if self.world.vitals.death().is_some() {
            self.world.velocity_changes.clear();
        }
        std::mem::take(&mut self.world.velocity_changes)
    }

    fn take_scripted_motion(&mut self) -> Option<(u32, ScriptedMotion)> {
        self.world
            .scripted_motion
            .take()
            .filter(|_| self.world.vitals.death().is_none())
    }

    fn owner_move(&self) -> Option<(MoveToKey, [f64; 3], f64)> {
        self.live_move()
    }

    fn movement_modifiers(&self) -> Option<&BTreeMap<ModifierKey, Modifier>> {
        self.modifiers.as_ref()
    }

    fn projectile_leases(&self) -> Option<&ProjectileLeases> {
        (!self.leases.blocks().is_empty()).then_some(&self.leases)
    }

    fn take_shots(&mut self, command: Option<Command>, mover: &Mover) -> Vec<PosedShot> {
        let shots = self.component.take_reported_shots().unwrap_or_default();
        #[cfg(feature = "inspector")]
        if !shots.is_empty() && crate::owner_trace::enabled() {
            let pose_frame = command.map(|command| command.frame);
            for report in &shots {
                let shot = report.event;
                crate::owner_trace::record(format_args!(
                    "owner-volley vm-shot body={:08x} shot_frame={} pose_frame={pose_frame:?} instance={} state={} counter={} start_ms={} ordinal={}",
                    shot.entity,
                    shot.frame,
                    shot.instance,
                    shot.raw_state,
                    shot.activation,
                    shot.activation_ms,
                    shot.ordinal
                ));
            }
        }
        let Some(command) = command else {
            return Vec::new();
        };
        let [x, y, z] = mover.pose().position;
        let hero = self.world.hero;
        let origin = [
            x,
            y + mover.eye_height() + f64::from(mover.spring_offset()),
            z,
        ];
        let mut posed = Vec::with_capacity(shots.len());
        let mut projected_observer = None;
        let mut pose = |report: ReportedShot| -> Option<()> {
            let shot = report.event;
            let node = self.component.shot_node(&shot)?;
            let flashbang = LEGACY_PROJECTILE_CONTROL.matches_shot(hero, shot.graph, node.pos);
            let stun_duration = flashbang
                .then(|| {
                    LEGACY_PROJECTILE_CONTROL
                        .duration(&self.world.graphs)
                        .cloned()
                })
                .flatten();
            // In retail 104319 data, every non-headshotting weapon explicitly sets
            // `m_A872C70E = 1`, while headshotting weapons omit it and default to 2.0x.
            let default_critical = 2.0;
            let graphs_ref = Arc::clone(&self.world.graphs);
            let deadeye_has_targets = self.world.deadeye_scene.collision.is_some()
                || !self.world.deadeye_scene.targets.is_empty();
            let deadeye_targets_non_empty = !self.world.deadeye_scene.targets.is_empty();
            let locked_target = self.component.snapshot().and_then(|snapshot| {
                // Cassidy Deadeye (`536` + `5874`) or Soldier: 76 Tactical Visor (`2337`).
                if let Some(Value::Entity(entity)) = snapshot
                    .instances
                    .get(&shot.instance)
                    .and_then(|inst| inst.variables.get(&536))
                {
                    if let Some(Value::Array(visible)) = snapshot.variables.get(&5874) {
                        if visible.contains(&Value::Entity(*entity)) {
                            return Some(*entity);
                        }
                    }
                }
                if let Some(&Value::Entity(entity)) = snapshot.variables.get(&2337) {
                    if crate::script_expr::valid_entity(entity) && entity != shot.entity {
                        return Some(entity);
                    }
                }
                None
            });
            let locked_aim_dir = locked_target.and_then(|entity| {
                if deadeye_has_targets {
                    DeadeyeScene::shot_direction(&self.world.deadeye_scene, origin, entity)
                } else {
                    self.world
                        .spatial_frame
                        .as_deref()?
                        .target_aim_direction(origin, entity)
                }
            });
            let mut evaluate = |config: &Json| {
                self.component
                    .evaluate_shot_config(&shot, config, &mut self.world)
            };
            let impact_spawn = ImpactSpawn::from_volley(node, &mut evaluate).ok().flatten();
            if impact_spawn
                == Some(ImpactSpawn {
                    definition: BIOTIC_FIELD_ENTITY,
                    on_spawn_graph: None,
                })
                && node
                    .field("m_projectileEntity.m_entityDef")
                    .and_then(|config| evaluate(config).ok())
                    == Some(Value::Asset(BIOTIC_CANISTER))
                && self.leases.entity(BIOTIC_CANISTER, shot.frame, 0).is_some()
            {
                posed.push(PosedShot::BioticField(PosedBioticField {
                    shot,
                    feet: [x, y, z],
                }));
                return Some(());
            }
            let speed = node.field("m_projectileSpeed").unwrap_or(&Json::Null);
            if !speed.is_null() && to_float(&evaluate(speed).ok()?) != 0.0 {
                let gravity = authored_gravity(&node.fields, &mut evaluate).ok()?;
                let count =
                    authored_number(node.field("m_numProjectilesPerShot"), 1.0, &mut evaluate)
                        .ok()?;
                if !count.is_finite() || count < 1.0 || count > 32.0 {
                    return None;
                }
                let ballistics = ProjectileBallistics::from_authored_with_critical(
                    node,
                    default_critical,
                    &mut evaluate,
                )
                .ok()??;
                let direct_policy = node
                    .field("m_6396149F")
                    .map(crate::game_hitscan::HitTargetPolicy::from_authored);
                let blast_policy = node
                    .field("m_D2D11CE9")
                    .map(crate::game_hitscan::HitTargetPolicy::from_authored);
                let target_policy = match (
                    direct_policy.filter(|_| ballistics.direct.is_some_and(|h| h.amount > 0.0)),
                    blast_policy.filter(|_| {
                        ballistics
                            .blast
                            .as_ref()
                            .is_some_and(|b| b.hit.amount > 0.0 || b.knockback.is_some())
                    }),
                ) {
                    (Some(d), Some(b)) => d.union(b),
                    (Some(d), None) => d,
                    (None, Some(b)) => b,
                    (None, None) => crate::game_hitscan::HitTargetPolicy::default(),
                };
                let piercing = node
                    .field("m_6396149F.m_0A5ED456")
                    .filter(|v| !v.is_null())
                    .and_then(|v| evaluate(v).ok())
                    .is_some_and(|v| crate::script_expr::truthy(&v))
                    && ballistics
                        .blast
                        .as_ref()
                        .is_none_or(|b| b.hit.amount <= 0.0 && b.knockback.is_none());
                let post_bounce_fuse = node
                    .field("m_B752FBAE")
                    .filter(|v| !v.is_null())
                    .and_then(|v| evaluate(v).ok())
                    .map(|v| to_float(&v))
                    .filter(|v| v.is_finite() && *v > 0.0);
                let sticky_delay = node
                    .field("m_55E8761C.m_12654E6E")
                    .filter(|v| !v.is_null())
                    .and_then(|v| evaluate(v).ok())
                    .is_some_and(|v| crate::script_expr::truthy(&v))
                    .then_some(1.0);
                let persist_entity = node
                    .field("m_55E8761C.m_147777B3")
                    .filter(|v| !v.is_null())
                    .and_then(|v| evaluate(v).ok())
                    .is_some_and(|v| crate::script_expr::truthy(&v));
                let direct_effect = node
                    .field("m_6396149F.m_modifyHealth.m_277A4ECA")
                    .filter(|v| !v.is_null())
                    .and_then(|v| evaluate(v).ok())
                    .and_then(|v| match v {
                        Value::Asset(guid) if guid != 0 => Some(guid),
                        _ => None,
                    });
                let blast_effect = node
                    .field("m_D2D11CE9.m_modifyHealth.m_277A4ECA")
                    .filter(|v| !v.is_null())
                    .and_then(|v| evaluate(v).ok())
                    .and_then(|v| match v {
                        Value::Asset(guid) if guid != 0 => Some(guid),
                        _ => None,
                    });
                // If the volley specifies a persistent projectile entity (`m_147777B3 = true`)
                // without an explicit `m_49CBA3BC` impact spawn (e.g. Concussion Mine,
                // Dynamite, Translocator, Sentry Turret, Pulse Bomb), promote its authored
                // entity definition and primary graph from the retail entity catalog.
                let impact_spawn = impact_spawn.or_else(|| {
                    if !persist_entity {
                        return None;
                    }
                    let definition = ballistics.definition?;
                    let on_spawn_graph = graphs_ref.entity_body(definition).and_then(|b| {
                        b.graphs
                            .iter()
                            .filter_map(|binding| binding.graph)
                            .find(|&g| !matches!(g, 0x0015 | 0x0033 | 0x0043 | 0x004b | 0x08b6))
                            .map(|idx| 0x0580_0000_0000_0000 | u64::from(idx))
                    });
                    Some(ImpactSpawn {
                        definition,
                        on_spawn_graph,
                    })
                });
                if ballistics.direct.is_some_and(|hit| hit.amount < 0.0)
                    || ballistics
                        .blast
                        .as_ref()
                        .is_some_and(|blast| blast.hit.amount < 0.0)
                {
                    return None;
                }
                if impact_spawn.is_none()
                    && ballistics.definition.is_none()
                    && !ballistics.direct.is_some_and(|hit| hit.amount > 0.0)
                    && !ballistics
                        .blast
                        .as_ref()
                        .is_some_and(|blast| blast.hit.amount > 0.0 || blast.knockback.is_some())
                {
                    return None;
                }
                let predicted = node.is_networked()
                    && local_prediction(node)
                    && predicted_sync(node, &mut evaluate).ok()?
                    && count == 1.0
                    && ballistics
                        .definition
                        .is_some_and(|def| self.leases.entity(def, shot.frame, 0).is_some());
                let stun = if flashbang {
                    let effect = node.field("m_D2D11CE9.m_modifyHealth.m_277A4ECA")?;
                    if !LEGACY_PROJECTILE_CONTROL.has_effect_marker(&evaluate(effect).ok()?) {
                        return None;
                    }
                    Some(Stun::from_seconds(to_float(
                        &evaluate(stun_duration.as_ref()?).ok()?,
                    ))?)
                } else {
                    None
                };
                let skip_owner = local_prediction(node);
                let can_present_volley = !predicted
                    && node.is_networked()
                    && ballistics.definition.is_some()
                    && ballistics.speed.is_finite()
                    && ballistics.speed > 0.0
                    && ballistics.lifetime.is_finite()
                    && ballistics.lifetime > 0.0
                    && node.field("m_49CBA3BC").is_none_or(|value| {
                        value.is_null() || value.as_array().is_some_and(Vec::is_empty)
                    });
                if count == 1.0 {
                    let base_aim = locked_aim_dir.unwrap_or_else(|| aim_direction(&command));
                    let direction = posed_projectile_direction(node, base_aim, &mut evaluate)?;
                    let presentation = if can_present_volley {
                        self.observer_projectile(
                            &mut projected_observer,
                            report,
                            &ballistics,
                            origin,
                            direction,
                            skip_owner,
                        )
                    } else {
                        None
                    };
                    posed.push(PosedShot::Projectile(Box::new(PosedProjectile {
                        stun,
                        shot,
                        ballistics,
                        origin,
                        direction,
                        gravity,
                        predicted,
                        target_policy,
                        piercing,
                        impact_spawn,
                        post_bounce_fuse,
                        sticky_delay,
                        direct_effect,
                        blast_effect,
                        presentation,
                    })));
                } else {
                    if !node.field("m_5EC1EB38").is_some_and(Json::is_object) {
                        return None;
                    }
                    let base_dir =
                        posed_projectile_direction(node, aim_direction(&command), &mut evaluate)
                            .unwrap_or_else(|| aim_direction(&command));
                    let mut directions = Vec::new();
                    for_each_authored_hitscan_direction(
                        node,
                        &report,
                        base_dir,
                        &mut evaluate,
                        |direction| directions.push(direction),
                    );
                    let presentation = if can_present_volley {
                        self.observer_projectile(
                            &mut projected_observer,
                            report,
                            &ballistics,
                            origin,
                            aim_direction(&command),
                            skip_owner,
                        )
                    } else {
                        None
                    };
                    for direction in directions {
                        posed.push(PosedShot::Projectile(Box::new(PosedProjectile {
                            stun,
                            shot,
                            ballistics: ballistics.clone(),
                            origin,
                            direction,
                            gravity,
                            predicted: false,
                            target_policy,
                            piercing,
                            impact_spawn,
                            post_bounce_fuse,
                            sticky_delay,
                            direct_effect,
                            blast_effect,
                            presentation: presentation.clone(),
                        })));
                    }
                }
                return Some(());
            }
            let hit_config = node.field("m_6396149F")?;
            let hit = HitDefinition::from_authored_with_critical(
                hit_config,
                default_critical,
                &mut evaluate,
            )
            .ok()??;
            if hit.amount <= 0.0 {
                return None;
            }
            let hit_policy = crate::game_hitscan::HitTargetPolicy::from_authored(hit_config);
            let reach = node.field("m_FEC435C6").unwrap_or(&Json::Null);
            let reach = if reach.is_null() {
                0.0
            } else {
                to_float(&evaluate(reach).ok()?)
            };
            let mut push_hitscan = |direction| {
                let shot_box = Box::new(PosedHitscan {
                    source: shot.entity,
                    origin,
                    direction,
                    command,
                    shot_frame: shot.frame,
                    reach,
                    hit,
                });
                if hit_policy.affects_allies || hit_policy.healing {
                    posed.push(PosedShot::AllyHitscan(shot_box, hit_policy));
                } else {
                    posed.push(PosedShot::Hitscan(shot_box));
                }
            };
            if let Some(direction) = locked_aim_dir {
                push_hitscan(direction);
            } else if locked_target.is_some() && deadeye_targets_non_empty {
                return None;
            } else {
                for_each_authored_hitscan_direction(
                    node,
                    &report,
                    aim_direction(&command),
                    &mut evaluate,
                    push_hitscan,
                );
            }
            Some(())
        };
        for report in shots {
            pose(report);
        }
        posed
    }
}
