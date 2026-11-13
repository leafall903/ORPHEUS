# Ultimate charge: the game's own rules

Build 1.74.0.0.104319. Read from data on 2026-11-12, and seen live against ORPHEUS the same day;
see the [index](README.md) for the evidence labels.

## Summary

The rules are in the client's data. The heroes' common graph `004B` runs graph `0B8B`, whose
server-only nodes charge the ultimate: 5 points every second by themselves, and an amount carried
by a game message, each scaled by ruleset keys. The cost is in the same points.

ORPHEUS does not play those nodes. It charges by its own code, which filled every meter in 60 s.
The passive now adds 5 points a second, as the graph does. The charge from combat is still a
placeholder, and what the graph's messages carry is not decoded yet.

An earlier version of this note said no graph adds charge. That was wrong: the scan behind it
looked at a truncated list and at an XML dump that leaves expressions out.

## The cost

Read from data. A body's graphs set the cost into entity variable `#338`, in points. `004B` node 2
and `01C7` node 54 write it, and four StateStack states (`0FFD`, `1131`, `1132`, `11F3`) change
it while they are on. At spawn, by the server's own run of the graphs:

| Body | Cost |
|---|---|
| Lúcio | 2940 |
| Brigitte, Moira | 2800 |
| Soldier: 76, Bastion, Zenyatta, Baptiste | 2310 |
| Junkrat | 1925 |
| Mercy | 1820 |
| D.Va's mech (`0407`), Reinhardt, Winston | 1540 |
| Sombra | 1400 |
| Tracer | 1260 |
| D.Va's pilot (`040A`) | 319 |

All 32 playable heroes have a cost on their first frame, between 1260 and 2940 apart from the
pilot.

## Graph 0B8B

Read from data. `004B` node 17 (state 10) is a SubScript to `0B8B`: 87 nodes, 21 states. The
charge is entity variable `#342`. Nearly all of the graph is server-only.

### The passive

Entry node 2 starts state 0, a BooleanSwitch on

    #2426e && #342e < #338e && STU_F7262B36() && !#842e

Its true branch passes two more switches (state 14 on `STU_A3DEF368().id(11002)`, state 15 on
`#11942e`, both taken when false) into state 1, a Wait of 1 s (node 3). When the Wait ends, node 4
sets

    #342e = min(#338e, #342e + 5.0 * valueOr(#9532e, 1) * valueOr(#12918e, 1))

