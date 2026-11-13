# Remote weapon fire

Build 1.74.0.0.104319. Seen live between 2026-11-02 and 2026-11-09 in a separate offline service,
on hero bots and on a stationary hero body used as a viewer target; see the [index](README.md)
for the evidence labels and the address convention.

## Summary

How a client draws shots fired by a body it does not own. The body's weapon graph reaches it as
a plain (H = 0) statescript frame, and each shot may also need a shot record and a projectile
entity. Which of those a weapon needs depends on its Volley's shot mode. Two findings concern
the wire itself: a variable binding in a plain frame names a remote slot, not a state, and a
datagram over the client's size is never received.

## Shot modes

Each `STUStatescriptStateWeaponVolley` has a runtime config whose mode (`+0x11C`) decides who
fires a remote shot. Read live from the client for 12 heroes:

| Mode | Heroes | What a shot record does (handler `0xAE7570`) |
| --- | --- | --- |
| 1 | Cassidy, Ana, Torbjörn, Zenyatta | Plays only the per-shot muzzle effect (`0xAE64F0`). |
| 2 | Roadhog, Reaper, Sombra, D.Va | Builds the shot itself, then the muzzle effect. |
| 3 | Soldier: 76, Mei, Bastion, Lúcio | Nothing: the Volley fires itself from its own update (`0xAE5D90`). |

These agree with the rule in `game/projectiles.py` (`_sync`) for the 11 heroes whose Volley data
was at hand. Sombra agrees only if her rate, the dynamic variable 8421, counts as 0 when the
mode is resolved. D.Va's mech graph was not checked against the rule.

A shot record reaches its Volley when the record's key equals the Volley's key and its event
equals the Volley's event counter (`0xB404B0`). Seen live for Cassidy: every record matched and
was consumed, and the Volley's shot index followed.

## Mode 3: one magazine per Volley start

- A mode-3 Volley fires while its shots-fired count is below a limit that equals the ammo
  variable 53 the server sent: 30 for Soldier: 76, 120 for Mei, 20 for Lúcio.
- After that many shots it stops until the Volley begins again. A Volley left on across reloads
  fired once, when the body appeared, and then never again, although every shot record was
  matched and consumed.
- A remote Volley needs nothing but variable 53 to fire. Sent with the Volley alone and no
  variable 53, there was no muzzle flash. With variable 53 and no other variable, there was.
- Ending the Volley while the body reloads and beginning it with the next magazine made
  Soldier's and Lúcio's fire show for every magazine.

## Mode 1: the shot is a server-made projectile

What draws a mode-1 shot is the Volley's `m_projectileEntity` (Cassidy `02A7`, Ana `15B7`,
Torbjörn `2A66`, Zenyatta `0236`; each has an `STUProjectileVisualComponent`), made by the server
and named in the shot record. Tried live, in order:

| Try | Sent | Drawn |
| --- | --- | --- |
| 1 | The entity alone, re-made every frame along its path | Nothing. |
| 2 | The entity alone, made once | Nothing. |
| 3 | The entity, a shot record that names it, and a projectile state with position, velocity and the shooter's body as source | Cassidy's tracer and muzzle star, Ana's darts, Torbjörn's rivets, on every shot. |
| 4 | As 3, without naming the entity in the record | Nothing. |
| 5 | The entity made under the map, named in the record, no projectile state | Cassidy's tracer as before. Ana shows only her muzzle flare, Torbjörn no rivet, Zenyatta nothing. |

So the record's link is what makes the client draw the entity, a hitscan tracer starts from the
record alone, and **a flying projectile also needs its projectile state at launch**.

The projectile state's reader (`0xD68980`) takes a "stopped" bit first. A stopped state carries
no velocity block. The service sent a stopped state at the impact point and destroyed the entity
half a second later.

## The shot record

`game/projectiles.py` writes the record for the same parser (`0xD81F80`). Two points were settled
live:

- **The pitch is positive up.** The parser turns yaw and pitch into `(sin yaw * cos p, sin p,
  cos yaw * cos p)` (`0xDB1360`). A record written with the pitch positive down drew every
  record-driven shot mirrored up and down: a record aimed straight up drew a blast on the
  floor. With the sign fixed, D.Va's tracers ran to the target's chest.
- **The event list needs its framing bits.** One event is `1 1 1`, the time, the record, then
  `0 0`. With only two leading 1 bits the client reads the state bit as set and crashes in the
  state parser on a null read.

## Variable bindings in a plain frame

Read from code: the binding reader (`0xA0D4F0`) checks the frame's H bit.

- H = 1: the bound state is `m_statesBitCount` bits wide and is a state index.
- H = 0 (branch at `0xA0D726`): it is `m_remoteSyncNodesBitCount` bits wide and is an index into
  the graph's remote-synced node list. The client then takes that node's state.

