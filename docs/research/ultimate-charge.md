# Ultimate charge: the game's own rules

Build 1.74.0.0.104319. Read from data on 2026-11-12, and seen live against ORPHEUS the same day;
see the [index](README.md) for the evidence labels.

## Summary

The rules are in the client's data. The heroes' common graph `004B` runs graph `0B8B`, whose
server-only nodes charge the ultimate: 5 points every second by themselves, and an amount carried
by a game message, each scaled by ruleset keys. The cost is in the same points.

ORPHEUS does not play those nodes. It charges by its own code, which filled every meter in 60 s.
The passive now adds 5 points a second, as the graph does. The charge from combat is still a
placeholder. The graph gives a point for a point of damage dealt and of healing done, and has no
entry for an elimination.

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

Read from data. Four message entries add an amount the message carries:

| Message | Who posts it | What it carries | What `0B8B` sets |
|---|---|---|---|
| `0012.025` | no graph: the engine. 124 entries in 109 graphs take it | damage dealt: the amount (`0013.0A4`), the body hit (`0015.0A4`), the hit's tags (`003D.0A4`) | node 23: `min(#338e, #342e + #336 * valueOr(#9532e, 1) * valueOr(#13022e, 1))` |
| `0148.025` | no graph. Only `0B8B` takes it | the same three parameters | node 23 too, and only when the tags hold identifier `090D` |
| `0044.025` | no graph: the engine. 16 graphs take it | healing done: the amount (`0040.0A4`), the body healed (`003F.0A4`), a kind (`0041.0A4`) | node 32: `min(#338e, #342e + #499 * valueOr(#9532e, 1) * valueOr(#13022e, 1))` |
| `00EB.025` | eight SendGameMessage nodes: `006B` 189, `0B9E` 80, `0CBC` 28 and 135, `0D1D` 5, `0F9D` 106, `157F` 14, `22F0` 3 | an amount (`002A.0A4`) | node 19: `clamp(#342e + #499 * valueOr(#9532e, 1), 0, #338e)` |

What the two engine messages are is read from their other listeners and from ORPHEUS's own code.
Reaper's graphs `0016` and `0019` take `0012` and add its amount to a counter. Graph `0071` takes
`0044`, keeps a sum by the body named in `003F.0A4` and tests that this body is not the owner.
And `junkenstein_ai.summoner_damage_event` already feeds applied damage to the Summoner's `0B8B`
as `0012` with exactly these parameters.

The damage path, from `0012` to node 23:

1. Node 53: the body hit must pass one of four tests. It carries filter bit 0, or target tag
   `02ED`, or it is hostile and carries one more tag, or it is hostile and passes a condition.
   Bit 0 is what the server gives a hero's body (see [TrackTargets](track-targets.md)).
2. Nodes 30 and 36: the hit's tags must not hold identifier `01FD` or `025A`.
3. Node 5: the body hit must not have its variable 6442 set. Node 65: `#12321e` must be off.
4. Node 28: `STU_A58C1E63` of the body hit and the owner, the body hit without its variable 602,
   and `!#842e`. Node 22: `#2426e`.
5. Node 84 takes `STU_A6B80C63()` off the amount, down to zero. Then node 60 halves it or node
   98 takes a quarter off, each under a `STU_25ECB55F` test, or neither.
6. Node 23 adds what is left.

The healing path, from `0044` to node 32: the kind must not be identifier `0292` or `025A`, the
body healed must not have its variable 17625 set, then `!#842e` and `#2426e`.

So at the default scales a point of damage dealt to a hero's body, or of healing done, is a point
of charge, before the reductions of step 5. No entry of `0B8B` takes an elimination, and none of
the eight senders of `00EB` was read for what it grants.

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
  elimination adds 20%. The graph gives a point for a point of damage dealt to a hero's body and
  of healing done, with the tests above, and nothing for an elimination.
- Feeding a player's `0B8B` the damage message, as the Summoner's is fed, adds nothing yet: with
  the gate held on, one `0012` of 100 damage left the charge where it was. Node 53's filters are
  the likely stop, since `STU_AC829876` is answered only in Hack's graph on a player's body.
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

What `0148` is and who posts it, what each of the eight senders of `00EB` grants, the native
code that posts `0012`, `0044`, `0011` and `0013`, why the server's instance of the loop stays
off, and the evaluators `STU_A3DEF368`, `STU_A58C1E63`, `STU_25ECB55F` and `STU_A6B80C63`. By
their use here, `STU_A58C1E63` reads as "hostile to" and `STU_25ECB55F` as "carries this target
tag"; neither was read in code.
