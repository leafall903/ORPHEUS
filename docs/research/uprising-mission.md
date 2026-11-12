# Uprising: the mission's phases

Build 1.74.0.0.104319. Seen live on 2026-11-05 and 2026-11-06 in a separate offline service, and
on 2026-11-12 against ORPHEUS; see the [index](README.md) for the evidence labels.

## Summary

Uprising's objective box is drawn by a director graph, one per phase, that the server puts on the
game mode entity at run time. `0D4A` draws "DEFEND THE PAYLOAD" with a countdown, `0D4B` draws
"ESCORT THE PAYLOAD" with the progress bar. The countdown is the mode entity's `#1303`, the
variable the hero select counts down on. The mode script reaches the directors through a
sequencer graph, `0D47`, as SubScripts the server runs.

ORPHEUS now shows these two phases by hand. It plays no mission behind them.

The players start at four plain markers in an Underground tunnel, which the mode script names by a
target tag. The map has no spawn point entities, so the map extractor found no start until it
learned that rule.

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

Read from data: the directors are not attached from outside. `0D28` state 10 (node 27) is a
SubScript to the sequencer `0D47`, and `0D47` states 5 to 9 are SubScripts to `0D48`, `0D49`,
`0D4A`, `0D4B` and `0E05`, chosen by a switch on its `#59`. An earlier version of this note said
only the server could create the directors because no entity definition names them; the mode
script names them itself. `0FEF` is not among the five. By the order of the phases in the
recording below, `0D48` would be the hack phase, `0D49` the rendezvous and `0E05` the last phase;
that pairing is not checked.

Seen in a recording of a retail match (Hard, four players): the phases run hack the terminals,
rendezvous at the payload, defend the payload for 4:00, escort the payload, kill the OR14-NS
units within 6:00. The defend clock starts at 4:00.

Not decoded: the hack, rendezvous and kill phases and their widgets, what the real director feeds
`#2190`, what moves the escort bar (`0D4B`'s instance variables 624 and 6627 are candidates),
the "OBJECTIVE COMPLETE" banner, and what sets `0D28`'s own `#59` so that it reaches the
sequencer.

### The waves

Read from data, one pass, not played and not checked against the client. Every director spawns
its enemies through one shared graph, `0D1C`: its node 13 is a `STUStatescriptActionCreateEntity`
that makes a hero from the caller's variables (hero `#8398`, position `#8370`, controller `#9318`,
skin `#8172`). The directors call it as a SubScript once per group, with the group's size, pacing
and place. The defend director `0D4A` does so across four states of 60 s each. Which units come
when, and at which markers, is read but not verified, and is not recorded here yet.

## The map

Read from data. The mission's 89 placeables on King's Row (Uprising) are 68 markers of entity
`16DC` along the route, 19 of `0176`, one `07A4` and one `1B3E`. The map has no base map and one
entity chunk of 1145 records over 88 definitions, and none of those definitions has the spawn
component `STU_737C5C41` that the other maps' spawn points have.

### Where the players start

The players start at four `0176` markers that carry target tag `0x310` in their instance data:
records 1125 to 1128, placeables 1420 to 1423, in a row about 1.3 m apart around
(-65.4, -4.4, -91.6). No other placeable of the map carries a team tag, and no placed definition
carries `0x310` itself.

The mode script names them. `0D28` node 23 writes `0x310` into the mode entity's var 8364 at
index 0, and its state 8 then runs `0C8F`, the spawn graph the PvP modes share. `0C8F` gathers the
entities carrying the tag in 8364 index 0 for team 0 and index 1 for team 1. On game message
`0685.025` it picks one for the requester's team and answers with `0687.025`, whose parameter
`03A1` is the chosen marker. Index 1 is never set, so team 1 has no list. The mode's record gives
only team 0 a controller and a body script, and the queue card names one team of four.

Junkenstein's Revenge and Retribution are built the same way: `0C8C` and `100B` write `0x310`
into var 8364 index 0, and their maps hold six and four `0176` markers with that tag. Storm
Rising is not: its mode script never names var 8364, and what its four specially tagged spawn
rows are for is not decoded.

The directors name two more `0176` markers: tag `0x339` (record 967), which `0D4A` writes into
var 8931, and tag `0x337` (record 423), which `0D4B` writes there. What var 8931 does is not
decoded.

Context, not a decoded rule: `0C8F` asks for entities by tag alone, and ORPHEUS's port of it
(`StaticTagTarget`, through `static_tag_targets` in `script/scene.py`) does the same. Every PvP
map also has 42 to 134 team-tagged entities on definitions the server does not create. A server
holds only the placeables it creates, so the extractor's rule stays inside that set.

Not decoded: which player gets which marker (ORPHEUS's port of the picker `STU_5E805B5A` takes a
random one not used in the last 3 s), what receives `0687.025` for Uprising (its controller
`0D2A`, `1194` and `0D29` have no entry for it, unlike PvP's `0C90`), and whether the native
picker also wants the spawn component.

The facing is not settled either. By their rotation the four markers stand side by side and face
across their row, all four within ten degrees of one another. The server gives each body its
marker's yaw and keeps it through the hero pick (checked on the server). In the client the view
at the start faced a wall, with the rails a quarter turn away, so the client's camera may not be
taking the spawn's yaw in this flow. That was not checked.

## Compared with ORPHEUS

ORPHEUS already sends `0D28` with its loading marker on beside `0CB6`, which is what lets the
client enter the map. It did not send a director, so the HUD had no objective.

It now sends the phase's director as one more root instance of the mode entity, with `#1303`.
Natively the director is a child of the sequencer's state; the root instance is what was seen to
draw:

- `0D4A` from the moment the heroes are assembled, counting 240 s down, sent once a second.
- `0D4B` when the clock reaches zero, under the next instance index, sent once.

This is a presentation driven by hand. The directors' own server states never run: no waves
spawn, the payload does not exist, the escort bar stays empty and the mission never ends. The
"PAYLOAD HEALTH" panel is left closed, since there is no payload to read.

The map extractor (`tools/extract_game_maps.py`) now lists the four `0x310` markers as team 0
spawn rows: an entity the server creates whose only team tag is `0x310`. Over all 95 maps the
rule adds 14 rows: these four, Retribution's four and Junkenstein's six. It leaves out six
Junkenstein markers tagged `0x30D`, which `0C8C` never writes, and one marker of a menu backdrop
map that is tagged for both teams. The players take the markers in the server's usual turn order.
Map data extracted before this change still has no start for the map.

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

Later the same day, the same card with the decoded start. The four rows were built with the
extractor's own row code from the installed map chunk and added to the local map data, because
the local DataTool lacks the mode the extractor's entity input needs; the changed extractor
itself was not run.

- The map loaded, the hero select opened and the defend box followed as before.
- The player stood on the tracks of an Underground tunnel with tiled walls, at full health on
  solid ground. The view faced the tunnel's side wall. About a quarter turn to the right it looked
  along the rails at a train whose front reads "KING'S ROW".

Not checked: a second player (so the four markers were seen with one body only), a player who
joins during the escort phase, and the Story mode `000F`.
