# Tracer combat lane (0003)

Target: Overwatch 1.74.0.0.104319, hero `02E0000000000003`, body
`0400000000000001`. **OPEN: full hero combat is not accepted.**

## Verified server change

The actual pistol graph 0016, state8, reports 20 shots/second, two projectiles per
shot, six damage/projectile and two ammunition consumed per shot. The old combat
consumer resolved only one bullet per report. It now resolves each hitscan bullet
separately so armour mitigation applies per bullet. An unobstructed close-range
40-round magazine deals 240 body damage in the controlled fixture, versus 120
before the fix. This is resource-backed counting, not a reconstruction of spread.

All bullets still share the command's ray. Authored spread, muzzle origins,
critical-hit defaults and animated hitboxes need separate evidence. Other heroes'
multi-projectile hitscan reports also use the count; their complete weapon
behavior is not accepted by this patch. Ballistic projectile counts are unchanged.

## Acceptance matrix

S = selected server fixture passed; O = open, including original-client result.
NPC columns mean the specific requested class, not a training-bot substitute.

| Action | Enemy player | Trooper | Enforcer | Sniper | Assassin | Heavy Assault | Original client |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pulse Pistols body damage | S | O | O | O | O | O | O |
| Reload and subsequent fire | S | O | O | O | O | O | O |
| Critical hits / spread / falloff | O | O | O | O | O | O | O |
| Quick melee | S | O | O | O | O | O | O |
| Blink displacement / collision / charges | O | O | O | O | O | O | O |
| Recall position / health / ammo / cleanse | O | O | O | O | O | O | O |
| Recall invulnerability / targeting | O | O | O | O | O | O | O |
| Pulse Bomb throw / stick / fuse / explosion | O | O | O | O | O | O | O |
| Ultimate gain / consumption | O | O | O | O | O | O | O |
| Incoming primary fire | S (Soldier) | O | O | O | O | O | O |
| Incoming abilities / control / death | O | O | O | O | O | O | O |
| Outgoing death / notices | S | O | O | O | O | O | O |
| Manual NPC corpse retention | N/A | O | O | O | O | O | O |

The additional training-NPC fixture kills with a paired shot and retains the body
past the normal respawn deadline. It disables NPC script delivery; it proves only
damage/counting/lifetime integration, not one of the five PVE classes or its AI.
The runtime AI spawner hero table inspected here contains training bots only.
The parent lane owns the requested NPC spawn/control interface.

| Cross-hero behavior | Evidence / remaining work |
| --- | --- |
| Armour | Two six-damage bullets remove six armour, not seven; fixture passes. |
| Health-pool shields | Five shield points are consumed before seven health; fixture passes. |
| Friendly fire | Same-team player remains undamaged in a non-FFA fixture. |
| Walls | Both bullets stop when the collision ray reports a nearer surface. |
| Death | First lethal bullet gives one kill/death notice; second gives no extra damage. |
| Barriers | O; health-pool shields do not establish barrier collision. |
| Deflect / absorption | O; no reflected/absorbed-projectile acceptance. |
| Invulnerability / status / control | O; generic `_damage` has no complete status gate. |
| Ally healing | No healing or team semantics changed by this patch. |

## Reproduction and results

Run from the lane's `source` checkout with `PYTHONPATH` pointing to that checkout
and `ORPHEUS_RUNTIME_DIR=D:\overwatch 1.7.4\runtime-104319`. Reuse the original
checkout's `.venv\Scripts\python.exe` without writing to its source or the runtime.

```
python -B -m unittest discover -s tests -p test_game_tracer.py -v
python -B -m unittest discover -s tests -p test_game_combat.py
python -B -m unittest discover -s tests -p test_game_projectiles.py
```

The eleven Tracer tests cover actual body graphs and player command processing:
paired damage, per-bullet armour, shields, lethal first bullet, whole magazine,
wall occlusion, team filtering, explicit reload, melee, incoming Soldier fire,
and retained training-NPC death. Twenty-eight existing combat tests and 37
projectile tests also pass (76 total). Ruff and `git diff --check` pass.

Exploratory command probes found no authoritative movement after Blink. Recall
and charged-ultimate probes produced no flight objects or VM fault, but those
negative observations do not fully characterize either skill. The known skill
graphs are Recall001A, Blink0024 and Pulse Bomb0034. No general State fallback
or fault-free execution is treated as a gameplay implementation.

## Client and delivery status

No original-client session was started or controlled, no lease was claimed, and
no client acceptance is asserted. Existing Overwatch PID12292 and original server
Python PID22924 were observed and left alone; absence of a lease does not make
those processes lane-owned. Runtime assets were read in place and not copied.

Development stays on the private clone's main. The integration commit and
format-patch are listed in the adjacent lane STATUS.txt. No root-source merge,
manual push or public deployment is performed by this lane.
