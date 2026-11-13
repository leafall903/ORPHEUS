# Shot origin and client position

Build 1.74.0.0.104319. Seen live between 2026-11-08 and 2026-11-10 in a separate offline service;
see the [index](README.md) for the evidence labels and the address convention.

## Summary

A report that shots stopped landing where the bots were drawn turned out to be the shooter's
position, not the targets'. The work left four results that apply to any server for this build:
the per-hero eye heights and a live check of them, a live check of the click aim and the view
delay, the layout of the client's own mover object, and a measure of how well the ported client
mover tracks the client.

## Eye heights

Read from data: each hero body's `STUFirstPersonComponent` has `m_E29993A5` (standing) and
`m_816B830F` (crouched), in metres from the feet to the first-person camera. The component's own
defaults are 1.55 and 1.15.

Seen live for Soldier: 76, both stances: the server's hit volumes were drawn over the game window
as seen from the server's eye, at feet + 1.55 m standing and feet + 1.085 m crouched. They sat on
the drawn bodies in both stances, near and far. With one 1.6 m eye for both stances, the crouched
volumes sat about 0.5 m low. So the two fields are the camera heights that shots should start
from.

Not matched: the client's camera eases between the two heights, and the service switched at
once.

| Hero | Body | Standing | Crouched |
| --- | --- | --- | --- |
| Ana | `1424` | 1.25 | 0.875 |
| Ashe | `251C` | 1.445 | 1.0115 |
| Baptiste | `29A6` | 1.35 | 0.945 |
| Bastion | `01FD` | 1.82 | 1.35 |
| Brigitte | `1B1B` | 1.58 | 1.2 |
| Cassidy | `02A3` | 1.425 | 0.9975 |
| D.Va | `0407` | 1.74 | 1.74 |
| D.Va (pilot) | `040A` | 1.32 | 0.8 |
| Doomfist | `1235` | 1.5 | 1.2 |
| Echo | `2593` | 1.7 | 1.1 |
| Genji | `0225` | 1.2 | 0.84 |
| Hanzo | `0141` | 1.35 | 0.945 |
| Junkrat | `0380` | 1.25 | 0.875 |
| Lúcio | `03FC` | 1.175 | 0.8225 |
| Mei | `08BA` | 1.3 | 0.91 |
| Mercy | `00E4` | 1.45 | 1.015 |
| Moira | `1C46` | 1.7 | 1.1 |
| Orisa | `1498` | 1.78 | 1.246 |
| Pharah | `0032` | 1.35 | 0.945 |
| Reaper | `0022` | 1.55 | 1.085 |
| Reinhardt | `0024` | 1.8 | 1.26 |
| Roadhog | `028F` | 1.6 | 1.12 |
| Sigma | `2AB1` | 2.1 | 1.75 |
| Soldier: 76 | `03CF` | 1.55 | 1.085 |
| Sombra | `121C` | 1.3 | 0.91 |
| Symmetra | `26F0` | 1.3 | 0.91 |
| Torbjörn | `28B6` | 1.0 | 0.7 |
| Tracer | `0001` | 1.25 | 0.875 |
| Widowmaker | `001C` | 1.5 | 1.05 |
| Winston | `01C0` | 1.5 | 1.05 |
| Wrecking Ball | `200B` | 1.83 | 1.5 |
| Zarya | `03BF` | 1.6 | 1.12 |
| Zenyatta | `0216` | 1.55 | 1.15 |

Only Soldier: 76 was checked live. The other rows are the data's values.

## Click aim and view delay

The command's click data and view delay follow the reading already in `game/commands.py`. Seen
live:

- Three single shots with the mouse still logged a click aim equal to the frame's aim,
  `(-32768, 0)` both. A swapped order would read `(0, -32768)`, so the yaw-first order holds.
- On loopback the command's delay field read 82 to 84 ms and the click's sub-frame varied (192,
  124, 255).
- The resulting view time was 20 to 28 ms behind the server's clock, about 6 cm for a bot at
  patrol speed. On loopback the eye and the click aim matter more than the rewind.

The client's own hit test computes the same view time (`0x5AEEC0`).

## The client's mover object

The client never sends its position. Its own mover object holds the simulated feet and the
command frame they belong to, which allows a read-only measure of server against client at the
same frame.

