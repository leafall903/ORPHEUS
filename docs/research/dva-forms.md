# D.Va's two forms

Build 1.74.0.0.104319. See the [index](README.md) for the evidence labels and the address
convention. Graph flow is **read from data** (the runtime pack's graphs `026A`, `0276`, `0B2F`),
the config vars are **read from code** where a function is named, and the last sections say what
ORPHEUS does and what was seen in the client.

## Summary

The hero's body is always the pilot, entity definition `040A`. Her own graph `026A` makes the mech,
definition `0407`, as an entity she owns, and puts her in it. The mech's graphs tell her when to
leave: one game message, `016C.025`. Nothing in the graphs makes a pilot, because she never goes:
while she is in the mech she is stowed, and when she leaves it she is the same entity.

ORPHEUS plays one body for a player. It keeps that, and makes a change of form a change of body
at a place, as a hero pick is. The graphs still decide when.

## The entities and how they find each other

| Config var | Evaluate, worker | Reads | Meaning |
| --- | --- | --- | --- |
| `STU_F7262B36 {m_801B298F}` | `0xCDBDE0`, `0xD15520` | component 43 of the entity | the player who drives it; empty for a mech she left |
| `STU_F76C486C {m_D63CBEC2}` | `0xCE2CF0`, `0xD1BEE0` | component 72 | the player it was made for; it outlives the driver |
| `STU_0AC15F78 {m_entity}` | `0xCEDBD0`, `0xD24F40` | component 44 of a player | the hero he plays: the pilot |
| `STU_E72F6348 {m_entity}` | `0xCE4A60` | | whether the entity exists |
| `STU_71A87E92 {m_entity}` | `0xCCC750` | the entity's translation | a body's feet |

The component numbers come from the three mask helpers the workers call: `0xBD4680` (bit 43),
`0xBD59A0` (bit 44) and `0xBD3DF0` (word 1, bit 8: 72). What the workers do with the component
is behind the client's obfuscated lookup and was not followed; the meanings are what the graphs
use them for. `STU_8CBDD81C` (`0xCE6430`, worker `0xD16460`) is another position: the graph takes
0.75 m off the pilot's to get the floor, so it is not her feet. It was not read.

So `STU_0AC15F78(STU_F7262B36(self))` in a mech's graph is "the pilot who drives me", and
`STU_0AC15F78(STU_F76C486C(self))` is "the pilot I was made for", which still works after she
left.

The node classes, by what the graphs do with them. Their code was not read:

- **`STU_B6E3C77D`** makes an entity: `m_entityDefinition`, `m_position`, a facing
  (`m_AD8E614B`), an owner (`m_D749D7D8`, here `STU_F7262B36` of the pilot: the mech gets her
  player) and an output variable (`m_B3019338`, `#38`). Other heroes' graphs use it for their own
  placed entities (`00FA`, `0169`, `0622`).
- **`STU_523EE38D {m_entity}`** destroys an entity.
- **`STU_CA861E44 {m_C98661CF}`** is on while the pilot is in the entity it names.
- **`STU_43DB06B1`** searches for the spot to put an entity on, and answers with one of two
  outputs: a spot (`m_F6955E81`) or none (`m_425337C6`).
- **`STU_54FEB21C`** is a state that ends by itself; with no time authored the graphs put it
  before an action only the server can do.
- **`STU_43B63AD0 {m_980D78D3: STU_92CA44CC}`** and **`STU_D6EB7F8D`** (lists of colours and
  texts) change nothing the server keeps. The maintainers read the first as the base action
  (`nodes.ultimate_base_action`).

## The pilot's graph, 026A

**Entry.** Node 2 asks `STU_F7262B36(self)`: does a player drive me?

- No: she is stowed. Movement off (node 5), `STU_33A46033`, `STU_D07EBF01` and `STU_9028E035`
  on, and node 115 clears `#6828`.
- Yes: nodes 148 and 147 copy the mech's ultimate cost and charge into `#7351e` and `#2511e`.

Then, unless `#6868` is set, with a living player and `#12428e` off, node 211 plays script `303B`
on `#38` and **node 7** makes the mech at her own position less 0.75 m, and state 19 begins. Game
message `0013.025` (node 6) takes the same path when `#38` is empty or gone: a respawn makes a
new mech.

**State 19, in the mech.** On its begin it sends herself `007D.025` and sets her charge `#342e`
to 0. While it is on: node 20 (`STU_CA861E44`, the mech in `m_C98661CF`), movement off, her aim
follows the mech's pitch and yaw (node 17, `STU_9935FBC8` with `STU_40AC5AE4` and `STU_C8C4C20D`
of `#38`), and node 83 (`STU_72DDE005`) with a `ModifyHealth` of 9999 on herself.

