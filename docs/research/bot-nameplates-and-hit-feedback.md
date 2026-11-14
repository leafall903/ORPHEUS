# Bot nameplates and hit feedback

Build 1.74.0.0.104319. Seen live on 2026-11-07 in a separate offline service, in the Practice
Range with all 15 authored bots; see the [index](README.md) for the evidence labels and the
address convention.

## Summary

Three pieces of feedback on a Practice Range bot were made to work in the original client: the
hit marker, the named elimination notice, and the overhead name and health widget. Each needs
something different from the server. The widget is the one with ordering rules.

## Hit marker: message 25001

- 25001 carries the target entity, the damage dealt, a flags byte and a frame stamp. Its handler
  (`0x6C2D40`) adds to the shooter's hit counters, and the crosshair widget (`0x8A`) draws from
  those counters.
- **Flag bit 0 is the critical hit.** Seen live: a body hit drew the white marker, and a hit with
  bit 0 set drew the red one. A read-only emulation of the handler confirmed that the message
  increments the counters.
- The stamp that worked was the packet clock's frame at the hit:
  `1000 + round(seconds since match start * 1,000,000 / quanta)`. No other stamp was tried.

## Elimination notice: message 25005

- 25005 carries the shooter's body, the victim's body, a score and an assist flag. Its handler
  (`0x6C2B50`) posts `0F76.025` to the game mode entity, where the client-only graph `1FC0`
  draws the notice. The mode entity has to carry the common HUD graph `0CB6` for that.
- **The victim's name came from a controller entity, not from the body.** The notice read
  "ELIMINATED TRAINING BOT 100" with each bot given a controller entity that carries
  components 26, 74, 44 and 75 and a name on the roster. The client resolves the display name
  through that entity.
- The controller's entity id and its participant slot were kept across the bot's respawns, and a
  human player who joined later took an unused slot, so that a bot's name is not overwritten.
- The `100` is the score byte: the share of the victim's health credited to the shooter.

## Overhead name and health widget: graph `0D24`

Read from data:

- A Range bot body's graphs lead through `0BE2` to `0C18` to `0D24`.
- `0D24` state 35 creates the widget (schema `0A88:120`) under the game mode's world-items
  presenter, which it finds through variable 14206 (collection 5912). State 51 (schema `019C`)
  is the health bar and state 53 (schema `128`) the localized "Training Bot" name.
- State 35 is a client-only state.

What it needed, seen live:

1. **Visibility is an instance variable.** Input 8807 is instance scope, at filtered presence
   bit 0. Setting an entity variable with the same number did nothing. The widget's IsVisible
   property (`27A3`) must come out true. The simple-name input 14680 is at filtered presence
   bit 3.
2. **Entity inputs:** 478 false, 11264 false and 20017 true, sent as initialized values.
3. **The mode graph.** The presenter reads variable 14206 from the mode entity's HUD graph.
   This comes from the data; the service always had that graph on the client first, so a
   widget made before it was not tried.
4. **Order of delivery.** The body's create had to be acknowledged before the graph was sent,
   and the graph's first full frame acknowledged before the change that starts state 35. With
   the body and the graph in one packet, the widget was missing for a bot's first life and
   present after its respawns.
5. **Health** came from the body's component 51, with no extra message.
6. **Death** removed the widget, and the respawn brought it back full.

How the service delivered it, which differs from the authored flow:

- It hosted `0D24` on the bot's controller entity, not on the body. That needs entity variable
  29415, the authored target-body override, set to the body. Client BooleanSwitch 72 and
  Stack 71 read it into instance variable 8154. A one-time network write of 8154 itself was
  overwritten by the client's own initialization.
- It started state 35 with an owner-style delta. The full-frame reader (`0xA0BE30`) skips
  client-only states, while the delta reader (`0xA0C620`) indexes the unfiltered state table
  and accepts one. That delta's variable mask covers the graph's 32 slots, not the 4 slots of a
  filtered full frame. The service did not test whether the client starts state 35 by itself
  when the graph arrives the authored way.

Result: a read-only scan of the client found 15 active state-35 presenters attached to the world
HUD, one per bot. On screen each bot had its name and a full bar from its first life, the bar
fell with damage, went away at the elimination and came back full on the respawn.

## Component 123 on a Range body