and links back to the Wait. So the meter gains 5 points each second while the switch holds.
`STU_F7262B36` is "somebody drives this body" (see [D.Va's two forms](dva-forms.md)). `#2426e`
is held on by graph `01C7` while one of the mode's standing messages reaches the body (see
[TrackTargets](track-targets.md)).

### What the hero does

Three message entries add an amount the message carries:

| Entry | Path | What the node sets |
|---|---|---|
| `00EB.025` | nodes 11, 18 | node 19: `clamp(#342e + #499 * valueOr(#9532e, 1), 0, #338e)` |
| `0044.025` | nodes 34, 80, 85, 35, 21 | node 32: `min(#338e, #342e + #499 * valueOr(#9532e, 1) * valueOr(#13022e, 1))` |
| `0148.025` | nodes 54, 12, 5, 65, 28, 22, 17 and on | node 23: `min(#338e, #342e + #336 * valueOr(#9532e, 1) * valueOr(#13022e, 1))` |

On the way to node 23 the amount `#336` is reduced: node 84 takes `STU_A6B80C63()` off it down
to zero, node 60 halves it and node 98 takes a quarter off, each under a condition. Every path
also tests `#2426e`, and all three test `!#842e`.

So at the default scales one point of the amount is one point of charge. What each message
means and what its amount is are not established: who posts them is not decoded. Damage dealt and
healing done are the likely amounts, and a bonus for an elimination could arrive the same way.

### Full, empty and the HUD

- Node 76 sets `#342e = #338e` when `ruleset(#13024e)` holds, from messages `0013.025` and
  `0067.025` and from an Entry: a full meter at spawn.
- Node 27 does the same from state 5, on `#232e && #342e < #338e`.
- Node 48 (message `0610.025`) sets the cost, or the cost less 50 when `#7418` holds. Node 96
  (message `14F2.025`) sets `min(#338e, #32280 * #338e)`.
- StateStack states 11 and 13 hold `#342e` at 0 while their switches are false: `valueOr(#9530e,
  True)`, and `valueOr(#10482e, True) || valueOr(#10480e, True)`.
- State 8 keeps `floor(100 * #342e / #338e)` in `#7730e`, which fits the meter's number being
  rounded down.

Outside `0B8B`, 41 SetVar nodes in 38 graphs set `#342e` to 0, the reset when an ultimate is
used. D.Va's graphs `026A` and `0276` also write it when she changes form.

## The ruleset keys

Read from data. The hero ruleset schema `0007` holds the keys `0B8B` reads, with these defaults:

| Key | Variable | Default | Where `0B8B` uses it |
|---|---|---|---|
| `253C` | `#9532` | 1.0 | every add |
| `3276` | `#12918` | 1.0 | the passive |
| `32DE` | `#13022` | 1.0 | the adds of nodes 23 and 32 |
| `32E0` | `#13024` | false | the full meter at spawn |

Rulesets `0037` and `0038`, named "Total Mayhem - Hybrid" and "Total Mayhem - Payload", set
`253C` to 1.5.

## The Practice Range

Read from data. ORPHEUS runs the Practice Range as mode `0018`. That mode's schemas are `0060`,
`0006` and `0008`, and none holds a key of the table above. Schema `0060` has three keys of its
own: `6C03`, which only the Training Bot's graph `0BA8` reads, `6B7A`, which no graph found
reads, and `730A`, which two graphs outside the bundle name (`265F`, `26F8`). The ruleset named
"Practice Range" (`0034`) is written for mode `005A` and sets one mode variable, `254C`. No
ruleset names mode `0018`. So no ultimate key is changed for the Range, and two Range-only keys
are unidentified.

## Outside the data

Community sources for Overwatch 1 give the same passive, 5 points a second for every hero: a
[Blizzard forum thread](https://us.forums.blizzard.com/en/overwatch/t/ult-charge-points/338580)
and a [mechanics guide](https://www.furiouspaul.com/overwatch/basics/mechanics.html).

## Compared with ORPHEUS

The server's body does run an instance of `0B8B` (checked on six heroes), but the authored loop
does not start there. `#2426e` is unset on an idle body, and with it held on, as the target
searches of Mercy and Sombra do, the loop still stayed off and the meter gained exactly 5 points a
second. Why the switch stays off is not established. ORPHEUS charges by its own code instead.

Its passive filled any meter in 60 s whatever the cost, in every mode. It now adds 5 points a
second by default. A body's time to a full meter is its cost over 5, resolved once when the body
spawns: 462 s for Soldier: 76, 364 s for Mercy, 308 s for D.Va's mech, 280 s for Sombra, 64 s for
D.Va's pilot.

Differences from the graph that remain:

- The server adds a little every frame, where the graph adds 5 once a second.
- None of the graph's conditions is applied but the cost: `#2426e`, `#842e`, the driver test and
  the two inner switches are not read, so the server may charge where the game does not.
- The ruleset keys are not applied. ORPHEUS loads schema defaults only, so each would read 1.0.
- A graph that changes the cost later keeps the time resolved at spawn.
- The charge from combat is a placeholder in `ult_charge.py`: 380 damage fills any meter and an
  elimination adds 20%. The graph adds a point for a point of a message's amount; until the
  senders are decoded it is not shown what damage, healing or an elimination give.
- Reaper, Hanzo, Pharah and Winston have no ultimate state the server finds, so their passive
  never pauses while their ultimate runs. That is older than this change.

## Seen live against ORPHEUS

Before the change, 2026-11-12: in recordings of Junkrat in Uprising and of D.Va, Mercy and Sombra
in the Practice Range, the meter climbed about 2% a second with little or no fighting.

After the change, the same day, Practice Range, Soldier: 76 standing still in the spawn room. The
meter read 0%, 6%, 12%, 19%, 25% and 27% at 3, 30, 60, 90, 120 and 129 s after the pick. Five
points a second against 2310 gives 0.6%, 6.5%, 13.0%, 19.5%, 26.0% and 27.9%, which the meter shows
rounded down. Not checked: another hero, another mode, and the meter in a fight.

## Not decoded

Who posts the messages `0012`, `00EB`, `0044`, `0148`, `0011`, `0013`, `0067`, `0610` and `14F2`
and what each means, why the server's instance of the loop stays off, and the evaluators
`STU_A3DEF368`, `STU_A58C1E63`, `STU_25ECB55F` and `STU_A6B80C63`.
