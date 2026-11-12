# Python and Rust parity

The default `main` branch is the community-maintained Python implementation. The `rust` branch is the native developer preview. Use the Python implementation and target-build observations as evidence for behavior. Compare supported workflows and their verification level independently of the branches' commit counts.

The initial comparison uses Python `74f36cd` and Rust `be0d41d`. Update this checklist alongside new evidence and record the commit or fixture that supports each change.

| Cutover workflow | Python reference | Rust evidence | Required to close |
| --- | --- | --- | --- |
| Authorized local startup and authentication | Windows launcher and local server use a prepared 104319 runtime. | The native CLI starts on loopback with a prepared base pack. Encrypted login, handoff and map loading pass headless checks. | Establish the supported original-client connection procedure, retain the runtime preflight and verify the tested release checkout with 104319. |
| Stock Soldier: 76 spawn, switch and rejoin | The authored body path is available with prepared data. Client playtests have reported incomplete presentation and skills. | Headless selection, skin replacement and fixture body delivery pass. The stock owner body hits an unsupported effect action at startup. | Attach the stock weapon and script in the target client and complete the repeated switch and rejoin checks. |
| Soldier weapon, Sprint, Helix and Biotic Field | Reference implementations and selected automated and playtest evidence. | Isolated authored rifle, reload, Sprint and Helix timelines. Encrypted combat uses a fixture weapon and predicted straight projectile. | Verify stock input, HUD, ability presentation, target damage and healing through the original client. |
| Bots, movement and collision | Reference roster, patrol, damage and respawn are available for comparison. | Room ECS, Lua patrol and encrypted fixture damage and respawn pass. Prepared movement and patrol regressions use real target geometry. | Record the upstairs route report, shared character collision and original-client movement, damage and respawn. |
| HUD, feedback and ultimate charge | Selected wire fixtures and an ultimate-charge model using community estimates. | Selected combat notification and controller records have fixtures. Native parity for ultimate charge, broader HUD and audio remains open. | Confirm the target rules and verify health, eliminations, kill feed, critical-hit feedback, audio and ultimate progression. |
| Multi-peer match and performance | Use prepared scenarios and documented reference behavior. | Two independent solo rooms can run on one worker in an encrypted workload. Timing and world-bearing packet gaps are observable. | Verify same-room agreement on movement, damage, ownership and respawn. Compare release builds on the same workload. |

The [migration guide](RUST_MIGRATION.md) defines the acceptance gate. The [restoration roadmap](ROADMAP.md) covers the broader build. Record each reference at a specific commit, fixture or original-client build. An isolated graph, fixture match and original-client pass are separate evidence.

## Reference and cutover policy

The [shared World query contract](BEHAVIOR_CONTRACTS.md#strict-world-queries) pins Python strict-host semantics at `50ba408` for known positions, known unset variables and unavailable queries. It compares typed query results and diagnostic categories through production expression paths. Python stops a strict component on an unavailable query; Rust records an expression issue and rejects the affected operation. This fixture supplies no evidence of equivalent fault recovery or original-client acceptance.

- Use the Python code on `main`, from a separate checkout, as the behavioral reference. Keep Python `main` and Rust `rust` contributions on their respective branches.
- Evaluate new Python behavior against the target build and the cutover checklist. Record the source and expected result before deciding whether to port it. Community estimates remain provisional.
- Pin the Python comparison commits before starting cutover acceptance. During the cutover window, keep the pinned reference limited to fixes that unblock verification. Any change to community development policy on `main` belongs in a separate maintainer decision.
- Keep game data, private captures, resource keys and signing material local. Public test fixtures must be safe to publish.
