//! Immutable-per-room prepared scene state and simulation accessors.

use super::*;
use std::time::Duration;

pub(super) struct CustomMode {
    pub(super) start: CustomMatchStart,
    pub(super) graphs: Arc<GraphStore>,
    pub(super) variables: Variables,
    pub(super) presentation: (MatchPhase, u32),
    pub(super) codecs: Option<Arc<crate::script_codecs::CodecTable>>,
}

pub struct PreparedScene {
    pub(super) junkenstein_validation:
        Option<crate::pve_encounter::validation::JunkensteinValidation>,
    pub(super) map: GameMap,
    pub(crate) custom_slot: Option<i8>,
    #[cfg(any(test, feature = "inspector"))]
    pub(super) movement_cache: String,
    pub(super) ground: Arc<CollisionScene>,
    pub(super) combat_rays: Arc<CollisionScene>,
    pub(super) combat_sweeps: Option<Arc<ContactWorld>>,
    pub(super) contacts: Option<Arc<ContactWorld>>,
    pub(super) breakables: Arc<crate::collision_scene::BreakableState>,
    pub(super) controller: Controller,
    pub(super) mode_ui: BitWriter,
    pub(super) custom_mode: Option<CustomMode>,
    pub(super) initial_board: Component,
    pub(super) simulation: RoomSimulation,
    pub(super) scripted_bots: usize,
    pub(super) movers: Arc<MoverCatalog>,
    pub(super) hurt_volumes: Arc<HurtVolumeCatalog>,
    pub(super) heroes: Arc<HeroCatalog>,
    pub(super) pools: Arc<PoolCatalog>,
    pub(crate) owner_body_factory: Option<Arc<OwnerBodyFactory>>,
}

impl PreparedScene {
    #[cfg(test)]
    pub(crate) fn fixture_for_owner_admission(heroes: HeroCatalog) -> Self {
        Self::fixture_for_owner_admission_with_bots(heroes, Vec::new())
    }

    #[cfg(test)]
    pub(crate) fn fixture_for_owner_admission_with_bots(
        heroes: HeroCatalog,
        bots: Vec<Bot>,
    ) -> Self {
        let mut scene = Self::fixture_for_bots(bots);
        scene.contacts = Some(Arc::new(ContactWorld::new(Vec::new()).unwrap()));
        scene.controller = Controller::fixture_for_owner_admission();
        let stats = PlayerStats::default();
        scene.initial_board =
            StatsCatalog::default().component(&stats, scene.map.mode_guid, None, None);
        scene.heroes = Arc::new(heroes);
        scene
    }

    #[cfg(test)]
    pub(crate) fn test_combat_rays(&self) -> Arc<CollisionScene> {
        Arc::clone(&self.combat_rays)
    }

    #[cfg(test)]
    pub(crate) fn fixture_enable_room_health_scripts(&mut self) {
        self.simulation.enable_host_health_events();
        self.simulation.enable_spatial_queries(
            self.map.free_for_all,
            Arc::clone(&self.ground),
            crate::script_graph::RoomSpatialNeeds::Queries,
        );
    }

    #[cfg(test)]
    pub(crate) fn fixture_set_hurt_volumes(&mut self, volumes: HurtVolumeCatalog) {
        self.hurt_volumes = Arc::new(volumes);
    }

    #[cfg(test)]
    pub(crate) fn fixture_set_movement_probe(&mut self, ground: Arc<CollisionScene>) {
        self.ground = ground;
    }

    #[cfg(test)]
    pub(crate) fn fixture_set_combat_voice(&mut self, catalog: CombatVoiceCatalog) {
        self.simulation.set_combat_voice(Arc::new(catalog));
    }

