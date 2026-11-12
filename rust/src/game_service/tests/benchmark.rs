//! Repeatable headless loopback load for the native worker timing inspector.

use super::*;

use std::path::PathBuf;

use serde_json::{Value as Json, json};
use sha2::{Digest, Sha256};

use crate::{
    commands::FIRE, game_bots::BOT_ENTITY, game_heroes::SOLDIER, game_timing::DurationBins,
    scene_assets::SceneAssets, script_player::fixture_hitscan_factory,
};

const WARM_UP: Duration = Duration::from_secs(2);
const FIRE_EVERY: u32 = 18;
const MAX_COMMAND_BURST: u32 = 32;
const TARGET: u32 = BOT_ENTITY + 1;

#[test]
#[ignore = "opt-in timing workload with an authorized base 104319 Practice Range runtime"]
fn prepared_worker_timing_two_solo_rooms() {
    let Some(runtime) = std::env::var_os("ORPHEUS_TIMING_BENCH_RUNTIME") else {
        // Keep the normal prepared regression run separate from a benchmark.
        eprintln!("Timing workload skipped because ORPHEUS_TIMING_BENCH_RUNTIME is unset.");
        return;
    };
    let duration = std::env::var("ORPHEUS_TIMING_BENCH_SECONDS").map_or(60, |text| {
        text.parse::<u64>().expect("integer benchmark seconds")
    });
    assert!(
        (1..=300).contains(&duration),
        "benchmark must run 1 to 300 seconds"
    );
    let runtime = PathBuf::from(runtime);
    let manifest = std::fs::read(runtime.join("manifest.json")).unwrap();
    let manifest_sha256 = format!("{:x}", Sha256::digest(manifest));
    let schemas = Arc::new(Schemas::load(runtime.join("data/game_schemas_174.json")).unwrap());
    let assets = SceneAssets::load(runtime).unwrap();
    let factory = Arc::new(fixture_hitscan_factory(0.125));
    let scenes = std::array::from_fn::<_, 2, _>(|_| {
        let preparation = Instant::now();
        let mut scene = assets.prepare(GameMap::practice_range()).unwrap();
        let prepare_scene_us = micros(preparation.elapsed());
        let [x, y, z] = scene.map().spawn_point(0, 0).unwrap().0;
        let (bots, _) = scene.bot_simulation();
        let target = bots.body_mut(TARGET).unwrap();
        assert!(target.spawn.route.is_empty());
        target.position = [x - 4.0, y, z];
        let factory = Arc::clone(&factory);
        scene.owner_body_factory = Some(Arc::new(move |hero, spawn| {
            if hero.guid == SOLDIER {
                Ok(Some(Box::new(factory.start(spawn)?)))
            } else {
                Ok(None)
            }
        }));
        (scene, prepare_scene_us)
    });
    let prepare_scene_us = scenes.each_ref().map(|(_, micros)| *micros);
    let mut service = GameService::bind_with_timing(local(), Arc::clone(&schemas), true).unwrap();
    let address = service.local_addr();
    let report = thread::scope(|scope| {
        let (ready, loaded) = mpsc::channel();
        let mut starts = Vec::new();
        let mut drivers = Vec::new();
        for (slot, (scene, _)) in scenes.into_iter().enumerate() {
            let ticket = service
                .open_prepared_practice(
                    [11, slot as u64],
                    scene,
                    player().pop().unwrap(),
                    PlayerCard::default(),
                )
                .expect("benchmark needs a base Practice Range runtime");
            let (start, command) = mpsc::channel();
            starts.push(start);
            let ready = ready.clone();
            let schemas = Arc::clone(&schemas);
            drivers.push(scope.spawn(move || {
                let client = Client::new(address, &ticket);
                let mut driver = Driver::join(client, &schemas);
                ready.send(()).unwrap();
                let deadline = loop {
                    match command.try_recv() {
                        Ok(deadline) => break deadline,
                        Err(mpsc::TryRecvError::Empty) => driver.step(&schemas, None),
                        Err(error) => panic!("benchmark stopped during warm-up: {error}"),
                    }
                };
                let mut counts = PeerCounts::default();
                while Instant::now() < deadline {
                    driver.step(&schemas, Some(&mut counts));
                }
                counts
            }));
        }
        drop(ready);
        for _ in 0..2 {
            loaded
                .recv_timeout(Duration::from_secs(10))
                .expect("benchmark clients join");
        }
        thread::sleep(WARM_UP);
        let before = service.debug_snapshot().unwrap();
        assert_eq!(before.rooms.len(), 2);
        let began = Instant::now();
        let deadline = began + Duration::from_secs(duration);
        for start in starts {
            start.send(deadline).unwrap();
        }
        let peers: Vec<_> = drivers
            .into_iter()
            .map(|driver| driver.join().unwrap())
            .collect();
        let after = service.debug_snapshot().unwrap();
        let observed_window_us = micros(began.elapsed());
        assert_eq!(after.rooms.len(), 2);
        let before = before.timing.unwrap();
        let after = after.timing.unwrap();
        let tick_duration = interval_histogram(
            &before.tick_duration_us,
            &after.tick_duration_us,
            after.bin_upper_exclusive_us,
        );
        let schedule_lateness = interval_histogram(
            &before.schedule_lateness_us,
            &after.schedule_lateness_us,
            after.bin_upper_exclusive_us,
        );
        json!({
            "workload": "two independent solo Practice Range rooms on one worker",
            "fixture": "synthetic hitscan against a repositioned stock target",
            "runtime_manifest_sha256": manifest_sha256,
            "duration_seconds": duration,
            "warm_up_seconds": WARM_UP.as_secs(),
            "prepare_scene_us": prepare_scene_us,
            "observed_window_us": observed_window_us,
            "tick_duration_us": tick_duration,
            "schedule_lateness_us": schedule_lateness,
            "missed_ticks": after.missed_ticks - before.missed_ticks,
            "input_budget": {
                "turns": after.input_budget.turns - before.input_budget.turns,
                "used": after.input_budget.used - before.input_budget.used,
                "peer_limit_hits": after.input_budget.peer_limit_hits - before.input_budget.peer_limit_hits,
                "turn_limit_hits": after.input_budget.turn_limit_hits - before.input_budget.turn_limit_hits,
                "turns_with_backlog": after.input_budget.turns_with_backlog - before.input_budget.turns_with_backlog,
            },
            "peers": peers.into_iter().map(PeerCounts::report).collect::<Vec<_>>(),
        })
    });
    println!("ORPHEUS_TIMING_BENCH {report}");
    service.stop().unwrap();
}

