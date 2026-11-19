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
| **M4 · Two-Client Combat** | Two clients agree on movement, LOS, damage & respawn under loss | Multi-room fault isolation & two-room worker timing benchmark live; two-client live match open. |
| **M5 · Modes & Heroes** | Full match rules & declared abilities across supported heroes | Practice Range & Normal Junkenstein offline replays pass; Doomfist/WB/other heroes open. |
| **M6 · Reliability & Cutover** | Measured p95/p99 tick latency, clean deployment, Rust matches/exceeds Python | `orpheus-wire`/`collision`/`runtime` extracted; MSRV `1.85` & Linux CI green. |

---

## Original-Client Acceptance Gates

### 1. Practice Range & Stock Soldier: 76
- [ ] Verify stock weapon attachment, ammo HUD, Sprint, Helix Rockets, and Biotic Field in live client `104319`.
- [ ] Verify upstairs patrol route geometry matches contact solver bounds and hitscan wall queries (use `--features inspector` to diagnose).
- [ ] Resolve bot overhead health bars (#7), kill feed localized names (#8), headshot SFX (#9), and Practice Range boundary death (#13).

### 2. Junkenstein's Revenge (Normal Cassidy, Card `0x3C`)

*Headless offline replays already pass Natural Deadeye Victory, Early/Late Defeat, Team Score, Boss Speech, Combat Roll, Flashbang E-input, and all 15 Boss damage/retirement cases. All items below require live 104319 client acceptance before checking off:*

- [ ] **Castle gate & recap HUD:** Gate mesh states (intact, 3 damaged poses, destroyed) match health visually; post-match recap displays non-zero elapsed seconds alongside Victory/Defeat heading.
- [ ] **Cassidy abilities & score:** Live primary/Fan damage, `0B8B` ultimate charge HUD, Combat Roll camera/refill, Flashbang throw & stun reaction animation, Deadeye locks & fire, and HUD team score.
- [ ] **Enemy AI & intros:** Zomnic walking speed/door attacks, Zombardier target acquisition & projectile visuals, Witch heal-vs-pistol target switching, and once-only Boss intro cameras/pursuit/voicelines across all 5 Bosses.
- [ ] **NPC Hero Gallery:** Verify visual selection, model framing, and idle animation on client for the 23 NPC Hero Gallery entries.

---

## System Checklist

- [ ] **Research & Replay:** Generate deterministic replays covering random seeds, loss schedules, and multi-client sessions.
- [ ] **Navigation & Collision ([#63](https://github.com/leafall903/ORPHEUS/issues/63)):** Shared map-agnostic static navigation queries (`walkable_ground`, `capsule_clearance`, `walk_edge`, `find_path`, `valid_spawns`, `snap_spawn_points` in `orpheus_collision::navigation`) landed and were verified across Practice Range (`0688`), King's Row (`00D4`), Temple of Anubis (`005B`), Hanamura (`0165`), and Kanezaka (`0CD3`) ([#63](https://github.com/leafall903/ORPHEUS/issues/63)); shared player-vs-player and player-vs-bot character collision blocking remains open.
- [x] **ECS & Script Boundary ([#65](https://github.com/leafall903/ORPHEUS/issues/65), [#120](https://github.com/leafall903/ORPHEUS/pull/120)):** Player bodies and Junkenstein gate/encounter state live under `RoomSimulation`'s `bevy_ecs` world alongside bots and projectiles (`WorldDelivery` retains delivery-only cursors), Practice Range patrol runs on the versioned, resource-bounded Lua `think()` ABI (`v2`), and safe-boundary script hot reload with pre-activation validation and rollback landed in [#120](https://github.com/leafall903/ORPHEUS/pull/120).
- [x] **Workspace Modularity ([#60](https://github.com/leafall903/ORPHEUS/issues/60), [#64](https://github.com/leafall903/ORPHEUS/issues/64)):** Split `script_enemy.rs`, `script_expr.rs`, `game_replication.rs`, `script_snapshot_wire.rs`, `script_player.rs`, and `game_service.rs`, and extracted `orpheus-statescript`.
- [ ] **3D Inspector & Reliability ([#62](https://github.com/leafall903/ORPHEUS/issues/62), [#84](https://github.com/leafall903/ORPHEUS/issues/84), [#121](https://github.com/leafall903/ORPHEUS/issues/121)):** Bounded read-only raycast, contact-normal, AI route/spawn, and StateScript VM overlays ([#62](https://github.com/leafall903/ORPHEUS/issues/62)) and opt-in `--debug-control` local room pause/single-step controls ([#84](https://github.com/leafall903/ORPHEUS/issues/84)) landed in the `--features inspector` web viewer; the ~100s client access violation ([#121](https://github.com/leafall903/ORPHEUS/issues/121)) remains open (recycled body-ID notice protection in [#12](https://github.com/leafall903/ORPHEUS/issues/12) is complete, and Quick Melee `0x0043` owner StateScript handling landed for `#121` pending client verification).