    #[cfg(test)]
    pub(crate) fn fixture_for_bots(bots: Vec<Bot>) -> Self {
        use crate::collision_cache::{CollisionCache, Mesh, Triangle};

        let cache = CollisionCache {
            header: serde_json::from_str(
                r#"{"meshes":[[3,1]],"hulls":0,"models":0,"placements":0,"identifiers":[]}"#,
            )
            .unwrap(),
            meshes: vec![Mesh {
                vertices: vec![[-1.0, 0.0, 0.0], [-1.0, 2.0, 0.0], [-1.0, 0.0, 1.0]],
                triangles: vec![Triangle {
                    corners: [0, 1, 2],
                    wings: [-1; 3],
                    mask: 0x18,
                    surface: 0,
                }],
            }],
            hulls: vec![],
            models: vec![],
            placements: vec![],
        };
        let ground = Arc::new(CollisionScene::movement_probe(&cache, &BTreeSet::new()).unwrap());
        let breakables = Arc::new(crate::collision_scene::BreakableState::new());
        Self {
            junkenstein_validation: None,
            map: GameMap::practice_range(),
            custom_slot: None,
            #[cfg(any(test, feature = "inspector"))]
            movement_cache: String::new(),
            combat_rays: Arc::clone(&ground),
            combat_sweeps: None,
            ground,
            contacts: None,
            breakables,
            controller: Controller::fixture_for_test(),
            mode_ui: BitWriter::new(),
            custom_mode: None,
            initial_board: Component {
                index: 0,
                fields: Vec::new(),
            },
            simulation: RoomSimulation::new(bots).unwrap(),
            scripted_bots: 0,
            movers: Arc::new(MoverCatalog::default()),
            hurt_volumes: Arc::new(HurtVolumeCatalog::default()),
            heroes: Arc::new(HeroCatalog::default()),
            pools: Arc::new(PoolCatalog::default()),
            owner_body_factory: None,
        }
    }

    pub(crate) fn simulation(&self) -> &RoomSimulation {
        &self.simulation
    }

    pub(crate) fn simulation_mut(&mut self) -> &mut RoomSimulation {
        &mut self.simulation
    }

    pub(crate) fn simulation_and_rays(&mut self) -> (&mut RoomSimulation, &TriangleIndex) {
        (&mut self.simulation, self.ground.triangles())
    }