pub(super) struct Driver {
    client: Client,
    first_command: u32,
    next_command: u32,
}

impl Driver {
    pub(super) fn submit_input(&mut self) {
        let command = command_batch(self.next_command, 1, -16384, 0, false);
        self.client.send(ACK, command);
        self.next_command += 1;
    }

    pub(super) fn join(mut client: Client, schemas: &Schemas) -> Self {
        client.handshake();
        let load = client.receive();
        assert_eq!(messages(schemas, &load)[0].message.id, 20300);
        assert!(client.window.accept(load.seq));
        let mut payload = BitWriter::new();
        let loaded = encode(schemas, 21602, &Fields::new()).unwrap();
        write_channel(&mut payload, &[(0, loaded)], &[]).unwrap();
        client.send(ACK, payload.into_bytes());

        let deadline = Instant::now() + Duration::from_secs(5);
        let mut controller = false;
        let mut mode = false;
        while !(controller && mode) {
            let packet = client.receive();
            assert!(client.window.accept(packet.seq));
            for record in world_records(schemas, &packet) {
                if record.script.is_some() {
                    controller |= record.entity == player_entity(0);
                    mode |= record.entity == GAME_MODE_ENTITY;
                }
            }
            client.send(ACK, Vec::new());
            assert!(
                Instant::now() < deadline,
                "waiting for prepared world presentation"
            );
        }
        let first_command = pick_soldier_for_weapon(&mut client, schemas);
        Self {
            client,
            first_command,
            next_command: first_command,
        }
    }

