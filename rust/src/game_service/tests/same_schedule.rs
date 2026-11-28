//! Opt-in offline comparison of a decoded schedule against a real Practice Range room.
use super::*;
use crate::commands::{Click, Command, SUBFRAME};
use crate::game_bots::BotSpawn;
use crate::game_clock::{FIRST_TICK, SEND_EVERY};
use crate::game_replication::BodyId;
use crate::game_replication::tests::owner_effects::{script_receipts, selected};
use crate::game_replication::tests::practice_observation::{processed, rifle_state};
use crate::scene_assets::SceneAssets;
use crate::script_init::RandomCheckpoint;
use serde::Deserialize;
use serde_json::{Value as Json, json};
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;

mod minefield;
mod startup;
mod transport;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Offer {
    entity: String,
    frame: u32,
    forward: i8,
    right: i8,
    yaw: i32,
    pitch: i32,
    buttons: u32,
    action: u8,
    extra: u16,
    click: Option<(u8, i16, i16)>,
}

impl Offer {
    fn command(self) -> Command {
        assert_eq!(self.entity, "player/0/body/1");
        assert!(self.buttons <= 0x00ff_ffff);
        assert_eq!(self.buttons & SUBFRAME != 0, self.click.is_some());
        Command {
            frame: self.frame,
            forward: self.forward,
            right: self.right,
            yaw: self.yaw,
            pitch: self.pitch,
            buttons: self.buttons,
            action: self.action,
            extra: self.extra,
            click: self.click.map(|(subframe, yaw, pitch)| Click {
                subframe,
                yaw,
                pitch,
            }),
        }
    }
}

fn bits(n: f64) -> String {
    assert!(n.is_finite());
    format!("{:016x}", n.to_bits())
}

struct BotSlot {
    spawn: Arc<BotSpawn>,
    wire: Option<u32>,
    generation: u32,
}

