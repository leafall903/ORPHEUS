## Change

<!-- Describe the change, the behavior it affects, and the related issue if available. -->

## Verification

<!-- Check the lanes you ran. Record the commands or steps and the outcome, including failures or skips. -->

- [ ] Source-only checks or synthetic tests
- [ ] Offline server or encrypted replay with a prepared runtime
- [ ] Manual acceptance with the original client

<!-- For gameplay changes, give the build, map and mode, hero and action, and expected and observed results. If manual acceptance is pending, say so. Keep protocol observations separate from client HUD, animation, effects, and audio observations. -->

## Review

- [ ] I reviewed the diff and kept it scoped to this change.
- [ ] For behavior changes, I included a [shared contract](https://github.com/leafall903/ORPHEUS/blob/rust/docs/BEHAVIOR_CONTRACTS.md) or documented the reproduction, evidence gap and follow-up, with client acceptance status.
- [ ] For Rust server changes, I followed the [architecture rules](https://github.com/leafall903/ORPHEUS/blob/rust/docs/NATIVE_SERVER_ARCHITECTURE.md#rust-contribution-rules) and documented any behavior changes with reference evidence.
- [ ] I updated `CHANGELOG.md` and the relevant verification record when applicable.
- [ ] I removed credentials, keys, account identifiers, IP addresses, and personal paths from any shared evidence. Game files, runtime data, and complete captures stay in local storage.
