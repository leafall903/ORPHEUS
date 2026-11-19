# Shared Behavior Contracts

ORPHEUS uses small, language-neutral JSON fixtures under `fixtures/behavior/` so the **Python** server (`main`) and the **Rust** server (`rust`) stay aligned on byte-level protocol encoding, VM rules, and gameplay mechanics without sharing source files or shipping game assets.

---

## How It Works

1. Any Python fix or feature that changes wire bytes, state-machine lifecycle, or query rules should include a minimal synthetic JSON contract under `fixtures/behavior/<name>-v1.json`.
2. Both Python and Rust run their real production code paths against the same fixture file:
   - Python tests read `ORPHEUS_BEHAVIOR_FIXTURES` (defaulting to `fixtures/behavior/`).
   - `rust`'s `shared-behavior-contracts` CI job checks out `main` alongside `rust` and runs `main`'s Python consumers against `rust`'s `fixtures/behavior/` directory so neither side can drift silently.
3. Public fixtures must contain synthetic or approved inputs only—no copyrighted client assets, private keys, or full packet captures.

### Standard JSON Envelope

| Field | Meaning |
| --- | --- |
| `schema_version` | Schema integer version (currently `1`) |
| `case_id` | Stable semantic contract identifier |
| `target_build` | Overwatch build (`104319`) |
| `source` | Pinned commit/revision and code paths that generated the expected outputs |
| `evidence` | Provenance category (`synthetic-reference`, `synthetic-strict-host-reference`, etc.) |
| `client_acceptance` | Original-client observation status (`pending` or recorded session notes) |
| `input` | Explicit inputs (byte arrays encoded as hex strings) |
| `expected` | Expected output bytes, events, errors, or state snapshots |

---

## Contracts on `main`

The tables list selected fixtures. The [fixture directory](../fixtures/behavior/) contains the full set. Python also checks shared StateScript behavior in the [VM](../tests/public/test_statescript_vm_contracts.py), [entity](../tests/public/test_statescript_entity_contracts.py) and [combat](../tests/public/test_statescript_combat_contracts.py) contract suites.

### Public CI Contracts (No Runtime Data Required)

| Fixture | Scope & Behavior | Python Test | Rust Status |
| --- | --- | --- | --- |
| [jam-framing-v1.json](../fixtures/behavior/jam-framing-v1.json) | JAM stream framing encryption/decryption (`00..1f` key, all split boundaries & 1-byte delivery) | `tests.public.test_behavior_contracts` | Covered in CI (`orpheus-wire`) |
| [world-queries-v1.json](../fixtures/behavior/world-queries-v1.json) | Strict VM World queries (`origin`, `position`, foreign vars, missing/null vs. unavailable host services) | `tests.public.test_world_query_contracts` | Covered in CI (`--test world_queries`) |
| [profile-settings-v1.json](../fixtures/behavior/profile-settings-v1.json) | Client settings persistence (`22200`–`22204` $\rightarrow$ `+0x130` signed 32-bit keys / 64-bit values $\rightarrow$ `20802`) | `tests.public.test_profile_settings_contracts` | Covered in CI (`--test profile_settings`) |
| [seismic-slam-wave-v1.json](../fixtures/behavior/seismic-slam-wave-v1.json) | Seismic Slam immediate wave reach sampling, target deduplication & child state delivery | `tests.public.test_behavior_contracts` | Covered in `rust/tests/seismic_slam.rs` |
| [seismic-slam-placement-v1.json](../fixtures/behavior/seismic-slam-placement-v1.json) | Seismic Slam airborne selector: idle, miss retains prior point, success publishes before MoveTo | `tests.public.test_behavior_contracts` | Covered in `rust/tests/seismic_slam.rs` |
| [body-short-action-retry-v1.json](../fixtures/behavior/body-short-action-retry-v1.json) | Owner & observer short frame-action encoding, retry with original stamp across loss, and ACK retirement | `tests.public.test_behavior_contracts` | Covered in `rust/tests/body_short_action_retry.rs` |
| [server-only-movement-v1.json](../fixtures/behavior/server-only-movement-v1.json) | Channel-2 direction mod throttles (`0D06` Rocket Punch dash) & owner record window (+250 ms tail) | `tests.public.test_behavior_contracts` | Covered in `rust/tests/server_only_movement.rs` |
| [emote-weapon-selection-v1.json](../fixtures/behavior/emote-weapon-selection-v1.json) | Weapon manager selects an available slot for emotes and keeps the equipped slot on normal branch re-entry | `tests.public.test_behavior_contracts` | Pending Rust coverage |
| [wheel-spray-pose-v1.json](../fixtures/behavior/wheel-spray-pose-v1.json) | Confirmed spray command pose and eye spring offset across retries and fallback | `tests.public.test_wheel_spray_pose_contract` | Pending Rust coverage |
| [deathmatch-arena-spawns-v1.json](../fixtures/behavior/deathmatch-arena-spawns-v1.json) | Team and solo Deathmatch spawn assignment on arenas with shared points | `tests.public.test_deathmatch_arena_contract` | Pending Rust coverage |
| [escort-objective-placeables-v1.json](../fixtures/behavior/escort-objective-placeables-v1.json) | Escort opening world includes objectives, health packs and mode-filtered server scenery. Late viewers receive the final assembly countdown | `tests.public.test_escort_objective_placeables_contract` | Pending Rust coverage |
| [escort-round-v1.json](../fixtures/behavior/escort-round-v1.json) | Single custom Escort round with checkpoint timing, overtime, results, native route HUD and pusher state, and checkpoint-specific spawn-room doors and barriers | `tests.public.test_escort_round_contract` | Pending Rust coverage |
| [custom-room-preset-refresh-v1.json](../fixtures/behavior/custom-room-preset-refresh-v1.json) | Room settings, preset snapshot and roster before starting a match | `tests.public.test_custom_room_refresh_contract` | Pending Rust coverage |
| [custom-room-live-controls-v1.json](../fixtures/behavior/custom-room-live-controls-v1.json) | Live setting saves, spectator slots, hero selection reset on team changes, return to lobby, and one restart after the old match exits | `tests.public.test_custom_room_live_controls_contract` | Pending Rust coverage |
| [custom-mode-start-v1.json](../fixtures/behavior/custom-mode-start-v1.json) | Custom mode start rule lookup, in-game administrator start, and waiting phase presentation on the game link | `tests.public.test_custom_mode_start_contract` | Pending Rust coverage |
| [assembly-hero-selection-v1.json](../fixtures/behavior/assembly-hero-selection-v1.json) | First confirmation, Change Hero request, and reselection before the assembly countdown expires | `tests.public.test_assembly_hero_selection_contract` | Pending Rust coverage |
| [ffa-wheel-chat-v1.json](../fixtures/behavior/ffa-wheel-chat-v1.json) | Native wheel chat action and localization variables delivered to the sender's controller in free-for-all | `tests.public.test_ffa_wheel_chat_contract` | Pending Rust coverage |
| [custom-match-presentation-v1.json](../fixtures/behavior/custom-match-presentation-v1.json) | Native custom game subtitles and team objectives during loading and hero select | `tests.public.test_custom_match_presentation_contract` | Pending Rust coverage |
| [spawn-sector-v1.json](../fixtures/behavior/spawn-sector-v1.json) | Reconstructed horizontal spawn sector, projected center, zero-axis fallback and independent wall/floor rejection | `tests.public.test_spawn_sector_contract` | Pending Rust coverage |
| [patrol-victim-velocity-v1.json](../fixtures/behavior/patrol-victim-velocity-v1.json) | Floor-following patrol stops for the next tick on a live victim PlayScript channel-0 velocity reset | `tests.public.test_patrol_victim_velocity` | Pending Rust coverage |
| [owner-first-command-anchor-v1.json](../fixtures/behavior/owner-first-command-anchor-v1.json) | Initial owner command admission anchored to body-script frame when no command history exists | `tests.public.test_behavior_contracts` | Covered in `rust` (`game_replication::tests`) |

