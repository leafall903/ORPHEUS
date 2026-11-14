# Kill feed

Build 1.74.0.0.104319. Seen live on 2026-11-09 in a separate offline service; see the
[index](README.md) for the evidence labels and the address convention.

## Summary

The server's part is small: give the dead body's script game message `003E.025`, and replicate
the pulser that results. Everything after that runs on the client, behind two gates on the game
mode entity's kill feed graph `0DEA`. The second gate reads an Options value that the client only
holds if the lobby sent it in message 20802. With that byte at zero the line is built and never
drawn.

## The path

1. The dead body's `01CF` takes `003E.025` (parameters: killer `0012`, crit `0095`, loadout
   `03E6`, assists `03E7`), stores the killer in `#392` and pulses `st108`, a ClientOnlyPulser.
2. On each client, `st108`'s client-only chain runs nodes 64, 65, then 63 or 62, then 66 or 67,
   and sends `0765.025` to the game mode entity (`STU_A3DEF368`).
   - Node 64 tests `STU_CD7F3617(owner)`, the body's name.
   - Node 65 tests `#392 && #392 != owner()`. When true, node 66 sends a line that names the
     killer. Otherwise node 67 sends one without.
3. The mode entity's `0DEA`, a client-only child of `0CB6`, takes `0765.025` and builds the line.

Read from data. Seen live: at the death the client evaluated `STU_CD7F3617`, then resolved
`STU_A3DEF368` to the mode entity, and in the same tick `0DEA`'s line path ran.

## Gate 1: `#9380e` lets the message in

- `0DEA`'s `0765.025` entry (node 4) is on only while `#9380e` is true (node 3). State 0, a
  BooleanSwitch on `#9380e`, also holds the feed's presenter (state 1).
- Stack state 2 sets `#9380e = !STU_A77AC2AC() && (STU_0D179CAF() || !STU_33C5B1DE() ||
  STU_CEFF290A())`. It is the idle subgraph of GameMessageEntry state 11, which waits for
  `07E3.025`. When that message comes, node 21 sets `#9380e` to false.
- So **`07E3.025` turns the feed off**. Its senders in the client data are `12B8` state 2 and
  `2983` state 0. `2983` posts `07E3.025` to the mode entity for the local
  player after `13B8.025`: the "hide the kill feed" side of a setting.

What the natives read (from code):

| Native | Offset | Meaning |
| --- | --- | --- |
| `STU_A77AC2AC` | `0xCE8580` | True only in game mode `0x0230000000000019`. |
| `STU_33C5B1DE` | `0x93A2E0` | The value of UI variable `0x0D800000000004AA`. |
| `STU_0D179CAF`, `STU_CEFF290A` | `0x92CFC0`, `0x92E0A0` | Unread: both jump into packed code. |

Seen live in the Practice Range: all four evaluated to 0, `0DEA` began states 0, 1, 2, 11, 17 and
21, so this gate was open without any help from the server.

## Gate 2: `#1962` shows the presenter

- The presenter (state 1) shows only while its condition `#1962` is true.
- Stack state 12 sets `#1962 = STU_016D65A8() ? (STU_F4C77F4A() || STU_CEFF290A()) :
  STU_CEFF290A()`.
- `STU_016D65A8` (`0x935C50`) is `!valid || [settings + 700]`. `valid` (`0x773E30`) is true when
  the game state is 4 and the account settings system is filled (its 16 bytes at `+224` and its
  byte at `+753`). The account settings system is entry 9 of the client's system table (vtable
  at image offset `0x26113C8`).
- `STU_F4C77F4A` (`0x1B7AB10`) evaluates `GetGameRulesetValue` on the mode entity for key
  identifier `0x2544`. The Practice Range's mode lists ruleset schema `0x0006`, the custom game
  general settings, so `0x2544` is probably its Kill Feed setting. That reading is an inference.
  The evaluator (`0xCCFAC0`) takes the expression's own value first.

