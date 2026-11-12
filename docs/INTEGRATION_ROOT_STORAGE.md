# Actual projected NPC storage boundary

The A90 actual encrypted-peer ServerSession projection fixture originally failed
strict whole-server capture. Identity tracing found two unintended private
cross-component aliases: the body/controller BotWorld weapon_buttons lookup
pointed to the same source-record dictionary, and the played0256 parameter array
held the caster variable's same Python Entity value record.

Each BotWorld now owns a dictionary with the same authored button values.
Barrier PlayScript routing copies Entity values into its existing native
parameter arrays before submitting to the same child component. Entity numbers,
actor/Component/NpcContext identities, manager ownership, graph definitions and
VM clocks are unchanged. Neither snapshot exporter nor shared VM runtime is
relaxed. No new pool, driver, manager or storage whitelist is introduced.

The new A90 fixture initially attempted damage/heal as a reversible hash probe;
that also retains legitimate Vitals wire/credit history, so health restoration
alone did not restore the digest. Its stored-pool perturbation now restores the
same field directly. Original hash equal/unequal assertions and unknown-field
rejection remain, with an added body/controller lookup independence check.
No original gameplay assertion or numeric contract is changed.

Validation: projected NPC tests plus existing damage/runtime checks20/20 pass
in53.272s. Real capture distinguishes pool amount and recipient Component,
returns to the identical digest after restoration and rejects an unknown field.
All five actual NPC recipients, original pool expiry, death, removal and team
change tests pass. Baseline error traces and the intermediate fixture failure
remain in external parallel-merge/root-projected-npc-storage-* logs.

Separately canonical56557266 shared integration checks40/40 passed138.534s,
including Hog charge, Zarya sparse Graviton flight/charge, Nano wall scaling,
Pharah impact cover, Sentry services and actual raw bounced Sigma sphere storage.
This is a scoped receipt, not a claim that all source delivery consumers pass.
