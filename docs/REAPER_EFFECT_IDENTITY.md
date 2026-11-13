# Reaper effect identity audit (104319, 2026-11-13)

Status: protocol identity checked; native enemy/friendly colors remain open.
This audit does not supersede the newly reopened Soldier Biotic Field or Mei
Cryo-Freeze reports. Those belong to their original hero lanes.

The original body0022 mounts graphs0020,0033,0044,0075,00F2,0043,004B,08B6,
with manager0015 and weapon0019. The extracted graph closure contains56 graphs,
60 visual-producing nodes and one unavailable referenced common graph0F9C.
This is a graph inventory, not a complete visual-resource dependency extraction.
External raw graph records and native disassembly remain outside Git.

| Item | Original producer / target | Identity and remaining work |
| --- | --- | --- |
| Hellfire guns | 0019 nodes1/2, CosmeticEntity0029, target self | Native local children represented by state handles. Both states reach owner/remote peers; native child owner/team inheritance not executed here. |
| Primary visual projectiles | 0019 node10/state6, entity004A,20 pellets | Authored visual entity reference exists, so projectiles are applicable. No separate persistent summon or barrier is implied. Client projectile construction/color not accepted. |
| Wraith Form | 0020 node12/state6 effect004A, node51 effect236C, target self | `m_teamColor` unset. State6 has no remote slot; remote clients receive the authored surrounding state graph. Native local activation/color remains open. |
| Death Blossom | 0044 node33/state13 effect00AC; node36/state15 visual entity1320; node12 effect0186 | Effect target self, teamColor unset. State13/15 reach existing remote peers. Native effect/projectile construction not accepted. |
| The Reaping | 0075 node23 effect199B, target self | No persistent soul summon inferred. Native healing-effect color remains open. |
| Shadow Step preview | 00F2 node13/state4, CosmeticEntity019D, out38, position432 | All three receivers get the same source body team and preview handle, including current late-bootstrap path. Native cosmetic owner/team inheritance remains open. |
| Shadow Step destination actor | 00F2 node74/state43, CreateEntity1F0E, input1883, output12105 | Actor output is absent in current host and owner/remote data. Native allocation/owner/faction unresolved; do not fabricate an actor or substitute body into12105. |
| Shadow Step actor effect | 00F2 node19/state10 effect0282, target12105 | Depends on the unresolved actor. Color cannot be accepted from state replication alone. |
| Shadow Step ancillary effects | 00F2 node26 effect01E7; nodes28/29 effects028B/028C; node57 effect0186; nodes107/108 effects3310/3311 on preview handle38 | Authored self, default or preview targets retained; teamColor unset. Native fallback/context must be confirmed before changing ownership. |
| Persistent summons/deployable shields/barriers | No hero-specific summon, deployable shield or barrier producer in these mounted skill graphs | Not applicable to those categories; Shadow Step1F0E and the two projectile definitions above are separately retained in scope. Shared cosmetic/emote graphs are indexed in the external closure. |

The in-memory encrypted three-peer check uses normal SYN, ownership, hero pick,
create/full ACKs and real command datagrams. Owner team0, enemy observer team1
and allied observer team0 all decode source body filter bits0x800000. The same
preview handle survives the existing remote_body_create -> create ACK -> current
remote full bootstrap and clears on cancel. The late check resets observer
models; it does not claim an additional real connection or UI join test.
The local evidence also covers one primary input, confirmed Shadow Step,
its current cast bootstrap, recovery, Wraith and Blossom. It is not an expanded
color acceptance matrix and does not execute a renderer.

Important unresolved source context: root instances0020,0044,00F2 and0019 report
source_entity()==0 in this host. Original source getter1B92E50 falls back to typed
builtin001B when its cache is invalid. Original effect code991426..99147A uses
that getter when its explicit context input is absent. This is a concrete source
investigation lead, not proof that every root should be overwritten with body.
Allocation-root semantics, explicit input mapping, client source initialization,
and the native final team/color consumer remain unverified. Do not hard-code a
palette, assert all effects are blue, or treat entity+AC copying as proof of a
team/color calculation.

The a3a aggregate of the initial4aaf regression failed because selector00F2/3
omitted m_B4802CE3 under the existing strict none+u8 writer. Its child repair now
supplies the original ctor/begin0 -> typed-confirm1 -> next-begin0 lifecycle only
for Reaper00F2/3/state0/remote0 with the original A1AAB8C4 input. The authored
primary-input link already dispatches that typed confirm; no second confirmation
or combat relocation path is added. The original4aaf assertion is retained.
The existing strict codec and missing-field rejection remain unchanged. The new
explicit state field is included in bodyVM and full-server snapshots. These
producer/serialization checks close that missing-byte defect, not rendered
colors. Native original build104319 UI was not used because no trusted
leased1.74 surface was available.
No client/server was started; no frozen source or previous force/hit/lifecycle
implementation was changed.

Repeatable guard: tests/runtime/test_reaper_effect_identity.py and synthetic
fixtures/behavior/reaper-effect-identity-v1.json check identity transport only.
The test intentionally makes no assertion about missing12105 or source0, so it
does not freeze those defects as expected behavior.