| Offset | Content |
| --- | --- |
| `+0x00` | Vtable, image offset `0x260E7C8`. |
| `+0x20` | Second vtable, image offset `0x260E880`. |
| `+0x70` | f32 x, y, z: the feet **after** that frame's command, always on the 1/1024 m grid. |
| `+0xA0` | u32: the command frame, equal to the frame of the command the server receives. |
| `+0xE0` | u32 7000: the command quanta in microseconds. |
| `+0x2A0` | f32 5.5, 4.95: run and backpedal speed for Soldier: 76. |

- The objects sit in an array `0x1610` bytes apart, 16 found per map. Free ones hold zero
  positions and default speeds (5.5, 5.5).
- No global in the image points at the array, and the entity pointers on the way are
  XOR-obfuscated. A reader has to scan private memory for the vtable, then keep the slots.
- A new body (respawn, hero switch) takes another slot, sometimes just outside the 16 found.
- The position and the frame are written separately, so a sample is good only when two reads
  agree.
- The client updates the slot about every 8 ms. Its other copies of the position are either
  render-interpolated or a 64-entry history ring with a timestamp and no frame.

Frame alignment, seen live: a strafe's input changed at frame 74106, and the slot tagged 74106
had already moved. So the tagged position is the state after that command, the same point as a
server mover after its step for that command.

For drawing server volumes over the game window: the vertical field of view is 70.5328 degrees
at every aspect ratio, in the largest centred 16:9 rectangle of the window.

## How well the ported mover tracks the client

Measured with the reader above against the character mover of the LizRollin runtime at revision
`9f8f3bc`, before ORPHEUS's later changes to it:

- A 57 s walk in the Practice Range as Soldier: 76: at most 0.039 m apart, and 0.000 m at the
  end.
- That is dead reckoning alone, from the commands. Owner corrections were added afterwards for
  every hero but D.Va and put the client on the server's state whenever the two differ.

For comparison, the flat mover it replaced was 1.85 m off after a ten-leg walk. Its largest
single error was the backpedal: the client backs up at 0.9 of the run speed (4.95 m/s for
Soldier: 76, from the mover object itself).

Other client values measured on flat ground, for checking a mover:

| Input | Client |
| --- | --- |
| Forward | 5.5 m/s steady. Speeds up in about 40 ms and slows down in about 60 ms. |
| Backward | 4.88 to 4.95 m/s. |
| Strafe | 5.5 m/s. |
| Forward and strafe | 5.55 m/s. |
| Backward and strafe | 5.14 m/s, bent about 49 degrees from straight back. |
| Crouched, either way | 2.95 to 3.0 m/s, with no backpedal penalty. |
| Jump | Start speed 5.95 m/s, gravity 17.43 m/s², apex 1.014 m, 0.69 s in the air. |

The client's movement heading stayed within 0.13 to 0.3 degrees of the command's yaw.

## A dead end worth knowing

Per-frame movement records for the player's own body, stamped at the command frame, put the
camera several metres under the floor. The record is a delta against a zero state (`0xC5F680`,
about 20 fields), so every field left out reaches the client's mover as zero. Stamping itself
works: a record's frame is the packet frame minus a u32 (`0xC66D00`). The client also registers
`debug.DisableErrorCorrection`, and its reader (`0x8013D0`) blends a corrected position in over
time.

## Compared with ORPHEUS

_Written against the `kjs` branch of this repository; see the [index](README.md)._

- `game/combat.py` uses one standing eye height, 1.55 m, for every hero, and a crouched height
  for Soldier: 76 alone (1.085 m, others 1.15 m). It marks both as unverified. This note
  verifies Soldier's two values and gives the other heroes' values. Torbjörn's shots would
  start 0.55 m too high and Sigma's 0.55 m too low. The same constant is the `eye` of every
  combat target, which the critical-hit rule reads.
- `game/commands.py` already decodes the view delay and the click aim the way this note saw
  them work.
- `game/mover.py` descends from the mover measured above. The measurement was not repeated on
  ORPHEUS's version.

## Open

- The other heroes' eye heights were not checked live.
- The crouch transition of the camera.
- The tools used here, a read-only reader of the mover object and an overlay that draws the
  server's hit volumes over the game window, are not in this repository.