Seen live: Lúcio's weapon graph `0776` has 7 state bits and 6 remote bits. A plain frame that
bound variable 49 with a 7-bit state index put every later field one bit off. The client began
states that were never sent, and no gun drew. On Soldier's graph the client froze when his body
arrived, most likely from the same misread. Written as a 6-bit remote slot, both worked.

## Variable 49: where the fire starts

- A Volley's own aim fields are empty for these weapons. Shots and muzzle effects start at the
  weapon entity, variable 49, which is the output of the graph's CosmeticEntity state. With
  variable 49 unbound they fall back to the body's origin: the fire draws at the feet. That
  reading was an inference from the code, and the binding below bore it out.
- Binding variable 49 to the CosmeticEntity's remote slot, with the CosmeticEntity begun and
  variable 478 false (its gate is `var34 || var478`), put the gun in the hand and the fire on
  the gun. Seen live for Lúcio, Soldier: 76, Roadhog, Sombra and Ana.

| Hero | CosmeticEntity state / remote slot | Scope of variable 49 |
| --- | --- | --- |
| Mei, Lúcio, Sombra | 0 / 0 | Entity. |
| Cassidy | 27 / 16 | Entity. |
| Ana | 61 / 34 | Entity. |
| Roadhog | 16 / 9 | Instance, presence bit 12. |
| Soldier: 76 | 11 / 5 | Instance, presence bit 7. |

- Not solved: Torbjörn (his gate also reads variable 842), Reaper and Zenyatta (variable 49 is
  not a synced variable of theirs), Bastion and D.Va (no CosmeticEntity in the weapon graph).
- An effect state that plays on variable 49's entity crashes the client when variable 49 is
  unbound (a read at offset `0x1F8` of a null pointer). Seen for Mei's spray effects. Roadhog's
  muzzle effect state had crashed the client earlier with a read at the same offset.

## Mei's spray

Mei's primary Volley (state 13) has a subgraph with two effect states that play on variable 49's
entity: state 11 (remote slot 5) and state 12 (remote slot 6). A remote Volley does not begin its
subgraph, so the spray never showed, even with the Volley firing in the client. Beginning those
two slots while she fires (begin, then a 2-bit counter) drew the spray at her blaster.

## The Practice Range bots' shots

- The Range shooters (graph `0B92` to `0BA3`) fire through a Volley in mode 1, with projectile
  entity `1476`.
- `1476` holds a projectile-visual component, a model component without a model, and a child
  `148D`. The child holds only an effect component: the orange orb.
- Made by the server alone, `148D` rendered and `1476` did not. `148D` has no mover, so the
  client ignored movement records for it.
- The service re-made the orb every frame, one step further. That was before the recipe of
  try 3 above was found, and `1476` was not retried with it.

## Datagram size

Read from code: the client's transport accepts a datagram's sequence number (`0x3F9FC0`) before
it reads the payload, so a datagram that arrives is always acknowledged. One that is never
acknowledged did not arrive.

Seen live: a frame of 1237 payload bytes, a new bot's 20301 (about 1180 bytes) plus one entity
record, was never acknowledged. The server resent it forever and every later reliable message
waited behind it. With frames held to the 1230-byte payload, 14 body swaps ran with no resends.

## Compared with ORPHEUS

_Written against the `kjs` branch of this repository; see the [index](README.md)._

- **Bindings.** `statescript.write_bindings` writes a binding's state with `states_bits` in
  every kind of frame, and `script/decode.py` reads it back the same way, so the two agree with
  each other and a test built on the decoder passes. `remote_full_frame` and `remote_delta`
  reach it through `write_var`. By this note a plain frame that carries a bound variable is
  misread by the client whenever the graph's two widths differ, and names the wrong state when
  they are equal. `Graph.remote_bits` already exists for the other width. Not tested against
  ORPHEUS.
- **Flying projectiles for other viewers.** `projectiles._show_others` makes the entity under
  the map and names it in the shot record. As read, no projectile state goes out at launch.
  That is try 5: by this note other viewers would see a hitscan tracer but not a dart, a rivet
  or an orb. Not tested against ORPHEUS.
- **Datagram size.** `send_frame` already holds frames to `MAX_PAYLOAD`, and
  [PACKET_BUDGET.txt](../PACKET_BUDGET.txt) calls its evidence backend-only. This note adds the
  client side: a frame 7 bytes over was not received.
- **Shot record and modes.** Both already match what was seen live here.

## Open

- Zarya's beam for a remote viewer. Sending the Stacks, the two effect states and the bound
  CosmeticEntity of graph `0241` drew no beam.
- Soldier's Helix Rockets from a remote body: only a blast at the bot's position was seen,
  before the pitch fix. No retry after it is recorded.
- Gun binding for Torbjörn, Reaper, Zenyatta, Bastion and D.Va.
- The tools used for the live reads, a statescript tracer plugin and a read-only reader of
  Volley state, are not in this repository.
