# Rust migration

The Halloween integration also incorporates public Rust `f763beb9` (room
fault isolation). Encounter creation/retirement, foreign-variable snapshots,
healing, voice/sighting delivery and result-screen exit now use room-owned
members and guarded simulation phases. Combat queries and Deadeye target
preparation preserve unavailable/error results instead of treating a broken
registry as an empty scene. Existing source-only contracts and client gates
remain separate; see the current integration record in RUST_PARITY.md.

The combined tree integrates upstream Rust `13acf15`, including `cfea41b`
(PR #17), the Rust-only source tree and contribution rules. The tagged
Soldier session regression covers 900 frames of idle, fire, reload, idle-fidget
and Sprint, including both snapshot views and owner HUD transitions. Unbound
message maps are omitted from wire projections while remaining available to
server lookups. Owner frames omit switches polled on untrusted native answers
and retain their live subgraphs. Missing animation durations are accepted only
for the verified unconsumed idle-animation form. The combined encrypted rifle,
Helix and Biotic Field coverage below remains required alongside this session.
Upstream's reported client observations are separate evidence; this merge does
not establish new original-client acceptance for the combined server.

The migration target is a Rust implementation of ORPHEUS for retail build 1.74.0.0.104319. Native development takes place on the `rust` branch. The community-maintained Python server on `main` is the behavioral reference and holds the Python tests and data preparation and extraction tools. Use a separate `main` checkout for comparisons. The `ow174/`, `tests/` and `tools/` paths cited in research and format records and in Rust provenance comments refer to the pinned Python reference `74f36cd` (see the [parity checklist](RUST_PARITY.md)); read them with `git show 74f36cd:<path>` from a `main` checkout. Current `main` has since reorganized its layout. Track replacement workflows in the [parity checklist](RUST_PARITY.md) and broader gameplay fidelity in the [restoration roadmap](ROADMAP.md). The [native server architecture](NATIVE_SERVER_ARCHITECTURE.md) sets the room ownership, script host and inspection boundaries.

| Stage | Scope | Exit criteria |
| --- | --- | --- |
| Transport and serialization | JAM lobby handshake and framing, lobby and game message codecs, game UDP encryption and ACKs, bit streams and command records | Rust reproduces the reference bytes and fragmented streams, rejects malformed records, and handles loss, duplicates and rollover. |
| Login and lobby | Battle.net login, accounts, profiles, lobby, chat, parties, catalog, queues and match handoff | Match the reference service requests and replies. Load existing profile and external runtime formats. Verify a lobby client can acquire a valid match seat. |
| Match simulation | Tick scheduling, collision, movement, statescript, combat, bots, heroes, maps and modes | Match the registered state and packet order from encrypted Practice Range and two-peer replays. Check sustained-fire tick time and snapshot gaps with the same inputs. |
| Server tools and dashboard | Data preparation and extraction tools, replay and diagnostics, administration API and browser UI | Reproduce output formats and acceptance checks with Rust tooling. Expose bounded simulation state and collision views through the Rust dashboard backend. |
| Windows deployment | Launcher, retail relay and server-only distribution | Build the Windows tools and relay, and boot the audited checkout from the separate runtime. Verify supported connection paths with the original client. |
| Cutover | Switch the normal entry points to Rust and retire the Python server | Demonstrate broader verified coverage in Rust, verify the supported replacement workflows with the original client, and complete a server-only smoke test of the audited public checkout. |

The experimental native server joins the local Battle.net listener, encrypted lobby and UDP match worker. Its login subset includes presence, progression, the hero collection, the dated Arcade roster, priority passes, career mode rules, celebration content and party and ranked messages. A headless test uses an existing profile over the encrypted lobby socket, requests a solo Practice Range seat and uses the returned keys to authenticate UDP and receive the map load. Further tests with the base graph bundle cover ownership, baseline retries, controller and mode delivery, bot movement, hero selection and replacement under packet loss, and ordinary command acknowledgement.

Retail gallery equip is a post-login lobby request, including while the hero-selection screen is open. The native lobby saves the loadout and returns the catalog confirmation. The match worker updates the reserved seat's appearance and uses the same body replacement path when the active hero's skin changes. Encrypted loopback tests check the saved loadout, confirmation and replacement skin record. Original-client verification of this repair is pending.

The worker simulates ordinary player movement for the base Practice Range on its prepared collision. Its capsule keeps ordered per-body contacts and applies the same pose and wire quantization as the reference. Input work is round-robin, with up to 16 poses or events per peer and 64 per event-loop turn. A command advances acknowledgement after its last reconstructed pose. A 96-frame lead limit and a bounded input queue protect the worker during bursts. A separate tested correction history selects the eligible pose and retains the commands needed for replay. Sending owner corrections still requires authored hero movement parity. Measure worker tick tails and snapshot gaps before cutover.

The Rust statescript components cover expression evaluation, supported startup and same-time actions, scoped message entries, static timers, logical button edges and buffering, variable stacks, a subset of ability activation and owner and observer full and cumulative state frames. The isolated Soldier: 76 Sprint graph matches a Python reference sequence for activation, cancellation, HUD state and its movement modifier. Synthetic body tests exercise replication and correction delivery when an eligible component exists. The tagged combined pack now enables the authored Soldier owner stream and its rifle, Helix and Biotic Field paths described below. The enriched timer fixture verifies a Practice Range bot's firing eligibility in isolation; full live NPC body execution, other hero graphs and original-client acceptance remain open.

The prepared body factory builds the authored player roots and weapon slots, checks the initial snapshot once per base Practice Range runtime, and creates a fresh component for each supported body. Encrypted loopback tests attach both a fixture component and the authored Soldier body with its target tags. Lost owner frames recover without resetting their baseline. The authored test advances the startup timer before firing the rifle, then checks the hit notice and the training bot's updated health. Owner snapshots encode supported variable bindings and selected animation, effect and message states. The worker spreads long gaps without a pose across its normal input budget, releases old buttons, and advances the retained script timers before acknowledging the next movement command.

The authored Soldier rifle regression verifies held fire, release and reload. Owner frames introduce each volley with the starting ammo and resume live ammo updates after acknowledgement. The native worker passes posed direct shots through retained target poses and the map and capsule intersections. In a separate encrypted test, an authored fixture weapon damages a bot, retries a dropped hit and updates its health, then retires the old bot and creates a new one. This path serves direct fire against Practice Range bots with both the fixture and the authored Soldier body. Full player and bot scripts and original-client presentation remain pending.

The authored Helix graph matches the reference for activation, six-second cooldown and the next permitted shot. The native worker uses the same shot stream for hitscan and predicted straight projectiles. It advertises authored leases in the owner body create, sweeps current opposing bot poses and map collision, and applies direct and splash damage through the shared health path. Owner confirmation follows the exact datagram carrying the matching volley state, with the existing fallback when no report was sent. Explosion release waits for the projectile create acknowledgement. Encrypted loopback covers both the fixture and the authored Soldier body with Helix ballistics. The authored path verifies 40 direct damage, 80 inner splash, dropped-create recovery and release. Splash casts to the nearest capsule surface and respects static cover. Observer launch, scripted flight and original-client projectile presentation remain open.

The authored Biotic Field cast confirms the predicted canister and schedules the server healing area at the caster's launch pose, following the Python reconstruction. The solo Practice Range worker applies the elapsed healing ticks to the caster and live friendly training bots within capsule range. Tests cover the authored graph, field clock, bot health updates and encrypted canister retirement. The owning peer receives the attached field after canister acknowledgement. The prepared encrypted replay verifies the active and fade frames against Python and field retirement before canister retirement. Original-client rendering, multiplayer recipients and healing score attribution remain open.

The stock Soldier preflight reports unsupported startup operations. With authored target tags it now generates the startup tree, including the typed communication message table, and attaches the owner VM. An earlier offline comparison matched the 3,261-bit owner and 3,067-bit observer frames byte for byte from the same reference snapshot. The randomized timer uses a deterministic local sequence whose original-client parity is pending.

Rust carries the authenticated controller and newly allocated body into each human owner VM. The controller, current possessor and possessed-body queries resolve that pair, and the tests cover its replacement and the authored Soldier startup and HUD lookups. Foreign and AI relationships still require their own authoritative lifetimes and validation.

The delayed conditional state uses the shared VM clock and can cancel either branch's pending transition. Its branch flags are available to owners and observers, while pending timers are owner-only. The tips state uses the base graph lifecycle and an empty payload. The native tips registry and presentation remain open.

The common body graph registers its authored base health, armour and shield pools in the shared `Vitals`. The script queries and component 51 use the actual current values, including after damage to mixed pools. Owner health updates wait for the body creation acknowledgement and retain explicit slot clears when a newer update supersedes an older one. The remaining amount is available before the end plug. Live output timing and pool IDs are derived from the authored graphs and existing protocol fixtures. Native resize rules, named pools, additional pool flags, live player damage and original-client slot clear behavior remain open.

The base graph bundle can include authored mode values without adding bot graphs. Supply the target build's `.0C5` XML directory as `--mode-xml` to [the graph extractor](https://github.com/leafall903/ORPHEUS/blob/main/tools/extract_game_graphs.py) in a `main` checkout, write its output outside the repository, then pass that output to `prepare_server_runtime --game-graphs` when assembling the external bundle. Mode values and graph data stay local.

Pass the target build's exported `.003` XML directory to `prepare_server_runtime --target-tags-xml` to import target tags and component 92 presence. The command enriches body, entity and Junkenstein entity records in the external graph bundle and hashes the result in the runtime manifest. It preserves existing fields when an export is missing. Older bundles can still load, with omitted metadata treated as unknown. The owner VM combines the direct body definition with single literal tag grants and suppressions from active graph states. Those effects are scoped to the body and are released on state end or instance teardown. The fresh body also uses the same filter mask for component 26 creation and script queries. Instance overrides, grants that start during a suppression, foreign entities and further flag transitions still need verification.

The owner VM reads the initial on-fire flag through the current possessor and the controller's component 64 baseline. The fire scoring and decay rules remain open. An authenticated human body has an explicitly absent AI control entity. For server-local message tables, the shared interpreter preserves the authored typed keys and values and uses their order for lookup. Network serialization of maps remains unverified.

The operand-free instance exit uses the shared event queue and removes pending work and descendants when it runs. It can complete the still-linked parent SubScript through the normal finish event. Dynamic instance selectors, named exits and teardown order for active descendants still need 104319 verification. The verified direct-owner AI state finishes in the same clock when the body has no AI controller. The fieldless resource query returns a typed zero asset.

The owner movement sampler reads the same mover pose used by the match worker. It applies changed Boolean outputs before its startup subgraph and during ticks, and uses the existing owner flags codec. The preflight and live body use the same spawn mover. A freshly constructed body starts with the embedded standing record, following the later client's constructor evidence. Its exact 104319 startup context and scripted movement modes still need verification. The crouch effect at `01C7`, node `74`, follows the Python reference's server-side action flow. Its original-client presentation still needs verification.

The native server serves the solo Practice Range and admits solo Junkenstein Arcade queues on loopback. Junkenstein reads the encounter and defender controller bindings from the Revenge and Endless mode exports and validates their shared and defender body graphs before preparing the entrance and gate. Arcade admission starts the two authored encounter roots using the admitted defender roster, selected difficulty and challenge flags, and the validated castle gate. The prepared worker delivers the gate and entrance frame over encrypted UDP. The encounter clock starts at world entry. A selected hero feeds the authored ready receiver and starts the encounter countdown once, even if the hero changes during the countdown. The worker sends the two authored countdown states and their current value as acknowledged mode deltas. The local inspector reports elapsed encounter time and countdown activation. The placed gate runs its own graph, and the Normal encounter reaches the authored first Zomnic spawner with its roster, placement tag and search radius. The first spatial search is bounded across room ticks and returns collision-checked candidates. The Normal fixture compares 172 candidates with the Python reference, and retiring its instance cancels later completion. With the explicit external NPC catalog, the first Zomnic body and controller initialize and publish at the selected candidate, with their two component-88 behavior requests verified. Sustained navigation, gate attacks and damage, broader enemy creation, encounter effects, multiplayer, and original-client acceptance remain open. Build with `--features inspector` and use `--debug-port 3726` for the optional local read-only simulation inspector. It projects active room state and sampled collision geometry. Other matchmaking modes, social actions and administration features remain pending.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\authorized-base-runtime'
cargo run --locked --bin orpheus_server -- --data-dir 'C:\path\to\local-state'
```

To prepare one pack for both entry paths, use a source pack with the Practice
Range and Junkenstein collision caches and the authored
`data/junkenstein/junkenstein-map-world.json` placement export. Supply a
graph bundle with both modes and the authored body target tags.

```powershell
cargo run --locked --bin prepare_server_runtime -- --source 'C:\path\to\prepared-source' --destination 'C:\path\to\unified-runtime' --map 0688 --map 06C9 --game-graphs 'C:\path\to\tagged-graphs.json.gz'
```

Use a separate state directory or the existing server directory to reuse its profiles. The native server selects a saved profile from `default_account.txt`, the first saved profile, or the local template, in that order. Pass `--account` to select one explicitly. Run only one server at a time against the same state directory. The native ports default to TCP 3724, 1119 and 21119, and UDP 3730. All are configurable using `--help`. Press Enter to stop the listeners.

Public Rust tests use synthetic protocol vectors and local loopback connections. They need neither a client nor prepared deployment data. The Rust `prepare_server_runtime` command assembles an external runtime pack from an authorized prepared checkout or an existing runtime pack. The Practice Range pack was checked against the Python tool by comparing the manifest and every copied file.

```powershell
cargo test --locked --workspace --all-targets
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets -- -D warnings
```

Keep data formats and existing verification scenarios stable during replacement. Reuse shared codecs and keep each subsystem as small as the observed behavior allows. Use the same clock, input, collision data and packet-loss schedule for both implementations. Check bytes where the format is fixed, and compare decoded event order and registered state when runs contain nondeterministic values. Measure tick latency and snapshot gaps before claiming a performance improvement. The target client remains the final check for presentation and feel.

Record the source and target build of external research in [CREDITS.md](../CREDITS.md) and the relevant change. Treat Beta hit-confirm and voice behavior as leads to verify against the retail graph and message schemas. Keep client executables, prepared data, private archives, logs, and compiled binaries outside published source.