    pub(crate) fn enemy_projections(
        &self,
    ) -> Result<impl Iterator<Item = crate::script_snapshot_wire::SceneProjection<'_>>, CombatError>
    {
        self.simulation.enemy_projections()
    }
    #[cfg(test)]
    pub(crate) fn gate_projection(
        &self,
    ) -> Option<crate::script_snapshot_wire::SceneProjection<'_>> {
        self.simulation.gate_projection()
    }
    pub(crate) fn take_enemy_creates(&mut self) -> Vec<EntityUpdate> {
        self.simulation.take_created()
    }
    #[cfg(test)]
    pub(crate) fn take_encounter_voice_requests(
        &mut self,
    ) -> Vec<crate::pve_encounter::voice::VoiceRequest> {
        self.simulation.take_encounter_voice_requests()
    }
    pub fn map(&self) -> &GameMap {
        &self.map
    }

    pub fn scripted_bots(&self) -> usize {
        self.scripted_bots
    }

    pub(crate) fn bot_scripts_ready(&self) -> bool {
        self.scripted_bots == 0 || is_practice_range(&self.map)
    }

    pub(crate) fn movement_ready(&self) -> bool {
        (self.custom_slot.is_some() || is_practice_range(&self.map) || self.map.is_junkenstein())
            && self.bot_scripts_ready()
            && self.contacts.is_some()
    }

    #[cfg(test)]
    pub(crate) fn movement_index(&self) -> &TriangleIndex {
        self.ground.triangles()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn debug_geometry(&self) -> DebugGeometry {
        DebugGeometry::new(
            &self.map,
            self.movement_cache.clone(),
            MOVER_CATEGORY,
            Arc::clone(&self.ground),
            self.contacts.clone(),
        )
    }

    pub(crate) fn player_spawn_mover(&self, body: u64, team: u8) -> io::Result<Mover> {
        let slot = self.custom_slot.map_or(0, |slot| slot as usize);
        self.player_spawn_mover_for_slot(body, team, slot)
    }

    pub(crate) fn player_spawn_mover_for_slot(
        &self,
        body: u64,
        team: u8,
        slot: usize,
    ) -> io::Result<Mover> {
        let (position, yaw) = self
            .map
            .spawn_for_stage(
                team,
                slot,
                self.payload_round()
                    .map(|round| round.room_stage())
                    .or_else(|| self.capture_round().map(|round| round.stage))
                    .or_else(|| self.control_round().map(|round| round.stage()))
                    .unwrap_or(0),
            )
            .ok_or_else(|| invalid("no spawn for the selected hero"))?;
        let yaw = (yaw * 65536.0 / 360.0).round_ties_even() as i32;
        self.player_spawn_mover_at(body, team, position, yaw)
    }

    pub(crate) fn player_spawn_mover_at(
        &self,
        body: u64,
        team: u8,
        position: [f64; 3],
        yaw: i32,
    ) -> io::Result<Mover> {
        let contacts = self
            .simulation
            .payload_contact_world()
            .or(self.contacts.as_ref())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::Unsupported,
                    "the scene has no native player collision",
                )
            })?;
        let mut mover = Mover::new(
            *self.movers.for_body(Some(body)),
            Arc::clone(contacts),
            position,
            yaw,
            mover_category(&self.map, team),
        )
        .map_err(invalid)?;
        mover.set_breakables(Arc::clone(&self.breakables));
        Ok(mover)
    }

    pub(crate) fn owner_body_runtime(
        &self,
        hero: &Hero,
        spawn: OwnerBodySpawn,
    ) -> io::Result<Option<Box<dyn OwnerBodyRuntime>>> {
        let Some(factory) = &self.owner_body_factory else {
            return Ok(None);
        };
        let runtime = factory(
            hero,
            spawn,
            self.owner_body_setup(spawn.controller, hero.body),
        )?;
        if let Some(runtime) = &runtime {
            crate::game_replication::validate_body_baselines(runtime.as_ref(), spawn.entity)
                .map_err(|error| io::Error::new(io::ErrorKind::Unsupported, error))?;
        }
        Ok(runtime)
    }

    pub(super) fn owner_body_setup(&self, owner: u32, body: u64) -> OwnerBodySetup {
        OwnerBodySetup {
            team_member_counts: self.simulation.team_member_counts(),
            spatial_frame: self.simulation.owner_startup_spatial_frame(owner),
            room_activity: Some(self.owner_room_activity()),
            mover_settings: *self.movers.for_body(Some(body)),
        }
    }

    pub(crate) fn owner_room_activity(&self) -> OwnerRoomActivity {
        if self.map.is_junkenstein() {
            OwnerRoomActivity::Encounter {
                active: self.simulation.encounter_active(),
            }
        } else {
            OwnerRoomActivity::Regular
        }
    }

    pub fn heroes(&self) -> &HeroCatalog {
        &self.heroes
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn heroes_arc(&self) -> Arc<HeroCatalog> {
        Arc::clone(&self.heroes)
    }

    #[cfg(feature = "lua-mods")]
    pub(crate) fn combat_rays_arc(&self) -> Arc<CollisionScene> {
        Arc::clone(&self.combat_rays)
    }

    pub fn pools(&self) -> &PoolCatalog {
        &self.pools
    }

    pub fn controller(&self) -> Controller {
        self.controller
    }

    pub fn mode_ui(&self) -> &BitWriter {
        &self.mode_ui
    }

    pub(crate) fn custom_mode_view(&self) -> Option<(MatchPhase, u32)> {
        self.custom_mode.as_ref().map(|mode| mode.presentation)
    }

    pub(crate) fn selected_phase(&self) -> Option<MatchPhase> {
        self.custom_mode_view().map(|(phase, _)| phase)
    }

    pub(crate) fn holds_assembly_spawn(&self, team: u8) -> bool {
        self.has_objective_presenter()
            && self.control_round().is_none()
            && team == crate::pvp_mode::FIRST_ATTACKING_TEAM
            && self.selected_phase() == Some(MatchPhase::Assemble)
    }

    pub(crate) fn hero_select_objective(&self, team: u8) -> Option<(u64, u8)> {
        use crate::pvp_mode::FIRST_ATTACKING_TEAM;
        if self.control_round().is_some() {
            return None;
        }
        if team >= 2 || !self.has_objective_presenter() {
            return None;
        }
        Some((
            if team == FIRST_ATTACKING_TEAM {
                0x0de0_0000_0000_6e64
            } else {
                0x0de0_0000_0000_6e63
            },
            team,
        ))
    }

    pub(crate) fn payload_round(&self) -> Option<&crate::pvp_mode::EscortRound> {
        self.map.payload_route.as_ref()?;
        self.simulation.pvp_objective()?.escort_round()
    }

    pub(crate) fn payload_presentation(
        &self,
    ) -> Option<(&GraphStore, &crate::script_codecs::CodecTable)> {
        self.payload_round()?;
        self.objective_presentation()
    }

    pub(crate) fn capture_round(&self) -> Option<&crate::pvp_mode::AssaultRound> {
        self.map.capture_course.as_ref()?;
        match self.simulation.pvp_objective()? {
            crate::pvp_mode::PvpObjectiveRuntime::Assault(round) => Some(round),
            crate::pvp_mode::PvpObjectiveRuntime::Hybrid(round) => Some(&round.capture),
            _ => None,
        }
    }

    pub(crate) fn control_round(&self) -> Option<&crate::pvp_mode::ControlRound> {
        match self.simulation.pvp_objective()? {
            crate::pvp_mode::PvpObjectiveRuntime::Control(round) => Some(round),
            _ => None,
        }
    }

    pub(crate) fn has_objective_presenter(&self) -> bool {
        self.objective_presentation().is_some()
    }

    pub(crate) fn objective_presentation(
        &self,
    ) -> Option<(&GraphStore, &crate::script_codecs::CodecTable)> {
        if self.payload_round().is_none()
            && self.capture_round().is_none()
            && self.control_round().is_none()
        {
            return None;
        }
        let mode = self.custom_mode.as_ref()?;
        Some((&mode.graphs, mode.codecs.as_deref()?))
    }

    pub(crate) fn request_custom_mode_start(&mut self) -> bool {
        self.custom_mode
            .as_mut()
            .is_some_and(|mode| mode.start.request_start())
    }

    pub(crate) fn advance_custom_mode(
        &mut self,
        now: Duration,
        loaded: [usize; 2],
    ) -> io::Result<()> {
        let Some(mode) = self.custom_mode.as_mut() else {
            return Ok(());
        };
        mode.start.advance(now, loaded, &self.map.team_sizes);
        let mut presentation = mode.start.presentation(now);
        if presentation.0 == MatchPhase::Assemble {
            if let Some(round) = self.simulation.pvp_objective_mut() {
                round.start_custom_assembly(now.as_secs_f64());
            }
        }
        if let Some(end) = mode.start.assembly_ends().filter(|&end| now >= end) {
            if let Some(pvp) = self.simulation.pvp_objective_mut() {
                pvp.start_custom_setup(end.as_secs_f64());
                presentation = (pvp.phase(), pvp.phase_seconds(now.as_secs_f64()));
            }
        }
        if presentation != mode.presentation {
            if (self.map.payload_route.is_none() && self.map.capture_course.is_none())
                || mode.codecs.is_none()
            {
                self.mode_ui = custom_mode_presentation(
                    &mode.graphs,
                    self.map.mode_guid,
                    &mode.variables,
                    presentation.0,
                    presentation.1,
                )?;
            }
            mode.presentation = presentation;
        }
        Ok(())
    }

    pub(crate) fn hide_assemble(&self) -> bool {
        !self.map.is_junkenstein()
            && !self
                .custom_mode_view()
                .is_some_and(|(phase, _)| phase == MatchPhase::Assemble)
    }

    pub fn junkenstein_gate(&self) -> Option<&JunkensteinGate> {
        self.simulation.junkenstein_gate()
    }

    #[cfg(test)]
    pub(crate) fn gate_snapshot(&self) -> Option<&script_init::Snapshot> {
        self.simulation.gate_snapshot()
    }

    #[cfg(test)]
    pub(crate) fn gate_resolved_variable(
        &self,
        instance: u16,
        variable: crate::script_expr::Variable,
    ) -> Option<Value> {
        self.simulation.gate_resolved_variable(instance, variable)
    }

    #[cfg(test)]
    pub(crate) fn gate_team(&self) -> Option<i8> {
        self.simulation.gate_team()
    }

    #[cfg(test)]
    pub(crate) fn step_test_gate_frame(
        &mut self,
        frame: u32,
        message: u64,
        parameters: Variables,
    ) -> Result<(), script_init::InitFault> {
        self.simulation
            .step_test_gate_frame(frame, message, parameters)
    }

    #[cfg(test)]
    pub(crate) fn reset_test_bodies(&mut self) {
        self.simulation.reset_test_bodies();
    }

    #[cfg(test)]
    pub(crate) fn queue_test_encounter_entity_message(
        &mut self,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, script_init::InitFault> {
        self.simulation
            .queue_test_encounter_entity_message(message, parameters)
    }

    #[cfg(test)]
    pub(crate) fn queue_test_encounter_frame_message(
        &mut self,
        frame: u32,
        instance: u16,
        message: u64,
        parameters: Variables,
    ) -> Result<usize, script_init::InitFault> {
        self.simulation
            .queue_test_encounter_frame_message(frame, instance, message, parameters)
    }

    #[cfg(test)]
    pub(crate) fn queue_test_spawn_result(
        &mut self,
        frame: u32,
        key: script_init::SpawnSearchKey,
        positions: Vec<[f64; 3]>,
    ) -> Result<(), script_init::InitFault> {
        self.simulation
            .queue_test_spawn_result(frame, key, positions)
    }

    #[cfg(test)]
    pub(crate) fn encounter_spawn_search_keys(
        &self,
    ) -> Vec<(script_init::SpawnSearchKey, script_init::SpawnSearch)> {
        self.simulation.encounter_spawn_search_keys()
    }

    #[cfg(test)]
    pub(crate) fn set_test_gate_capacity(&mut self, capacity: f64) {
        self.set_gate_capacity(capacity).unwrap();
    }

    pub(super) fn set_gate_capacity(&mut self, capacity: f64) -> io::Result<()> {
        self.simulation.set_gate_capacity(capacity)
    }

    pub(crate) fn protects_cassidy(&self, hero: Option<u64>) -> bool {
        self.junkenstein_validation
            .is_some_and(|settings| settings.protects(hero))
    }

    pub(crate) fn encounter_snapshot(&self) -> Option<&script_init::Snapshot> {
        self.simulation.encounter_snapshot()
    }

    #[cfg(test)]
    pub(crate) fn encounter_countdown(&self) -> Option<CountdownFrame> {
        self.simulation.encounter_countdown()
    }

    #[cfg(test)]
    pub(crate) fn encounter_countdown_wire(
        &self,
        countdown: &CountdownFrame,
    ) -> io::Result<Option<BitWriter>> {
        self.simulation.encounter_countdown_wire(countdown)
    }

    #[cfg(test)]
    pub(crate) fn encounter_screens_complete(&self) -> bool {
        self.simulation.encounter_screens_complete()
    }

    #[cfg(test)]
    pub(crate) fn encounter_screen_delta(
        &self,
        previous: Option<&ProjectedSnapshot>,
    ) -> io::Result<Option<(ProjectedSnapshot, BitWriter)>> {
        self.simulation.encounter_screen_delta(previous)
    }

    #[cfg(test)]
    pub(crate) fn encounter_notification_delta(
        &self,
        previous: Option<&ProjectedSnapshot>,
    ) -> io::Result<Option<(ProjectedSnapshot, BitWriter)>> {
        self.simulation.encounter_notification_delta(previous)
    }

    #[cfg(test)]
    pub(crate) fn encounter_phase_wire(
        &self,
        delivered: &std::collections::BTreeMap<u16, u16>,
    ) -> io::Result<Option<(u16, u16, BitWriter)>> {
        self.simulation.encounter_phase_wire(delivered)
    }

    #[cfg(test)]
    pub(crate) fn encounter_active(&self) -> bool {
        self.simulation.encounter_active()
    }

    #[cfg(test)]
    pub(crate) fn encounter_match_result(&self) -> Option<Component> {
        self.simulation.encounter_match_result()
    }

    #[cfg(test)]
    pub(crate) fn encounter_post_match_wire(&self) -> io::Result<Option<BitWriter>> {
        self.simulation.encounter_post_match_wire()
    }

    #[cfg(test)]
    pub(crate) fn encounter_result_wire(&self) -> io::Result<Option<BitWriter>> {
        self.simulation.encounter_result_wire()
    }

    #[cfg(test)]
    pub(crate) fn encounter_outcome_wire(&self, stamp: u32) -> io::Result<Option<BitWriter>> {
        self.simulation.encounter_outcome_wire(stamp)
    }

    pub(crate) fn try_step_encounter(
        &mut self,
        tick: u32,
        hero: Option<u64>,
    ) -> Result<(), CombatError> {
        let spawn_mover = self.movers.for_body(Some(0x0400_0000_0000_16be));
        self.simulation
            .step_encounter(&self.ground, spawn_mover, tick, hero)
    }

    #[cfg(test)]
    pub(crate) fn step_encounter(&mut self, tick: u32, hero: Option<u64>) {
        self.try_step_encounter(tick, hero)
            .expect("valid Junkenstein ECS gate and encounter handles");
    }

    pub fn bots(&self) -> Result<impl ExactSizeIterator<Item = &Bot>, CombatError> {
        self.simulation.bodies()
    }

    #[cfg(any(test, feature = "inspector"))]
    pub(crate) fn bot_actors(&self) -> Result<impl ExactSizeIterator<Item = Actor>, CombatError> {
        self.simulation.debug_actors()
    }

    pub(crate) fn deadeye_scene(
        &self,
        team: u8,
    ) -> Result<crate::script_player::DeadeyeScene, CombatError> {
        let queries = BotQueries::new(
            self.map.free_for_all,
            &self.movers,
            &self.ground,
            self.contacts.as_ref(),
        );
        let targets = self
            .simulation
            .living_enemies(team)?
            .map(|(bot, health)| crate::script_player::DeadeyeTarget {
                velocity: Some(bot.velocity),
                script_state: self.simulation.body_script_state(bot.entity),
                entity: bot.entity,
                pose: queries.target(bot),
                health,
            })
            .collect();
        Ok(crate::script_player::DeadeyeScene {
            health: Some(self.simulation.body_healths()?),
            targets,
            collision: Some(Arc::clone(&self.combat_rays)),
            view_subjects: self.simulation.boss_view_subjects()?,
        })
    }

    #[cfg(test)]
    pub(crate) fn invalidate_bot_component(&mut self) -> bool {
        self.simulation.invalidate_bot_component()
    }

    pub(crate) fn bot_simulation(&mut self) -> (&mut RoomSimulation, BotQueries<'_>) {
        let protected = self
            .protects_cassidy(Some(crate::game_heroes::CASSIDY))
            .then_some(crate::game_heroes::CASSIDY);
        self.simulation.set_protected_hero(protected);
        (
            &mut self.simulation,
            BotQueries::new(
                self.map.free_for_all,
                &self.movers,
                &self.ground,
                self.contacts.as_ref(),
            )
            .with_combat_geometry(&self.combat_rays, self.combat_sweeps.as_ref())
            .with_breakables(&self.breakables)
            .with_hurt_volumes(&self.hurt_volumes),
        )
    }

    pub(crate) fn retire_projectiles(&mut self, connection: u32) {
        self.simulation.retire_projectiles(connection);
    }

    #[cfg(test)]
    pub(crate) fn set_junkenstein_defenders(&mut self, defenders: Arc<[u32]>) {
        self.simulation.set_junkenstein_defenders(defenders);
    }

    pub(crate) fn clear_projectiles(&mut self) {
        self.simulation.clear_projectiles();
    }

    pub(crate) fn step_bots(
        &mut self,
        tick: u32,
        patrol: &PatrolScript,
    ) -> Result<(), CombatError> {
        self.simulation.prepare_npc_movement_frame(tick)?;
        let contacts = self.simulation.movement_contact_frame();
        let floor = self.ground.triangles();
        let map = &self.map;
        let movers = &self.movers;
        let world = self.contacts.as_ref();
        let queries = BotQueries::new(map.free_for_all, movers, &self.ground, world)
            .with_breakables(&self.breakables);
        self.simulation.for_each_body_mut(|bot| {
            let combat_motion = bot.has_combat_motion();
            if let Some(contacts) = contacts
                .as_ref()
                .filter(|_| bot.patrol_active() || combat_motion || bot.has_anchor_return())
            {
                contacts.stage_bot(bot, tick);
            }
            let category = mover_category(map, bot.spawn.team);
            let mut ground = ground_height(floor, category);
            if combat_motion {
                let settings = movers.for_body(Some(bot.spawn.body));
                if let Some(world) = world.filter(|_| !bot.has_collision()) {
                    bot.attach_collision(settings, Arc::clone(world), category, &mut ground);
                    bot.set_breakables(Arc::clone(&self.breakables));
                }
                if bot.step_combat_motion(tick, settings, &mut ground) {
                    return;
                }
            }
            queries.rejoin_patrol(bot, tick, category);
            if let Err(error) = bot.step(tick, patrol, ground) {
                eprintln!(
                    "Patrol for bot {:08x} stopped at frame {tick}: {error}",
                    bot.entity
                );
            }
        })
    }

    pub fn initial_world(&self, viewer: &PlayerAppearance<'_>) -> io::Result<Vec<EntityUpdate>> {
        let mut updates = initial_world(
            &self.map,
            viewer,
            self.initial_board.clone(),
            std::iter::empty(),
        )?;
        if let Some(barriers) = self
            .payload_round()
            .map(|round| &round.barriers)
            .or_else(|| self.capture_round().map(|round| &round.barriers))
        {
            updates.retain(|update| {
                !barriers
                    .entries
                    .iter()
                    .any(|entry| entry.entity == update.entity && entry.kind.is_room_visual())
            });
        }
        updates.extend(self.simulation.initial_creates()?);
        Ok(updates)
    }
}