Seen live: with byte `+700` at 0, `STU_016D65A8` and `STU_CEFF290A` both evaluated to 0, `#1962`
was false, and no line drew although gate 1 was open and the line was built.

## What fills byte `+700`

Read from code, in the Options screen:

- The copy to the UI (`0xAA74C0`) maps the settings system's `+700` to UI variable
  `0x0D80000000006426`, `+701` to `0x6047`, `+703` to `0x62BC` and `+704` to `0x5FE9`.
- The save (`0xAA78E0`) copies the struct at `+0x2B8` and fills byte `+4` from `0x6426`, `+5`
  from `0x6047`, `+7` from `0x62BC` and the int at `+8` from `0x5FE9`. That is the layout of
  20802's block `+0x120`, the block message 22203 saves.
- The Options defaults (`0xAA8650`) set `0x6426` and `0x62BC` to 1, and `0x6047` and `0x5FE9`
  to 0.

So retail's default has the feed on, but the settings system holds only what the lobby sent.
`+700` is `+0x2B8 + 4`: **byte `+0x4` of 20802's block `+0x120`**.

The block is the Gameplay page of Options: `+0x4` is Kill Feed Display, `+0x5` Always Skip Kill
Cam and `+0x7` Tips Display. Seen live: Always Skip Kill Cam set to On and saved through 22203
came back On at the next launch.

Seen live, with the settings system's bytes logged on change:

| Sent at login | Settings system | Result |
| --- | --- | --- |
| No 20802 | `+700` = 0 | No line. |
| 20802 with the five flags of `+0x108` on | No change at `+700` | No line. |
| 20802 with `+0x120` bytes `+0x4` and `+0x7` on | `+0x2BC` = 1, `+0x2BF` = 1 | `STU_016D65A8` = 1, the line draws. |

The last run set both default-on bytes together. Byte `+0x4` is the one `STU_016D65A8` reads;
`+0x7` alone was not tried.

