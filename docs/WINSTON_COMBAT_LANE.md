# Winston combat lane (build 104319)

Hero 02E0000000000009; body 04000000000003C0. Acceptance remains OPEN.
Baseline 100c66c45bea81083a410a4ba13830ab5532ca93. No original-client
combat acceptance has been performed for this change.

## Implemented partial restoration

Actual 00FE weapon graph st10 TrackTargets and st13 ModifyHealth must both
be active. The server evaluates the authored radius, angle and amount, then
routes continuous damage through Combat._damage, including real pools,
notices, statistics, kill attribution and retained-NPC death handling.
One simulation command frame is consumed at most once per current body.
Body retirement clears the guard. Allies are excluded by the ordinary match
team rules; FFA retains the ordinary FFA semantics. No healing is converted.

DATA from the external 104319 graph bundle: radius evaluates to 8, var243
angle to 40, and amount to 60. The loadout is 0B9000000000002F. Graph 00FE
is a target-search/ModifyHealth weapon, not a WeaponVolley hitscan.

RECONSTRUCTED, NOT NATIVE-VERIFIED: angle as cone half-angle, capsule-width
cone edge, source at the existing combat eye, nearest capsule-axis height,
amount per second, line-of-sight to that axis point, and 20% beam reduction
while armour remains. These require original-client/native comparison. The
beam armour branch prevents applying five points of per-hit mitigation on
every 16 ms continuous-damage slice; existing discrete damage is unchanged.
Native target-search payloads are still empty, so visible electricity locks
and their replicated target list are not established by this server change.

## Acceptance matrix

Cells name actual evidence, not a completion claim. NPC test means a real
Bot actor with the training definition, NOT the five PVE combat classes.

Action | Enemy player | Enemy NPC | Ally | Original client
--- | --- | --- | --- | ---
Tesla Cannon | Actual input/state -> pools, multi-target, kill tested | Bot pools/death/corpse tested | no damage tested | OPEN
Quick melee | Existing shared 0043 implementation; Winston-specific test OPEN | OPEN | existing enemy filter only | OPEN
Jump Pack movement/launch/landing/knockback | OPEN | OPEN | OPEN | OPEN
Barrier Projector placement/HP/lifetime/collision | OPEN | OPEN | allied fire semantics OPEN | OPEN
Primal Rage activation/health/expiry | OPEN | OPEN | OPEN | OPEN
Primal Rage punches/knockback/Jump reset | OPEN | OPEN | OPEN | OPEN
Taking weapon damage | direct damage/pool chain tested | direct Bot damage/pool chain tested | friendly-fire acceptance OPEN | OPEN
Death/respawn | death suppresses Tesla tested | retained NPC not replaced tested | n/a | OPEN

NPC class | Winston -> NPC | NPC -> Winston | corpse/client
--- | --- | --- | ---
Trooper | OPEN | OPEN | OPEN
Enforcer | OPEN | OPEN | OPEN
Sniper | OPEN | OPEN | OPEN
Assassin | OPEN | OPEN | OPEN
Heavy Assault | OPEN | OPEN | OPEN

The prepared runtime at this baseline does not expose those five actors in
bots.table()['heroes']; parent owns their production spawn/control entry.
Do not relabel training-Bot tests as these five NPCs.

Cross-hero interaction | Status
--- | ---
ordinary health/armour and enemy/team filtering | partial actual-pool tests
static world occlusion | server boundary test; native geometry OPEN
barriers (including Winston bubble), projectile/beam pass rules | OPEN
Deflect and Defense Matrix beam immunity/pass-through semantics | OPEN
invulnerability, stun, hack, sleep, freeze and damage modifiers | OPEN
healing/ally support, armour overflow and timing | OPEN
native target list/electric arcs/audio/HUD/ammo/reload | OPEN

## Repeatable checks

From this clone, with PYTHONPATH equal to this clone and ORPHEUS_RUNTIME_DIR
set to D:\overwatch 1.7.4\runtime-104319, use the original .venv Python:

    python -B -m unittest discover -s tests -p test_game_winston.py -q
    python -B -m unittest discover -s tests -p test_game_combat.py -q
    ruff check ow174/game/combat.py tests/test_game_winston.py

Results: 12 Winston tests, 28 existing combat tests, Ruff pass. The environment
has no pytest module, so unittest (the repository's test implementation) was
used. No tests or runtime data were copied into the shared runtime directory.

## Client and integration

Observed an existing Overwatch process PID12292/window title 守望先锋, without
an attributable lane lease. Process command line/path were not readable.
No lease was acquired, client was not controlled, and no normal service was
stopped or reconfigured. This is not a running client acceptance session.

The lane stays on private clone main. Parent must review/apply the patch;
there is no root-main merge or push. Publication and exporter execution are
left to parent integration as required by lane isolation.
