# Restoration Roadmap (`rust`)

Target build: **Overwatch 1 retail 1.74.0.0.104319**.
- For Rust verification & Python parity status, see [STATE.md](STATE.md).
- For workspace architecture, crate boundaries, and `#60`–`#65` tracking, see [NATIVE_SERVER_ARCHITECTURE.md](NATIVE_SERVER_ARCHITECTURE.md).
- For active tasks, see [GitHub Issues](https://github.com/leafall903/ORPHEUS/issues).

---

## Project Milestones

| Milestone | Exit Criteria | Current `rust` Status |
| --- | --- | --- |
| **M0 · Research & Replay** | Pinned build provenance, extractors, deterministic encrypted protocol replay | Pinned to 104319; synthetic fixture CI & runtime pack validation live. |
| **M1 · Body & Weapon Init** | Soldier: 76 spawns cleanly, keeps model/weapon/ammo HUD for 60s across 10x switch & rejoin | Authored Soldier startup & encrypted loopback pass; live client 60s retest open. |
| **M2 · Rifle & Prediction** | Sustained firing, reload, and Sprint agree with client under loss | Authored rifle, reload & Sprint VM timelines pass offline; live client prediction open. |
| **M3 · Single-Player Combat** | Training bots take damage, die, respawn cleanly with visible health/kill-feed/SFX | Bot combat, respawn & nameplate wire delivery pass offline; client UI/audio open. |
| **M4 · Two-Client Combat** | Two clients agree on movement, LOS, damage & respawn under loss | Distinct local accounts can log in to one native lobby in loopback checks. Multi-room fault isolation and the two-room worker timing benchmark are live. A two-client retail match is open. |
| **M5 · Modes & Heroes** | Full match rules & declared abilities across supported heroes | Practice Range & Normal Junkenstein offline replays pass. The native lobby supports two clients browsing, joining, changing teams, and starting one custom match. Retail custom-room acceptance remains open. Generic player MoveTo finishes or aborts through authored plugs in synthetic coverage; the isolated Widowmaker hook press runs the authored trace→movement chain on real data under the reference production policy (now the Rust default), with the near-hit explosion branch and the out-of-range miss pinned on real data; the full kit passes a sixty-frame press window with zero reports under that policy (strict keeps eight support-graph reports with this upstream); on the authored clock the kit admits the press and runs the pull to the terminal; the phase-2 timer runs the authored stop action and retires the line in owner and observer projections. The full kit casts again after cooldown expiry, and a ground jump does not reset the armed cooldown. Bone-relative miss retract and live acceptance remain open. Full ability replay is open. |
| **M6 · Reliability & Cutover** | Measured p95/p99 tick latency, clean deployment, Rust matches/exceeds Python | `orpheus-wire`/`collision`/`runtime` extracted; MSRV `1.85` & Linux CI green. |

---

## Original-Client Acceptance Gates

- [ ] Create a custom room from the Game Browser and verify the King's Row preview and host slot. Leave and create another room.
- [ ] Use both retail clients to create public rooms, join in both directions, move into an occupied team with a free seat, and load the same match. Check a live team change and return or restart from the L menu.
- [ ] On King's Row, trade nonlethal hits between the two retail clients. Check that movement, aim, firing, reload and abilities stay responsive after the first hit in both directions.
- [ ] In the original 104319 client, rename the room and both teams, move the host between teams and spectators, and save a different preset and a change within that preset. Verify the room preview and available slots.
- [ ] Start the default King's Row Hybrid room in the original client, confirm hero selection and spawn, then repeat with a saved map and preset.
- [ ] Run retail Escort as both teams and join after a checkpoint. Check assemble reselection, setup possession and headings, live L-menu team changes, cart pitch on slopes, waypoints, gate movement, audio, and the result presentation.
- [ ] Run a retail custom Assault round as both teams. Check the locked A waypoint during setup, segment animation and sound, the A/B transition, room doors and boundaries, overtime audio, and the result camera.
- [ ] Run a retail custom Control match as both teams. Check spawn rooms, the two opening clocks, overtime, round rollover, late entry, and native HUD, music, and result presentation.
- [ ] Verify the overtime banner, fuse, and music for both teams and a late viewer in the timed modes and Control. Check a last-second touch, the burning fuse, renewed contact, and the exit on objective completion or expiry.
- [ ] Run a retail custom Hybrid round on King's Row as both teams. Check the A-to-payload HUD handoff, delayed unlock and late entry, checkpoint indicators after switching teams, and the result camera. Check the gate impact and debris on Eichenwalde and the shared capture volumes on Blizzard World.
- [ ] Check the locked cart before A on King's Row, the signs in Eichenwalde's last attacker spawn from each side, and the intro theme across H close and reopen in Practice Range and a custom match.

### 1. Practice Range & Stock Soldier: 76
- [ ] Verify stock weapon attachment, ammo HUD, Sprint, Helix Rockets, and Biotic Field in live client `104319`.
- [ ] Verify projectile impact and retirement, ground-shot self-launch, and quick-melee knockback in the original client. The prepared server baseline is recorded in [STATE.md](STATE.md#practice-range-playtest-follow-up).
- [ ] Confirm several consecutive training bot kills and respawns keep the client in the room. Compare head, torso and lateral shots against the standing combat calibration.
- [ ] Verify upstairs patrol route geometry matches contact solver bounds and hitscan wall queries (use `--features inspector` to diagnose).
- [ ] Verify hostile character blocking without rubber-banding and allied pass-through with separation. Check the downed interval and the first frame of the authored bot respawn animation separately for movement and weapon contact.
- [ ] Compare allied and hostile bot firing animation, projectile interception, and the walk-back animation after displacement. Check weapon occlusion through a recorded railing gap against a shot into the adjacent solid bar.
- [ ] Compare initial placement and breakage in the original client's Practice Range. Reproduce the missing green bottles at their starting positions, and check whether the reported computer screens register weapon hits and play their authored break effects.
- [ ] Compare effective weapon damage across authored falloff thresholds. Cassidy's alternate fire already has a [room settlement regression](../fixtures/behavior/cassidy-fan-falloff-v1.json) with a 200 HP target and six controlled torso contacts.
- [ ] Verify directional hit reactions and human and nonhuman impact effects from authored body data. Verify the communication wheel, emotes and voice lines, and spawn voice cues in the original client.
- [ ] Compare the Practice Range multikill expiry and elimination eligibility against the authored graph. Verify emplaced-weapon elimination audio once the original Torbjörn turret is usable.
- [ ] Resolve bot overhead health bars (#7), kill feed localized names (#8), headshot SFX (#9), and Practice Range boundary death (#13).

### 2. Junkenstein's Revenge (Normal Cassidy, Card `0x3C`)

*Headless offline replays already pass Natural Deadeye Victory, Early/Late Defeat, Team Score, Boss Speech, Combat Roll, Flashbang E-input, and all 15 Boss damage/retirement cases. All items below require live 104319 client acceptance before checking off:*

- [ ] **Castle gate & recap HUD:** Gate mesh states (intact, 3 damaged poses, destroyed) match health visually; post-match recap displays non-zero elapsed seconds alongside Victory/Defeat heading.
- [ ] **Cassidy abilities & score:** Live primary/Fan damage, `0B8B` ultimate charge HUD, Combat Roll camera/refill, Flashbang throw & stun reaction animation, Deadeye locks & fire, and HUD team score.
- [ ] **Enemy AI & intros:** Zomnic walking speed/door attacks, Zombardier target acquisition & projectile visuals, Witch heal-vs-pistol target switching, and once-only Boss intro cameras/pursuit/voicelines across all 5 Bosses.
- [ ] **NPC Hero Gallery:** Verify visual selection, model framing, and idle animation on client for the 23 NPC Hero Gallery entries.

---

## System Checklist

- [x] **StateScript actor startup (32/32 retail heroes)**. All 32/32 retail heroes compile, start, and step through primary/secondary/ability1/ability2/ult/melee/reload cleanly in `prepared_all_32_heroes_compile_start_and_step_cleanly` with zero hero/graph hardcoding (`StateKind::GroundRay`, `ground_wave.rs`, generic `body_ruleset.rs` and `eye_height`). Original-client ability acceptance across all 32 heroes remains open.
- [x] **Track B primitives, PvP mode contracts & PvE consolidation.** Five zero-hardcoding room primitives (`rust/src/room_primitives.rs`), four PvP mode contracts (`rust/src/pvp_mode.rs`), breakable prop geometry (`STU_E62A2EFC`), and consolidated `rust/src/pve_encounter/` module are in place and verified.
- [x] **Lua 5.4 Mod / Workshop API (`mod_script.rs`) & unified launcher.** Sandboxed Lua 5.4 mod host (`--features lua-mods --mod-script <file>`, `MOD_ABI_VERSION = 1`, atomic `reload` rollback) and unified `scripts/setup_and_play.ps1` / `scripts/PLAY_ORPHEUS.cmd`.
- [ ] **Phase 3A rays and facing.** The [static ray expression](STATESCRIPT_DEVELOPMENT.md#read-the-nearest-static-ray-hit) queries the room's geometry. The [actor ray expression](STATESCRIPT_DEVELOPMENT.md#find-the-first-actor-along-a-ray) uses frozen room capsules and static occlusion. [Facing claims](STATESCRIPT_DEVELOPMENT.md#control-a-bodys-facing-for-a-state) apply a direction for the lifetime of an authored state. The observation state routes each successful ray sample into authored graph logic, even when multiple observations share an output. A graph can also declare instance values for owner snapshots and clear a derived target indicator when sampling is unavailable. [Held input declarations](STATESCRIPT_DEVELOPMENT.md#start-and-stop-states-with-held-input) register automatically, own their branch state lifetimes, and honor release and death admission. [Inspector ray samples](STATESCRIPT_DEVELOPMENT.md#inspect-statescript-ray-samples) show the source site, sampled frame, and ray outcome. Original ability mappings and client zoom acceptance remain open.
- [ ] **Research & Replay:** Generate deterministic replays covering random seeds, loss schedules, and multi-client sessions.
- [ ] **Navigation & Collision ([#63](https://github.com/leafall903/ORPHEUS/issues/63)):** Shared map-agnostic static navigation queries (`walkable_ground`, `capsule_clearance`, `walk_edge`, `find_path`, `valid_spawns`, `snap_spawn_points` in `orpheus_collision::navigation`) landed and were verified across Practice Range (`0688`), King's Row (`00D4`), Temple of Anubis (`005B`), Hanamura (`0165`), and Kanezaka (`0CD3`). Shared player and NPC contact behavior still requires prepared and original-client verification.
- [x] **ECS & Script Boundary ([#65](https://github.com/leafall903/ORPHEUS/issues/65), [#120](https://github.com/leafall903/ORPHEUS/pull/120)):** Player bodies and Junkenstein gate/encounter state live under `RoomSimulation`'s `bevy_ecs` world alongside bots and projectiles (`WorldDelivery` retains delivery-only cursors), the default StateScript patrol graph selects waits and route slots while Rust moves bots, and the optional Lua debug override retains safe-boundary hot reload with validation and rollback.
- [x] **Workspace Modularity ([#60](https://github.com/leafall903/ORPHEUS/issues/60), [#64](https://github.com/leafall903/ORPHEUS/issues/64)):** Split `script_enemy.rs`, `script_expr.rs`, `game_replication.rs`, `script_snapshot_wire.rs`, `script_player.rs`, and `game_service.rs`, and extracted `orpheus-statescript`.
- [ ] **Phase 3B displacement and 3D movement.** The [authored displacement action](STATESCRIPT_DEVELOPMENT.md#schedule-collision-checked-displacement) schedules a bounded sweep through static geometry and eligible actor capsules for players and NPCs. A moved player pose opens an owner correction window. Regular player input and NPC steering in rooms with physical geometry share that actor contact path. Shared regression and original-client movement acceptance remain open.
- [ ] **Map boundary deaths.** [Authored lethal volumes and respawn settings](STATESCRIPT_DEVELOPMENT.md#declare-map-lethal-volumes) use the room's health and body replacement paths. Verified Practice Range regions, mode timing, and original-client acceptance remain open. [#13](https://github.com/leafall903/ORPHEUS/issues/13)

- [ ] **3D Inspector & Reliability ([#62](https://github.com/leafall903/ORPHEUS/issues/62), [#84](https://github.com/leafall903/ORPHEUS/issues/84), [#121](https://github.com/leafall903/ORPHEUS/issues/121)):** Bounded read-only raycast, contact-normal, AI route/spawn, and StateScript VM overlays ([#62](https://github.com/leafall903/ORPHEUS/issues/62)) and opt-in `--debug-control` local room pause/single-step controls ([#84](https://github.com/leafall903/ORPHEUS/issues/84)) landed in the `--features inspector` web viewer; the crash in [#121](https://github.com/leafall903/ORPHEUS/issues/121) passed the maintainer's original-client retest and is closed. Recycled body-ID notice protection ([#12](https://github.com/leafall903/ORPHEUS/issues/12)) and the Quick Melee owner StateScript fix ([#156](https://github.com/leafall903/ORPHEUS/pull/156)) are also in place.
