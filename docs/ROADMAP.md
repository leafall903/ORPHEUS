# Restoration Roadmap

Target build: **Overwatch 1 retail 1.74.0.0.104319**.
See [Python and Rust gameplay parity](PARITY.md) for per-behavior evidence and the next Soldier verification scenarios.
For completed test runs see [docs/STATE.md](STATE.md) and [CHANGELOG.md](../CHANGELOG.md). For active tasks see [GitHub Issues](https://github.com/leafall903/ORPHEUS/issues).

---

## Milestones

| Milestone | Scope | Current Status |
| --- | --- | --- |
| **M0 · Research & Replay** | Protocol specs, asset extractors, deterministic encrypted session replay | Target build pinned; v1/v2 controlled server journal & replay exist; external `.pb`/keys still local. |
| **M1 · Body & Weapon Init** | Stable spawn, weapon/ammo HUD, 10x hero switch & rejoin | Headless loopback passes; 60s original-client Soldier stability pass pending (#3). |
| **M2 · Rifle & Prediction** | Sustained firing, reload, lost-input command/ammo recovery | Server rifle/ammo/Sprint tests pass; client Sprint/fire desync investigation open (#4). |
| **M3 · Single-Player Combat** | Hit/miss/kill/respawn, overhead HP, elimination banner & kill feed | Server combat/respawn works; bot HP bar (#7), kill feed name (#8), headshot sound (#9) open. |
| **M4 · Two-Client Combat** | Synchronized movement, LOS, combat, and respawn across two clients | Collision movement & loopback checks pass; two-client original-client session pending. |
| **M5 · Modes & Heroes** | Full match lifecycle, objectives, and abilities across supported heroes | Practice Range, Tutorial & 47 PvE cards enter cleanly; Doomfist/Wrecking Ball/McCree partial. |
| **M6 · Reliability & Release** | Tick latency budgets, fault isolation, clean Windows deployment | Server-only startup & packet budget guards pass; [loopback worker measurements](PERFORMANCE.md) track server timing; multi-client playtest latency remains open. |

---

## Hero Checklists

### Soldier: 76 (Primary Baseline)
- [x] **Server simulation:** body/weapon VM, rifle firing & reload, Sprint, Helix Rockets, Biotic Field, Practice Range bots.
- [ ] **Original-client 60s verification ([#3](https://github.com/leafall903/ORPHEUS/issues/3)):** confirm weapon model, ammo HUD, Sprint, Helix, and Biotic Field across repeated switches/rejoins.
- [ ] **Sprint/fire prediction ([#4](https://github.com/leafall903/ORPHEUS/issues/4)):** isolate client prediction desync when pressing/holding fire around Sprint.

### Doomfist
- [ ] **Hand Cannon ([#5](https://github.com/leafall903/ORPHEUS/issues/5)):** server fires 11 pellets (`1/4/6`) with falloff, but Training Bot damage and firing sound on shots 2–4 failed in live client tests.
- [ ] **Rocket Punch ([#36](https://github.com/leafall903/ORPHEUS/issues/36), [#47](https://github.com/leafall903/ORPHEUS/issues/47)):** dash movement, wall stop, charge-walk slow (`2.7–2.8 m/s`), and camera tracking verified in client; target knockback/damage, `STU_FBFF5DE7` wall impact, and 1P gauntlet spawn-anchor glitch (#47) remain open.
- [ ] **Rising Uppercut ([#37](https://github.com/leafall903/ORPHEUS/issues/37)):** self-launch and PvP target reaction scripts work in tests; Training Bot knockup/damage and `STU_6FEE499E` remain open.
- [ ] **Seismic Slam ([#6](https://github.com/leafall903/ORPHEUS/issues/6)):** grounded leap, airborne static collision placement, close sphere, forward shockwave, and landing effects implemented; full client retest on updated `main` pending.
- [ ] **The Best Defense & Meteor Strike ([#38](https://github.com/leafall903/ORPHEUS/issues/38)):** passive shields (30/ability, 75/ult, cap 150, decay 3/s) implemented in tests; Meteor Strike early landing input (`0C51` unknown nodes) and impact audio/VFX remain open.

### Wrecking Ball
- [ ] **Quad Cannons & Roll:** authored spread cone/decay, automatic reload on exit, and Roll speed/lockout transitions implemented offline; client acceptance pending.
- [ ] **Grappling Claw:** static wall hook attachment and range/floor rejection implemented offline; swinging/retraction and high-speed collision open.
- [ ] **Piledriver:** altitude check, windup/descent, tapered AoE damage (`100 -> 20`), and player reaction scripts implemented offline; NPC launch impulse and client acceptance pending.
- [ ] **Adaptive Shield & Minefield:** `100 + 75 * nearby` temporary shield pools and 15-mine volley deployment/arming/proximity detonation implemented offline; client acceptance pending.

### PvE Entrances & Hero Sandbox
- [x] **Archives & Junkenstein Entrances (47/47 cards):** all 47 difficulty/challenge cards plus Tutorial and Practice Range load hero select, enter map, and return to menus cleanly on client 104319.
- [ ] **32-Hero Upstream Integration Candidate:** 32 hero deliveries (including scoped Rialto/McCree, Ana, Mei, Winston, Pharah, and Genji work from PR [#16](https://github.com/leafall903/ORPHEUS/pull/16)) exist on branch `codex/python-hero-upstream-20261113` (Draft PR [#32](https://github.com/leafall903/ORPHEUS/pull/32)); remains separate from `main` pending conflict resolution and CI fixes.

---

## Core Subsystems

- [ ] **Research & Data Preparation:** 95 catalog maps have extracted collision caches; external runtime bundle excludes raw game assets and private signing/content keys.
- [ ] **Lobby, Accounts & Communication Wheel:** login, hero select, skin equip, persisted settings (`22200`–`22204`), equipped voice lines, sprays, and emotes (Mercy Relax/Flurry weapon & 3P camera verified on 104319; displacement emotes like 「有胆就来」 still need root-motion work).
- [x] **Private custom room basics:** original client verified creation, team switching, map preview, map loading and return to the room.
- [ ] **Custom matches:** verify remaining lobby and hero selection presentation, shared room admission, and Escort gate poses, HUD pusher counts and arrows, health-pack pickup, and multi-round play. The single-round Escort flow and progress HUD are restored.
- [ ] **Network Synchronization:** headless tests cover reliable body creation and retirement, packet budget trimming, and uint32 command rollover. Prepared loopback tests also verify body ID reuse after lost hits, destroy retries, and ID exhaustion ([#12](https://github.com/leafall903/ORPHEUS/issues/12)). Multiplayer client acceptance is tracked separately.
- [ ] **Maps & Environment:** static collision caches built, but Practice Range out-of-bounds death plane / respawn (#13) and dynamic player-vs-player collision blocking remain open.
- [ ] **HUD, Audio & Kill Feed:** enemy bot overhead HP bars (#7), localized bot names in kill feed (#8), headshot audio (#9), Athena multikill audio (#10), and bot death voice listeners (#11) remain open.

---

## Research Notes Index (Internal Protocol & Graph Docs)

Detailed low-level RVA/STU notes in `docs/`:

- **Rialto / Retribution / McCree:** [RETRIBUTION_RESTORATION.txt](RETRIBUTION_RESTORATION.txt) · [PVE_COMBAT_SANDBOX.txt](PVE_COMBAT_SANDBOX.txt) · [MCCREE_COMBAT.txt](MCCREE_COMBAT.txt) · [MCCREE_ROLL.txt](MCCREE_ROLL.txt) · [DEADEYE_TARGET_BRIDGE.txt](DEADEYE_TARGET_BRIDGE.txt) · [ASSASSIN_PIN_BRIDGE.txt](ASSASSIN_PIN_BRIDGE.txt) · [ENFORCER_MANUAL_CONTEXT.txt](ENFORCER_MANUAL_CONTEXT.txt) · [NPC_NAVIGATION.txt](NPC_NAVIGATION.txt)
- **Communication Wheel & Animation:** [COMMUNICATION_WHEEL.txt](COMMUNICATION_WHEEL.txt) · [ANIMATION_ALIAS_STATE.txt](ANIMATION_ALIAS_STATE.txt) · [MODEL_LOOK_STATE.txt](MODEL_LOOK_STATE.txt) · [VOICE_STIMULUS_STATE.txt](VOICE_STIMULUS_STATE.txt) · [DATA_FLOW_MAPPING.txt](DATA_FLOW_MAPPING.txt)
- **StateScript VM Internals:** [PLACED_SCRIPT_INITIALIZATION.txt](PLACED_SCRIPT_INITIALIZATION.txt) · [SCENE_SCRIPT_HOST.txt](SCENE_SCRIPT_HOST.txt) · [ENTITY_SCRIPT_COMPONENTS.txt](ENTITY_SCRIPT_COMPONENTS.txt) · [ENTITY_CONTROL_STATE.txt](ENTITY_CONTROL_STATE.txt) · [ENTITY_QUERIES.txt](ENTITY_QUERIES.txt) · [ENTITY_POSSESSION_QUERIES.txt](ENTITY_POSSESSION_QUERIES.txt) · [ENTITY_TEAM_QUERY.txt](ENTITY_TEAM_QUERY.txt) · [PLAY_SCRIPT_CORE.txt](PLAY_SCRIPT_CORE.txt) · [PLAY_SCRIPT_QUEUE.txt](PLAY_SCRIPT_QUEUE.txt) · [PLAY_SOURCE_CONTEXT.txt](PLAY_SOURCE_CONTEXT.txt) · [SHARED_VARIABLE_STATE.txt](SHARED_VARIABLE_STATE.txt) · [VARIABLE_ASSIGNMENTS.txt](VARIABLE_ASSIGNMENTS.txt) · [ARRAY_ACTIONS.txt](ARRAY_ACTIONS.txt) · [NUMERIC_MODIFIERS.txt](NUMERIC_MODIFIERS.txt) · [CONFIG_EVALUATION.txt](CONFIG_EVALUATION.txt) · [CONFIG_ARRAY_SELECTION.txt](CONFIG_ARRAY_SELECTION.txt) · [CONTROLLER_HERO_ITERATION.txt](CONTROLLER_HERO_ITERATION.txt) · [STATE_INPUT_JOIN.txt](STATE_INPUT_JOIN.txt) · [REGISTERED_STATES.txt](REGISTERED_STATES.txt) · [ORDINARY_SCOPE_PHASE.txt](ORDINARY_SCOPE_PHASE.txt) · [FOR_CODE_BINDINGS.txt](FOR_CODE_BINDINGS.txt)
- **Messages, Effects & Replay:** [GAME_MESSAGE_ACTION.txt](GAME_MESSAGE_ACTION.txt) · [MESSAGE_MANAGER.txt](MESSAGE_MANAGER.txt) · [MESSAGE_PARAMETERS.txt](MESSAGE_PARAMETERS.txt) · [MESSAGE_RECEIVE.txt](MESSAGE_RECEIVE.txt) · [MESSAGE_RECEIVER.txt](MESSAGE_RECEIVER.txt) · [MESSAGE_SENDER.txt](MESSAGE_SENDER.txt) · [AUXILIARY_EFFECT_ALLOCATION.txt](AUXILIARY_EFFECT_ALLOCATION.txt) · [AUXILIARY_EFFECT_PARAMETERS.txt](AUXILIARY_EFFECT_PARAMETERS.txt) · [AUXILIARY_EFFECT_STATE.txt](AUXILIARY_EFFECT_STATE.txt) · [AUXILIARY_SCOPE_HISTORY.txt](AUXILIARY_SCOPE_HISTORY.txt) · [AUXILIARY_SUBGRAPHS.txt](AUXILIARY_SUBGRAPHS.txt) · [SIMULATION_CONTRACT.txt](SIMULATION_CONTRACT.txt) · [PACKET_BUDGET.txt](PACKET_BUDGET.txt)
