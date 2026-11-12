# PVE entrance acceptance

> This is the original candidate's verification record imported with PR #16. See the
> [changelog](../CHANGELOG.md) for checks run after integration into `leafall`.

Original retail build **1.74.0.0.104319** passed native menu selection, scene load,
hero selection where applicable, actual-world/HUD entry and settled-menu return
for all 47 Archives/Junkenstein cards on 2026-11-07.

| Entrance group | Accepted cards |
| --- | ---: |
| Uprising, Retribution and Storm Rising story, four difficulties each | 12 |
| The same three missions in all-heroes mode, four difficulties each | 12 |
| Archives challenge missions | 9 |
| Junkenstein's Revenge, four difficulties | 4 |
| Junkenstein Endless, four difficulties | 4 |
| Junkenstein challenges | 6 |
| **Activity total** | **47 / 47** |

Tutorial and Practice Range also passed entry and return. Tutorial uses its
authored solo red team and fixed Soldier: 76 controller, with controller creation
acknowledged before body creation and possession. Five consecutive Tutorial
cycles, a subsequent Practice Range cycle and normal client closure passed in one
process. A later client completed 39 activity cards consecutively and closed normally.

The menu routes are Play > Arcade > Mission Archives, Challenge Missions or
Junkenstein Modes, and Training > Tutorial or Practice Range. Local profiles and
the new-account template have Archives and Halloween enabled together. Queue
minimum players remains one for the existing local single-player setup.

The accepted external map and graph inputs were promoted to the normal runtime
with SHA-256 verification and pre-deployment backups. The normal local service
was restarted on its existing ports. The usual launcher logged in and passed
Practice Range, Tutorial and Retribution All Heroes Normal entry/return smoke
checks in one client, which then closed normally. The service remains available.

Implementation validation passed 141 focused unit tests, Ruff, Git diff checks
and independent decoding of all 47 card/map controller and loading frames. The
local verification directory retains `pve-native-entrances.json`,
`pve-training-native.json`, `pve-entrance-frames.json` and
`pve-normal-deployment.json`, with per-card matches, times, source hashes and logs.
Those inputs, profiles, logs and game assets are excluded from publication.

This is entrance acceptance. Mission AI, objectives, challenge effects, Tutorial
guidance and complete gameplay are not restored by this milestone. Uprising
story showed its native restricted hero picker; Retribution and Storm Rising
story showed all heroes, so authored story heroes were used and their
restrictions remain unverified. Earlier intermittent cleanup crashes did not
recur in the final sessions; indefinite stability and their complete causes are
not established.

GitHub synchronization could not run because the machine's required
`work/sync_orpheus_publication.py` helper is absent. Local tested commits remain
on `main`; no alternative push or history rewrite was performed.
