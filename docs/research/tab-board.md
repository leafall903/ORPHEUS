# Tab board

Build 1.74.0.0.104319. Seen live on 2026-11-10 in a separate offline service; see the
[index](README.md) for the evidence labels and the address convention.

## Summary

The scoreboard key did nothing because the client holds the board shut until a game message
that only a server-sent state of the mode graph posts. Turning that one state on opens the board
in every mode tried. The board's own numbers are component 64 of the local player's entity, with
the layout already described in `game/stats.py`, except that its stat GUID fields use the compact
id encoding.

## Why the key does nothing

Read from data, then traced live in the client:

1. The HUD's host graph `041C` runs on a client-made entity. It starts the board's graph `01BE`
   and, beside it, graph `0CC9`.
2. In `01BE` the key works. While logical button 77 is held, a Stack (state 15) sets
   variable 1729, "board open". Traced: state 15 began and ended with the key, and the board's
   presenter (state 24) was on the whole time.
3. `01BE` also holds variable 1729 off with a second Stack that has a priority (state 17), for
   as long as variable 6699 is off. Traced: state 17 never ended.
4. Only `0CC9` turns variable 6699 on: while game message `06DF.025` is being posted (its
   state 3) and `13BF.025` is not (its state 13).
5. `06DF.025` is posted by `STU_38EE1100` states of the mode graphs. There are 19 of them in the
   data, each a remote-synced state. A client runs a mode graph's remote states only as the
   server sends them.

So the server has to carry the mode's own graph on the game mode entity, with the one state that
posts `06DF.025` on.

The state's remote data is one bit after its start bit: 0, for "no optional integer". This was
calibrated live on another mode graph (`0D28`). Without the bit every later slot of the frame is
read one bit off.

## Which state, by mode

Slots are indexes into the graph's remote-synced state list.

| Mode | Mode graph | Remote slot | Seen live |
| --- | --- | --- | --- |
| Practice Range `0x18`, and its other mode `0x5A` | `13C0` | 4 | Board opens. |
| Skirmish `0x10` | `0D76` | 2 | Board opens (Hanamura). |
| Assault, Escort, Hybrid | `0F4A` | Per phase: assemble 4, setup 7, attack 10, round complete 26 | Opens in Assault's setup and attack, and in the attack phase of Escort (Dorado) and Hybrid (Eichenwalde). The other phases were not run. |
| Control `0x17` | `0EC2` | 1 | Board opens, and the mode's capture header shows (0%, lock, 0%). |
| Deathmatch `0x1D` | `0F60` | 0 | Board opens with the mode's own stats (Final Blows, Solo Kills). |
| Team Deathmatch `0x1E`, `0x70` | `0F73` | 0 | Board opens. |
| Elimination `0x07` | `0CE8` | 1 | Board opens. |
| Capture the Flag `0x09` | `0D0B` | 6 | Board opens. |
| Mode `0x46` (stats: Enemies Frozen, Teammates Thawed) | `1A59` | 6 | Board opens. |
| Uprising, Retribution, Snowball Offensive | `0D28`, `100B`, `0CE4` | 0, 0, 5 | Not run. |

- A mode graph also starts client-only HUD states. Control's capture header above is one. Mode
  `0x67`, an Assault variant with graph `29A6`, put an "UNSPECIFIED 0.0" objective header up and
  was left out.
- Not run: Lucioball `0x20`, Yeti Hunt `0x29`, Junkenstein `0x03` and `0x2A`.

## What the board shows

- The map, the mode and the difficulty.
- A tile per player entity of each team, bots included, with a dead one crossed out.
- The local player's own numbers, from component 64 of that player's entity.

## Component 64

`game/stats.py` and `game/onfire.py` describe the layout. Two things were settled live:

- **The 14 stat GUID fields (5 to 18) are the compact id kind**, a byte mask followed by the
  non-zero bytes, not a raw u64. Sent as raw u64, the board showed three "UNKNOWN STAT" entries.
  Sent as ids, every stat had its name.
- **The slot assignment in `stats.py` is right.** Each stat was sent with a number of its own:
  fields 5 to 12 are the mode's player stats, with their values in 21 to 28 and their medals in
  37 to 44, and fields 13 to 18 are the hero's own stats, with their values in 29 to 34. The
  client names each stat and prints its value by the stat's display type (`11`, `4,444`,
  `00:33`, `42%`).

Sent in the entity's create and again when a number changed, at most twice a second: a kill
showed "1 ELIMINATION" with a gold medal and "35% WEAPON ACCURACY", and a death "1 DEATH".

For Soldier: 76 in the Practice Range the lists were Eliminations, Objective Kills, Objective
Time, Hero Damage Done, Healing Done and Deaths, then Weapon Accuracy, Kill Streak - Best, Final
Blows, Helix Rocket Kills and Tactical Visor Kills.

## On Fire

- Component 64's field 36 burns the portrait, as `game/onfire.py` reads it. Seen live: no flames
  after two kills of the same bot, flames from the fourth.
- The rule behind the score is not in the client. `003F.054`'s second table is 20 keyed
  settings (key 19 = 90.0, key 7 = 0.04 and so on), not a rule.
