# Credits and acknowledgments

This repository builds on existing Overwatch preservation work. Its lobby improvements, dashboard, packaging and experiments would not have been possible without the code, tools and findings shared by other researchers.

## Foundation

- **[Boi-027](https://github.com/Boi-027/Overwatch-1-v1.74-Lobby-Research)** — the original Overwatch 1.74 Lobby Research repository on which this project is based, including its protocol research, implementation and reference material. Please check out the original project.
- **Arlecchino** — the original copyright holder named in the upstream MIT license. The original notice is retained in [LICENSE](LICENSE).

## Protocol research and shared findings

- The local Cassidy immunity routing cases use synthetic post-AI damage and
  the existing authorized prepared 104319 scene/owner assets. They exercise
  actual room dispatch without claiming native attack or client evidence.

- Flashbang entry admission follows loaded 224E/4's Boolean expression, public
  schema variable7312(hex), and the absence of that override in0228/7. Its
  synthetic cases compare effective linked bindings through Rust and the normal
  Python VM pinned at e1128509458882a325c743f67ebed4a5c0c2f1a7.
  Fresh native inspection also confirms BCA9E0 zero-initializes filter25 and
  CF4340 writes the query result without inversion; these facts do not establish
  a Boss category mask. Captures and native reports remain external.

- The natural Reaper E-input regression uses authorized Normal mode graphs,
  Cassidy 01E9 projectile037D data, actual castle collision, the authored Reaper
  intro/pursuit and existing room control bridge. It connects owner input,
  normal lease admission, flight, NPC health and observer projection with
  explicit prepared-runtime adapters. This is headless behavior evidence,
  without original-client, complete224E reaction or network-transport claims.

- Generic entity-filter tag clauses follow authorized native D0EC70's ordered
  +58/+60/+68 collection and DB2FD0's any/all/excluded predicate using original
  DB5030/DB4E70 membership helpers. A 72-case synthetic fixture is checked by
  restricted native emulation with declared storage and production Rust
  dispatch. Existing local lane023B native probes located the helper windows;
  fresh verification records stay external. No complete actor or client claim
  is made and no captured bytes are redistributed.

- Team-score regressions follow authorized 0CBC death/tag branches and
  0CC7/1/5/63 accumulation, Normal multiplier and gate-bonus graphs: real 06A4
  (39E) and 06C8 (3AF) carriers feed effective entity2499 and the 1188 sync slot5
  projection. Independent body-tag constants establish all eight base-point
  values. Restricted Rust replays cover actual Cassidy kills, natural result
  branches and earned-score retention on defeat. These are headless authored-
  graph observations, without Python or original-client presentation claims.
  Localized stat names and native field174 analysis are research inputs only;
  captures, game data and generated evidence remain outside the source tree.

- NPC preview selection uses authorized 104319 STU definitions inspected with
  OWLib/DataTool and immutable native code: 93CBF16B at 9491A0, cosmetic builder
  64EC30, component-index table F525E0 and iterators 18384A0/1838D10. Native
  emulation establishes model4, local-idle42, health51 and script47; the original
  iterator consumes compiled production pruning output. These findings support
  the preview reconstruction, not original-client presentation. Captures, XML,
  native adapter probes and generated binaries stay outside Git.

- Total health CEB6C0/9B7648C3 follows pinned Python `e1128509`
  `SceneWorld.native`, `_health_value` and `play._scalar`: live totals,
  no-write unbound lookups, the declared numeric upper bound and float32
  results. Twenty-one synthetic cases use normal scene/evaluator readers;
  extreme values use explicit f64 bit patterns to avoid decimal parser drift.
  Real room/owner vitals supply actor facts; no health is inferred from team,
  tags or script-variable defaults. No original-client result is claimed.

- Mover velocity reads: pinned Python `e1128509` `SceneWorld.native`,
  `_position` and `play._scalar` establish CF28B0/D1FD80's default mover path,
  first-config scalar selection, no-write results and finite float32 vectors.
  The 19 synthetic cases use normal Python/Rust evaluators. Contains/branch
  routing follows `nodes.contains`, `nodes.branch` and `expr.contains_value`
  (AA85A30/AA83CB0); 16 synthetic branch cases reuse existing VM equality.
  These are reconstruction comparisons, not original-client observations.

- Room fault isolation is integrated from public Rust `f763beb9` / PR #31
  (`game_service/room.rs`, `game_service/state.rs`, typed combat errors and
  the room-recovery fixture/tests). Contributor commit history is preserved.
  Local encounter delivery is adapted to those room-owned boundaries; the
  recovery policy is server behavior, not captured original-game fidelity.

- Corpse-retirement action routing and phase-exit policy follow pinned
  Python `e1128509` `ow174/game/script/nodes.py::retire_junkenstein_corpse`.
  The 12 synthetic cases preserve the six admitted sites, explicit host
  request flags, invalid arguments and refusal behavior through normal VM
  dispatch. This credits the reconstructed reference policy, not recovered
  universal destroy-entity behavior or original-client acceptance.

- Foreign script-component read semantics: pinned Python `e1128509`
  `ow174/game/script/scene.py` (`entity_var_slot`, `entity_var`) and
  `ow174/game/script/runtime.py` (`Evaluation.member`, `Component.set`).
  The ten synthetic contract cases use actual scene bindings and normal
  evaluator paths. Rust transports authoritative frame snapshots; the
  reference's live variable-slot notification ordering remains distinct.
  No game assets, captures or runtime packs are included.

- Cassidy Combat Roll: local Python `e1128509` `ow174/game/mccree.py` and
  `tests/test_mccree_roll.py` supply sampled-velocity delta, next-frame timing,
  horizontal cap and collision/reference cases. Interpreter fallback behavior
  is preserved only at scoped 01F0 sites; unknown native effects remain open.
  Separately prepared 104319 graphs/codecs supply authored expressions and
  linked payload shapes. No runtime data is imported.

- **Xref** — big thanks for sharing a knowledge base of captured Overwatch 2 game traffic and research on the 1.74 game server. It gave us the facts behind the game server, matchmaking and hero select, and saved a lot of time.
- **The AyakaPS Discord community** — fellow Overwatch researchers who share findings, information and research with one another. Thanks to the members whose shared work helped this project.
- **Logo2K, a researcher in the AyakaPS community** — shared research notes and a lobby-map reference:
  - [Overwatch 1.74 lobby research](https://rentry.co/3bswrgmw#overwatch-174-lobby-protocol-channel-setup-login-and-loot-boxes)
  - [Lobby map list](https://rentry.co/wcwyv3r8) — a list of every lobby map, updated as additional information is found.
- **Zagrion (AyakaPS)** — credited in the upstream research for the server-to-client key work and TCP reassembly method used to recover the reference traffic.
- **Sidiusz** — credited by the upstream project for helping investigate and resolve research blockers.
- **Blizless** — acknowledged alongside AyakaPS in the inherited handshake implementation for state-blob findings.

## Tools and related projects

- **[LizRollin's 1.74 runtime branch](https://github.com/LizRollin/overwatch-1.74-lobby-server/tree/9f8f3bcb5eccab6fb7738bee3c2371fcf0623f29)** —
  the expression interpreter, native statescript codecs, driver, character physics, collision,
  combat and associated tests evaluated and integrated at this pinned revision. Its MIT notice
  is retained. Upstream live-test claims and known defects are recorded separately from our local validation.
- **[AipNooBest's research](https://github.com/AipNooBest/overwatch-1.74-lobby-server/tree/f8426859c3195691d379f2fb291a475dfa40139e)** —
  additional body initialization and state-payload research used for comparison. Later HUD corrections
  were checked before using earlier conclusions.

- **[overtools / OWLib](https://github.com/overtools/OWLib)** — DataTool, TankLib, asset definitions and extraction tools used to inspect client data and prepare catalogs.
  The local target-build extraction also uses the `overwatch/1.74` source revision
  `7afe5f52706e7002ed8c2717299cd60846f3fa3a`, with its pinned TACTLib and revorbstd dependencies.
- **[overtools / TACTLib](https://github.com/overtools/TACTLib)** — CASC/TACT reading and related tooling used by the extraction toolchain.
- **[Plasmawatch](https://github.com/plasmawatch/login-server)** — Battle.net login-server emulation and protocol reference work.
- **[Prometheus](https://github.com/saturn-xvi/prometheus/tree/ad1fea7e6c04c9959c7115cb8ecd9c79c93c1ccb)** —
  early/beta Overwatch research, also compared here for controller/model associations,
  statescript structure and replication methods. Its beta component IDs and memory writes
  are not transplanted into the 1.74 server; it remains a separate project with its own license.
- **The authors of the community TLS relay** — the foundation of the relay used for the local retail connection.

## Rust migration and behavior references

- **[Bevy ECS](https://github.com/bevyengine/bevy/tree/v0.16.1/crates/bevy_ecs)** provides the standalone entity and component storage used by the Rust match worker. Its default features are disabled.
- **[mlua](https://github.com/mlua-rs/mlua/tree/v0.11.6)** and **[Lua](https://www.lua.org/)** provide the runtime for the event-driven patrol pilot. Its behavior comes from the existing ORPHEUS patrol reconstruction and build 104319's authored route data.

- **Boi-027, Arlecchino and the upstream 1.74 server contributors** supplied the existing lobby handshake,
  JAM cipher and framing that serve as the Rust port's protocol reference. The original MIT notice is retained.
- **LizRollin and the original game runtime contributors** supplied the inherited game transport,
  float32 collision and character mover, statescript runtime and payload layouts, and the hitscan,
  predicted projectile and combat notice behavior used as Rust references. The pinned runtime
  source is listed above.
- **[RustCrypto](https://github.com/RustCrypto)** maintains the AES-GCM, HMAC and SHA-256 crates used by the Rust
  transport. **[The Rust Random Project](https://github.com/rust-random/getrandom)** supplies access to operating
  system randomness for the lobby challenge.
- **[Prost](https://github.com/tokio-rs/prost)** and **[prost-reflect](https://github.com/andrewhickman/prost-reflect)**
  supply protobuf framing and reflection over the external Battle.net descriptors.
- **[Tungstenite](https://github.com/snapview/tungstenite-rs)** supplies WebSocket framing for the local
  Battle.net listener.
- **[lzma-rust2](https://github.com/hasenbanck/lzma-rust2)** supplies XZ decoding for the movement
  and expanded-ray collision caches.
- **[Mio](https://github.com/tokio-rs/mio)** supplies portable UDP readiness polling and cross-thread
  wakeups for the Rust game worker.
- **[flate2](https://docs.rs/flate2/)** supplies gzip decoding for the external state graph bundle.
- **roxmltree** parses the local DragonML entity definitions used by the runtime preparation tool. [Project source](https://github.com/RazrFalcon/roxmltree)
- The statescript VM uses a deterministic SplitMix64 stream for local random expressions. The fixed-increment generator follows the public-domain implementation. [Sebastiano Vigna's SplitMix64](https://prng.di.unimi.it/splitmix.c)
- **TripleFault and Gamayun contributors** supplied a same-build comparison for encrypted combat scenarios and
  tick measurement design. The locally reviewed 104319 VM code in `internal/vm/natives.go` also corroborates
  the separate possessed-body and current-possessor queries and the health pool type selectors.
  Its `registry.go` and `states.go` provide behavior references for the synced game-code state,
  the tips state and the input bridge. Its `switch.go` and `instance.go` corroborate delayed
  branch timing and event ordering. Its `internal/world/health.go` provides an independent comparison
  for the component 51 fields and the provisional pool ID order. Gamayun identifies its Go implementation
  as AGPL-3.0-or-later and retains the MIT notice for inherited code.
- The frozen Python `ow174/game/onfire.py` and Gamayun's `internal/vm/natives.go` document the
  possessor's component 64 on-fire lookup. Gamayun's `internal/expr/value.go` provides a comparison
  for ordered map values and first-match lookup.
- The local build 104319 graphs exported with OWLib provide the single-tag grant and suppression
  paths in `01C7`, `01CF` and `0251`. Locally reviewed build 104319 codec tables from Gamayun and
  Boi-027's research corroborate the inherited empty payload for state `STU_E56BA8D8`.
- The local build 104319 graph bundles from Gamayun and Boi-027 and the OWLib exports corroborate
  the operands for the instance exit, absent AI state and zero-resource lookup. Local client
  155284 inspection provides the queued exit, parent completion and typed zero return evidence.
  The frozen 104319 state decoder and runtime provide finish-event and teardown comparisons.
- The build 104319 graph and codec tables corroborate the movement sampler's authored fields
  and 13-bit owner payload. Local 155284 client inspection identifies its movement-record inputs,
  changed-output writes and embedded record at owner context construction. The frozen 104319 mover
  provides a separate comparison for the airborne, crouch, ground-contact and jump flags.
- The frozen `ow174/game/projectiles.py` and `ow174/game/combat.py` provide the owner projectile
  lease, exact packet acknowledgement, release and swept flight references. The local build 104319
  Soldier weapon graph supplies Helix's authored lifetime, direct damage and splash settings.
- **The Claudewatch contributors** supplied the Beta 1.0.0.3 research lead for attacker-side hit-confirm effects
  and separate entity and announcer voice messages. ORPHEUS checks these leads against 104319 client data before
  implementing them. The reference is a locally reviewed binary distribution.

- The local Python `junkenstein_ai.create` and `pools.authored_pools` provide
  the Boss profile lookup, encounter body capacity and explicit controller
  validation reference. File hashes pin this comparison in `docs/RUST_PARITY.md`.
- The 104319 DataTool entity exports declare component 92 as `STU_76AF64F0`.
  Local Python `scene.py` supplies the `STU_D3EB8D87` query reference.
- Local Python `scene.SceneWorld.native`, `expr.py` and `junkenstein_ai.py`
  supply the On Fire query and default value behavior for scene enemies.
- Local Python `junkenstein_scene._voice_recipients`, `scene.SceneWorld`
  and `entity_queries.py` supply the 00BD roster, filter and entity query
  references.
- Local Python `scene.SceneWorld` and the build 104319 codec table supply
  the outgoing combat damage filter and its link payload. The pinned
  `combat.py` and `driver.py` provide the hit-tag and observer comparisons.
- Local Python `runtime.Instance` message delivery and the defender
  bindings in `junkenstein_scene.py` supply the caller and instigator
  outputs and passive defender graph reference. The same module's
  `mount_player_intro_receiver` supplies the 06AB receiver startup reference.
- Local Python `game_message.py` and `scene.SceneWorld` supply the scene
  message delivery and effective body target-tag references for 06B0.
- Local Python `junkenstein_scene.py`, `scene.SceneWorld.routed_game_message_targets`
  and `nodes.py` supply the 06DE/06AC scene routes, linked controller
  guard and the base actions reached on the receivers. Their 06BE route
  and defender aim-lock placement also supply the result references.
  Python `statescript._write_frame_action` and `observers.acknowledged_delta`
  supply the outcome action layout and revision timing.
- Local Python `nodes.py`, `scene.SceneWorld.suppress_same_team_contact`
  and `mover.Mover.set_same_team_contact` supply the wave contact
  lifecycle and intro action continuation references.
- Local Python `junkenstein_dialogue.bind_gate_voice` supplies the gate
  narrator stimuli, line eligibility, and sample counts.
- Local Python `junkenstein_ai.bind_boss_voice`, `voice_stimulus.py`
  and `voicelines.queue_voice_notice` supply the Boss intro line IDs,
  enUS sample counts, timing and notice 27500 encoding.
- Local Python `nodes.FilterArray`, `scene.SceneWorld.filter_array_candidate`
  and `array_actions.insert` supply the 0CBA spawn roster reference.
- Local Python `play.resolve_request`, `play_queue.Queue`, and
  `runtime.Instance.start_entries` supply the PlayScript request and
  loaded-graph lifetime reference.
- Local Python `scene.SceneWorld.play_script_context` and the authored
  104319 0CBA and 0C95 call sites supply the room and enemy pair routing
  reference.
- Local Python `nodes.collect_targets` and
  `scene.SceneWorld.static_tag_targets` and its native transform reads
  supply the target array and transform reference. The 104319 0C95 export
  supplies the 0CE2 child bindings.
- Local Python `nodes.JunkensteinAIFacing`, `expr.evaluate` and
  `junkenstein_scene._apply_spawn_facing` supply the controller facing
  lifecycle, angle conversion and Boss yaw selection reference.
- Local Python `scene.SceneWorld.native`, `nodes.AuthoredBodyHealthPool`,
  `nodes.LocalCombatAccumulator` and `combat.Vitals` supply the enemy
  definition, wave health and dedicated-server local-context references.
- Local Python `entity_control.EntityControl`, `nodes.RegisteredState` and
  `statescript._write_state_link` supply the intro control lifecycle and
  owner and observer link encoding references.
- Local Python `nodes.RegisteredState` and `nodes._BOSS_BASE_STATES`
  supply the two cinematic registrations and their base-state child
  lifecycle reference.
- Local Python `nodes.RegisteredState`, `message_state.MessageState`,
  `message_receiver` and `statescript.write_payload` supply the time-scale,
  broadcast, pause and linked-target references for the Boss intro.
- Local Python `message_state.MessageState`, `nodes.SubScript`,
  `play.evaluate_value` and `runtime.World.selects_ai_behavior` supply the
  Zomnic wave sender, controller child binding and selection references.
- Local Python `scene.SceneWorld.native`, `runtime.World.selects_ai_behavior`
  and `nodes.JunkensteinDisplayNameOverride` supply the Reaper controller
  query, dormant behavior and localized name lifecycle references.
- Local Python `scene.register_ai_weapon_record` and `nodes._BOSS_BASE_STATES`
  supply the weapon record lifecycle and Reaper and Roadhog controller
  placement reference.
- Local Python `scene.SceneWorld.native` supplies the Tutorial comparison
  for the `STU_A77AC2AC` owner-body query.
- Local Python `nodes.button_held`, `LogicalButton` and `DisableLogicalButton`
  supply the held-input, block lifetime and listener resync reference.
- Local Python `nodes.EncounterTargetTag` supplies the authored enemy grant
  and cleanup reference. Enemy and player bodies share the existing Rust
  grant and suppression accounting.
- The prepared 104319 common-body `0033` pool states and the existing Rust
  `script_player::BodyWorld` supply the enemy pool lifecycle comparison. Local
  Python `driver.BodyWorld.native` corroborates the active-mode native result.
- Local Python `junkenstein_ai.py`, `script.nodes` and `script.scene`
  supply the comparison for explosion target selection, gate messaging,
  ForCode state notifications and corpse retirement.
- Local Python `junkenstein_scene.py` and `combat.py` supply the Zomnic
  contact projection and health application reference.
- Local Python `npc_navigation.py`, `mover.py` and `junkenstein_ai.py`
  supply the bounded gate search, capsule movement and arrival reference.
- Local Python `script/rulesets.py` and `junkenstein_ai.py` supply the
  Junkenstein magazine multiplier and Reaper's ordinary primary input policy.
- Local Python `weapon_spread.py`, `combat.py` and `script/scene.py` supply
  Reaper's deterministic pellet rings and encounter difficulty damage profile.
- Local Python `junkenstein_scene.py`, `script/scene.py` and `script/nodes.py`
  supply the Witch staff's target selection, recipient effect and health queries.
- Local Python `junkenstein_ai.py`, `junkenstein_scene.py` and `script/scene.py`
  supply the Summoner's weapon inputs, beam traces and difficulty profile.
- Microsoft Windows documentation for [VirtualProtect](https://learn.microsoft.com/windows/win32/api/memoryapi/nf-memoryapi-virtualprotect),
  [file mapping access](https://learn.microsoft.com/windows/win32/memory/file-mapping-security-and-access-rights)
  and [memory protection constants](https://learn.microsoft.com/windows/win32/memory/memory-protection-constants)
  informed the Gallery mapped-page failure investigation. The original-client
  retry still failed; these references do not establish a working patch policy.

Thank you as well to everyone who shared captures, tools, notes, testing, corrections and encouragement, including contributors whose names were not recorded in the materials available to us. If a credit is missing or needs correcting, please open an issue so it can be fixed.

Original copyright and attribution notices are retained. References to related projects acknowledge their work; their code and materials retain their respective licenses.

- Junkenstein mission-notification bootstrap: the separately prepared target-build
  104319 graph bundle supplies the 0C8C:68 -> 1188 dependency and its 0694/06A8
  GameMessageEntry bindings. Runtime graph data remains external to this source tree.
- Ongoing mission notifications use the same external 104319 graph's state,
  variable and message bindings; the regression drives its original 0694 and
  06A8 messages through the existing Rust VM and snapshot codec.
- Notification entity-input selection follows the same external graph's
  `sync_vars` entity scope flags; prepared transport checks use its original
  Normal/Cassidy clock and the existing reliable mode stream.

- The local build 104319 0C8C phase sends (17/83/126) and 1188 client-only
  message listeners establish the mission HUD startup dependency. The existing
  persistent-send and reliable-stream codecs are reused; no game assets are
  added. Python comparison remains pinned to cc620be.

- Battle/overtime clock repair: local 104319 graph 0C8C states 37/60 and
  Python cc620be `statescript.remote_delta` supply independent five-vector
  wire comparisons using external prepared graph/codec data. No assets imported.

- HUD announcement dispatch: external 104319 graph 1188:58 and Python cc620be
  `ow174/game/script/nodes.py::send_client_only_game_message` establish that
  the 0FCE client-targeted action has no server gameplay effect. The bounded
  client-variable inspection and isolated error log establish the projection
  failure; no capture or game data is included in this source change.

- Post-match manager inputs: the external 104319 graph 180A:4 declares local
  winner 7525 and optional parameter 10186 as receiver sync variables. The
  authorized client read-only comparison establishes absence before and
  winner 1 after projection. Python reference remains cc620be; no external
  code, runtime asset, log or capture is imported by this change.

- Post-match entity inputs: external 104319 graph 0283 declares entity sync
  slots 10274 and 32468; the local read-only native comparison finds 10274 null
  while the authored server holds true. Python comparison stays pinned to
  cc620be. No game asset, capture or runtime data is included in this change.

- Native match-result fields: authorized local 104319 disassembly identifies
  MECGameMode at component 114, the winner comparison at RVA 996891/9974E3,
  and the native float binding at CCDFB3 (offset 170). The mode component's
  live layout supplies the corresponding wire slots 7 and 9. Python reference
  remains cc620be; no captured binary, asset or disassembly is committed.

- Final-objective announcement: external 104319 graph 1188 nodes 58 and 59
  have the same 0FCE client-message and target shape, with different display
  text parameters. Python cc620be's client-only message handling remains the
  behavioral reference. No runtime assets or live logs are committed.

- Common-NPC mover preparation: the field mapping and four omitted-field
  defaults follow `tools/extract_game_movers.py` at local Python integration
  reference `fd09a36f`. Authorized local 104319 entity XML supplies the three
  NPC records; generated values and game data remain external. Event-driven
  numeric-modifier publication follows `NumericPropertyModifiers` in
  `ow174/game/script/nodes.py`, and gate speed resolution follows
  `ObjectiveApproach.update` in `ow174/game/junkenstein_ai.py`, both pinned
  to Python `e1128509`. Rust and Python random streams remain distinct.

- Cassidy Flashbang motion: authorized local 104319 graph 01E9 node 7 supplies
  speed, reach, gravity, launch bias and blast parameters. The float32 launch
  normalization follows `ow174/game/weapon_spread.py::launch_direction`, and
  the scoped range-end detonation policy follows `mccree.py`/`combat.py`,
  pinned to Python `e1128509`. The latter is a declared reconstruction;
  no asset, binary, XML or generated graph data is committed.

- Flashbang NPC control: duration comes from authorized 104319 graph 0228/7's
  schema override for 224E variable 215. Scoped interruption and exclusive
  deadlines follow `Combat._hit_effect`, `Combat._interrupt` and paused NPC
  route tests at Python e1128509. The Rust bridge uses authored ability exits
  and shared command/room lifetimes; full 224E semantics and native reaction
  presentation remain unverified. No extracted effect data is committed.

- Inline PlayScript and loaded instance policy: Python e1128509
  `ow174/game/script/play.py` (D05510/D055D0/D058D0),
  `play_queue.py`, `runtime.py::play_schema` and `start_entries` supply map
  ordering, duplicate-key, schema, source, reuse, replacement and once-entry
  comparisons. The shared Rust authored-map primitive is reused. Authorized
  local 104319 0228/224E graphs establish the effect-entry replay; no graph
  bytes, native binary or extracted asset is committed.

- Deferred effect execution: Python e1128509 `nodes.Defer` and
  `runtime.Instance.run_deferred` supply phase, priority, tie-order and
  cancellation comparisons (native AAA27C0). Ordered array removal follows
  that reference's 104319 1B6C00B/D56020 description for STU_DA54F8F7;
  the Rust primitive also serves authorized local graph 224E/176. The
  prepared effect uses actual Reaper body roots and entity-filter facts;
  no extracted graph, binary, capture or game asset is committed.

- Cassidy fire and encounter death inputs: Python e1128509
  `script/rulesets.py` supplies extracted 104319 Cassidy schema 0026's
  290C=true; `mccree.critical_default` supplies its explicitly declared
  primary-only implicit 2x policy. `test_mccree_gun.py` independently verifies
  70/140 primary and 50 noncritical fan damage. `Combat._bot_died` and
  `_death_parameters`, plus `script/bot.py`'s 003E message, establish the
  death-input comparison. Authorized external owner/Boss graphs and scene
  collision supply the local probe; no game asset, graph, binary or log is
  committed. Downstream death-native services are still incomplete.

- Shared unkeyed array insertion: Python e1128509 `script/array_actions.py`
  and `tests/test_script_array_insert.py` supply native 104319 1B6B720,
  1B6BCB0, F65260, F663A0 and F672D0 comparisons for merge/single insertion,
  typed equality, provider order, index conversion and strict storage limits.
  `script/expr.py` documents the empty-key 7BD2CA19 cached base evaluator
  (26292E0/123CC10). Explicit Rust host admission retains precedence for the
  existing Arcade queue input. The local source-only 9c0935be baseline
  independently reproduces the stale HUD-order test. No prepared graph,
  game asset, native binary or generated log is committed.

- Array behavior contract: the public fixture uses synthetic typed values and
  independently stored expected states from Python e1128509's insertion
  tests. Both the production Rust VM and the pinned Python action handler
  consume the same 19-case JSON file. The comparison runner uses only the
  standard library and reads a separately supplied reference checkout;
  no Python implementation, runtime pack or game asset is copied into Rust.

- Engine-setting reads: Python e1128509 `script/scene.py::SceneWorld.native`
  documents CF4A60's uint32-key dispatch and EVALUATION_FAILED result;
  `script/expr.py::evaluate` supplies caller-initial preservation.
  `junkenstein_scene.py` supplies the three typed 104319 headless-host
  settings. A public synthetic 14-case contract compares the production
  evaluators against stored results using a separately supplied pinned
  checkout; the original-client settings table and acceptance remain
  unverified. No game asset, native binary or prepared runtime is imported.

- Delivered target facts / combat selection: Python e1128509
  `ow174/game/script/scene.py::_filter_entity`, `_first_bound_combat_target`,
  `SceneWorld.native` and `entity_team.py::index_from_filters` establish the
  scoped killer relation, tag requirements and ordered target query. Public
  fixtures contain synthetic identities, flags and tags only; the comparison
  runner reads the separately supplied pinned implementation. Prepared game
  graphs stay external. No original-client fidelity is claimed.

- Witch friendly filters: Python e1128509 `scene.py::_filter_entity`,
  `_first_bound_combat_target`, `SceneWorld.native`, `play.py::evaluate_scalar`
  and `runtime.py::Component.parameter_scope` establish relationship masks,
  candidate parameter 8, true scalar initial values and lexical restoration.
  The 28-case fixture contains synthetic identities/positions/tags only; its
  runner reads the separately supplied pinned implementation. No game asset
  or Python implementation is copied into the Rust source tree.

- Shared voice lifecycle: Python e1128509 `script/voice_stimulus.py`,
  `script/nodes.py::VoiceStimulus`, `script/expr.py::guid_of`,
  `script/play.py::_asset`, and `junkenstein_scene.py` establish callbacks,
  explicit +80 polling, float32 timings, failed/destroyed cleanup and passive
  end plugs. Native references are D43F70, D44090, D446A0, 9FC440 and 1B8C690.
  The public 38-case fixture uses synthetic services, identities and a graph;
  expected traces were recorded independently through the pinned Python VM,
  then checked by both consumers. No game voice asset or Python source was
  imported. Actual Boss line/resource/backend and client audio remain open.


- Boss speech services: pinned Python e1128509
  `junkenstein_ai.py::bind_boss_voice`, its body/spawn/defeat/barrier numeric
  cue and sample-count facts, and `voicelines.py::queue_voice_notice` establish
  line eligibility, factory voice sets, recipients and raw duration rules.
  The 191-case fixture uses synthetic identities and explicit random draws;
  expectations come from the separately supplied normal Python backend.
  No audio/media payload or Python implementation is copied. Seed-sequence
  parity and original-client sound acceptance are not established.


- Defender body startup: pinned Python 46b22130
  `script/entity_queries.py::evaluate` establishes 00707E1E scalar selection
  and explicit component44 body-field versus absence/unavailability. The
  18-case fixture uses synthetic identities/services and independently stored
  Python results. `tools/extract_statescript_codecs.py` documents AAA02B0's
  parent-typeinfo lookup; the separately prepared 104319 runtime catalog
  resolves E9485CAF to base State registration 160, constructor AA826B0 and
  vtable 8B6BC800. Numeric provenance is recorded without binary/media data.
  Python differential coverage here excludes that native factory fallback.


- Applied damage/charge: Python e1128509 `combat.py::hit_def`,
  `script/runtime.py`, `script/nodes.py` and `script/scene.py::native` establish
  ordered damage asset tags, normal VM callbacks and C1CF60's ordinary
  two-team branch. Separately prepared 104319 0B8B nodes 3/4, 23, 59/60 and
  97/98 establish passive growth, live multipliers, cap and ordered target
  reductions. The public 41-case contract is synthetic with independent expected
  results from the normal pinned Python services; no full game graph, binary
  or media payload is imported. Host live-defender admission is an explicit
  scoped room policy, not complete native game-state delivery equivalence.

- Common corpse retirement: pinned Python e1128509
  `junkenstein_scene.py::JunkensteinSceneHost.before_units` and
  `script/scene.py::queue_enemy_retirement` establish the eight-second grace
  and pair retirement policy. Synthetic services exercise these normal source
  methods; no game asset or implementation source is imported.

- Retired enemy queries: pinned Python e1128509
  `script/scene.py::SceneWorld.unbind`, `entity_var_slot` and
  `script/entity_queries.py::evaluate` establish known entity removal versus
  an unavailable query/component service. Eight synthetic cases use those
  normal services with independently stored expectations. The Rust room keeps
  explicit pair-retirement facts; it does not infer an entire namespace.

- Retired entity teams: the same pinned Python e1128509
  `script/entity_team.py::evaluate` (104319 CEEDC0/D25400 query) establishes
  signed -1 for a known missing target. The extended eight-case retirement
  contract uses its normal lookup/context service, including a live target
  whose script backend is faulted. Unknown lookup services remain unavailable.

- Foreign slot notifications: Python e1128509 `script/runtime.py` live Var
  watchers, `InstanceContext.member`, CHANGED dispatch for 0C8C/33 and base
  transition completion, plus `script/nodes.py::SubScript`, establish the
  three synthetic lifecycle traces. Only fixture graph-store records are
  substituted; normal loader/VM/slot/handler behavior supplies expectations.
  Rust's room-frame notification delivery is explicit producer policy; exact
  native callback timestamps and all other producers remain outside this scope.

- Victory-view handoff: Python e1128509
  `script/scene.py::SceneWorld.routed_game_message_targets` explicitly routes
  0CEC/2 and /3 to 0CA5/0CED recipients. Normal `game_message.execute` collects
  their scalar parameters, and `nodes.send_client_only_game_message` forwards
  the separate client-only cue on the server. Three synthetic cases use these
  normal services; no authored game graph or media payload is imported. The
  prepared real child/parent probe uses authorized external runtime data.

- Auxiliary view-owner absence: pinned Python 46b22130
  `nodes.SpatialBindingState` and the normal scene declaration distinguish
  known absent ownership from unavailable/present services. Six synthetic
  cases compare that supported path; Rust rejects unsupported ownership at
  state admission, whereas Python rejects it at auxiliary scope dispatch.
  Native 104319 911920/911E50 and AD6500/AD6510 -> AE03F0 share the 978A30
  auxiliary owner lookup and early-return path. The latter E8D976BE branch is
  native-only evidence, with no Python class equivalence claim. Authorized
  immutable-module inspection and codec metadata remain outside the repository.

- Deadeye aim-ray policy: pinned Python e1128509 `combat.ray_capsule`,
  `combat.ray_world`, `combat.capsule_visible` and `collision.RayWorld` check
  six synthetic geometry cases. The Rust bridge now admits its actual emitted
  ray through target capsule contact, rather than the pinned nearest-surface
  selection shortcut. This is an explicit Rust host-policy change, not a
  claim of normal Python Deadeye or native selection equivalence. The prepared
  balcony reproduction uses authorized external collision and body settings.

- Boss view-subject facts reuse the existing typed target-tag snapshot semantics
  and authoritative enemy movement yaw/position already used by combat and
  replication. Prepared 0CED receiver coverage now includes a real external
  Roadhog definition and its large-body tag. Runtime NPC definitions, graph
  payloads and scene geometry remain authorized external inputs.

- Flashbang delivery: authorized 104319 01E9/7 launch metadata and the previously
  inspected 0228 wrapper's PlayScript/schema plus 000F hit entry establish the
  prepared adapter reproduction. The public five-case fixture is a synthetic
  Rust host-policy contract, not an imported game graph or Python-equivalence
  claim. The complete five-Boss room replay needs external authorized data.
- Ability press retention: pinned Python e1128509 `nodes.Ability.can_start`,
  `take_press` and normal `Component.run_frame` establish four shared synthetic
  traces. A rejected press retains its original timestamp and is consumed only
  when start gates pass. That source labels its PC/PS4 start-gate reconstruction
  unverified; no exact native start-gate claim is made here.

- Flashbang arbitration: authorized 224E/122's `STU_3ACE35FB` predicate and
  Reaper 01C9/45's higher-priority entity stack supply the prepared reproduction.
  Seven synthetic cases use normal pinned Python e1128509 stack states, query
  evaluation and message-driven release, and the existing Rust arbitration
  kernel. The real Wraith state is started directly, not through a full ability.
  Codec parent fallback registration160 resolves 0DB9E66B to the PC base State:
  normalized constructor1B726B0, vtable27AC800, nullsub reader1CE5640. This scopes
  the additional plain child to01C9/65; restricted graph/native data remain local.


- Map placement/category investigation: authorized 104319 native 1B48140 and
  original 1B483B6..1B48420, TankLib `teMapChunk.cs`'s CommonStructure and
  teMapPlaceableEntity.Structure, and exact external castle ENTITY chunk
  headers establish 230 matching admission/config results (31 requests for 57).
  The 18391E0 boundary only records inputs. This neither constructs runtime
  components nor establishes dynamic Boss classification; restricted inputs
  and the replay/report remain in external verification storage.


- Flashbang control exclusions: authorized 224E/9's loaded excluded-tag clause
  and the existing eight Flashbang cases in the native-backed tag-clause
  fixture establish the new room-bridge guard. It reuses the production
  exclusion evaluator and existing effective tag services. The original
  DB323B/DB5030/DB4E70 evidence applies only to tag semantics; no actor-category
  initialization or complete native reaction claim follows from this change.


- Integrated Flashbang filter evidence executes the authorized 104319 CF4340
  entry, D0DA20 callback, BCAAE0 mask logic, D0EC70 stage, resolved DB2FD0 tag
  predicate/helpers, null 1B8D970/90BFC0 defaults, absent-world DA8BD0 default
  and F66AF0 result writer. Explicit actor/component resolution, literal tag
  conversion, tag storage and variant ownership remain adapters. The public
  fixture stores 64 synthetic results, not game payloads or actor inventories;
  the immutable image and replay/report remain external verification inputs.


- Nested Flashbang destination research uses original 104319 1B81210 path
  parsing, with the existing 013B indexed-destination probe as a starting point,
  and disassembly of 1BA64F0/F65340/F65410. Twelve synthetic constant-index
  paths reproduce an array element followed by a field identifier. This does
  not establish assignment/growth/COW/watchers. The isolated accepting-input
  Rust trace, diagnostic patch and native inputs remain outside publication.


- Native nested destinations: original 104319 1B81210 parses destination paths;
  1BA64F0 dispatches element/member addressing; F65340/F65410, F681A0,
  9E9600/688740 and F683E0/F68480 establish scalar coercion, integer-zero
  holes, the 1000-index boundary and retained identifier-map bucket growth.
  Twelve path cases, 21 storage cases and two insertion/update sequences use
  synthetic owned variants/stack, bounded bump allocation/free and an inert
  mapped-stack probe. Assignment notification and real actor COW/watchers are
  not inferred from those native helper executions. The Rust VM notification
  checks have their own scope. Original probes/captures remain external.
- Pinned normal Python e1128509 silently skips complex destinations. Frozen
  Python unified ea472943 already has a scoped 224E record_assignment consumer;
  this change is not claimed as normal Python parity or a Python delivery.
- Actual Rust combined reconciliation preserves the shared implementation and
  history submitted in 76d37b23. Its current room/service equivalents were
  reviewed against the Halloween lane before conflict resolution. The added
  self-damage clock fixture is a synthetic host contract derived from the
  authoritative room clock and optional movement-wire representation, not a
  new native capture or original-client timing claim.
- 545AB3B8 fallback: immutable 104319 type descriptor3198560, typeinfo2DEDFE8,
  raw vtable274B010 and captured1735-record list32969D8 establish the missing
  exact binding and registered Action ancestor2DD5430. Original dispatch1B74220,
  resolver1B92DA0 and base action1B6EBA0 execute against synthetic graph/context,
  output/observer adapters and a registry retaining the real captured type keys.
  Exact-binding controls are synthetic. These probes and native bytes remain
  external; the shared fixture does not establish actor classification or client
  reaction behavior. The replay reuses the owned unbound-action probe helper.
- Absent filter targets: original104319 CF4340 and D0DA20 execute with explicit
  missing/null/zero/invalid/known-absent selectors and actor lookup, absent world
  service and variant cleanup adapters. BCAAE0 and D0EC70 are asserted unreachable
  despite a nonzero relationship mask/non-null excluded-tag config. The five
  results establish lookup-before-predicate behavior; host JSON validation is
  checked separately. Native inputs and replay remain outside publication.
- Flashbang State25: the captured default registry resolves0DB9E66B typeinfo
  2DEB120 through parent2DD53F0 to record3294268/constructor1B726B0. Original
  constructor execution on bounded owned storage yields27AC800; original empty
  reader1CE5640 returns without reading a deliberately unavailable payload.
  The separate shared host lifecycle/observer test does not infer health-tag
  multipliers or full original-client reaction behavior. Native bytes and probe
  outputs remain external to publication.

- Flashbang attacker notification: target-build authored 224E/22 sends message
  0622 to local28714 with parameter2B from local1903; 01D8/54 copies it to
  local215 and has no output links. The publication fixture is synthetic host
  transport data; original graph packs remain external. No additional combat
  semantics or client acceptance are inferred.

- Flashbang velocity row: offline original D35310 recognizes raw row vtable
  2749180/typeinfo2DEDC88; D2BAC5..D2BB50 evaluates priority, upper and lower
  configs and calls C32860 with selector1/operation5. Original C31870 replay
  checks horizontal cap0..3 on frame101 with lifetime[101,102), preserving Y.
  The bounded emulator/queue adapters reuse existing lane0079 research helpers;
  native bytes and probe outputs remain external. Synthetic host displacement
  evidence is separate from native NPC motor and original-client acceptance.

- Body yaw/pitch query restoration reuses lane0200's original numeric-owner
  probe (source SHA0976c594d6260f945df1b9402f48edf4ccc4e2c76640ecac266c9a32ece6eb2e).
  CC53F52B resolves descriptor301A360/initializerD0AD70/evaluatorCD4990/getterC62940;
  7BE6D76D resolves301A420/D0AD50/CDBA20/C61F70. The replay declares instance,
  selector, entity and encoded component28 lookup fixtures; arithmetic/getters
  execute original instructions. Publication contains synthetic numeric cases,
  not native bytes. Packed host poses do not prove arbitrary quaternion or
  moving-platform transform parity.

- Captured Flashbang aim restoration reuses lane0200's CAC810 scalar-config and
  composed-update probes. The scoped original mode0/selector1 replay supplies
  36 zero-limit cases with declared unparented D1EEF0 input(entity0, angle0).
  Degree factor260FC98, pitch bounds2613F94/2613F98 and packed factor2614F70
  pin conversion and limits. Linked motor sampling/cache/write evidence and
  host signed16 pose projection remain separate. Native bytes and probe outputs
  stay external; general linked transforms and player owner-full caches are
  not accepted by this change.

- PC CreateEntity boundary: captured registry1735 resolves raw72C9C62F/typeinfo
  2DE0CE8 to exact record2F63F00 priority2. Original dispatcher1B74220 and resolver
  1B92DA0 select the constructor's262F4E0 slot48/1CE5640 in cold/cached cases.
  Graph/cache/admission fixtures are explicit; authored payload90..140(hex) is
  unread. This is PC implementation evidence, not server creation/category
  evidence. Native bytes, research code and replay logs remain external.

- Rust compatibility: PR #33 Windows Rust 1.85 compiler diagnostics and local
  1.85.0 checks identified unsupported let-chain and if-let guard syntax. The
  source-only rewrite preserves branch ordering and existing shared contracts;
  native captures and runtime inputs remain external.

- Local Halloween validation settings follow the user's explicit invulnerable
  Cassidy and high-health-gate acceptance scenarios. They reuse the existing
  gate instance 1 variable 8567 notification/binding path and room damage
  delivery. The new fixture specifies host test policy, not native defaults;
  runtime data, profiles and original-client results remain external.
