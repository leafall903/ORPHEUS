# Ultimate charge: what the client's data holds

Build 1.74.0.0.104319. Read from data on 2026-11-12, and seen live against ORPHEUS the same day;
see the [index](README.md) for the evidence labels.

## Summary

The client's data holds each ultimate's cost and a ruleset multiplier for how fast it charges. It
does not hold the rates: no graph adds charge, so the passive trickle and the charge from damage
and healing are server code. ORPHEUS filled every hero's meter in 60 s. It now adds 5 points a
second, the community's figure for the passive, against the cost the data gives.

## The cost

Read from data. A body's graphs set the cost into entity variable `#338` from their Entries, in
points:

| Body | Cost |
|---|---|
| Soldier: 76 | 2310 (authored as 1650 times 1.4) |
| Junkrat | 1925 |
| Mercy | 1820 |
| D.Va's mech (`0407`) | 1540 |
| Sombra | 1400 |
| Tracer | 1260 |
| D.Va's pilot (`040A`) | 319 |

Sixteen nodes in the bundled graphs write `#338`. Four of them are StateStack states (`0FFD`,
`1131`, `1132`, `11F3`), so some graphs change a cost while a state is on.

## The charge

Read from data. The charge is entity variable `#342`. Fifty graphs of the installation name it.
In the bundled graphs the nodes that write it are SetVar actions in ability graphs, beside the
ability state and the HUD presenter that read it. None adds to it.

## The ruleset multiplier

Read from data, with the meaning inferred. The hero ruleset schema `0007` has key `253C` with the
default 1.0. No graph of the installation reads that key, so its consumer is not in the data. Two
rulesets set it to 1.5: `0037` and `0038`, whose names are "Total Mayhem - Hybrid" and "Total
Mayhem - Payload". Both also set key `2538` to 0.25, and 71 graphs read `2538`. Total Mayhem is the
mode with short cooldowns and faster ultimates, so `2538` reads as the cooldown scale and `253C` as
the ultimate generation scale. Neither name was found as a string.

## The Practice Range

Read from data. ORPHEUS runs the Practice Range as mode `0018`. That mode's schemas are `0060`,
`0006` and `0008`, and none holds `253C`. Schema `0060` has three keys of its own: `6C03`, which
only the Training Bot's graph `0BA8` reads, `6B7A` and `730A`. The ruleset named "Practice Range"
(`0034`) is written for mode `005A` and sets one mode variable, `254C`. No ruleset names mode
`0018`. So nothing in the data makes the Range charge faster than any other mode.

## Not in the data

The passive rate and the rates for damage and healing. Community sources for Overwatch 1 agree on
a passive of 5 points a second for every hero, with the cost being what differs between heroes:
a [Blizzard forum thread](https://us.forums.blizzard.com/en/overwatch/t/ult-charge-points/338580)
and a [mechanics guide](https://www.furiouspaul.com/overwatch/basics/mechanics.html). The data's
costs are in the scale those sources use, point totals in the low thousands.

## Compared with ORPHEUS

ORPHEUS's passive filled any meter in 60 s whatever its cost, in every mode, so that a server with
no combat rule still charged. That is 38.5 points a second for Soldier: 76.

It now adds 5 points a second by default. A body's time to a full meter is its cost over 5,
resolved once when the body spawns:

| Body | Passive only |
|---|---|
| Soldier: 76 | 462 s |
| Junkrat | 385 s |
| Mercy | 364 s |
| D.Va's mech | 308 s |
| Sombra | 280 s |
| Tracer | 252 s |
| D.Va's pilot | 64 s |

A graph that changes the cost later keeps that time, which is a shortcut. The ruleset key `253C`
is not applied: ORPHEUS loads schema defaults only, so it would always read 1.0.

The charge from combat is unchanged and is still a placeholder: 380 damage fills any meter and an
elimination adds 20%, against costs of 1260 to 2310 points. The same community sources give one
point for each point of damage.

## Seen live against ORPHEUS

Before the change, 2026-11-12: in recordings of Junkrat in Uprising and of D.Va, Mercy and Sombra
in the Practice Range, the meter climbed about 2% a second with little or no fighting.

After the change, the same day, Practice Range, Soldier: 76 standing still in the spawn room. The
meter read 0%, 6%, 12%, 19%, 25% and 27% at 3, 30, 60, 90, 120 and 129 s after the pick. Five
points a second against 2310 gives 0.6%, 6.5%, 13.0%, 19.5%, 26.0% and 27.9%, which the meter shows
rounded down. Not checked: another hero, another mode, and the meter in a fight.
