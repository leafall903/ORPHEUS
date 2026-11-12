# Original optional NPC inputs

Root restores original0016 npc.Definition.mover and controller metadata. The
original and current MoverData schemas both contain the same34 fields, with no
missing/extra field mapping or invented defaults. Declared mover dictionaries
must contain all finite numeric fields and positive standing/crouching capsule
dimensions. Absent mover input retains the selected global body table.

Definition.controller is validated and retained as the original spawner hint.
The source test explicitly does not claim controller mounting from this hint.
The composed graph association remains the sole actual controller provision;
this restoration does not override it or allocate a second controller.

Spawn passes the explicit mover to the existing Bot. movement_data participates
in actual target capsule selection, route speed, manual/impulse mover creation,
navigation and movement spring encoding; respawn retains it. The same current
physical mover and modifier/Hook/Knockbacks lifetimes are used. Global runtime
packs are unchanged. Exact server Bot storage includes movement_data.

Restored four original0016 metadata test methods with unchanged assertions in
test_game_npc. Those and the existing NPC/manual/navigation suite pass73/73 in
20.943s. Logs: ../parallel-merge/root-original-npc-metadata-check.log.

Actual controlled-server probe spawns a real catalog NPC with explicit2.2 speed
and.7 radius, retains the exact data in its existing physical mover, mutates and
restores the whole-server stored digest, and rejects an unknown Bot field.
Probe/log: ../parallel-merge/root-npc-explicit-mover-server-probe.py/.log.
This does not claim that all frozen Sentry/support geometry checks pass; worker
C owns those original consumers. No listening service or original client was started.