- The rule used was a community reading of retail
  ([PCGamesN, 20 June 2016](https://www.pcgamesn.com/overwatch/overwatch-on-fire-guide)): on
  fire from 250 points, 5 points a second draining all the time, and an elimination worth the
  percent of the target's health the player took. That percent is the score byte of
  message 25005.

## The hero switch line

`game/notices.py` already sends it. Seen live in Skirmish: "Player switched to Reaper (was
Soldier: 76)", one line, once. The name is the player entity's component 29 `m_battleTag`, which
also names the player's tile on the board and the label under the ultimate meter.

## Still wrong on the board

- **Every tile's ultimate charge reads 0%**, the local player's included. Ruled out live: the
  body's variables 342 and 338 copied to the player entity, component 64's field 35, and
  component 74's fields 1, 4 and 8.
- **The board's time stays at 0:00.** Its text reads variable 2499 through the game mode entity
  (`01BE`'s presenter), but a mode entity given variable 2499 = 125.0 still showed 0:00.

## Compared with ORPHEUS

- `combat.mode_frame` carried only the mode UI graph `0CB6` on the game mode entity, with its
  state 21. No frame carried a mode graph such as `13C0`, so nothing posted `06DF.025` and the
  board stayed shut, as [WIP.md](../WIP.md) reports.
- `game/onfire.py` declared fields 5 to 18 of component 64 as `u64`.
- Changed since, for the Practice Range: `combat.MODE_GRAPHS` names the mode's graph, the remote
  slot of its posting state and the state the mode UI hangs from, and `mode_frame` then lists
  `13C0` as instance 1 with remote slot 4 (state 6) on and `0CB6` as instance 2, the child of
  instance 1's state 21, with `0CB6`'s own remote state off. ORPHEUS's frame writer produces the
  same 96 bits the offline service sent (`08e00910b60c14a002003000`). The 14 stat GUID fields
  are declared `id`.
- Changed 2026-11-11: Deathmatch and Team Deathmatch have their row. The state the mode UI hangs
  from is the mode graph's SubScript state that names `0CB6`: `0F60` 13 and `0F73` 10, like
  `13C0` 21. The same state in the graphs not listed yet: `0CE8` 14, `0D0B` 3, `0D76` 15,
  `1A59` 2. Control's `0EC2` has no such state: its client-only state 162 starts `1FEE`, whose
  state 8 starts `0CB6`, so the client nests the mode UI itself and a frame would list `0EC2`
  alone. The objective modes' `0F4A` does the same through its client-only state 11 and `1FEB`
  state 0.
- The other modes still carry `0CB6` alone. Control's row was tried and is held back until an
  elimination is seen under its frame. ORPHEUS runs no objective phases, and a board state without
  its phase state draws a stray header (below).

## Seen live against ORPHEUS

2026-11-10, original client, Practice Range, Soldier: 76, with the change above.

- **The board opens** while Tab is held, at the spawn and after a fight.
- Its tiles: 11 "TRAINING BOT" on the enemy side, and the player with 4 "FRIENDLY BOT" on the
  other. The bots are there because each has a controller entity
  ([nameplate note](bot-nameplates-and-hit-feedback.md#seen-live-against-orpheus)).
- Every stat has its name: Eliminations, Objective Kills, Objective Time, Hero Damage Done,
  Healing Done and Deaths, then Weapon Accuracy, Kill Streak - Best, Final Blows, Helix Rocket
  Kills and Tactical Visor Kills. No "UNKNOWN STAT".
- After four kills of one bot: "4 ELIMINATIONS" and "840 HERO DAMAGE DONE", each with a gold
  medal. The hero's own stats stayed 0; ORPHEUS does not count them yet.
- The player's tile showed the ultimate charge, 23% and later 95%, unlike the offline service's
  tiles. The board's time still read 0:00.
- The elimination notice still drew with the bot's name under the new frame.

2026-11-11, original client, Soldier: 76, one player, matches from the Arcade cards.

| Mode | Map | Result |
| --- | --- | --- |
| Team Deathmatch `0x1E` | Black Forest | Board opens: 4 against 4 tiles, the player's tile with its ultimate charge, Final Blows and Solo Kills among the stats. The HUD is unchanged. |
| Deathmatch `0x1D` | Petra | Board opens with the lone player's tile. It lists the general player stats, not Final Blows and Solo Kills. |
| Control `0x17`, trial row `0EC2` slot 1 alone | Busan | Board opens: 6 against 6 tiles. The capture header draws (0%, lock, 0%) and stays locked, as the server runs no Control rules. |
| Assault `0x14`, trial row `0F4A` slot 10 alone | Volskaya Industries | Board opens, and an "UNSPECIFIED 0.0" objective header draws at the top of the screen. The row was not kept. |

- No elimination happened in these matches. The kill feed and the elimination notice under these
  frames are unchecked, Control's above all, since its frame does not list `0CB6`. That row was
  not kept either.
- Elimination, Capture the Flag, Skirmish, `0x46`, `0x5A` and `0x70` were not on the Arcade that
  day and are unseen against ORPHEUS.

## Open

- The board's time, above. The tiles' ultimate charge is open for the offline service only.
- The objective modes: `0F4A` needs its phase states sent with their board states.
- An elimination in Control and in the modes not yet seen against ORPHEUS.
- Medals were gold for any counted stat above zero. Ranking among teammates was not tried.
- The tools used for the live reads, a statescript tracer plugin and a script that lists every
  mode graph's posting slots, are not in this repository.