Sending component 123 (`MECCharacterBody`) on Range body `1403` crashed the client: those bodies
have no such component. `game/match.py` in this repository records the same assert.

## The Range bot's pose

For any hit test that uses the model's own volumes:

- The training bot's bind pose is not what the client draws. The entity stands at floor height,
  and the idle clips (`39FC`, `39FD`) lift bone 2 from Y 0.8236 to Y 1.3236. Volumes taken from
  the bind pose sit 0.5 m too low.
- The bind pose also holds the arms straight forward. The drawn pose is animation `3952` frame 0
  plus the additive breathing clip `3950`. Six hard points read from the running client matched
  that pose within 5 mm.

## Compared with ORPHEUS

_Written against the `kjs` branch of this repository; see the [index](README.md)._

- **25001.** `game/combat.py` sends it with the same fields and marks "bit 0 = critical" as
  unverified. This note saw it live. ORPHEUS stamps it with the shot's frame; only the packet
  clock's frame was tried here.
- **25005.** `combat._kill_notice` names the bot's own entity as the victim and sends score 0.
  An ORPHEUS bot was one entity, the body with its health and component 26
  (`Match._bot_create`), with no controller entity. That was the cause of the missing
  elimination names: bots now get a seat and a controller (`Match._seat_bots`,
  `Match._show_bots`), and the notice names them. See the second run below.
- **Widget.** ORPHEUS runs the bot body's own graphs in its script runtime, keeps `0D24` as the
  child of `0C18` on the body, and sends that child as a separate chunk once the mode script is
  delivered (`server._flush_bot_nameplates`). That covers point 3 above and avoids the body
  override. Still to check in the client, in this order: instance variable 8807 and the
  IsVisible result (point 1), the three entity inputs (point 2), and whether the body's create
  is acknowledged before the chunk goes out (point 4).

## Seen live against ORPHEUS

_Written against the `kjs` branch of this repository; see the [index](README.md)._

2026-11-10, original client, Practice Range, Soldier: 76, the enemy training bot killed 12 times
(the [kill feed note](kill-feed.md) describes the run).

- The elimination notice drew on a kill and read "ELIMINATED" with no name after it.
- No name and no health bar drew over the bot, before it was hit or after.
- The server sent 25001 for each hit. The hit marker itself was not caught: the frames were
  saved about three seconds apart.

A second run the same day, after the bots were given controllers:

- Each seated bot has a participant record with its name, a controller entity with its team
  (26) and seat (74) in its team's list, and after the client acknowledged both the controller
  and the body, 44 and 75 naming the body and the bot's hero. A respawned bot keeps its
  controller, which then possesses the new body.
- **The notice read "ELIMINATED TRAINING BOT"**, the name in red. The same bot was killed six
  times; the three notices caught in frames all carried the name, so it holds across respawns.
- The hero select screen was unchanged: it listed no bots.
- Still no name and no health bar over the bot, and still no kill feed line.

A later run found one reason for both: the runtime pack held no statescript for the bot bodies,
so the server ran none of their graphs and sent no `0C18` or `0D24`
([kill feed note](kill-feed.md#seen-live-against-orpheus)). The checklist above still waits for
a run with a pack that holds them.

With such a pack, the same day: **a red "TRAINING BOT" name and a health bar drew over each
enemy bot** in view, hit or not, and the enemy bots had red outlines. The friendly bots'
widgets and the bar's change on a hit were not looked at ([kill feed note](kill-feed.md#with-a-pack-that-holds-the-bot-bodies)).

## This source: seats and controllers for the bots

2026-11-14. This source now seats the Practice Range's spawner bots and gives each a
client-visible controller, as the `kjs` branch did: `Match._seat_bots`, `Match._show_bots`,
`Bot.seat_controller`. The controller is separate from the server-only NPC controller component
that a bot's body script may have.

Seen in the original client, the same day: the notice read "ELIMINATED TRAINING BOT", the kill
feed line drew for the player's kills, and the Tab board listed the seated bots
([kill feed note](kill-feed.md#this-source-a-nameless-bot-body)).

## Open

- Whether the client starts `0D24` state 35 by itself in the authored flow.
- Assists: only the finishing shooter was credited.
- Hero bots' limbs were left in their bind poses; only the Range bot's idle pose was matched.