    pub(super) fn step(&mut self, schemas: &Schemas, mut counts: Option<&mut PeerCounts>) {
        let packet = self.client.receive();
        let received = Instant::now();
        assert_eq!(packet.kind, KIND_FRAME);
        assert!(self.client.window.accept(packet.seq));
        let mut reader = BitReader::new(&packet.payload);
        let tick = reader.var_a().unwrap();
        if let Some(stats) = counts.as_deref_mut() {
            let channel = read_channel(&mut reader, schemas).unwrap();
            let command_ack = reader.var_a().unwrap();
            assert_eq!(reader.bits(7).unwrap(), 0b010_0000);
            let has_world_records = reader.bits(7).unwrap() != 0;
            stats.packets += 1;
            stats.packet_gaps.record(tick, received);
            if has_world_records {
                stats.world_packets += 1;
                stats.world_gaps.record(tick, received);
            }
            stats.first_ack.get_or_insert(command_ack);
            stats.last_ack = command_ack;
            stats.hits.extend(
                channel.reliable.into_iter().filter_map(|message| {
                    (message.message.id == 25001).then_some(message.sequence)
                }),
            );
        }
        let last = tick + 1;
        let catch_up = last.saturating_sub(MAX_COMMAND_BURST - 1);
        if self.next_command < catch_up {
            if let Some(stats) = counts.as_deref_mut() {
                stats.skipped_commands += u64::from(catch_up - self.next_command);
            }
            self.next_command = catch_up;
        }
        if self.next_command > last {
            self.client.send(ACK, Vec::new());
        }
        while self.next_command <= last {
            let phase = (self.next_command - self.first_command) % FIRE_EVERY;
            let (count, buttons) = if phase == 0 {
                (1, FIRE)
            } else {
                ((last + 1 - self.next_command).min(FIRE_EVERY - phase), 0)
            };
            let command = command_batch(self.next_command, count as u8, -16384, buttons, false);
            self.client.send(ACK, command);
            self.next_command += count;
            if let Some(stats) = counts.as_deref_mut() {
                stats.commands += u64::from(count);
                stats.fire_pulses += u64::from(buttons == FIRE);
            }
        }
    }
}

#[derive(Default)]
pub(super) struct PeerCounts {
    packets: u64,
    world_packets: u64,
    commands: u64,
    fire_pulses: u64,
    skipped_commands: u64,
    first_ack: Option<u32>,
    last_ack: u32,
    hits: HashSet<u32>,
    packet_gaps: GapSamples,
    world_gaps: GapSamples,
}

impl PeerCounts {
    pub(super) fn report(self) -> Json {
        assert!(self.world_packets > 1, "expected world replication");
        assert!(
            self.fire_pulses > 0 && !self.hits.is_empty(),
            "expected fixture hit delivery"
        );
        let ack_progress = self.last_ack - self.first_ack.unwrap();
        assert!(ack_progress > 0, "expected accepted input");
        json!({
            "packets": self.packets,
            "world_packets": self.world_packets,
            "commands_sent": self.commands,
            "fire_pulses": self.fire_pulses,
            "skipped_commands": self.skipped_commands,
            "command_ack_progress": ack_progress,
            "distinct_hit_notices": self.hits.len(),
            "frame_gap_frames": gap_summary(self.packet_gaps.logical_frames),
            "observed_packet_gap_us": gap_summary(self.packet_gaps.arrival_us),
            "world_record_gap_frames": gap_summary(self.world_gaps.logical_frames),
            "observed_world_record_gap_us": gap_summary(self.world_gaps.arrival_us),
        })
    }
}

#[derive(Default)]
struct GapSamples {
    previous: Option<(u32, Instant)>,
    logical_frames: Vec<u64>,
    arrival_us: Vec<u64>,
}

impl GapSamples {
    fn record(&mut self, tick: u32, received: Instant) {
        if let Some((previous_tick, previous_at)) = self.previous.replace((tick, received)) {
            self.logical_frames.push(u64::from(tick - previous_tick));
            self.arrival_us.push(micros(received - previous_at));
        }
    }
}

fn micros(duration: Duration) -> u64 {
    u64::try_from(duration.as_micros()).unwrap()
}

fn gap_summary(mut values: Vec<u64>) -> Json {
    values.sort_unstable();
    assert!(!values.is_empty());
    let quantile = |percent: usize| values[(values.len() * percent).div_ceil(100) - 1];
    json!({
        "samples": values.len(),
        "p95": quantile(95),
        "p99": quantile(99),
        "max": values.last(),
    })
}

fn interval_histogram(before: &DurationBins, after: &DurationBins, upper_us: &[u64]) -> Json {
    let samples = after.samples - before.samples;
    assert!(samples > 0, "expected worker timing samples");
    let counts: Vec<_> = after
        .counts
        .iter()
        .zip(before.counts)
        .map(|(a, b)| a - b)
        .collect();
    assert_eq!(counts.iter().sum::<u64>(), samples);
    let interval = |percent: u64| {
        let rank = (samples * percent).div_ceil(100);
        let mut seen = 0;
        let bin = counts
            .iter()
            .position(|count| {
                seen += count;
                seen >= rank
            })
            .unwrap();
        json!({
            "lower_inclusive": bin.checked_sub(1).map_or(0, |i| upper_us[i]),
            "upper_exclusive": upper_us.get(bin),
        })
    };
    json!({
        "samples": samples,
        "p95_us_bin": interval(95),
        "p99_us_bin": interval(99),
        "bin_upper_exclusive_us": upper_us,
        "counts": counts,
    })
}
