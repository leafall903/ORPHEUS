Reaper root allocation source
=============================

Original 104319 root allocator977F20 reads its supplied source at97815A,
compares it against the invalid EntityID atRVA3241288 (captured value0),
and falls back throughD0D9B0 to the actual component owner at978169..978171.
At97817A it calls1B94B10, synchronously seeding typed instance builtin001B
and cache+234 before initialization. This is root allocation, distinct from
97AA20's new PlayScript allocation and its separately supplied instigator.

create_body_graphs now seeds only Reaper's declared initial, manager and weapon
roots before their init calls. Existing Ashe/B.O.B. cases remain. It does not
change dynamic PlayScript source policy, generic Component.create, descriptor
encoding, body ownership or team metadata. Reloads that omit initial roots still
seed the manager/weapon roots they actually allocate.

Missing/zero descriptor source already selects the receiving script's owner
body in the original allocator. A Reaper body root therefore needs no explicit
source descriptor. The bug established here is the server VM's zero builtin/cache
versus that native allocation contract; no rendered color failure is inferred.

The focused regression uses actual encrypted in-memory SYN/ownership/hero-pick,
create/full ACK and command/delta paths. It checks ten original root graph IDs,
typed001B/cache and implicit descriptor fallback for self/enemy/ally. An existing
observer is reset and reintroduced with remote_body_create/current full frames;
this is not a new connection case. Source body team bits remain8388608 for every
viewer in the explicit synthetic2v1 scene. Removing only the Reaper source seed
makes the same final method fail16 source assertions; the fix passes1 method.
Native render/palette, full allocator execution and live client acceptance remain
unverified. Original byte receipts and test logs are external lane0002 evidence.