**Eject.** Game message `016C.025` ends state 19 (node 31). Node 83's end, if she has health,
plays alias `0439.0C8` for 1 s (node 48), casts a ray 2 m up from 0.5 m above her feet, and with
nothing above her runs `STU_CFCE55DC` toward that point with `#38` (node 138).

**Call Mech.** Node 9 is the ability on button 37, when `#342e` equals `#338e`.

1. Node 26: not dead (`#478e`) and not `#6868`. Otherwise 0.1 s later node 25 destroys `#38`.
2. Node 38 searches for a landing spot, within `#956` = 1.27 m times her scale. With a spot,
   `#680` is that distance and `#1626` the spot. Without one, on the ground or with floor 3 m
   below, `#680` is 0.
3. A 0.2 s Wait (node 12) with alias `0433.0C8`, then node 72 sets `#6472`: the spot, or her own
   position less 0.75 m. Node 212 plays `303B` on the old `#38`, and **node 15** makes the mech
   at `#6472`.
4. With no spot she steps back `#956` along her facing over 0.7 s (node 95, a MoveTo).
5. 0.7 s after the mech is made, node 35 searches 2.5 m around a point 0.5 m above its feet for
   hostile bodies in sight, and node 36 hits them for **250**.
6. 1.75 s after the mech is made (node 81) state 19 begins: she is in it. If that Wait is
   aborted, node 30 destroys the mech. Node 116 sets `#6828` at the same moment.

`#6828` is a blackout: presenter 193 (widget `04C8`) is on while it is, and draws the screen
black through `#146`. The Entry sets it and only the stowed branch clears it.

## The mech's graphs

**A destroyed mech, 0276.** Game message `0011.025` (node 4) with `#33688e` set and `#842e` off
starts three things on the server:

- the wreck state (node 13, a 999 s Wait): effect `16E1`, movement off, `#11264e` on, and the
  ultimate's button sends `0592.025` when the charge is full;
- a 1 s Wait (node 15), cut short when the pilot is dead, then node 5 sends **`016C.025`** to
  `STU_0AC15F78(STU_F7262B36(self))`: eject;
- 0.75 s after that (node 6) state 22 ends, and with no driver left node 7 hits the mech and
  2 s later **node 3** destroys it.

No graph writes `#33688e`, and what posts `0011.025` was not looked for. Message `007A.025`, which
the pilot sends when a mech is made, also removes a mech nobody drives, 3 s later (node 94).

**Self-Destruct, 0B2F.** Node 0 is the ability on button 37 with a full charge. Its Entry sets
`#215` = 3, `#2305` = 50, `#2306` = 900, `#2577` = 1000, and two Stacks hold `#6252` = 20 and
`#1657` = 4, both times the mech's scale.

1. `#842e` on, charge to 0, and node 17 sends **`016C.025`** to the pilot: she leaves at once.
2. A Wait of `#215`, 3 s (node 16), with alias `0175.0C8` and the warning ring (node 49).
3. Node 8 searches `#6252` m around a point 1.3 m above the mech, with sight, for entities with
   filter bit 6 and neither bit 2 nor 4, that pass `STU_A58C1E63` against the mech. Its distances
   go to `#9326`.
4. For each one found, node 24 gives the damage for its distance `d`: `#2577` within `#1657`,
   else `#2305 + (#2306 - #2305) * (#6252 - d) / (#6252 - #1657)`. That is 1000 inside 4 m,
   then 900 falling to 50 at 20 m. Node 22 is the hit, with two knockback rings.
5. With the blast, effect `1703`, and node 18 sends `0592.025` to
   `STU_0AC15F78(STU_F76C486C(self))`. In the pilot's graph that message fills her charge
   (node 77): Call Mech is ready.
6. 0.3 s later **node 13** destroys the mech.

## What ORPHEUS does

`src/ow174/game/dva.py`, with the hooks in `script/nodes.py` and `script/driver.py`. Each point
below is a **reconstruction** where it leaves the data.

1. **One body.** A change of form is `Match.switch_hero` with a place: a new body entity of the
   same hero, components 44 and 75 moved to it. The data keeps the pilot and adds the mech. This
   follows what the client was seen to accept in the earlier offline service.
2. **The owner's hero.** `STU_F7262B36` and `STU_F76C486C` answer with the player's entity.
   `STU_0AC15F78` answers with the player himself, since no pilot sits in the mech, and
   `BodyWorld.send_message` hands what the graphs send there to the body he plays now. `016C`
   from the mech he plays is the eject.