impl BotSlot {
    fn observe(&mut self, scene: &PreparedScene, rows: &mut Vec<Json>) {
        let sim = scene.simulation();
        let mut bodies = sim
            .bodies()
            .unwrap()
            .filter(|bot| Arc::ptr_eq(&bot.spawn, &self.spawn));
        let Some(bot) = bodies.next() else {
            self.wire = None;
            rows.push(json!(["bot/0", "body", null]));
            return;
        };
        assert!(bodies.next().is_none());
        if self.wire != Some(bot.entity) {
            self.generation += 1;
            self.wire = Some(bot.entity);
        }
        let id = format!("bot/0/body/{}", self.generation);
        rows.extend([
            json!(["bot/0", "body", id]),
            json!([id, "alive", !bot.dead]),
            json!([
                id,
                "health_bits",
                bits(sim.body_healths().unwrap()[&bot.entity])
            ]),
            json!([id, "position_bits", bot.position.map(bits)]),
        ]);
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Tick {
    tick: u32,
    elapsed_us: u64,
    missed: u64,
    inputs: Vec<Offer>,
}

fn player_vm_checkpoint(state: &State, owner: u32, expected: BodyId) -> Option<RandomCheckpoint> {
    let scene = &state.rooms.get(&MATCH)?.world.as_ref()?.scene;
    let body = scene.simulation().player_body_by_owner(owner)?;
    if body.id != expected {
        return None;
    }
    body.runtime.as_ref()?.random_checkpoint()
}

#[test]
#[ignore = "requires a private decoded schedule, prepared 104319 runtime and new observation path"]
fn same_schedule_practice_room() {
    let Some(schedule) = std::env::var_os("ORPHEUS_ROOM_SCHEDULE") else {
        // Prepared-suite runners have no experiment-specific schedule or output.
        assert!(std::env::var_os("ORPHEUS_ROOM_OBSERVATION").is_none());
        return;
    };
    let destination = PathBuf::from(
        std::env::var_os("ORPHEUS_ROOM_OBSERVATION").expect("new experiment output path"),
    );
    let runtime = PathBuf::from(std::env::var_os("ORPHEUS_RUNTIME_DIR").expect("prepared runtime"));
    let scene = SceneAssets::load(&runtime)
        .unwrap()
        .prepare(GameMap::practice_range())
        .unwrap();
    if !scene.movement_ready() {
        write_observation(
            &destination,
            &json!({"status":"unavailable","reason":"unsupported-native-practice-room"}),
        );
        return;
    }
    let roster_size = scene.simulation().bodies().unwrap().len();
    let slot: usize = std::env::var("ORPHEUS_ROOM_BOT_SLOT")
        .map_or(2, |s| s.parse().expect("an unsigned bot slot"));
    let spawn = Arc::clone(
        &scene
            .simulation()
            .bodies()
            .unwrap()
            .nth(slot)
            .expect("bot slot outside the prepared roster")
            .spawn,
    );
    assert_ne!(spawn.team, 0);
    let spawn_description = json!({"team":spawn.team,"hero":format!("{:016x}",spawn.hero),
        "position_bits":spawn.position.map(bits),"yaw":spawn.yaw});
    let mut bot = BotSlot {
        spawn,
        wire: None,
        generation: 0,
    };
    let schemas = Arc::new(Schemas::load(runtime.join("data/game_schemas_174.json")).unwrap());
    let mut state = State::new(Arc::clone(&schemas), PatrolScript::load().unwrap());
    let ticket = state
        .open_match(
            MATCH,
            GameMap::practice_range(),
            player(),
            Some(WorldStart::new(scene, PlayerCard::default())),
            Duration::ZERO,
        )
        .unwrap()
        .remove(0);
    let server = UdpSocket::bind(local()).unwrap();
    let mut client = Client::new(server.local_addr().unwrap(), &ticket);
    let scene = &state.rooms[&MATCH].world.as_ref().unwrap().scene;
    let initial_bot = scene
        .simulation()
        .bodies()
        .unwrap()
        .find(|b| Arc::ptr_eq(&b.spawn, &bot.spawn))
        .unwrap()
        .entity;
    let mut names = std::collections::BTreeMap::from([(initial_bot, "bot/0/body/1".to_string())]);
    let setup = startup::join(
        &mut state,
        &server,
        &mut client,
        &schemas,
        initial_bot,
        &mut names,
    );
    let peer = state.rooms[&MATCH].members[&ticket.conn]
        .peer
        .as_ref()
        .unwrap();
    let initial_player = peer.world.active_body_id().unwrap();
    let owner = peer.world.owner();
    let before_schedule = player_vm_checkpoint(&state, owner, initial_player)
        .expect("observed player VM after possession");
    let first_scheduled_time = setup.now + Duration::from_micros(u64::from(RETAIL_QUANTA));
    let mut first_scheduled_frame = None;
    let mut ticks = Vec::new();
    let mut bot_deaths = Vec::new();
    let mut unavailable = std::collections::BTreeSet::new();
    let schedule = BufReader::new(File::open(schedule).unwrap());
    for line in schedule.lines() {
        let mut row: Json = serde_json::from_str(&line.unwrap()).unwrap();
        let t: Tick = serde_json::from_value(row.clone()).unwrap();
        assert_eq!(t.tick, ticks.len() as u32);
        assert!(t.tick < 100_000 && t.inputs.len() <= 1);
        assert_eq!(t.elapsed_us, u64::from(t.tick) * u64::from(RETAIL_QUANTA));
        assert_eq!(t.missed, 0);
        let now = first_scheduled_time + Duration::from_micros(t.elapsed_us);
        let command = t.inputs.into_iter().next().map(Offer::command);
        let room = state.rooms.get_mut(&MATCH).unwrap();
        let peer = room.members[&ticket.conn].peer.as_mut().unwrap();
        let before = processed(&peer.world);
        if let Some(command) = command {
            peer.world.queue_commands(state.clock.frame(), &[command]);
            while state.advance_input(now).unwrap() {}
        }
        let frame = state.clock.frame();
        state.tick(&server, now, t.missed).unwrap();
        assert_eq!(state.clock.frame(), setup.tick + t.tick + 1);
        first_scheduled_frame.get_or_insert(state.clock.frame());
        let receipt = if state.clock.frame() / SEND_EVERY > frame / SEND_EVERY {
            let sent = client.receive();
            assert!(client.window.accept(sent.seq));
            let mut reader = BitReader::new(&sent.payload);
            let sent_tick = reader.var_a().unwrap();
            read_channel(&mut reader, &schemas).unwrap();
            row["owner_frame"] =
                json!({"room_tick":sent_tick, "command_ack":reader.var_a().unwrap()});
            Some(startup::acknowledge(&mut state, &server, &mut client, now))
        } else {
            row["owner_frame"] = Json::Null;
            None
        };
        let room = state.rooms.get_mut(&MATCH).expect("room survived the tick");
        let peer = room.members[&ticket.conn].peer.as_mut().unwrap();
        let mut rows = Vec::new();
        let scene = &room.world.as_ref().unwrap().scene;
        bot.observe(scene, &mut rows);
        if let Some(wire) = bot.wire {
            names.insert(wire, format!("bot/0/body/{}", bot.generation));
        }
        names.insert(initial_player.entity, "player/0/body/1".to_string());
        let effects = receipt.map_or_else(Vec::new, |delivery| selected(&delivery, &names));
        let accepted = processed(&peer.world);
        row["accepted"] =
            json!(command.is_some_and(|c| accepted == Some(c.frame) && accepted != before));
        row["effects"] = json!(effects);
        let mut missing = Vec::new();
        if let Some(wire) = bot.wire {
            if let Some((death, deadline)) = scene.simulation().observed_bot_death(wire).unwrap() {
                if bot_deaths.len() < bot.generation as usize {
                    bot_deaths.push(json!({"generation":bot.generation,"observed_tick":t.tick,
                        "input_clock_frame":frame,"tick_clock_frame":state.clock.frame(),
                        "elapsed_us":t.elapsed_us,"lethal_source_time_bits":bits(death.time),
                        "lethal_source_frame":death.frame,"respawn_due_us":deadline.as_micros() as u64}));
                }
            }
        }
        let player = scene.simulation().player_body_by_owner(peer.world.owner());
        if let Some(body) = player {
            assert_eq!(
                body.id, initial_player,
                "profile does not cover player replacement"
            );
            let key = "player/0/body/1";
            let pose = body.mover.pose();
            let vitals = body.runtime.as_ref().unwrap().vitals().unwrap();
            let (ammo, reloading) = rifle_state(body);
            rows.extend([
                json!(["player/0", "body", key]),
                json!([key, "accepted_frame", accepted]),
                json!([key, "aim", [pose.yaw, pose.pitch]]),
                json!([key, "alive", body.alive()]),
                json!([key, "ammo", ammo]),
                json!([key, "health_bits", bits(vitals.total())]),
                json!([key, "position_bits", pose.position.map(bits)]),
            ]);
            if let Some(value) = reloading {
                rows.push(json!([key, "reloading", value]));
            } else {
                missing.push("player.reloading");
            }
        } else {
            rows.push(json!(["player/0", "body", null]));
        }
        rows.sort_by(|a, b| (a[0].as_str(), a[1].as_str()).cmp(&(b[0].as_str(), b[1].as_str())));
        row["state"] = json!(rows);
        row["missing"] = json!(missing);
        unavailable.extend(missing);
        ticks.push(row);
    }
    assert!(!ticks.is_empty());
    unavailable.insert("provenance.effective_data_sha256");
    let during_schedule = player_vm_checkpoint(&state, owner, initial_player)
        .filter(|end| {
            end.model == before_schedule.model && end.initial_seed == before_schedule.initial_seed
        })
        .map(|end| end.draws.wrapping_sub(before_schedule.draws));
    let result = json!({"status":"incomplete","scope":"stock Practice Range owner and selected ordinary spawner",
        "quanta_us":RETAIL_QUANTA,"bot_spawner_slot":slot,"bot_spawner":spawn_description,"bot_roster_size":roster_size,
        "transport":"loopback room loading and owner acknowledgements on ordinary worker ticks",
        "bootstrap_effects":setup.effects,"bootstrap_full_body_receipts":setup.scripts[0],"bootstrap_owner_delta_receipts":setup.scripts[1],
        "bot_death_timing":bot_deaths,"setup_delivery_ticks":setup.tick-FIRST_TICK,
        "startup_acks":setup.acks,"setup_elapsed_us":setup.now.as_micros(),"owner_component_start_frame":setup.component_frame,
        "owner_component_entity":initial_player.entity,
        "player_vm_random_model":before_schedule.model,"player_vm_initial_seed":before_schedule.initial_seed,
        "player_vm_rng_observation":{"draws_before_schedule":before_schedule.draws,"draws_during_schedule":during_schedule},
        "first_scheduled_room_tick":first_scheduled_frame,"initial_owner_delivery_phase":"after-possession-ack",
        "host_random_model":"os-entropy-transport-only","host_initial_seed":null,
        "server_player_slots":1,
        "input_boundary":"after owner identity, controller data and bound body delivery; before next room tick",
        "receipt_policy":"immediate successful owner record acknowledgement on scheduled sends","initial_room_tick":FIRST_TICK,
        "missing":unavailable,"ticks":ticks});
    write_observation(&destination, &result);
}

fn write_observation(destination: &std::path::Path, result: &Json) {
    let parent = destination.parent().unwrap();
    let mut file = tempfile::NamedTempFile::new_in(parent).unwrap();
    serde_json::to_writer(file.as_file_mut(), result).unwrap();
    writeln!(file).unwrap();
    file.persist_noclobber(destination)
        .expect("observation output must be new");
}
