# Credits & Contributors

ORPHEUS builds on community Overwatch 1.74 research, preservation tooling, and open-source Rust/Python libraries. Upstream copyright notices remain intact in [LICENSE](LICENSE).

## Contributors

- **[leafall903](https://github.com/leafall903)** — maintainer, native Rust server architecture/port, Python server & infrastructure.
- **[zichao-zhou](https://github.com/zichao-zhou)** — Rialto PvE and McCree combat foundation, Archives/Junkenstein entrance work, and scoped Python hero contributions ([PR #16](https://github.com/leafall903/ORPHEUS/pull/16)).
- **[memcpy](https://github.com/memorycpy)** — Reinhardt and Zarya hero mechanics (Barrier Field, Charge, Fire Strike, Earthshatter ground-wave, Particle Cannon, Particle/Projected Barriers, Graviton Surge), StateScript direction-limit and ground-wave primitives, General and Match lobby text chat, and owner hero reselection retry.
- Upstream lobby, runtime, and research contributors preserved in Git history and commit trailers (`Assisted-by:`).

## Upstream Foundations & Protocol Research

- **[Boi-027/Overwatch-1-v1.74-Lobby-Research](https://github.com/Boi-027/Overwatch-1-v1.74-Lobby-Research)** (**Boi-027** & **Arlecchino**) — original 1.74 lobby server implementation, handshake, JAM framing, and protocol documentation.
- **[LizRollin's 1.74 runtime branch](https://github.com/LizRollin/overwatch-1.74-lobby-server/tree/9f8f3bcb5eccab6fb7738bee3c2371fcf0623f29)** — inherited Python expression evaluator, StateScript codecs, driver, float32 mover, collision solver, and combat baseline used as the Rust port reference.
- **[AipNooBest's research](https://github.com/AipNooBest/overwatch-1.74-lobby-server/tree/f8426859c3195691d379f2fb291a475dfa40139e)** — body initialization and HUD/state payload comparisons.
- **TripleFault / Gamayun** — same-build (`104319`) comparisons for StateScript VM natives, health pools, delayed switches, encrypted combat scenarios, and worker timing.
- **AyakaPS community** (**Xref**, **Logo2K**, **Zagrion**, **Sidiusz**) & **Blizless** — captured reference traffic, lobby/game server protocol findings ([1.74 Match Protocol](https://rentry.co/z42v8ykq), [Lobby Research](https://rentry.co/3bswrgmw#overwatch-174-lobby-protocol-channel-setup-login-and-loot-boxes), [Lobby Map List](https://rentry.co/wcwyv3r8)), and state-blob findings.
- **Community TLS Relay Authors** — foundation for the local retail client connection relay.

## Tooling & Ecosystem

- **[overtools / OWLib](https://github.com/overtools/OWLib)** (`overwatch/1.74` DataTool/TankLib) & **[TACTLib](https://github.com/overtools/TACTLib)** — CASC/TACT extraction and catalog/voice/collision tooling.
- The runtime builder reuses ORPHEUS's Python catalog and collision extractors. Its STU exporter follows OWLib's DataTool XML output and adds a typed UUID serializer. OWLib is copyright © 2016–2022 overtools. [Pinned ORPHEUS extractors](https://github.com/leafall903/ORPHEUS/tree/cedec602c41ee14f158ea9fdd9f16d60e4f51f3d/tools) · [DataTool XML exporter](https://github.com/overtools/OWLib/blob/7afe5f52706e7002ed8c2717299cd60846f3fa3a/DataTool/ToolLogic/Extract/Debug/ExtractSTUType.cs) · [Upstream MIT license](https://github.com/overtools/OWLib/blob/7afe5f52706e7002ed8c2717299cd60846f3fa3a/LICENSE)
- **[Plasmawatch](https://github.com/plasmawatch/login-server)** — Battle.net login-server emulation reference.
- **[Prometheus](https://github.com/saturn-xvi/prometheus/tree/ad1fea7e6c04c9959c7115cb8ecd9c79c93c1ccb)** — early/beta Overwatch research compared for controller/model/replication structure (remains separate with its own license).
- **Claudewatch contributors** — Beta 1.0.0.3 leads for hit-confirm cues and entity/announcer voice routing (cross-checked against 104319 data).

## Rust Runtime Dependencies

- **[Bevy ECS](https://github.com/bevyengine/bevy/tree/v0.16.1/crates/bevy_ecs)** (`0.16.1`, `std` only) — room-local ECS storage for bots and projectiles.
- **[mlua](https://github.com/mlua-rs/mlua/tree/v0.11.6)** (`0.11.6`) & **[Lua 5.4](https://www.lua.org/)** — sandboxed script host for the `lua-mods` Workshop/Mod VM and optional `lua-debug` AI patrol override.
- **[RustCrypto](https://github.com/RustCrypto)** (`aes-gcm`, `hmac`, `sha2`), **[getrandom](https://github.com/rust-random/getrandom)**, **[Prost](https://github.com/tokio-rs/prost)** / **[prost-reflect](https://github.com/andrewhickman/prost-reflect)**, **[Tungstenite](https://github.com/snapview/tungstenite-rs)**, **[lzma-rust2](https://github.com/hasenbanck/lzma-rust2)**, **[Mio](https://github.com/tokio-rs/mio)**, **[flate2](https://docs.rs/flate2/)**, and **[roxmltree](https://github.com/RazrFalcon/roxmltree)** — cryptography, protobuf reflection, WebSocket/UDP I/O, compression, and DragonML parsing.
- **SplitMix64** — deterministic PRNG algorithm from [Sebastiano Vigna](https://prng.di.unimi.it/splitmix.c) for local StateScript random expressions.

If a credit is missing or needs correction, please open an issue.