The spawn sampler starts at the floor below the requested center and interprets its half-angle in the horizontal request frame. A direction with no horizontal component uses +X. Its first angular probe is at the negative edge, which the reconstruction includes when the path is supported and clear. The fixture exercises this geometry directly. The authored script path normalizes the direction and rounds the full degree angle to a float32 half-angle in radians, so an authored 90-degree request and a direct binary64 `pi / 4` request have different numeric boundaries. Retail candidate coordinates remain unverified.

Run the public contract test modules locally without runtime data:

```powershell
python -B -m unittest discover -s tests/public -p "*contract*.py" -v
```

### Runtime-Backed Contracts (Require Local 104319 Runtime Pack)

| Fixture | Scope & Behavior | Python Runtime Consumer |
| --- | --- | --- |
| [wheel-emote-presentation-v1.json](../fixtures/behavior/wheel-emote-presentation-v1.json) | Equipped emote controller slots, owner `023A` resource/camera framing, and angle-lock lifecycle | `tests/runtime/test_communication_wheel.py` |
| [wheel-emote-weapon-v1.json](../fixtures/behavior/wheel-emote-weapon-v1.json) | Mercy keeps her staff during and after Relax, Flurry and Caduceus in Practice Range and solo Hybrid, followed by a manual switch | `tests/runtime/test_communication_wheel.py` |
| [player-voice-v1.json](../fixtures/behavior/player-voice-v1.json) | Equipped hero voice-line lookup and friendly vs. hostile listener delivery | `tests/runtime/test_game_voicelines.py` |
| [wheel-spray-possession-v1.json](../fixtures/behavior/wheel-spray-possession-v1.json) | Equipped spray slots and empty slots on hero switch | `tests/runtime/test_communication_wheel.py` |
| [custom-room-v1.json](../fixtures/behavior/custom-room-v1.json) | Private room lifecycle, map preview, settings, and one handoff on repeated Start | `tests/runtime/test_custom_rooms.py` |
| [custom-room-modes-v1.json](../fixtures/behavior/custom-room-modes-v1.json) | Capture the Flag presets, Team Deathmatch, and free-for-all rosters and handoffs | `tests/runtime/test_custom_rooms.py` |
| [custom-room-names-v1.json](../fixtures/behavior/custom-room-names-v1.json) | Room and team name updates and persistence on match return | `tests/runtime/test_custom_rooms.py` |
