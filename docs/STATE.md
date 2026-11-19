# Verification & Parity State (`rust`)

Tracks implemented capabilities, Python-vs-Rust parity, verified baselines, and remaining gaps on `rust` targeting **Overwatch 1.74.0.0.104319**. For crate boundaries and contribution rules, see [NATIVE_SERVER_ARCHITECTURE.md](NATIVE_SERVER_ARCHITECTURE.md); for open milestones, see [ROADMAP.md](ROADMAP.md).

The [pinned Python → Rust parity matrix](https://github.com/leafall903/ORPHEUS/blob/232486a430b11cb105585ec5c0bfa6c47ee2a6d7/docs/PARITY.md) records 24 behaviors, their evidence and the next acceptance check for each. Use that revision to reproduce a comparison. For later results record the implementation, commit, runtime and kind of evidence, as Python on `main` and Rust on `rust` can both advance. Earlier Python observations remain available at revision `9f8f3bcb`.

---

## Public CI & Workspace Coverage (No Game Data Required)

| Lane / Suite | Scope Verified in CI (`.github/workflows/public-checks.yml`) |
| --- | --- |
| **Portable crates (Linux)** | `orpheus-wire`, `orpheus-collision`, `orpheus-runtime`, and `orpheus-statescript` compile and pass isolated tests without server/ECS deps (`cargo test --locked --all-targets -p orpheus-collision -p orpheus-runtime -p orpheus-statescript -p orpheus-wire`). |
| **Root workspace (`default` / `inspector`)** | `rustfmt --check`, zero-warning `clippy`, root library `438` (`319` public / `119` `#[ignore]`) tests in `default`, `444` (`325` public / `119` `#[ignore]`) tests in `--features inspector`, and `orpheus-statescript` `144` (`131` public / `13` `#[ignore]`) tests, plus MSRV `1.85.0` check. Workspace members and integration targets bring public totals to `571` / `577`; the synthetic action benchmark also validates both workloads in test mode. |
| **Cross-language contracts (`shared-behavior-contracts`)** | Checks out pinned `main` (`python-reference`) and verifies identical outputs across `jam-framing-v1.json`, `world-queries-v1.json`, and `profile-settings-v1.json`, `12` public Python tests, and `38` exact StateScript VM contracts on `rust`. |
| **C++ gallery preview policy** | `scripts/check_gallery_preview_policy.py` compiles `relay/gallery_preview_policy.h` and checks `18` resource-selection + `6` owned-tree pruning cases against `gallery-preview-policy-v1.json`. |

[#61](https://github.com/leafall903/ORPHEUS/issues/61) is complete. [#79](https://github.com/leafall903/ORPHEUS/issues/79) records the four Rust consumers and [#80](https://github.com/leafall903/ORPHEUS/issues/80) records the Python StateScript consumers.

---

## Implementation and prepared-runtime observations

Local integration check (2026-11-16): rifle/reload and controller/training-bot startup prepared tests passed with the target-tagged base pack, as did runtime preflight and headless startup. The longer `scene_assets::tests::stock_soldier_owner_session_idles_fires_reloads_and_sprints` test failed its fixed-instance assertion: instance `24` contained graph `0x0F82`, not `0x05AD`. The message table was present elsewhere; diagnostic inspection also found switch instance `19` versus expected `18`. At that revision the fixed instance assertion prevented the later frame checks from running.

Prepared follow-up (2026-11-18, [#72](https://github.com/leafall903/ORPHEUS/issues/72)). On Rust base `a8f5b06b` and the tagged base pack `b11115f3`, the test now resolves each required graph by its authored root or exact parent and attachment slot, then follows that instance for all 900 frames. The table, omitted owner switches, rifle ammo, reload, idle, sprint and both projections pass. Selecting the adjacent attachment slot for the message table fails at startup.

Python `45044d0d` and Rust share 24 startup graph paths on this pack. Two storage entries differ. Both VMs stop root `08B6` during startup. Python retains its empty stopped record and Rust removes it. In manager `004B`, node 21 checks an excluded target tag. The Rust owner lacks that tag and activates child `01CB` on slot 2. The Python host falls back to false for this filter shape and leaves that slot empty. These observations explain the different instance numbering. Whether the original client expects either difference in the replicated startup state remains open.

Follow-up (2026-11-18): on the same prepared runtime, the long test passes using unique graph and parent/attachment identities; a separate test pins the ordered live startup tree and the absent IDs 9 and 26. The additional `01CB` child at `004B` attachment 2 precedes `0BA4` and `05AD`. A test reader decodes descriptor sections of the actual owner/observer baseline and deltas and checks live parent attachments against VM snapshots; synthetic tests exercise removal, lost introductions, cumulative descriptor repetition and rejection of deltas that skip an unacknowledged chunk. The long test does not inspect queue retransmissions. The reader does not decode state links, variables, or the remaining payload, and it does not establish startup creation/retirement events or original-client compatibility. The earlier fixed-ID failure remains a historical observation, not a current test result.

| Workflow | Python reference | Rust implementation | Open client or parity gap |
| --- | --- | --- | --- |
| **Login, Profiles & Lobby** | Battle.net/JAM auth, profiles, settings (`22200`–`22204`), queues, Windows launcher + TLS relay. | Native BNet/JAM lobby, `profile-settings-v1` (CI verified), encrypted UDP seat handoff. | Standalone Rust launcher & public client connection flow open. |
| **Hero Gallery** | Loadouts, skins, equipped voice & emotes. | Loadout equip/persist, live skin body replacement, 23 NPC previews (C++ policy verified in CI). | Client Hero Gallery NPC framing & animation retest pending. |
| **Practice Range & Soldier: 76** | Full body/weapon VM, rifle, Sprint, Helix, Biotic Field, bots, format-5 collision. | Stock Soldier startup tree + rifle/Sprint/Helix/Biotic Field VM regressions; bots/projectiles in Bevy ECS + bounded versioned Lua `think()` patrol ABI (`v2`); all 43 movers match Python trajectories. | Client 60s stock-weapon mount, HUD/ammo sync, upstairs patrol, bot HUD/SFX (#3, #7–#9, #13), and original-client acceptance of the `08B6` / `004B -> 01CB` startup tree differences vs. Python pending. |
| **Movement & Collision** | GJK/contact solver, static ray queries, `0688` + 95 map caches. | `orpheus-collision` matches 10,148 Python movement records & 8,256 velocity cases; shared map-agnostic static navigation queries (`walkable_ground`, `capsule_clearance`, `walk_edge`, `find_path`, `valid_spawns`, `snap_spawn_points`) verified on synthetic fixtures and prepared `104319` Practice Range (`0688`), King's Row (`00D4`), Temple of Anubis (`005B`), Hanamura (`0165`), and Kanezaka (`0CD3`) collision caches ([#63](https://github.com/leafall903/ORPHEUS/issues/63), manifest SHA-256 `dad26628515e60e45e6128effd0c6408092dee8c8fc674ce00850b3463f8a90b`); player bodies live in `RoomSimulation` ECS ([#65](https://github.com/leafall903/ORPHEUS/issues/65)) and safe-boundary Lua AI hot reload with rollback landed in [#120](https://github.com/leafall903/ORPHEUS/pull/120). | Dynamic player-vs-player / player-vs-bot character collision blocking and live client checks (#7, #13) remain open. |
| **Doomfist & Wrecking Ball** | `leafall` merged into `main`: Slam, Rocket Punch dash/slow, Uppercut, Shield, Grapple, Quad Cannons, Piledriver, Minefield. | Rust has consumers for the movement, short-action retry, Slam placement and shockwave fixtures. | Verify the remaining ability behavior on the original client. |
| **Fault Isolation & Inspector** | Single-process dashboard & replay journals. | Room-owned errors & `catch_unwind` recovery (`room-isolation-v1`), `--features inspector` 3D viewer with bounded read-only physics, AI route/spawn, and StateScript VM overlays ([#62](https://github.com/leafall903/ORPHEUS/issues/62)), opt-in `--debug-control` local room pause/resume/single-step controls ([#84](https://github.com/leafall903/ORPHEUS/issues/84)), two-room worker timing benchmark. | Multi-player live room retest open. |

---

## Junkenstein's Revenge & Normal Cassidy (`0x3C`) Status

Headless offline regressions against pinned Python `e1128509` and prepared 104319 packs pass across the core encounter loop:

| Component / Mechanics | Rust Headless Coverage (`--ignored` with 104319 Packs) | Remaining Live / Client Gaps |
| --- | --- | --- |
| **All 14 Mode Cards & Gate** | Revenge/Endless/Challenge cards initialize; placed gate runs `0CA7`, health stages (`3` damage poses + destroyed), and team outcome projection (`MECGameMode` fields 7 & 9). Gate health/damage stages, encounter phases, and registration/rollback live in `RoomSimulation` ECS (`GateComponent`, `EncounterComponent`), verified by `9` self-contained unconditional unit tests (`game_combat::tests::encounter_ecs`); local base-pack runs lack Junkenstein graphs. | Gate visual state & post-match recap elapsed-time HUD binding remain open. |
| **Cassidy Primary & Fan (`0B8B`)** | Primary (`70`/`140` crit), Fan (`50` non-crit), ammo & passive/damage ultimate charge (`0.5`/`0.75` target-tag reductions): `scene_assets::tests::normal_cassidy_primary_and_fan_damage_all_boss_bodies`, `scene_assets::tests::normal_cassidy_common_npc_damage_charge_and_retirement`. | Live client attack feedback & HUD charge sync await client acceptance. |
| **Combat Roll (`01F0`)** | Collision-aware ~6.15 m Roll displacement across 5 directions, spawn-wall contact, and 5-to-6 ammo refill (`7b13651b` / `74d6ff78`). | Live directional/actor contacts & full hero matrix open. |
| **Flashbang (`224E`)** | Gravity/range flight blast (`800`-frame E-input replay), press-window gating, priority/excluded-tag checks, Reaper Wraith immunity rejection, velocity cap (`224E/31`), captured aim lock (`224E/34`), and targeted defender notices across all 5 Bosses. | Dynamic Boss spawn category bits & native hit-reaction animations await verification. |
| **Deadeye (`06AB` / `0CA5`)** | LOS target rays, lock progression, shot resolution, and natural two-cast 9-Boss Victory replay (`--junkenstein-validation` overrides tested separately). | Full live-client Deadeye lock visuals, SFX & natural Victory acceptance open. |
| **Bosses, NPCs & Score** | All 5 Boss body/controller pairs, common Zomnic/Zombardier/Shock Tire corpses (`8s` timer), Witch healing, Boss speech (`40E05F6B` / `junkenstein-boss-speech-v1`), `game_combat::tests::normal_encounter_first_reaper_finishes_intro_and_pursues_live_defender`, `game_combat::tests::normal_common_enemy_death_updates_authored_team_score`. | All 5 Boss intro cameras, Witch pistol swap, Zombardier combat, Reinhardt narration & Gallery client rendering await live 104319 client pass. |

*(Reference pack digests used for the original `2026-11-12` `13acf15` prepared-runtime baseline — Base `0688` SHA-256: `b11115f3201ebd0765cc8f0cf2ec0e72de6620b9913f8d17962c71649b528d58`; Enriched `0B92` SHA-256: `db7a7bdbddfb8b40e5e4c9a392d498ac36b13aed5144108311a985ea98e6d8f8`.)*

---

## Open Engineering Issues

| Issue | Scope |
| --- | --- |
| [#70](https://github.com/leafall903/ORPHEUS/issues/70) | Clean up transitional contract scripts, legacy prototypes, and stale branches. |
| [#121](https://github.com/leafall903/ORPHEUS/issues/121) | Diagnose and fix the 104319 client access violation (`0xC0000005`) during sustained Soldier Practice Range play. |
| [#131](https://github.com/leafall903/ORPHEUS/issues/131) | Complete the private verified Soldier gameplay trace comparison on the ARM64 runner and reconcile remaining RNG setup/parity items (`golden_python_room.py` now aligns `owner_component_start_frame = 1047` and `setup_delivery_ticks = 50`). |
