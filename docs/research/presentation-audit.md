# Presentation audit: D.Va, Mercy, Sombra

Build 1.74.0.0.104319. Run on 2026-11-13 in the test harness and in the original client, on the
`kjs` branch of this repository; see the [index](README.md) for the evidence labels. What "the
server turns on" below is that branch's server.

## Summary

For each ability of the three heroes: which presentation states its graphs hold (animation, voice
line, animated camera, HUD presenter, effect), which of them the server turns on when the button
is pressed, and what the client showed. The audit found one dead ability, D.Va's Defense Matrix,
whose cause was a misread ruleset value and is fixed. It found no voice line state in D.Va's or
Sombra's ability graphs, and no animated camera state in any graph the three bodies run. Sound
was not recorded, so no voice line is confirmed either way.

## Method

A state that the server turns on goes to the owner's client with the body's next frame, and the
client plays it. A client-only node runs in the client when the state around it arrives. So the
server's part of an ability's presentation is the set of states it turns on.

The harness run puts the hero in a flat arena with one enemy 8 m ahead and one ally beside,
presses one button for 45 frames, waits 240 frames, and lists the presentation states that came
on: `STUStatescriptStateAnim`, the voice line state `STU_40E05F6B`,
`STUStatescriptStateAnimatedCamera`, `STUStatescriptStateUXPresenter` and `STU_E13B30A8`, the
effect states `STU_691BFA55` and `STU_7CA2FBAF`, cosmetic entities and gamepad rumble. The meter
is filled before the ultimate's button.

The client side is from recordings in the Practice Range, one player.

## What the graphs hold

| Body | Animation | Voice | HUD | Effect | Camera |
|---|---|---|---|---|---|
| D.Va's mech | 24 | 7 | 25 | 17 | 0 |
| D.Va's pilot | 11 | 6 | 21 | 6 | 0 |
| Mercy | 16 | 9 | 28 | 15 | 0 |
| Sombra | 17 | 7 | 26 | 10 | 0 |

Six of each body's voice states are shared ones: five in graph `01CA`, all server-only, and one
client-only in the wheel's graph `02CF`. None of them came on in these runs. What is left:

- D.Va: one, in Defense Matrix's graph (`02CC` node 78), 0.75 s after a tag test. None in the
  graphs of the cannons, Boosters, Micro Missiles, Self-Destruct, the eject or Call Mech.
- Mercy: two in Valkyrie's graph (`10DA` nodes 34 and 36), and one in `0071` (node 82).
- Sombra: one, in graph `0C19` (node 106), on game message `0B75.025`. Nothing was found that
  sends it.

So a line such as the ultimate's call-out is not a statescript state for D.Va or Sombra. Where it
comes from was not looked for.

## What the server turns on, and what the client showed

**D.Va's mech**

| Ability | Server turns on | In the client |
|---|---|---|
| Fusion Cannons | 2 animations (`050A`) | Cannons flash, tracers, the Training Bot dies. |
| Defense Matrix | 3 animations, 4 effects (`02CC`), since the fix | The green grid for its 2 s, twice. |
| Boosters | 3 animations, 1 effect, 1 HUD presenter (`047E`) | The mech flies; the icon counts down. |
| Micro Missiles | 2 animations, 2 effects (`1128`) | The cooldown starts. The volley itself was not looked at. |
| Self-Destruct | 1 animation, 2 effects, 3 HUD presenters, 1 cosmetic entity | The warning, the eject, the blast, two kills. |
| Quick melee | 2 animations (`0043`) | Not looked at. |

**D.Va's pilot**

| Ability | Server turns on | In the client |
|---|---|---|
| Light Gun | nothing | It fires and its ammunition counts down. |
| Call Mech | nothing on her own body in this run | She raises her hand, the mech drops, she boards. The view leaves her eyes for the drop. |

**Mercy**

| Ability | Server turns on | In the client |
|---|---|---|
| Staff, heal | 1 effect (`006B`) | The yellow beam. |
| Staff, boost | 1 effect (`006B`) | The blue beam. The boost does nothing on this branch. |
| Guardian Angel | 1 animation (`006E`) | She flies to the ally. |
| Resurrect | nothing: nobody was dead | Not looked at. |
| Valkyrie | 1 animation, 1 effect, 1 HUD presenter, 2 voice states (`10DA`) | Not looked at on this branch. |
| Angelic Descent | 1 animation on jump and on crouch | Not looked at. |

**Sombra**

| Ability | Server turns on | In the client |
|---|---|---|
| Machine Pistol | nothing | It fires. |
| Hack | 2 animations, 1 effect, 1 cosmetic entity (`0B1B`) | The hand, the tether, the icon's count. |
| Stealth | 1 animation, 1 effect | The purple tint. |
| Translocator | 1 animation, 1 cosmetic entity | Not looked at on this branch. |
| EMP | 1 animation | Not looked at on this branch. |

## Found by the audit

- **Defense Matrix was dead.** Its ability was never enabled and its energy never filled. The
  cause was the read of a ruleset value named on another entity, which the server answered with
  0 in 49 nodes. Fixed; see [D.Va's two forms](dva-forms.md).
- **The camera is not a state.** No graph of the three bodies holds an animated camera state.
  The view that leaves her eyes when the mech drops comes from somewhere else, and it plays.
- **Many states never came on**: 14 of 24 animations for the mech, 11 of 16 for Mercy, 8 of 17
  for Sombra, and 19 or 20 HUD presenters for each. Most sit behind conditions this run does not
  meet (a hit taken, a kill, a death, a dead ally). They were not gone through one by one.

## Not done

- Sound. The machine's capture devices are a microphone and mixer buses that may carry it, so
  nothing was recorded, and no voice line is confirmed.
- What another player sees: third-person animation needs a second client.
- The never-on states, one by one.
- Valkyrie, the Translocator and EMP in the client on this branch.
