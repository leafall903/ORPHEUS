Body-root source defaults
=========================

Original104319 BE8E10 enumerates a component's body graph definitions. All four
qualified direct callers64E1FA/A125EE/A15BC1/BE9287 load their source from the
sameRVA3241288 invalid EntityID0. A15BB5 is an entity namespace gate, not a hero
branch. BE8E10 savesR8d at entryRSP+18; its stack delta88 makes thisRSP+A0 at
BE8F84/BE9064. Each BE9DE0 request forwards that source as argument6. BE9EC7 /
BE9F02 load it intoR9d for component facade70/78, whose wrappers forward it to
GameVM977F20. At97815A..97817A zero selectsD0D9B0's actual VM owner+B0 before
1B94B10 synchronously writes Entity001B/cache+234. Source selection is independent
of the authority/replication role bit carried separately by the wrappers.

create_body_graphs now applies that default to every root it allocates, before
caller initialization. This replaces the Reaper/Ashe/B.O.B. allowlist while
retaining their established owner sources. BotScript's explicit source_entity
is still applied afterwards, before init, and therefore wins. Created D.Va0407
roots use the mech entity; its separate Pet owner/controller and source_body
remain the pilot entity/body. No descriptor is made explicit: native implicit
SELF still selects the script's body, independent of viewer affiliation.

The current composed data inventory has32 hero,30 entity and19 Junkenstein
records, with no authored initial001B overrides. That is a read-only definition
inventory, not an all-hero execution matrix. The three focused methods construct
Soldier/Mei players, actual hero-pick created and controlled D.Va plus pilot,
and ordinary1403 NPC/default versus explicit source. They check cache, typed001B
and unchanged implicit descriptors on actual declared roots. Against the prior
allowlist, the same cases fail47 source assertions with zero test errors.

Only create_body_graphs changes. Generic Component.create, create_initial_graphs
(including separate AI controller setup), dynamic PlayScript sources, world
ownership and transport are unchanged. No original allocator execution, native
render/palette acceptance, pressure test, backend restart or client run is claimed.
Original instruction receipts and audit logs remain external lane0002 evidence.