The drawn line read "TRAINING BOT" (red, with the bot's icon), then "SOLDIER: 76" (blue, with the
hero's icon), beside the "YOU WERE ELIMINATED BY TRAINING BOT" notice.

## Bodies without `01CF`: the Practice Range bots

- A hero body carries `01CF`: `st108` is remote slot 15 and `#392` is presence bit 15. A presence
  bit counts the graph's instance-scope sync variables in slot order.
- The Range's four bot bodies (`1403`, `143E`, `146E`, `14ED`) list `0033`, `0BA8`, `0BE2`,
  `0BA3` and `174F`. `0BE2` references `0C18`, one of the 15 graphs in the data that post
  `0765.025`.
- `0C18` is `01CF`'s death part alone: entry `003E.025`, then `#10155` false, then state 8, a
  ClientOnlyPulser at remote slot 1, then the two `0765.025` sends. Its `#392` is presence
  bit 0. Its conditions are `01CF`'s: `STU_CD7F3617()`, then `#392 && #392 != owner()`, then
  `!#7082e || !#392.id(7082)` with a killer and `!#7082e` without.
- The client posts one line per pulser count it has not seen. A hero bot's body dies once, so
  its pulser goes out fresh at count 1. A Range bot is revived as the same body, so its count is
  its number of deaths.

Seen live: two Range bots killed gave two lines "SOLDIER: 76" to "TRAINING BOT", marked as the
local player's own; a second death of one of those bodies added one line. A Reaper bot gave
"SOLDIER: 76" to "REAPER" at its death and again for its next body, and the player's death read
"REAPER" to "SOLDIER: 76".

## Compared with ORPHEUS

_Written against the `kjs` branch of this repository; see the [index](README.md)._

- `game/combat.py` documents the same path and gives `003E.025` to the dead body's script.
  `game/script/driver.py` names both pulsers (`KILL_FEED_PULSERS`), and `combat.mode_frame`
  keeps `0CB6` on the mode entity. Instance variables are filtered by each graph's own
  server-only list (`replicated_vars`), so `01CF`'s `#392` is not held back by another graph's
  list.
- `content/player.py` builds 20802 from the schema's empty value and copies `+0x120` only when
  the profile has saved it. Before `GAMEPLAY_DEFAULTS` was added there, a new profile's
  **block `+0x120` went out as zeros**. By this note that hides the feed's presenter.
  It would also explain a feed that appears only after a player has opened and saved Options
  once, since 22203 then stores the client's defaults.
- Changed since: when `+0x120` was never saved, 20802 now carries the client's Options defaults
  for it, bytes `+0x4` and `+0x7` set (`GAMEPLAY_DEFAULTS`). A saved block is sent as saved.
  Covered by `tests/runtime/test_player_settings.py`.

## Seen live against ORPHEUS

_Written against the `kjs` branch of this repository; see the [index](README.md)._

2026-11-10, original client, with the option default above in the server and a profile that had
no saved settings. Practice Range, Soldier: 76, driven by a virtual gamepad through an input
automation tool.

- The enemy training bot was killed 12 times. The server logged each kill: five or six 25001
  hits, one 25005, then the respawn three seconds later. On screen the bot fell apart and
  came back.
- **No kill feed line drew.** Sixteen frames were saved about three seconds apart. Eight show
  the bot down, one of them with the elimination notice on screen, and none has anything in
  the kill feed corner.
- The elimination notice read "ELIMINATED" with no name after it.
- **The client took the option.** In the same session its Options screen, Gameplay page,
  showed Kill Feed Display On, Always Skip Kill Cam Off and Tips Display On: the three values
  the server sent. The client saved no option block in that session (no 22203), so the screen
  showed what login gave it. The byte itself was not read from memory, and the screen was not
  compared with a login that sends zeros.

So the option arrives and is still not enough for a bot kill on ORPHEUS. What this run does not
show:

- Whether the bot's fresh pulser went out. `combat.kill_feed_allowed` holds it back until the
  mode script is delivered and the client has both the bot's and the killer's identity.
- Whether the client's own conditions passed. The first one in `0C18` is `STU_CD7F3617()`, the
  body's name. The notice had no name for this bot, so a nameless body was a candidate for
  both symptoms.
- A player's death, which runs through `01CF` instead, was not tried.

A second run the same day tested the name. With each bot given a controller entity, the
notice read "ELIMINATED TRAINING BOT"
([nameplate note](bot-nameplates-and-hit-feedback.md#seen-live-against-orpheus)). Six kills,
three of them caught with the notice on screen: **still no kill feed line**. So a missing name
was not what kept the line away, or not alone.

A third run tested the frame. The Practice Range's mode entity then carried `13C0` with `0CB6`
as its child and `0CB6`'s remote state off, bit for bit the frame of the service where the feed
draws ([Tab board note](tab-board.md#compared-with-orpheus)). Four kills, the notice named:
still no kill feed line. So how `0CB6` sits on the mode entity is not the cause either.

A fourth run found where the chain stops. The server logged its bot death path, and a
statescript tracer plugin ran in the client (`offline/tools/asi/tracer.c`, loaded into the
running game with `offline/tools/asi/load_asi.py`). Seven bot kills:

- **The client's gates were open.** In `0DEA`, `STU_016D65A8` evaluated to 1, and
  `STU_A77AC2AC`, `STU_0D179CAF` and `STU_33C5B1DE` to 0: the values of the service where the
  feed draws.
- **The client never held `0C18`.** The tracer logged no state of that graph in the whole
  session.
- **The server never queued a bot death frame.** `Combat._bot_died` returned at its first
  check: the bot had no body script.
- **The runtime pack held no definition for the bot bodies.** `graph.entity_body` reads the
  graph bundle's optional `entities` table, and the pack's bundle had none. It also lacked
  graphs `0BA3`, `0BA8`, `0BE2`, `0C18` and `174F`. `Match._prepare_bot_script` then makes no
  script and says nothing.

So the cause was the pack and not the code. The standard graph bundle covers the hero bodies
only. [data/README.md](../../data/README.md#additional-statescript-definitions) describes the
bundle that adds the six Practice Range bot bodies (`1403`, `143E`, `146E`, `14A9`, `14D2` and
`14ED`). It is made from a DataTool XML dump of the client's `003` and `01B` assets. The
overhead name and health widget hangs from the same graphs
([nameplate note](bot-nameplates-and-hit-feedback.md)), so the same pack explains its absence.

## With a pack that holds the bot bodies

2026-11-10, original client, Practice Range, Soldier: 76. A graph bundle was made with the six
bot bodies and a new pack assembled around it, as [data/README.md](../../data/README.md)
describes. The bundle holds 6 entity bodies and 383 graphs: the hero bundle's 373, unchanged,
and `0A5B`, `0B4E`, `0BA3`, `0BA8`, `0BE2`, `0C16`, `0C17`, `0C18`, `0E8C` and `174F`. The
extractor's command line reads the hero table from the checkout's `data` folder, which a
runtime-pack layout does not have; calling its `collect` with the pack's table worked.

What the unchanged server then sends for a dying Range bot, read back with its own decoder:

1. At the death, a delta: `0C17` gets `#392`; `0C18` gets `#392`, `#9296` and `#1821`, and its
   state 8 goes on with the pulser's fresh bit and count 1.
2. Four ticks later, a delta: `0C17`'s pulser, state 16, fresh at count 1.
3. About a second later, a delta: both pulsers with the fresh bit off.

**The line draws when the client starts the pulser.** It read "RESEARCHER", the hero's icon, an
arrow, the bot's icon and "TRAINING BOT", killer in blue and bot in red.

**The client mostly did not start it.** Kills and lines by what the server sent at the death:

| Sent at the death | Kills | Lines |
| --- | --- | --- |
| The delta above (the unchanged server). | 19 | 0 |
| The same content as one full frame. | 14 | 1 |
| That full frame again after each acknowledgement, for about a second. | 4 | 4 |

The second and third rows were temporary changes for these runs. No server change was kept.

Seen with the statescript tracer in the client, in a run of the second row:

- At the one kill that drew a line, `0C18` state 8 began and read fresh 1, count 1 from the full
  frame. 47 ms later `0C17` state 16 began from the delta that followed, so a delta can start a
  pulser.
- At the kills without a line, the same body's `0D24` reacted to the death, and neither
  pulser began. The client applied none of that body's death frames.
- The server's datagram log shows each death frame sent in one datagram with the bot's health
  update and a movement record, and acknowledged.

Ruled out as the reason:

- The other variables in the delta. Two kills with only `#392` and the pulser drew no line.
- The health update sharing the frame's datagram. Two kills with the update one datagram later
  drew no line.

The repeated full frames drew one line a kill, never more. That fits the client's pulser, read
from code:

- Its reader (image offset `0x9FD760`) takes the fresh bit and the 8-bit count. On its first
  read it arms one pulse when the fresh bit is set, and otherwise takes the count as already
  done. Later reads only move the target count.
- Its update (image offset `0x9FD870`) fires the out plug once for each count between done and
  target. A count it has fired does not fire again.

Two traced sessions ended at their first kill. The cause was not established, and the later
runs went without the tracer.

## Why the client lost the death frames

A read-only sampler read each entity's statescript network part from the client's memory. The
part is found by its class vtable (image offset `0x263C160`). It holds the number of the last
frame applied (`+2312`), the list of chunks waiting (`+1864`, count `+1872`, each with its first
and last frame) and the instance records (`+88`).

Seen with the unchanged server, at a bot's death:

- Before the death the bot's stream had applied frame 2.
- After it the client held chunk 4, then chunks 4 and 5, as waiting, until the body was
  destroyed. **Chunk 3, the death delta, never showed in the list and was never applied.**
- The server's log shows chunk 3 sent in the datagram with the kill's reliable messages, the hit
  and the elimination notice, and chunks 4 and 5 in datagrams with none. All three datagrams
  were acknowledged.

Why one lost chunk is fatal, read from the client's code:

- The intake (image offset `0xA0A3E0`) keeps a chunk whose last frame is above the last one
  applied, and drops the rest.
- The pump (image offset `0xA0E630`) runs each tick. It applies a waiting chunk only when its
  first frame is at most one above the last applied, and a refused chunk waits for the next
  tick. A full frame has first frame 0, so it always qualifies.
- So after a lost delta every later delta waits for ever. The server sends nothing again,
  because the datagram was acknowledged. That is also why the repeated full frames above drew
  the line: the first one came with the notices and was lost, and a later one applied.

The test: a bot's chunk kept out of every datagram that carries reliable messages, the server
otherwise unchanged. Chunk 3 then showed in the list and applied, and 4 and 5 after it. In one
run of 15 kills, frames taken about 5.5 s apart showed a line in 13 of 18. With the kept change
below, 2 kills drew 2 lines and no bot's stream ended with a chunk waiting.

Not established:

- Where the client's datagram reader loses the chunk.
- Whether any reliable message does it, or only one that names that entity. The player's own
  stream kept advancing in datagrams that carried hit notices about other entities.

**Changed:** in `Client.send_frame`, a bot's statescript chunk and reliable messages take turns.
The messages go first. A chunk that waited goes in the next datagram, and that one carries no
reliable messages. Other players' bodies use the same kind of stream and are probably affected
the same way. That needs two clients and was not tried, so the change covers bot bodies only.

## This source: a nameless bot body

2026-11-14, read from data: in `0C18` the client-only chain from the pulser (state 8) reaches a
node whose condition is `STU_CD7F3617(owner)`, the body's name, and that node's false plug has no
links. A body with no name never sends the kill feed message, however its death frames arrive.

On this source a Range bot was a body alone, so it had no name. It now has a seat and a
controller that possesses its body (`Match._show_bots`, the
[nameplate note](bot-nameplates-and-hit-feedback.md#this-source-seats-and-controllers-for-the-bots)).
`combat.kill_feed_allowed` holds the fresh death pulse until the client has acknowledged that
possession; the pulse still has to go out within `ClientOnlyPulser.FRESH_MS` of the death.

What ruled the delivery out first, the same day, with the unseated bots: the server offered each
viewer the bot's fresh pulser at count 1 in a datagram with no reliable messages, the client
acknowledged it, and a read-only sampler of the client's statescript network parts showed each
dead bot's stream applying its death chunks (4, 5 and 6) with none left waiting. The elimination
notice read "ELIMINATED" with no name, and no line drew.

Seen in the original client with the seats, the same day, Practice Range, one player on a
virtual gamepad:

- **The line draws.** As Soldier: 76, ten kills of the enemy Training Bots: every frame sampled
  after a kill held its line, "RESEARCHER", the hero's icon, an arrow, the bot's icon and
  "TRAINING BOT", the killer in blue and the bot in red, two lines at once when two kills fell
  within the feed's hold.
- **The notice is named**: "ELIMINATED TRAINING BOT", the name in red.
- **The ability icon draws.** As D.Va, eight kills. The two bots her Self-Destruct killed gave
  two lines with the Self-Destruct icon in place of the arrow; cannon kills gave the arrow.
- The 66 kills between the Friendly Bots and the Training Bots in the first match drew no line
  in any sampled frame. Not explained: the server offered those pulsers the same way.
- The Tab board now lists the seated bots: a row of eleven Training Bots against the player and
  four Friendly Bots.
- Not timed: a kill within the first moments after a respawn or a join, before the new
  possession is acknowledged.

## Open

- Where the client loses a chunk that comes with reliable messages, and for which messages.
- Other players' bodies: whether their chunks are lost the same way. Needs two clients.
- A player's death, which runs through `01CF` and the same kind of frames, was not tried.
- The meaning of `STU_0D179CAF` and `STU_CEFF290A`.
- A line's critical-hit mark and assists (two of the other `003E.025` parameters) were not
  exercised.
- Why a bot's kill of another bot draws no line.
- The read-only memory reader used for the first live reads is not in this repository. The
  tracer plugin's source is, under `offline/tools/asi`.