3. **A destroyed mech** ejects her at once, alive, where it stood, and goes at once. The 1 s
   before the eject and the wreck's 2.75 s are not played: the server does not post `0011.025`.
4. **Self-Destruct.** The mech stays, and its body script runs on, no longer sent to anyone, on
   its owner's command clock. Its own Wait gives the fuse and its own node 13 removes it.
5. **The blast.** At node 8 the server hits each hostile body within the radius and in sight,
   once, with node 24's amount for its distance from the search point to its feet. The graph's
   own hit states, their knockback and tags, are not played. `STU_A58C1E63` is taken to mean
   hostile, and `STU_88687E54` is not computed, which takes the damage branch.
6. **Call Mech.** `STU_43DB06B1` answers "no spot" and `STU_54FEB21C` ends at once, so the mech
   stands at her feet 0.2 s after the press. It is a body nobody plays, placed as a bot's is,
   with movement records for its facing. Node 35's 250 is given as the blast's hits are. When
   state 19 begins the player gets a new mech body on that spot and the standing one goes: her
   own body's create carries movement state, and the standing one's cannot.
7. **A pilot on foot.** Node 7 does nothing for a pilot the server makes, and her `#6828` starts
   off.
8. **A dead pilot** returns in her mech after the mode's respawn time.
9. **Charges.** A new mech starts at 0. The pilot's copy of the mech's charge (`#2511e`) is not
   carried across.
10. **The Fusion Cannons.** Graph `050A` node 6 is a hitscan volley of 11 pellets of 2 a shot,
    in rings of 1, 4 and 6 at radius 0, 0.5 and 1 of a 4° spread (`#243`, which node 38 sets),
    damage falling from 10 m to 0.3 of it at 20 m, 6.7 shots a second on the server. A player's
    hitscan volley with authored rings now fires each pellet (`Combat._shoot` with
    `projectiles.launch_directions`, as the player's pellet projectiles already did). The count,
    radii and spread are the data's; the pellets' places around a ring (evenly spaced from a fixed
    start, no turn from shot to shot) are a reconstruction.

## Seen live against ORPHEUS

Original client, Practice Range, one player, five runs on 2026-11-12.

- **Self-Destruct.** The view is the pilot's at once: 150 health, the Light Gun, the ultimate's
  icon empty. 3 s later the screen flashes, both Training Bots ahead burst, "ELIMINATED TRAINING
  BOT" shows twice, and the icon is lit. The server log has the eject, the blast with three bodies
  in reach and two kills.
- **The Light Gun** fires, and its ammunition counts down from 20.
- **Call Mech.** She raises her hand, the mech drops in front of her, she climbs in through the
  hatch, and 1.75 s after it landed the view is the cockpit at 600 health, facing the way she
  faced.
- **Found and fixed on the way.** The pilot's screen was black until `#6828` started off. Call
  Mech turned the view to another heading until the standing mech got movement records: her
  boarding state turns her aim to the mech's yaw, and a body without a movement state has yaw 0.
- **Not seen.** The mech she left: she stood on its spot in most runs, and where the client draws
  it was not settled. A mech destroyed by damage, a dead pilot's return, a second player's view.
- **Seen and not settled.** From a ledge by the spawn room's exit the blast found nobody, with a
  Training Bot 8.8 m away in view from the cockpit. The search's point is 1.3 m above the mech's
  feet, and the server's rays from there were blocked. Whether the client's are is not known.
- **The cannons.** In those runs 12 s of held fire at a Training Bot 6 m away did not kill it: the
  server fired one ray a shot, one pellet of 2. With every pellet fired (point 10), a sixth run
  killed one 6 m away about 1.3 s after the fire began: "ELIMINATED TRAINING BOT", the kill feed
  line, the bot falling apart.

## Not decoded, not done

- The data's own arrangement in the client: the pilot kept and stowed, the mech driven. Component
  44 holds three entities (possessed, viewed, controlled), which may be how.
- What posts `0011.025`, what sets `#33688e`, and the wreck phase.
- The eject's launch (`STU_CFCE55DC`, node 83's hit) and the pilot's step back in Call Mech on
  the server.
- The landing-spot search `STU_43DB06B1`, and `STU_8CBDD81C`.
- The mech's charge kept over a time on foot, and its rise meanwhile: node 132 adds 5 a second
  to `#2511e`, up to the cost, while she has a player and the mech is not self-destructing.
- `#6868` and message `05D1.025`, which keep a pilot from making a mech at all.
- Which of the stowed branch's states kept the Light Gun from firing before the driver was
  answered.
