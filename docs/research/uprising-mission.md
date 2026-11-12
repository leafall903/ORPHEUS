# Uprising: the mission's phases

Build 1.74.0.0.104319. Seen live on 2026-11-05 and 2026-11-06 in a separate offline service, and
on 2026-11-12 against ORPHEUS; see the [index](README.md) for the evidence labels.

## Summary

Uprising's objective box is drawn by a director graph, one per phase, that the server puts on the
game mode entity at run time. `0D4A` draws "DEFEND THE PAYLOAD" with a countdown, `0D4B` draws
"ESCORT THE PAYLOAD" with the progress bar. The countdown is the mode entity's `#1303`, the
variable the hero select counts down on. No entity definition names these graphs, so nothing but
the server can create them.

ORPHEUS now shows these two phases by hand. It plays no mission behind them.

## The mode's graphs

Read from data. Mode `000F.0C5` (and `001A.0C5`, All Heroes) names the mode script `0D28`, a
team script `0D29` and a player controller `0D2A`. `0D28` has 78 states. Its state 29 is the
loading marker `STU_9FC136D7` that the loading screen waits for, and it reaches the common HUD
graph `0CB6` from state 12 and the objective widget `0E95` from state 31, both SubScripts.

`0CB6` builds the HUD's frame client-side: its states are client-only and they create `106B`,
whose presenter publishes the variable the objective widgets hang under. Without `0CB6` on the mode
entity nothing of the objective HUD draws.

## The directors

Read from data, and seen live in the other service by adding and removing instances in the mode
entity's frame:

| Graph | Size | What the client draws with it on the mode entity |
|---|---|---|
| `0D4A` | 139 states, 64 Waits, 49 SubScripts (45 to `0D1C`) | "DEFEND THE PAYLOAD" with "REMAINING" under a clock |
| `0D4B` | 39 states | "ESCORT THE PAYLOAD" with a progress bar, the payload marker and a checkpoint tick |
| `0FEF` | 9 states, one widget | Nothing new when it replaced `0D4B`. Its role is open. |

- `0D4A`'s plain entry starts two client-only presenters at once: the clock (widget `02CB`,
  bound to `#1303`) and the title (widget `02CF`). So the box appears as soon as the instance
  exists. No remote state has to be on.
- Its remote slot 0 posts `074E.025`, which opens the "PAYLOAD HEALTH" panel of `0E95`. That
  panel reads the health of the entity in `#2190`. With the slot off there is no panel.
- The clock shows minutes and seconds, and tenths of a second under ten seconds.
- Replacing `0D4A` by `0D4B` under a new instance index makes the client destroy the first and
  draw the second: a full frame destroys the network instances it does not list.

Seen in a recording of a retail match (Hard, four players): the phases run hack the terminals,
rendezvous at the payload, defend the payload for 4:00, escort the payload, kill the OR14-NS
units within 6:00. The defend clock starts at 4:00.

Not decoded: the hack, rendezvous and kill phases and their widgets, what the real director feeds
`#2190`, what moves the escort bar (`0D4B`'s instance variables 624 and 6627 are candidates),
the "OBJECTIVE COMPLETE" banner, and the waves `0D4A` spawns through `0D1C`.

## The map

Read from data. The mission's 89 placeables on King's Row (Uprising) are 68 markers of entity
`16DC` along the route, 19 of `0176`, one `07A4` and one `1B3E`. None names a graph and none is
a spawn point of the kind the other maps have: the map's entry in the extracted map data has no
spawns. Where the players start is not decoded.

## Compared with ORPHEUS

ORPHEUS already sends `0D28` with its loading marker on beside `0CB6`, which is what lets the
client enter the map. It did not send a director, so the HUD had no objective.

It now sends the phase's director as one more root instance of the mode entity, with `#1303`:

- `0D4A` from the moment the heroes are assembled, counting 240 s down, sent once a second.
- `0D4B` when the clock reaches zero, under the next instance index, sent once.

This is a presentation driven by hand. The directors' own server states never run: no waves
spawn, the payload does not exist, the escort bar stays empty and the mission never ends. The
"PAYLOAD HEALTH" panel is left closed, since there is no payload to read.

ORPHEUS cannot host the map from stock data, because the map has no spawn point. See below for
how it was run.

## Seen live against ORPHEUS

2026-11-12, one player, Arcade > Mission Archives > Uprising (All Heroes) > Normal, with the
Archives event on in a throwaway profile. The map data was a scratch copy with one start point
added by hand to King's Row (Uprising): King's Row's first attacker spawn, the point the other
service used. The repository and the local pack were not changed for it.

- The map loaded and the hero select opened with "ASSEMBLE YOUR TEAM" counting down.
- After the countdown the HUD showed "DEFEND THE PAYLOAD" with "3:51 REMAINING" nine seconds in.
- In an earlier run the same box read "5.0" and then "0.9", and the next capture showed "ESCORT
  THE PAYLOAD" with an empty progress bar and the payload marker.
- That earlier run started the clock at the first spawn, under the hero select, so the box opened
  at 3:22. The clock now starts when the heroes are assembled.

Not checked: a second player, a player who joins during the escort phase, and the Story mode
`000F`.
