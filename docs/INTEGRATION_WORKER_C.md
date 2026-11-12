# Integration worker C

Assigned by the user-authorized three-worker merge on 2026-11-12.

- Worktree: `verification/merge-worker-c`; branch: `integration/merge-worker-c`.
- Starting canonical commit: `bf7fad86`.
- Assigned deliveries: 0006 Torbjorn, 0008 Pharah, 0015 Bastion,
  0016 Symmetra, 00DD Mei, 012E Sombra.
- Root formally reassigned 0065 Junkrat and later0200 Ashe to worker A; C has
  no Ashe/Junkrat WIP and must not independently start their owner modules.
- Preserve and mount existing original-language implementations; no new gameplay,
  acceptance matrix, Rust runtime cutover, network, publication or client session.
  Commit partial functional batches despite incomplete whole-line acceptance.
  Root receives the committed batches and reconciles shared-file overlap.

## Current delivery

The first batch mounts Bastion's authored Self-Repair reports, bounded sampling,
actual restored-health notification and passive/damage ultimate gates. Existing
incoming8243 already applies Ironclad0.8; do not multiply another0.8 adapter.
The original scalar query is retained for delivery consumers and checks.
The original combat and charge check files remain unchanged.

First functional batch: `cd7cb8f350b21f3960abe2ff98b5ffeb053743cb`
(`feat: integrate delivered Bastion repair and charge`), received by root as
canonical0be0ca2a. Root retains the subsequent Component.input_suppressed bridge.

The second batch mounts Recon/Sentry authored spread, Recon critical default,
Sentry rear-core capsule and historical yaw. Its commit subject is
`feat: integrate delivered Bastion spread and rear core`; resolve its SHA from
this branch before reception. Four owner checks pass; shared checks pass46/49,
with all three failures reproduced unchanged atcd7cb8f3.

Third batch: `feat: integrate delivered Bastion Tank projectiles` mounts the
original Tank charge/contact/self-splash/impulse/body-retirement consumers.
Second batch SHA: `247f0bce`, received by root as canonical566dc09a.
The11 Tank player checks pass using the frozen real sniper helper solely to
satisfy the unchanged NPC fixture import; its normal import remains unresolved.
Shared Helix/Tracer projectile run records7 passes,1 external Matrix skip and
1 missing sigma_accretion import. No client or NPC acceptance is claimed.

Third batch SHA: `9d667dfd`, root received as canonical00fabf7a.
Local canonical synchronization: `a4e85027` (coordination merge, do not replay
as a functional batch). All49 original Bastion combat/charge checks now pass.

Fourth batch: `feat: integrate delivered Pharah hover and Jump Jet` mounts
0025 hover force,0027 reset/vector action and body-yaw query. All40 checks
(Pharah movement12, McCree roll15, script runtime13) pass. Root receives the
single functional SHA after the coordination merge; it already has its parents.

Fourth batch SHA: `c46b41e3`.
Fifth batch: `feat: integrate delivered Pharah Barrage channel` mounts the
mode0 persistent world-velocity SET and LogicalButton/WeaponVolley ultimate
query. Five original owner checks and37 shared physics/Tracer/Echo checks pass.
This does not mount the oscillating projectile consumer yet.

Fifth batch SHA: `adf49f18`.
Sixth batch: `feat: integrate delivered Pharah Barrage motion` mounts original
sampler and actual launch/curved chord/range consumers. Three original motion
checks pass with the real frozen sigma_grasp helper as fixture import fallback.
Shared Helix/Tank/server-snapshot run is active in tool session33339; inspect
`../worker-c-pharah-motion-shared-check.log` and retain the result next commit.
The unchanged `tests/test_pharah_control.py` is copied but deliberately not in
this batch: baseline8 checks records5 failures (channel abort, McCree double hit
and Flashbang boundary differences); do not weaken or drop those assertions.
Torbjorn pending resources check differs from source (including150 versus200 tick
fire cadence sampling); preserve both originals under distinct names later.

Sixth batch SHA: `1ecc533b`.
Seventh batch: `feat: integrate delivered Pharah channel interruption` mounts
source002B stack abort in combat and Sombra consumers. Original13 control/channel
checks retain4 baseline subcase failures in two other-owner timing/hit-count
methods; the real Barrage abort/recast now passes. Real Sombra service probe and
all26 script-runtime/Reaper-Shadow checks pass. `test_pharah_control.py` is now
submitted unchanged rather than left as an untracked fixture.
C6 full shared run remains in session33339; poll the external log before deciding
whether any new failure needs correction. Do not silently claim it passed.

Seventh batch SHA: `2d2da024`.
Eighth batch: `feat: integrate delivered Pharah curved reflections` mounts the
source restart/eye/seed/fallback/catch-up consumer, spring-aware launch and
point-wall/body-assistance policy. Five original curve/reflection cases pass.
Original eye/Helix/channel run:18/19 (retained McCree double-hit expectation).
Genji/Baptiste43 records6 failures/2 errors; all6 failures and Mei bubble error
reproduce at the preceding commit; one NPC data configuration error remains.
External logs: worker-c-pharah-{barrage-reflect-final,reflect-eye-shared-final,
reflect-services,reflect-services-baseline}-check.log.
Root received C5 asc254331d and C6 as37577fbd, with42 and14 focused checks passing.
C6 full server_snapshot session33339 is still active; do not rerun or label green.

Eighth batch SHA: `20598453`.
Ninth batch: `feat: integrate delivered Pharah blast impulses` mounts actual
Concussion/self-splash/ring/source force and modifier26/local ADD consumers.
All34 impulse/Helix/Tank/physics checks pass and curve/reflection5 still pass.
Explicit Rocket.is_barrage tags isolate the curve parser from Echo's motion
payload; Echo8 complete without the reported parser exception (6 pass,2 payload/
copied-charge differences in this pre-A7 tree). Root A7 must be synchronized
before calling these two residual differences current canonical behavior.
Logs: worker-c-pharah-{impulse-shared,motion-tag,echo-motion-tag}-check.log.
Root's Bastion/NPC audit found two interlocks to retain: Heavy capture changes
Sentry to Recon (wall295 versus235) and Heavy melee lacks displacement despite
correct damage. Do not remove original stance/movement bridges or assertions.
C6 full snapshot process53540/session33339 still runs; do not restart it.

Ninth batch SHA: `e9849f58`, received by root as2378f2d7; explicit motion tag
removed the Echo parser exception, with two original payload/charge differences
retained. C8 received asf3989db0. Clean local synchronization:45830030.
Tenth batch: `feat: retain delivered Pharah source sight and boundary behavior`
mounts the original independent middle/nearest source sight consumer. All45
original charge9/flight11/edges18/basic-combat7 checks pass; all17 impulse/Helix
checks pass. Source checks are unchanged; whole-line equivalence remains open.
C6 full server-snapshot process53540/session33339 remains active after26 CPU
minutes, with buffered PowerShell output still empty. Do not label it green or
start another copy; other concrete batches continue independently.

Tenth batch SHA: `c9b4f9e7`, root received as9b93d738; integrated profile62 pass.
Eleventh batch: `feat: integrate delivered prepared NPC impulse bodies` mounts
root-delegated narrow Bot/manual physics and owned Mei/Pharah target adapters,
plus the existing hook module's future shared-body handoff. Root attack_mode and
reposition cleanup are preserved; npc_control/npc_navigation are unchanged.
Original12 NPC cases:11 pass,1 Combat.hooks missing-consumer error remains.
All96 Bot/control/navigation checks pass, three original wall/team scenarios pass,
and explicit navigation resumes after settling in a direct handoff probe.
NPC inputs: ../retribution/npc-runtime/data; ../hero-lanes/0008/asset-research/
five-npc-ai-candidate.json.gz; ../hero-lanes/007A/mech-npc-movers.json.
Logs: worker-c-pharah-npc-impulse-integrated-check.log,
worker-c-npc-physics-shared-final-check.log, worker-c-pharah-npc-wall-focused-check.log,
worker-c-npc-impulse-navigation-probe.log. No source assertion was changed.

C6 full snapshot run is FINISHED:70/2615s,2 failures,10 errors,3 skips.
Three journal errors are INVALID as implementation evidence because ongoing
worker edits changed the captured model manifest; guards correctly rejected it.
Other legacy observation errors and Zarya/Moira assertions remain in the log.
Root explicitly requires any future long journal run on a frozen committed SHA
in a detached worktree, never the actively edited worker tree, and never a gate.
No running long test remains from session33339/process53540.

Eleventh batch SHA: `3e3aab4a` (ready for root receipt).
Twelfth batch: `fix: resolve Moira deployed target definitions` retains physical
Deployable targets while selecting their body metadata instead of Player.hero.
Actual Moira/Mei pillar probe:400->384, no VM fault. Two original Grasp checks
pass; one combined barrier/pillar test lacks its external descriptor and skips.
Logs: worker-c-moira-deployable-{contact-check,pillar-probe}.log.
No long test remains active. Future journal checks use frozen committed trees.

## Exact next work

1. Submit/reconcile C11/C12, synchronize latest canonical prepared-input entry
   and preserve root Echo curve_motion/source_entity fixes. Review remaining
   Pharah defense/barrier/control equivalence and continue the other six owner
   families. Moira Deployable correction is mounted; do not remove those targets. Core charge/flight/edges45 pass; NPC physical11/12 pass.
   Root/worker B still owns complete Roadhog hooks and root owns NPC input/nav;
   preserve the C11 narrow physical-body adapter when bringing those implementations in.
2. Audit the remaining Bastion cross-hero helpers; compare owner features, not
   just ancestry or file names. Core combat/charge49 checks already pass.
3. Continue the other six deliveries. Substantial missing owner families include
   Torbjorn turret/Molten Core, Symmetra portals/turrets, Junkrat mine/tire,
   Mei Blizzard/frost, Sombra cloak/translocator and Ashe/BOB.
4. For Torbjorn also compare the root-discovered preserved file
   `verification/combined-server-pending-tests/test_game_torbjorn_resources.py`
   against incoming/current checks; retain differing expectations without overwrite.

## Evidence and limits

The unchanged37-check Bastion baseline recorded9 failures and10 errors.
Focused Self-Repair/resource/death/gap/Tank checks passed7/7 after this batch.
Further charge and storage results belong to CHANGELOG.md and external logs.
No complete Bastion or original-client acceptance is claimed. The other seven
assigned deliveries are not yet reconciled by this worker.

Canonical synchronization after C9: f3989db0; preserve all original worker A/B/root
consumers. Only CHANGELOG required conflict resolution, retaining both entries.
C10 must verify received NPC/sniper, Doomfist and retained movement bridges.

Clean C11/C12 synchronization: root17b8eec7 (Echo curve_motion and reflected
source_entity ownership, integrated prepared-input profile, A/B current owners).
Only CHANGELOG conflicted; keep both entries. Resolve single functional SHAs
for reception; do not replay this coordination merge as an owner delivery.

Before Torbjorn/Overload work, synchronize roota65aee9d, including received
C11/C12 and A11 variable transition rechecks; preserve both changelog entries.

## C13 checkpoint: Torbjorn Overload and Pharah consumer boundary

C13 mounts original hero_torbjorn Overload/cooldown/control consumers, distinct
Vitals armour state/wire pool and snapshot fields, command/death/body-switch
entry points, and the original periodic armour-reduction opt-out. All17 original
Overload checks pass (92.738s); all30 DamageTests/Rally/Doomfist resources pass
(42.031s). Logs: worker-c-torbjorn-overload-final-check.log and
worker-c-torbjorn-armour-shared-check.log. Original assertions are unchanged.
Torbjorn turret/hammer/Molten Core/charge/resources are not complete. Next:
reconcile the original resource checks and owner fire consumers, preserving the
root-discovered pending variant separately, then actor-backed turret/hammer and
Molten consumers. No long test is active; no client acceptance was performed.

0008 code-level checkpoint (not a whole-line completion declaration):
- barrage_motion.py is mounted by Combat._shoot for the002B1 source, an explicit
  Rocket.is_barrage tag,16ms/chord flight/interception/range/lifetime consumers,
  and reflected source-eye/seed/budget/remainder handling. Keep root Echo's
  curve_motion/source_entity fixes when receiving later work.
- Actual player movement consumes pharah_thrust hover force and the delivered
  JumpJet reset/vector/yaw helpers. Mover script vector SET is distinct from
  scalar modifiers, follows gravity and precedes additive impulses.
- BodyScript.ultimate_running and the entity33 interruption stack connect
  Combat/Sombra aborts to authored channel lifetime. Launch-time source charge
  eligibility and no Barrage recharge after channel expiry are mounted.
- Actual HitDef/rocket blast consumers cover Concussion without a prediction
  lease, self damage, independent force rings/minimum/geometry, true impact and
  incident direction, two separate own-force sight samples and modifier26.
  Prepared NPC physical admission/manual mover/step/category paths and actual
  Mei pillar lifts are mounted through the explicitly delegated narrow adapter.
- Own Pharah work still needs impact-time cover freezing and attached/Particle
  barrier force-protection reconciliation: _pharah_impulses currently queries
  cover after damage and does not receive broken_particle_barriers. Do not
  hide this gap behind green core checks or claim that all remaining work is
  external. Original defense/phase/barrier interlocks require code comparison
  against the combined managers; source module imports alone are insufficient.
- Full Roadhog Combat.hooks is worker B0040's consumer; C11 only prepared the
  existing hook module's shared-body handoff and did not mount its actor host.
  That original NPC hook check remains an error. Root owns NPC input/navigation;
  preserve those owners and the delegated C11 physical adapter. DVa/Genji/
  Sigma/phase defenses use combined managers and still require owner-equivalence
  review, not silent copying or a source-fixture import fallback completion claim.
- The recorded older McCree/Flashbang subcase differences and incomplete client
  acceptance remain visible. They do not block committing existing consumers.

C13 SHA:5d56f941, ready for root receipt. C14 retains frozen0006 resources and
root's different pending resources as two unchanged files. Their initial28-check
run had six failing subcases: three missing hammer contacts, secondary ten-pellet
expansion, and two cross-expiry damage expectations caused by that expansion
(the timing assertions already passed). Mount the original projectile_patterns
unpredicted1683 radial consumer rather than changing fire-delay or tests.
Next functional batch mounts original hammer contact windows/late targets/control
cancellation/snapshot state and reconciles the owner turret repair dependency.

C14 radial run:36 checks/116.015s, six failing subcases (three missing hammer,
three standalone critical/falloff). Scoped standalone rivet follow-up: all10
original projectile+last-ammo+cross-expiry cases pass/16.327s. Logs:
worker-c-torbjorn-{rivet-resource,pellet-final}-check.log. No source assertions
were changed. Resource fire timing already passed; do not rewrite it for damage.

C14 SHA7a0ab40b ready for root receipt. C15 mounts source hammer body/window/
cancel/Genji107/snapshot consumers and optional contacted barrier APIs. Original
hammer13 plus background reload interaction:14 checks,12 pass, two repeat
interval differences retained (ordinary53vs47frames, boosted second contact
later than source33). Log worker-c-torbjorn-hammer-check.log. A conditional-marker
experiment did not change either difference and was reverted; no shared native
animation/stack semantics were weakened. Four direct actual barrier-method
probes validate one contacted hit and unchanged ordinary calls. Old shared
fixtures retain two descriptor skips and absent body_barriers error, log
worker-c-torbjorn-hammer-barrier-shared-check.log; unrelated old Tracer imports
also expect absent deflection.TAGS_PATH and were not counted as executed.

Next: reconcile worker B's reinhardt_hammer.surfaces(fight,player,volumes,amount,
seen) helper for native shell faces and reuse its actual capsule geometry.
Its seen namespace is ('surface', actor entity), whereas these optional legacy
area/melee contacted APIs use actor int entity. Torb sphere volumes are
(centre,centre,1). Preserve distinct per-swing source contacts. Zarya native
body damage remains existing combined policy; no extra double shell hit.
Then mount original actor-backed turret factory on existing NPC entities and
pool, with actual owner credit/geometry/snapshot/lifetime bridges. Its collection
is fight.turrets(owner.entity->source Turret); legacy prototype torbjorn_turret
manager has not been a production consumer. Hammer repair and Steel Trap
contact remain explicit pending owner consumers, not completed by empty maps.

Clean C15 boundary synchronizes root7b6c99ac, including B9 Winston jump/primal
and root reflected flight lifetime boundaries. Keep both Overload and Winston
command consumers, source Overload armour and B9 beam-factor overflow, and all
changelog entries. Functional C15 remains ea89b37d; do not replay this merge.

## C16 actor-backed turret and marks checkpoint

C15 SHAea89b37d has been received; root latest62dd2bf0 includes A/B/C batches,
not yet synchronized because C16 WIP was active. C16 mounts the remaining
source hero_torbjorn turret functions and torbjorn_mark actual impact/update/
priority paths. Existing NPC bodies, vitals and single gun driver are used.
NPC optional current_health/selected entities/aim_at are authorized by root;
bots owner/geometry, combat target/credit/muzzle, driver bound query/message,
body-switch reuse and snapshot fields connect production consumers. All
existing control/navigation paths remain. Throw uses original point contact,
not a carrier-radius hit hovering0.35m above its permitted landing surface.

Evidence: first turret8 run5pass/1fail/2errors exposed missing throw spawn;
source spawn/gravity then exposed collision-height. Final five throw/owner-death/
repair checks pass22.933s. Owner8 + NPC/damage15 pass in45.651s (run24 also
records one unexecuted mark fixture import); five mark core checks pass11.486s.
After preserving original defense/phase fixtures unchanged, imports resolve.
Last shared NPC/damage15 pass2.136s. Logs worker-c-torbjorn-turret-{first,throw,
throw-final,owner}-check.log and worker-c-torbjorn-mark-first-check.log.
Test fixtures import unmounted molten_core/phase_defenses/torbjorn_charge;
explicit test-only ow174.game.__path__ appended frozen0006/source/ow174/game.
No such fallback exists in production; no Molten or charge integration claimed.

Input comparison: worker-c-torbjorn-input-differences.json. Current combined
base/supplement shared graphs/entities/effect_scripts equal original0006 pack;
no differing records. Add graphs1688,1689,16DE,16FB,16FC; entities28B7,2903;
original deployables format_version1/turret/molten_core/primary_mark. Narrow
loader accepts that mapping, idempotent equal keys, rejects unequal collisions,
nonmapping entries and invalid version. For local checks only, external
worker-c-torbjorn-supplement.json.gz adds these records to the existing profile.
Root tools/prepare_hero_inputs.py owns final prepared profile composition.
No original input or full asset collection was copied/modified.

Exact next: submit C16 immediately, then synchronize root62dd2bf0 or its latest
clean descendant and reuse B12 reinhardt_hammer.surfaces with Torb's sphere to
reconcile native shield faces (do not duplicate manager/damage). Mount Molten
Core actual launch/rebound/area/status/damage/lifetime and source charge next.
Retain original hammer repeat differences53vs47/boosted33. Review remaining
NPC/phase/defense/reflect/mark contexts against combined owners; no complete
0006/0008 line or client acceptance is claimed. Other assigned owner families
remain as previously recorded and must continue after this owner batch.

C16 SHA b889f287 delivered to root. Clean boundary synchronizes root9ec59b74,
preserving its Hanzo/Orisa/Winston/Rein consumers plus Torb marks/turrets,
all snapshot fields and all changelog entries. Resolve conflicts by retaining
both turret update and Orisa pending cleanup, root command ordering, and
root expanded snapshot fields plus torbjorn_marks. Do not replay this merge.

C17 reuses B12 reinhardt_hammer.surfaces for Torb sphere volumes; keep the
original live-bubble path but exclude RaisedBarrier from that old route to
avoid duplicate surface damage. Default area callers are unchanged. Actual
probe uses ReinhardtWinstonBarrierTests.deploy(7), switches case.rein to Torb,
places at(0,1,0), waits35, sendsCommand(action=2), waits45, then twice advances
45frames(buttons=1)+25(buttons=0). Native shell700->590; body500 unchanged;
HERO_DAMAGE0. Initial probes without the35-frame fresh-body equip wait were
setup-invalid and left the rifle selected; no runtime timing was changed.
Three Torb original delayed/late/distinct-target hammer tests plus original
Rein native-shell hammer test pass4/4 in4.056s. No new tests/matrix were added.
Root C16 received0854671e, v4 inputs e1c5e6fd actually add15graphs/3entities/
4effects+deployables to canonical base, root23owner/NPC/damage checks pass.

Next immediately mount original Molten Core; status_effects topmetadata has
no direct reader anywhere in frozen0006/source/ow174 (rg confirmed). Original
Molten Core reads deployables.molten_core and directly resolves status_graph
16DE and its scopes. Root preserves status_effects as unselected input evidence;
do not silently import it into production or claim a consumer without evidence.

## C18 Molten Core checkpoint

C17 SHA0790f14f received7b921893. Rootlatestb6859d9b; C18 WIP was not interrupted
for synchronization. Actual original molten_core module now lives in this tree;
no production/source-package import fallback is used. Connect16DA spawn/gravity/
point chord/segment rebound/reflection speed to actual projectile sweeps;
16DD pool delivery/ack/FX,16DE status/scopes, overlap union, armour-only boundary
settlement and lifecycle all have real consumers. Existing managers handle
world cover, immunity, incoming scaling, legacy attached bubbles and damage.
Combined pool ordering includes temporary/extra/conditional/wave/Rally rather
than dropping their implementations. Exact scopes.Registry(states) is now
passively registered in both storage exporters (source reader preserved).

Original flight five pass9.426s: ceiling, double bounce, shallow/steep ramp,
ten-shot held ultimate, same-tick hero switch. Area five behavior pass; original
passive pool/status snapshot passes21.430s after Registry registration. Seven
area/lifecycle + NPC/damage15 pass in6.567s; that23-check group additionally
retains a Pharah fixture sigma_grasp import error, not an executed curve test.
Actual three Barrage curve checks separately pass after the same previously
recorded test-only0008 source package fallback. Logs:
worker-c-torbjorn-molten-{area,area-final,flight,flight-final,shared,barrage-shared}-check.log.
Initial source-constant adapter typo and exact Registry omission were corrected;
two edit guards rejected stale shared-file text before changing combat.py, so
those early flight errors are not final implemented behavior. No assertion was
edited. Core original Molten checks no longer require the frozen Molten import
fallback, and original turret fixture's formerly missing module is truly local.

Next: submit C18 promptly, synchronize latest clean canonical at batch boundary,
then mount original Torbjorn charge184-line host and reconcile Molten source
barrier/phase/NPC/reflect/expiry edges against current combined managers. Those
broader original checks are retained, not declared green by ancestry/imports.
Original0008 impact-time cover and Bastion NPC capture/movement differences
remain visible. The other six assigned families must continue afterward.

C18 SHA08c8d1bf delivered. Clean boundary synchronizes rootb6859d9b, including
received C16/C17, explicit v4 composer, owned-mech/Storm/Charge/current scopes.
Keep Molten update once, root Orisa pending cleanup, then root mark/turret
updates once; do not duplicate those managers when resolving the same feature
received by root under another merge SHA. This is coordination, not delivery.

Before C19, clean synchronization root0b8d98cb preserves new explicit volley
geometry/owner records and all existing consumers. Resolve storage fields by
keeping reconstructed_volley_patterns plus Molten pool/burn fields. Functional
C18 remains08c8d1bf, root receipt783de900; do not replay coordination merge.

## C19 original Torb charge checkpoint

C18 received783de900; root Molten9/turret1 + shared73 pass on v4. Before C19,
clean synchronization55cabb59 includes root0b8d98cb explicit volley geometry.
C19 mounts original torbjorn_charge normal0B8B owner/permission/query/timer/
012 tag/loss consumers for Torb only. Genji/Orisa still use their received
single host. Typed component43 and fold dispatch previously intercepted the
source compatibility queries, so narrow Torb0B8B/BodyWorld nonstrict routing
now lets the original normal gate and3EBB record reducer execute; strict and
other-owner typed behavior remain. Samehero unspent meter/passive deadline,
source assembly ultimate input and sandbox timer override are mounted.

Tagged shield compatibility acts on the exact existing extra pool objects and
wire IDs; actual grant and subsequent damage/decay mutate their authoritative
now/most/dirty state, preserving existing TTL and owner cleanup. No extra health
pool, second actor or fake damage observer was created. Original module's Ball
synchronization helper is retained but not a second production1566 consumer;
canonical _sync_adaptive_shield remains owner. Core actual cast/175 shield and
three attack-mode denial cases passed through that existing owner.

Runs: owner-gate six pass (worker-c-torbjorn-charge-owner-gate-check.log);
replacement/respawn passive two pass in the earlier shield-replacement group;
its six adaptive subcase failures were then fixed by the original3EBB reducer,
final adaptive three cases pass6.187s. Owner lifecycle eight run7pass/1assembly
failure8.364s; source Ability.can_start gate resolves it, three final assembly
cases pass. No original assertion changed. Shared60:55pass4skip1existingGenji
line206; root reproduced clean8dc396d6 in
parallel-c19-genji-passive-baseline-check.log. Root's independent minimal
Genji dispatch fix is not yet synchronized here; do not claim that baseline
failure as a C19 regression or suppress Genji's actual5-point passive grants.
Shared log worker-c-torbjorn-charge-shared-check.log; additional logs
worker-c-torbjorn-charge-{first,owner-lifecycle,assembly-final}-check.log.
Original charge fixture imports still-missing sigma_grasp and related old
helpers via explicit test-only frozen0006 source package fallback. Production
Torb charge/Molten/turret modules are all local; do not call that fallback an
integration of the missing shared helper owners.

Exact next: submit C19, sync root latest clean including independent Genji gate
and sniper snapshot fields, then audit deeper original Torb/Pharah interlocks
and continue remaining owners0016/0065/00DD/012E/0200. Shared difference:
canonical orisa_charge.damage sends empty0x624 records, while frozen0006's
shared Genji/Orisa charge host knew3EBB deductions. Preserve current hosts and
coordinate their own event-record enhancement; do not double-send012 or mark
this shared feature equal merely from module names. Original source charge
and scope functions are retained, but unconnected shared consumers remain
explicit. Existing0008 impact-cover and Bastion NPC differences also remain.
No client run or whole-owner completion has been claimed.

C19 SHA16153edb delivered. Clean boundary synchronizes rootde3c2335 before new
0016/0065 owner work: retain root Genji0B8B owner routing and typed missing-
instance guard, Torb0B8B/source record routing, Sniper BotWorld/PositionSearch/
storage fields, new projectile geometry records and Punch collision consumers.
Only changelog conflicted; keep both. Molten/mark/turret update each occurs once.
Do not replay this coordination merge as a hero delivery.

Before first0016 batch, sync root8937f36a (received C19ea3d56dc). Preserve root
Torb native direct torbjorn_charge.native dispatch with typed NotImplemented
fallthrough, Genji and missing-instance gates, original Combat.body_health
lookup semantics and all latest owner fields. Only changelog conflicted; keep
both entries. This coordination merge is not a new owner implementation.

## C20 first0016 owner checkpoint

Actual symmetra.py primary/target_query consumers now feed original157C100/101
TrackRay outputs, radius fallback, hit flags, live rate, tagged barrier feedback
and ammo query. Adapt to real existing barrier geometry and pool services; no
new barrier manager/actor/pool. Source has_tag lookup uses current complete
catalog; unknown definitions stay unqualified. BodyWorld.beam_barrier is real
state and exactly registered for passive storage. Do not copy source services
under alternate names and double-drive current Photon/Winston/Rein/Zarya.

Original test_symmetra_combat.py retained unchanged. First21 run15pass/6fail:
two primary failures exposed two existing _weapon_beam command calls; narrow
Symm once-per-command handoff resolves those. Final21:17pass/four unmounted
shield-recovery failures22.300s. Logs worker-c-symmetra-primary-{first,final}-check.log.
Actual probe from ReinhardtWinstonBarrierTests.deploy(7), switch rein->Symm,
place(0,1,0), wait35, advance10(buttons1): shell700->690.3997802734375, body500,
world.beam_barrier equals actual dome.body. Initial ad-hoc1e-6 comparison was
tighter than the native float32 per-tick representation; source assertions
were not edited. Existing Zenyatta dome case still expects absent .domes and
errors before its beam assertions; do not claim that case executed fully or
create an observer-only replacement manager.

No new shared method names were introduced without an AST duplicate-method
scan. It confirmed C19's preexisting Vitals API name collision, now independently
fixed by root4999fd44 (numeric temporary_shields stays; actual extra tag mapping
is tagged_shields; decay dispatch retains both clock and tag/amount). This WIP
has not overwritten that root correction and must sync it at the next clean
boundary. Root's source fixture attribute adaptors preserve literal assertions.

Next: submit this primary batch, then sync latest root including4999fd44 and
source health lookup/Sniper/Genji guards. Mount original Symm shield recovery
and owner charge, then actual sentry and portal/placement source managers
while preserving canonical Photon Barrier implementation. Original25s orb
and retained scheduling/respawn bridges require separate source comparison;
constants or source files alone are not completion. Source symmetra placement/
transit/teleport entry consumers have not yet been copied/mounted. Remaining
other owners0065/00DD/012E/0200 still require full reconciliation.

C20 SHA cc349c0f delivered. Clean boundary synchronizes root7ba03d3c, preserving
4999fd44 actual Vitals clock/tag dispatch and numeric/tagged API split, Genji/
Torb direct native routing, health lifetime and Venom source consumers. Verify
no duplicate non-property methods remain in touched classes; no whole-file
replacement or rollback of other owners. This coordination merge is not a new
delivery. C19's shared regression is fixed and retained by this synchronization.


## C21 original Symmetra recovery checkpoint

Original0033 recovery consumes existing Vitals shields. last_damage_at and
shield_accounted_at reset only for Symm after actual positive damage; recover
through now-TICK before damage. Read authored delay/rate and modifiers.
Runtime/BodyWorld native own-kind current/maximum queries are narrowly Symm0033
nonstrict; unsupported queries preserve typed fallthrough. Root4999fd44 shield
numeric/tagged APIs and clock/tag decay dispatch are preserved.

Original test_symmetra_combat21/21 pass21.061s; log
worker-c-symmetra-recovery-final-check.log. With v4/PYTHONPATH tests:
python -B -m unittest test_game_combat.DamageTests test_game_npc test_doomfist_resources -v
20/20 pass10.484s; worker-c-symmetra-recovery-shared-check.log.
Actual ServerSnapshotTests.scene/capture digest independently changes for
last_damage_at=1.25 and shield_accounted_at=2.5. First probe found C20's actual
beam_barrier field absent from server registry (VM-only registered); repaired
here and second actual probe passes. No original assertions changed.

Next: commit recovery and mount original Symm charge, then actual sentry and
portal/placement managers. Sentry source has substantial retained-history
replay/defense adapters; copying alone cannot count it integrated. Source25s
orb and retained flight remain unfinished. Other C20 owner boundaries persist.


C21 SHA ce7f0a78 delivered; root notes C20 receiver already repaired its server
beam field and C19 passive_charge_due, so retain their receiver registrations.

## C22 original Symmetra charge checkpoint

Source symmetra_charge.py actual0B8B queries, mode gate, command synchronization,
world idle deadlines and012 pool loss are connected for Symm only. Original
module's shared Genji constants/helpers remain present; canonical Genji's own
single host remains authoritative and is not routed through this new host.
Photon wall suppression reads existing photon_barriers.actors, no second pool.
Generic combat award excludes Symm so actual damage is not counted twice.
Same-hero respawn restores meter and resets the real charge_clock; selection
still resets normally. VM/server optional registries include this actual tuple.

First7 core run: sixpass/onecase with2 offset failures; clock initialized before
_spawn was wrong (time0 vs initialized16015). Match source placement after
successful_spawn; all7 then pass4.259s. Full original unchanged
 test_symmetra_charge + test_symmetra_combat =30pass28.268s, including the
25second orb lifetime and real pre/post-respawn hits (no claim that all source
retained scheduler branches are integrated). Log worker-c-symmetra-charge-full-check.log.
OriginalGenji passive+Damage21pass10.319s, worker-c-symmetra-charge-shared-check.log.
Actual ServerSnapshotTests scene, session.apply switch_hero Symm, capture and
export_body_vm: changing charge_clock tick+1/time+16 changes both digests.
First ad-hoc probe tried direct switch outside controlled journal root and
correctly rejected; repeat uses existing session.apply, no guard weakened.

Remaining: original source Photon-expiry/omitted-history clocks need comparison
against canonical actor scheduler; unmounted sentry/portal/placement services
and their retained-history interactions remain. Core9 charge passing does not
prove those cross-owner branches. Continue actual sentry deployment/beam/slow
and preserved original source checks next; no full-journal gate or client use.

C22 SHA5beccbdc delivered. Clean boundary sync root6ef42b61 preserves B18 BotScript polymorphism/nontransportability, A25/26 Widow and source reconciliation map. Server registry retains root passive_charge_due plus own charge_clock and one beam_barrier field. Coordination only; continue C23 sentry.


## C23 actor-backed original sentry core checkpoint

Actual2658 NPC/Vitals/BotScript owner core mounted in symmetra_turrets.Turrets,
Combat.sentries. Source157E/12 shot hands off before generic projectile; exact
source157F boot/1590 live beam/range and01E7 aura inputs. Generic bot gun skips
these actors, no second gun or pool. Existing Bot.combat_geometry retains source
1m/0.2m capsule; zero-locomotion summoned actors are driven solely by this owner
flight/attach logic (source Definition.mover constructor is not a second NPC API).
NPC.remove_body does exact existing cleanup. Source death persists, switch/leave
retire owned actors; source target change/destroy/retire destroys only its aura.
Mei pillar removal calls actualmanager.retire_attached_to; old unused prototype
module remains preserved, no fake .devices/.sentries health view.

Original test_symmetra_turrets.py copied byte-identically. First3 checks exposed
source Definition.mover positional mismatch; adapt to existing Definition pools
and actual geometry rather than replacing NPC/nav. Next3pass2.226s; full14pass
17.417s, worker-c-symmetra-sentry-core-check.log. Environment v4 + original
ORPHEUS_EXTRA_GRAPH_BUNDLES skip input. Actual source summon-graphs3graphs and
4entities composed in memory using existing graph.add_supplement(graph._data(),
{version,graphs,entities}); patch only _data to return that real composed data.
No fake health/geometry/assertions, no runtime pack writes. This is not yet the
final root explicit-profile rerun. Root notified complete minimal input diff:
base+v4 additions graphs01E7/157F/1590, actors2658/26B0/26F2/272D, zero shared
record conflicts/identicals. Source topmetadata only version/source_sha256.
Shared DamageTests + test_game_npc + test_symmetra_combat36pass15.682s (v4).

Exact unmounted retained boundary: source frozen_history_candidates,
_ice_support_rocket and related history/route isolation functions preserved;
not called from current Combat gap replay. Their original phase_defenses and
zarya projected-history helper protocols are not mounted. Normal update currently
passes empty damage_starts/cryo_expiries/barrier_cover_ends; therefore original
cross-owner phase/thaw/barrier-expiry omitted intervals remain unfinished.
Do not infer them from core14 pass. Source supporting-ice basic retire is mounted.
Next: explicit profile rerun, storage probe if useful, then mount these retained
consumers against existing defense/clock owners or proceed portal placement in
an independent actual batch. Keep all original checks and no acceptance matrix.


C23 SHA c31524d4 delivered, explicit0016 input composer profile still pending.

## C24 actual15F5/1647 transit checkpoint

Original symmetra.play_transit_script and teleport_body mounted through existing
compatibility_play_script/relocate_through_portal handlers. Existing legacy
portal fallback preserved. Only owned15F5/nonstrict BodyWorld position query
runs before typed spatial dispatch; unknown IDs remain None, other graphs and
strict paths retain their current services. Original World.teleport_body default
NotImplemented protocol restored, no pretend relocation for generic worlds.

Original test_symmetra_teleport_action.py byte-identical, first9:7pass/onefail/
oneerror (knownbody position missing; base protocol absent). Corrected source
consumers, final9pass1.836s, worker-c-symmetra-transit-final-check.log.
Original test_script_body_owner+DamageTests15pass2.027s,
worker-c-symmetra-transit-shared-check.log. Actual mover physics instance remains
same, position/history updates; old hit traces cannot interpolate teleport.
No client execution, no pair/placement/admission implementation claimed.
Next: original stateless placement_geometry/clock helpers and sourcePortalPair
actor factory/confirm/interaction against existing NPC/actor pool; preserve
canonical Photon barrier and DVa CallMech placement. No generic service overwrite.


C24 SHA184e4d32 delivered. Root explicit profile now runtime-inputs-v5,
.environment plus .test_environment (EXTRA only originalskip flag).

## C25 original static placement checkpoint

Source script/placement_geometry.py and symmetra_placement.py stateless math
preserved. Append source Clock/Record/StaticResult/publication/history/Confirmation
primitives to existing script/placement.py without replacing CallMech.Result/place.
Original World provider protocols and source Placement base now exist; current
PlacementPreview delegates only for actual Symm1571/69 and156B/57 when canonical
Photon.geometry is absent. Configured Photon retains current whole placement
path; no second wall manager. Original server forward-clock/disabled prediction
history policy is explicit. Confirmation remains unknown until real owner accepts.
Actual placement body/view axes and static null-parent transform route narrowly
before typed native services. Initial15:14pass/axis-stack failure exposed missing
poll_native on early answer; fix that dependency registration, final15pass2.691s.
VM registry stores actual PlacementPreview.last_frame/latest_frame; actual active
1571 preview export changes digest when latest_frame changes.

Explicit root v5 environment +test_environment, no _data patch:
python -B -m unittest test_symmetra_turrets test_symmetra_static_placement test_symmetra_placement_axes test_game_dva_placement test_symmetra_teleport_action -v
46run45pass1skip36.499s; worker-c-symmetra-placement-v5-check.log. C23 original14
now exercised on final additive v5 records rather than in-memory test composition.
Original pure test_script_placement_geometry+history44pass0.004s.
Original test_script_placement_results retained unchanged:6cases, sevenerrors
across fivecases from existing strict map SummonerPlacement refusing FA02 state
at construction; permissive provider-shape case/subcases execute. First combined
67run10errors1skip before actual source base class renamed Placement; after
proper source classname, strict boundary isolated to sevenerrors, no fakealias.
Do not weaken strict map guard or original assertions for this check. Original
strict generic Placement role still needs reconciliation with map placement;
current server owner path is permissive and actually works. No new matrix.

Next: original PortalPair/confirm/factory/interaction via existing NPC pool,
with original actor inputs already supplied by v5. Preserve current strict map
Summoner and configured Photon paths while completing these missing consumers.
Source sentry retained phase/ice/projected-history boundaries remain above.


C25 SHA433cd727 delivered; root will receive without strict-result gate.
0065 Junkrat now assigned A; no workerC WIP existed there. A owns trap/tire/charge;
Torb hammer trap consumer will coordinate actual manager API with A, no duplicate.

## C26 original PortalPair checkpoint

Original symmetra_portals.Portals factory/confirm/ready/interaction/dismiss/
retirement/lifecycle mounted as Combat.portals.272D/26F2 endpoints are real NPCs
with existing Vitals/health scripts/replication,50HP+250SHIELD, actual1.5m/.5m
geometry. No prototype Portals manager created under alternate health views.
Source MovementOverlay and pillar triangles are local temporary exit probes over
immutable map engine; _body_at preserves current mover stance/lift/category and
canonical peer_mask. Never replace shared mover, collision world or NPC/nav.
Player._advance_body_frame narrow sourcepressed bridge dismisses before body
command, interacts after it before combat consumes reports: actual same-frame
held beam/orb and hit discontinuity tests pass. Existing drivers remain intact.
Original samehero death retention/switch/leave cleanup, own/ally/range/static
LOS, blocked exits, current crouch, lockout, actualdamage/cooldown all connected.

First attempts exposed npc->navigation->Combat.Target import cycle (defer npc
imports inside actual factory/retire) and original fixture missing Winston
HERO/DEFINITION constants. Restore those actual0009/01C3 metadata only; B approved,
no Winston manager/behavior change. Full29 first run41.751s:
- Cancel-preview reentry failed because original source Anim.portal_cancel1571/30
  finish branch missing. Mount only nonstrict match-owned branch, original generic
  and strict flag paths retained; originalcancel now passes1.306s.
- Eight errors across sixcases still read old ice_wall/zarya_barriers/
  winston_barriers observation names; do not invent runtime manager aliases.
- Cryo termination onefail and projected bubble target three failing subcases
  remain original cross-owner differences; no changed literal/assertion.
Final21 owner cases (exclude exactly those8 cross-owner cases) allpass33.697s,
worker-c-symmetra-pair-owner-final-check.log. Corecancel fix not full29 rerun.
Original test_symmetra_portals.py remains byte-identical. Shared exact command:
python -B -m unittest test_script_animation_timing test_game_npc test_game_combat.DamageTests test_symmetra_teleport_action -v
27run26pass1generic Anim.client_playback flag failure4.057s. The preexisting
Anim suppress expression combines154DBA91/2DEAE6A9; C26 only ORs source1571/30
match role which that generic fixture does not enter. Do not claim that check
passed or alter its assertion; broader native flag reconciliation remains.

Actual controlled storage probe with v5 (not _data patch):
ServerSnapshotTests.scene(two=True), peers[1].message(21616,pick(owner.entity,
symmetra.HERO),now=100.8); for buttons16,0,1,0 use
peers[1].commands(owner.body_script.frame+1,4,button). Actualpair count1;
ready_at+=.016 changes serverdigest; actualendpoint Vitals.now[2]-=5 independently
changes it again. Export is passive; no original client or full journal gate.

Remaining worker low-confidence16–32 active hours: Symm retained/strict role/
cross-owner3–7h, Mei2–5h, Sombra1–3h, Ashe6–12h, Pharah impact-cover/Bastion
interlock/conflict closure4–5h. These overlap ranges and are not a calendar promise;
0065 now A. Next actual batch: current-owner fixture references can be adapted
without changing literal assertions, then original Mei modules/Blizzard and
Sombra/Ashe missing consumers. Do not linger on every private observer before
mounting actual missing code. Preserve all boundaries above and no new matrix.

C26 SHA152ea32d delivered. Clean sync rootb3ca1ee2 keeps all A/B/root consumers, source filter/wave/control lifetimes and C21–25 receiver variants. Only CHANGELOG conflict; preserve both. C27 now Mei source pool/status/Blizzard, not Junkrat (A owns0065).


C26 SHA152ea32d received root e8adaf8a. Clean sync7001fe89 from rootb3ca1ee2
retains A/B lifetime/source changes; root current v6 adds original Lucio status
bundle plus real5NPC test data, external asset hashes unchanged.

## C27 Mei original resource/charge checkpoint

Source mei.py is now present and actual mei.command_edges/permissive_state_enter
consumers run in the owning body VM. Automatic reload uses original ammo/timers;
low-ammo icicle waits for actual reload; existingwindup/cryo/freeze guards stay.
Its source sombra_hack.mei_command_edges consumer is adapted to the sole current
sombra_controls.locks (release/block original35/36/37/110/114/116 buttons, cancel
preview/ultimates). No duplicate Hack/EMP manager. Source05FE state41 callback
finishes only in permissive mode; original generic/strict conditional timers
remain intact. Existing barriers.cryo_heal is sole pool mutator: actual original
full-duration200/self-only case passes; do not add another healing driver.

Source mei_ultimate.py87lines mounted: default322seconds=5points/s, original
253C/32DE/3276 generation composition,32E0 spawn-ready,253A stop/block Q, actual
damage/self-healing grants (D5 excluded), no generic double award. Samehero
combat respawn preserves actual charge unless spawn-ready takes precedence;
hero selection still resets. No new fields or second ultimate timer.

Source Walls.place_player uses existing place_mover then replaces current mover
history pose. Actualplayer pillar deployment now calls it; NPC branch unchanged.
Actual real pillar probe (deliveredMeiCombat.duel, flat_world0, both actual movers
at0, step_pose current Command; walls.create one actualpillar) raises enemy and
keeps its same-frame history.position equal physical/mover position; body Y is
feetY+lift. This is an actual mutation helper, not an observer alias.

Validation chronology/boundaries:
- Original delivered combat29 and Cryo14 retained. First43run96.407s:5fail19error,
  chiefly missing source place_player/frost/cryo_samples protocols. No asserts
  edited. Original canonical tests/test_mei_combat.py retained separately; new
  test_delivered_mei_combat.py is frozen00DD source byte-identical. Cryo14 differs
  only its fixture import name to use that original delivered file.
- After place_player, selected12Cryo (exclude sampled-history and held-secondary
  cases) plus three original reload cases =15run67.076s:13pass, onecase4 suberrors
  still expecting fight.frost, oneactual in-flight icicle hit_entities difference.
  Log worker-c-mei-resource-final-check.log. Do not call the whole group passing.
- Source ultimate check file copied byte-identically. Its unused Blizzard/
  shield/deflect fixture imports still need later modules, so execute untouched
  UltimateTests class AST with real globals fixtures=test_delivered_mei_combat,
  cryo=test_mei_cryo, combat/mei_ultimate/rulesets/Content, unittest, patch, replace.
  No fake fixture/module/return, no assertion AST changes.13 exact source methods
  allpass11.140s, worker-c-mei-charge-core-check.log:
  default_passive_rate_fills_points_and_caps_at_authored_cost;
  actual_weapons_credit_effective_enemy_damage;
  overkill_and_dead_target_cannot_duplicate_damage_credit;
  allied_hits_and_misses_give_no_credit;
  actual_cryo_credits_only_effective_healing_and_not_overheal;
  death_stops_passive_respawn_retains_charge_and_hero_change_resets;
  combat_generation_zero_disables_damage_and_effective_cryo_income;
  fractional_combat_composition_and_entity_override_of_mode_default;
  passive_zero_preserves_combat_income;
  overall_and_passive_scalars_compose_and_can_change_live;
  resource_minimum_and_maximum_combat_scalars_respect_cost_cap;
  spawn_ready_wins_over_retained_charge_on_combat_respawn;
  enabling_spawn_ready_on_living_body_does_not_refill. Prefix each with test_.
  This is not a claim the whole raw ultimate module imported or all31 ran.
- Existing test_mei_combat +DamageTests20pass; two Ana wall lift/occupied cases
  stop at old fight.ice_wall observer name (22run20pass2error8.889s). Keep source
  assertions; actual lift/pose mutation separately proved above.

Mei module remaining: CryoHealing sampled historical status consumer, original
frost_view/hit and multi-source/stacking settings, full Blizzard, held secondary
exit, projectile range/synthetic falloff and wall-windup differences. Source
mei_frost not yet mounted. Do not count helper class presence as its consumer.
Next C28: original multi-source frost in the current Frost host, preserving
current player/NPC/control consumers and one aggregate; then Blizzard original
flights/area. Sombra/Ashe and0008/Bastion remaining boundaries still persist.


C27 SHAac284345 received root67825a29. Root v7 selects original013B controller
supplement; v8 additionally selects original Rein/Orisa geometry. Use configured
profile.environment and.test_environment; no source pack overwrite.

## C28 original multi-source Frost checkpoint

Original mei_frost.py57lines actualsettings/prune/combine consumed. Existing
Frost host now stores original aggregate sample; original Combat.frost/frozen
program consumers read it. frost_sources is the actual original body/channel
causal map and Frost.sources refers to that same object, not a copied observer
view or second manager. Legacy Frost(sources={body:(progress,expiry)}) calculation
is retained when no new sample exists; adopting a live default-host object
preserves its partial entries. Default and configured min/rate/duration/stacking
use original helper math. Frozen sample is not extended by more hits; owner aim
is captured and current input/movement/status2427 consume this same aggregate.
Actual NPC freeze deadline enters existing movement_pause/is_stunned; standard
NPC mover gets existing fight.movement_mods. No extra mover/driver.

Original Cryo, attached-shield, death/replacement and orphan cleanup clear the
causal map and visible aggregate. body_entities detachedbomb direct pop becomes
_clear_frost(bot), coordinated with A, no other DVa behavior touched. The current
Frost dataclass auto-storage registration includes sample; Combat registry adds
frost_sources. Shared reference identity is preserved by stored graph export.

Original9 test_mei_frost first8pass1fail: existing TacticalVisor was stopped by
generic_interrupt. Original00DD explicitly preserves that positive buff. Add
optional preserve=() to existing_interrupt/interrupt_controlled, defaults unchanged;
only freeze with actual defenses.soldier_visor_running passes real activeultimate
states. All other input/weapon/channel cleanup runs, strict/generic policies stay.
Final9allpass7.117s, worker-c-mei-frost-final-check.log. Original checks only change
fixture import to delivered29 source; methods/assertions remain AST-identical.

Original test_mei_frost_sources.py byte-preserved. Its untouched FrostSourceTests
class AST runs with actual globals fixtures=test_delivered_mei_combat,
frost_fixtures=test_mei_frost, actual dataclasses/unittest/Run/KANEZAKA_DM/content/
combat/mei/mei_frost. No fake fixtures, assertions or return values. Select11 by
excluding exactly these5 whose full fixture dependencies remain unmounted:
ice_wall_blocks_only_the_occluded_spray_frost_source;
actual_spray_and_own_blizzard_use_separate_frost_channels;
two_actual_blizzards_compose_independent_area_frost;
cryo_cleanses_both_actual_blizzards_and_restores_fresh_samples;
projected_shield_cleanses_source_storage_as_well_as_visible_frost.
Prefix test_. All11pass28.976s, worker-c-mei-frost-sources-first-check.log. Once
Blizzard/shield/wall fixtures mount, run original module with normal imports.
Do not count the selected class run as complete raw module coverage.

Shared exact command with v7 environment+test_environment:
python -B -m unittest test_doomfist_punch test_game_combat.DamageTests test_game_npc -v
36allpass17.452s, worker-c-mei-frost-shared-check.log. Actual controlled stored
scene(two=True), real second-player target, _frost_hit(source=first): confirms
Frost.sources is fight.frost_sources[target.entity]; second actualhit advances
sample and changes serverdigest. This uses actual new Frost objects, in addition
to the source stored primitive-tuple test. No full journal/client gate.

Remaining precise NPC bridge: npc_navigation.step earlycontinue bypasses the
standard mover mods; root authorized one narrow existing navigator mover/
track_mods/speed_scale bridge for autonomous partial slow. Do not change NPC
input/pathfinding/control clocks or create another mover. It may be next batch;
C28 code does not wait on it. Full freeze already uses existing is_stunned.

Photon audit requested by root: current photon_barrier.Barriers.geometry is
None without ORPHEUS_SYMMETRA_COLLISION; source0016 really uses unbounded plane,
so leaving only sourcepreview is not complete Q integration. Exact existing
mesh found at hero-lanes/0040/symmetra_collision_174.json (identical0002/barrier-
runtime copy),368bytes/two authoredtriangles, SHA256
 d11a09fc7224c2b73819a06a40f9dbb1cb35ff681c373aa1891a987de6eedfe8,
matching canonical GEOMETRY_SHA256. Root may choose original0040 path in v9.
Next C29: in the same existing Photon host, mount actual originalQ/plane fallback
when no mesh configured and retain configuredmesh path, onecast/HP/lifecycle.
No syntheticmesh, newwallmanager or fixture-only patch. Then continue authorized
NPC bridge and original Mei Blizzard, sampled Cryo history, Sombra/Ashe remainder.

C28 SHA4fd1d4f7 delivered. Clean sync root7e9de0af preserves affiliation capture and all accepted C27/A/B changes; coordination only. C29 Photon sourceplane/Q with original0040 exactmesh, existingonehost.

C29: original Photon Q/plane and configured mesh consumers
-------------------------------------------------------
Same existing photon_barrier.Barriers retains one cast/actor health/lifecycle.
When geometry is None, confirmation uses original0016 placement, actual Q creates
156C actor and original plane ray/closest contacts; configured original0040 mesh
still delegates Geometry.cast/closest and canonical placement. No invented mesh.
Existing interrupt helper is now consumed on control/death; deployed wall remains.
Ordinary melee now blocks body through Photon and damages its shell exactly once;
B12 hammer uses analytic capsule/plane contacts plus existing surface seen keys.

Public contact API: manager.contact(actor,origin,direction,reach,radius=0.0)
returns distance|None; trace(shooter,...,*,team=None) returns (distance,actor)|None.
contact's source plane uses centre ray (source behavior), configured mesh uses
existing cast. capsule_contacts(actor,a,b,radius) returns real contact points.
B29 FireStrike can reuse these; do not skip Photon merely because geometry=None.

Exact verification with v9 environment: python -B -m unittest
 test_game_moira_photon -v :9pass7.625s,
worker-c-photon-mesh-v9-final-check.log. v8 same production Q/plane with eight
original common methods invoked directly (class has mesh-only skip decorator),
all8pass; only mesh-specific invalid-confirmation/rotation excluded,
worker-c-photon-plane-v8-final-check.log. Assertions unchanged, no fake geometry.
Actual real Q plane probe traces 1000m sideways at distance5; actual existing
hammer contact lowers4000->3945 once and repeated seen set prevents duplicate.
Actual v9 Q probe at1000m sideways misses, proving authored finite mesh bounds.

Shared exact v9 command: python -B -m unittest test_symmetra_charge
 test_delivered_reinhardt_winston_barrier test_game_combat.DamageTests
 test_doomfist_punch -v :52run46.521s,41pass4fail7errors retained,
worker-c-photon-shared-check.log. Charge9,Damage8,Punch21 allpass; Win suite3pass,
4fail7errors: FireStrike shell remains700vs600, shatter body455vs500, float point
1.1000000238vs1.1, controlled/FixedBubble.ray/old shield observer protocols absent.
B29 is independently integrating Win FireStrike/Shatter consumers. No assertions
changed or omitted from that shared run. First failing plane/mesh logs retained.
Recommend v9 exact original0040 mesh profile for configured geometry; v8 remains
functional original0016 plane mode. No input file bytes changed.

Boundary: original private NPC/wire observer equivalence not established; no new
alias manager. Sentry retained-history phases, sparse Photon expiry charge clock,
generic strict placement, sampled Cryo and Blizzard remain actual pending gaps.
Next independent narrow batch: authorized autonomous navigator consumes existing
movement_mods for partial frost slow; preserve mover/clock/control/path strategy.
Then original Mei Blizzard/Cryo, Sombra and Ashe. Root also flagged DeviceWorld
old symmetra_portal imports: adapt actual fight.portals endpoint/service consumers,
not a name alias or second manager. Remaining rough estimate16-32 active hours,
low confidence, excludes root reception and client acceptance.

C29 SHA1bcd0e56 delivered. Coordination sync898cd33b accepted root intake;
CHANGELOG merge preserved both sides, shared code merged without conflict.

C30: authorized existing navigator frost movement bridge
------------------------------------------------------
Only npc_navigation.py changes: existing drive receives match, reads the same
fight.movement_mods; tracks existing source-script plus imposed modifier on the
same Mover and scales explicit MoveTo speed (Mover's MoveTo bypasses run_forward).
Existing ledge approach uses this helper when a real body world is present;
released ledge still has no horizontal target. No second state, Mover, Frost,
clock, control policy, input, path or search-budget change. Existing full freeze,
root/immovable/control gates remain intact. Preserve original _drop_step call
signature: initial combined check exposed one existing mock assertion against
signature, fixed code rather than editing the original assertion.

v9 exact check: python -B -m unittest test_npc_navigation test_mei_frost -v
46allpass14.815s, worker-c-frost-nav-final-check.log. Before ledge extension,
navigation37allpass6.143s. Real prepared Trooper (1D40/controller), actual
fight._frost_hit(target,source=player,initial=.5), same navigator.mover identity,
20worldticks distance1.6123046875 normal vs0.8056640625 slowed. Initial probe used
navigation's deliberately scriptless fixture and was invalid for Frost because
normal cleanup removes status on bodies lacking scripts; final probe uses actual
prepared Trooper body/VM, not a fake status receiver.

Next C31: mount original mei_blizzard.py flight/area/retire into same existing
projectile/deflection/absorption/status hosts, then normal-import original full
Mei ultimate/frost source modules when their real dependencies are mounted.
Keep sampled Cryo history and old wall/shield/deflect observers explicit; never
replace actual consumers by aliases or weaken strict guards/assertions.

C30 SHA55932da4 delivered.

C31: actual original Mei Blizzard owner
---------------------------------------
Original mei_blizzard.py flight/area implementation now consumed by Combat._shoot
064A/st3; one projectiles lease, actual ballistic chord/terrain/Mei wall anchoring,
200ms delay/4250ms area/20DPS, independent per-target200ms frost channel, existing
entity allocator/viewer create-destroy, world ticks/catch-up and body retirement.
Existing Combat._deflector supplies single Genji/Matrix/Sigma service and actual
projectile tag admission, with existing Grasp output plug for zero-damage consume.
No old source defense managers or module-name aliases. Reflection reassigns actual
owner/body and outgoing snapshot; existing _damage is source_scaled to avoid
applying captured Nano/Orisa twice. Defense/phase queries use existing actual
Cryo/phase hosts. Stored manager/flights/areas registered, including area deadlines,
static-impact side and source samples. Original passive storage test passed.

Restored original tests/test_mei_blizzard,wall,wall_placement,control,deflect,shields.
Only fixture API/import adaptation: delivered combat fixture name; Genji's HERO/
active observer -> existing deflection.GENJI/defenses.deflect_state, shield fixture
-> actual barriers.Bubble/defenses.ZARYA (old Bubble constructor still not claimed
equivalent); one raw frost tuple assignment -> combat.Frost(sample=originaltuple).
All original literal values/counts match AST Counter; no expected values edited.
No fake observer/health view, no second pool, no tests removed. Old shields observer
errors remain visible instead of aliases. The old cryo sampled consumer is still
not mounted; current barriers.cryo_heal remains sole healer pending next batch.

Exact v9 environment+test_environment commands:
python -B -m unittest test_mei_blizzard -v :21run106.663s,19pass1fail1error(two
subcases). worker-c-mei-blizzard-second-check.log. The error was raw tuple fixture
in frozen-input case; after actual Frost(sample=...) adaptation that original case
passes in the shared command below. Thus20/21 original cases pass across these
runs, not a fresh final all21 run. Wall near-face test expects one flight after32
frames but actual current wall has already anchored it (0flights); preserve original
assertion/geometry/timing rather than delay a real hit for the check.
First failed matrix_definitions=None integration log retained; final production
uses existing _deflector current tag policy, not an ad-hoc nullable catalog check.

python -B -m unittest test_mei_ultimate test_mei_deflect -v :48run44.488s,
40pass7fail1error,worker-c-mei-blizzard-ultimate-deflect-check.log. These are normal
imports and full original classes, not earlier AST selection. Ultimate31:29pass,
wall damage400vs325 fail and old fight.shields observer error. Deflect17:11pass,
remaining6 differences include spray damage/freeze, wall flight already anchored,
post-duration/rear icicle damage50vs125, spray230.75vs250, static cover175vs250.
Actual airborne Blizzard reflection ownership, area damage/frost, rear/allied
non-reflection, original-owner switch survival/reflected-owner retirement allpass.

python -B -m unittest test_mei_frost_sources -v :full normal module16run37.673s,
15pass1old fight.shields observer error; worker-c-mei-frost-sources-full-check.log.
Includes formerly unrun actual spray+Blizzard channels/two areas/Cryo cleanse and
wall source isolation. Source file still byte-identical to original delivered ref.

python -B -m unittest test_mei_blizzard.BlizzardTests.
 test_frozen_input_cannot_start_and_freeze_during_windup_prevents_launch
 test_game_combat.DamageTests test_doomfist_punch test_game_runtime -v:
30actualtests pass21.621s,plus1 loader error because module name was mistyped.
worker-c-mei-blizzard-shared-check.log. Correct separate command:
python -B -m unittest test_game_script_runtime -v :17pass.909s,
worker-c-mei-blizzard-runtime-check.log. Do not count the mistyped loader as a
runtime implementation failure or claim its group passed as written.

Next C32: reconcile original CryoHealing sampled VM/status history with existing
barriers.cryo_heal into one actual health consumer, then original normal Cryo
checks and status timing. Sombra/Ashe remain actual unmounted owner modules;
root flagged old DeviceWorld/driver symmetra_portals name lookups to adapt to
actual fight.portals and symmetra_portals.py, with endpoint attribution/EMP/lock.

C31 SHA9d13d1d9 delivered; root receiving. Ownership update:0200 Ashe formally
transferred whole to A (no C WIP), following prior0065 transfer. C retains0006,
0008,0015,0016,00DD,012E plus stated shared-owner reconciliation.

C32: original sampled Cryo health consumer
-----------------------------------------
Original mei.healing_sample and CryoHealing now drive actual cumulative healing
from Combat.command, including deferred body reports. The previous native27
barriers.cryo_heal claims the VM report but does not independently heal. One
Combat._heal_target writer retains actual pool, health sync, stats and effective
Mei ultimate income; optional healing_scaled defaultsFalse, only the already
sampled Cryo amount skips reapplying received scalars. Existing generic healing
behavior and other owners' reports remain unchanged.

One cryo_samples ledger per actual body/activation; retire removes it. Original
three-field status rows remain supported, an optional fourth actual received
scalar records canonical HealingEffects in that same chronological history.
Actual source interval starts/ends are sampled before prune/refresh/cleanse and
status replacement; legacy Vitals interval rules remain, no duplicate pool or
manager. Existing HealingEffects install/retire/immunity/prune hooks call the
same player-specific consumer, never NPC heal or input behavior. Source original
sample/cumulative integration still owns its timing; no fake observer alias.
Stored cryo_samples and original CryoHealing dataclass registered; original real
passive storage check passes. Node27 report no longer doubles sampled health.

Exact v9 checks:
python -B -m unittest test_mei_cryo test_baptiste_healing_status -v:
28run157.559s,25casepass,7failedsubcases across3cases, noerrors,
worker-c-cryo-sampled-first-check.log. Cryo14:12pass, original inflight
rocket.hit_entities missing defender and held-secondary health200vs125(two
subcases) remain identical named C27 boundaries. All full200/self-only, cancel,
cooldown, retirement, deadowner, input/protection and passive storage cases pass.
Baptiste14:13casepass, oneNPCcase4subfail Enforcer/Assassin hostile/friendly.
Root supplied independent v6 original79-group baseline and actual split-pool
probe parallel-merge/root-bap-shield-status-probe.log: hostile expected18.12/
actualHEALTH18.12+SHIELD81.6, charge18.119987; friendly expected103.44/
actualHEALTH80+SHIELD100,charge101.879959 with full-health overheal. These bodies
have original100shield and original total assertion omitted concurrent shield
recovery. Do not remove shield implementation or alter expected values.

python -B -m unittest test_mei_ultimate.UltimateTests.
 test_actual_cryo_credits_only_effective_healing_and_not_overheal
 test_mei_ultimate.UltimateTests.
 test_combat_generation_zero_disables_damage_and_effective_cryo_income
 test_game_combat.DamageTests test_game_script_runtime -v:
27allpass5.283s,worker-c-cryo-sampled-shared-check.log. First two original
full-heal/cancel checks also independently pass6.493s before broad check.
An initial manually guessed nonexistent test name was corrected to the actual
original method; it supplied no verification and is not counted.

Actual prepared Mei/Cryo + actual selected HealingEffects.Spec/_install amp
probe:20ticks heals24.000000000000014 HP (base16), same cryo_samples object and
Vitals, no double scaling. Anti probe revealed existing cross-owner difference:
HealingEffects.immune(target) isTrue while Cryo is active; typed anti is cleansed
by existing prepare and legacy healing_block cleared by _cleanse_biotic_block.
Therefore原00DD Ana/Cryo anti-persistence expectations are NOT proven equivalent.
Keep existing actual immunity/cleanup and original source assertions until that
specific policy is reconciled; this batch does not weaken current modifiers.

Next C33: Sombra owner/hack/stealth/translocator with actual existing Controls,
health packs and device endpoints. Root specifically flagged DeviceWorld and
BodyWorld old symmetra_portal lookups; adapt actual fight.portals ownership,
EMP/lock consumers rather than old-name alias manager. Mei own modules now
consumed, but source00DD Walls same-name footprint/lift/lift_clear/lift_positions/
move_bot/move_player/show/splash/splash_contacts/step_lifted_bot/volume_clear are
not present under those names in current host; many current geometry/physics
helpers differ. This is pending consumer-equivalence audit, not proof either
that those features are absent or that the entire delivery is complete.
Also retain0008 impact-time force cover,0015 NPC interlock,0016 retained sentry/
sparse charge expiry/generic strict placement and existing public observer gaps.

C32 SHA69f2d1c2 delivered. Clean intake sync722a8f33 incorporated root NPC phase2,
A Junkrat charge/Tire and B barrier consumers; no shared-code conflicts.

C33: original Sombra spread/Camo consumers
-----------------------------------------
012E original owner modules are sombra_camo/spread/replay (not a sombra.py file).
First two copied byte-exact and now actually mounted: spread in real _shoot after
other scoped aim policies; Camo received-damage graph entry with actual hit_tags,
original exclusion values and source clock dating. Bind existing world frame
anchor for Sombra, preserving offset between client/body and world frames.
Original480-step bounded Camo next_frame consumer advances accepted exit/cooldown
without a movement pose or fabricated button press. Existing Player gap driver
retains control, state and clock ownership. General Sombra EMP/passive replay
still needs original sombra_replay and translocator service adaptation.

Original runtime.send_message/send_instance_message optionalkw at installed only
in those two existing functions: same Event.post/message_entries queue, default
now, no changes to native dispatcher/strict/type/budget. Root approved and owns
reception conflict with B33's separate native early gate. Sombra damage entry
uses source012E Pharah primary/Barrage loadout ids to date the other receivers
as original; Camo individually dates its own receiver, mapped into actual anchor.
Original scopes for other heroes unchanged; no generic duplicate damage event.

Restored original test_game_sombra_combat/spread/npc. Combat fixture only maps
one source tags keyword to existing hit_tags argument; all literals/assert
expectations retained. Spread defers actual_npcs import into its own NPC class
setUpClass so ordinary original spread methods run while missing legacy NPC
fixture dependencies remain an explicit class error. The original NPC fixture
is preserved, not replaced by a mock/fake status provider.

v9 exact: python -B -m unittest test_game_sombra_spread test_game_sombra_combat -v
47methods64.400s;36methods pass,5failedcases,6errorcases plusNPCsetup error;
unittest reports failures5/errors11 counting subcases. Second log
worker-c-sombra-camo-spread-second-check.log. Spread9:7pass, near-pole downward
192vs184 and far burst lacks original critical-hit mixture fail; true sampled
rays/ramp endpoints/seed causality/reproducibility/static cover allpass.
Combat38:29pass; fails sprint interrupt, primary/melee availability afterHack,
lethal world-only Helix death deadline. Sixerrorcases are raw old fight.hacks
observer/list protocol and absent test_game_sombra_grenade fixture. NPCsetup
imports missing original phase_defenses module; no runtime alias added to fake it.
Actual Camo enemy gun/melee/Helix break, excluded tag keeps it running, zero damage,
primary exit, speed, cancel and original long-world-gap cooldown allpass. First
run lacked canonical optional runtime at and retained log records real TypeError;
second production implementation has no such errors.

Shared exact: python -B -m unittest test_script_runtime test_script_runtime_budget
 test_game_script_runtime test_game_combat.DamageTests -v :46allpass2.613s,
worker-c-sombra-runtime-shared-check.log. No extra tests or acceptance matrix.
Next C34: original translocators actual entity graph/physics/pool/recall and
Sombra replay. Original Controls owner/list/application lifecycle and actual
Portal endpoint/EMP services need reconciliation; no second manager/name alias.
Source018? retain exact ref012E. Remaining C own active-work rough8-16hours after
Ashe/Junkrat formal transfer, low confidence; shared reception/client not included.

C33 SHA8e7b300f delivered.

C34: original Translocator actor-backed host
-------------------------------------------
Actual0C19 non-predicted volley now consumed before projectiles.fired, so no owner
lease/second Rocket. Original translocators.py authority now runs world ticks,
finite-radius seven-sample cast, gravity20/bounce/settle, recalled checked capsule,
original source graph/recycle message, health5/lifetime and body/death retirement.
Use existing Combat.allocate_combat_entity; each entry is the same actual
actors.Deployable object in fight.deployables and ordinary _targets, sharing
one Vitals/entity. Source sphere target centre preserved by narrow Target mapping.
Original until_tick operational API proxies the existing expires_tick field, one
actual TTL, including setters used by original checks. Generic actor team is
captured at creation consistently with current server policy; source raw property
read owner.team dynamically. Runtime team mutation parity is not established.

Original entity record file is exactly012E/translocator-entity-research.json,
SHA256 e5781249ad864e4265abb609a41bbcf05cdbcc8b41ec336dc363bc36381aad43.
Set ORPHEUS_TRANSLOCATOR_DATA to that immutable original path; do not fabricate a
translocator_174.json or copy full assets. Its entity1575/extra_graphs are read by
original data() and supplied to the existing BotScript VM. Generic BotScript now
accepts original optional record/world/resolve_graph/source_entity; default NPC
world/controller/difficulty/pool paths retained. create_body_graphs and initial
helper use optional resolver, defaults global graph lookup. External BodyWorld
gets actual definition1575 and existing component registration, not NPC-only
controller fields; source association attached before instance init. Record has
no controller; no extra controller created. Source new recall classes preserve
strict unsupported guards. Nonstrict0C1A health hook on existing HealthPool state,
original narrow context queries precede unrelated typed interfaces only for
C19/C1A and recognized configs. B33 native early gate must remain on reception.

Same BodyWorld forwards actual owner messages to live beacon component; this was
a true missing consumer, not a fixture expectation adjustment. Checked recall
uses existing knockback retirement/Orisa recipient release, original mover
teleport reset and current shared body/movement driver. Source recall_poses is
cleared at first settled real pose. Ice Wall gets original cast_ray adapter over
current real pillar contact and immutable map, not another geometry manager.
Actor removal clears exact output, owner message/cooldown, controls, component
registry/foreign watches/instances, viewer destroy, Vitals/history/replacement.
New state/manager/actor causal storage registered in both existing serializers.

Verification v9 environment+test_environment plus explicit original
ORPHEUS_TRANSLOCATOR_DATA path above. Full raw test_game_sombra_translocator.py
copied byte-identical. Its top imports still depend on old pharah/shot_guards,
control and NPC fixture protocols; do NOT claim full normal-module execution.
Ten original TranslocatorTests methods compiled as untouched class AST with real
globals: math/os/unittest/Path/mock/replace, fixture=test_game_sombra_combat,
stored=test_server_snapshot, actual combat/npc/npc_control/reinhardt_barrier/
stats/translocators/world/FIRE/Command/expr/OWNER_HEALTH/send_instance_message.
No fake fixtures, health or assertion values. Exact suffixes (prefix test_):
actual_volley_binds_server_identity_and_original_health_pool_without_lease;
reconstructed_arc_lands_without_damage_or_endless_energy_gain;
gap_and_continuous_entity_motion_agree;
second_press_consumes_beacon_and_relocates_then_lands_at_destination;
manual_recycle_routes_original_message_and_enforces_original_cooldown;
owner_and_ally_actual_fire_do_not_destroy_the_entity;
hero_replacement_retires_old_entity_and_does_not_transfer_it;
actual_owner_death_retires_the_owned_entity;
active_entity_is_captured_with_cross_component_watchers;
enemy_actual_rifle_destroys_five_hp_entity_without_hero_kill_or_respawn.
10allpass38.961s, worker-c-translocator-core-second-check.log. Initial9 had5pass/
4subfail all due missing beacon message relay; retained first log. Real separate
actual throw/300tick probe:5HP, identity registered, sameactor in deployables,
settled y0.20001; real cross-wall recall z14.5166015625 vsdevice14.5165007043269,
owner component faultNone. After ten-case run, tiny precise foreign-watch
cleanup and settled-pose recall_poses consumer added; broad default-path check:
python -B -m unittest test_game_npc test_npc_navigation test_script_runtime
 test_script_runtime_budget test_game_script_runtime test_game_combat.DamageTests
 -v :90allpass9.279s,worker-c-translocator-shared-check.log. No full journal gate.
AST duplicate-method audit clean (property setters excluded); raw fixture hash
unchanged. Do not count selected class execution as all source translocator checks.

Next C35: original accepted owner replay (sombra_replay, translocator recall-pose
world-only physics and release/expiry boundaries), actual Controls list/application
protocol and healthpack service; then actual Photon/Sentry/Torb/Portal device EMP
and Hack endpoints. Root flagged actualfight.portals/symmetra_portals mapping;
no old-name manager alias. Source Control.locks already holds lists after current
root intake: audit against original BodyHack consumers instead of assuming a
new second hack manager. Raw old fight.hacks observer is not an actual consumer
absence by itself. Preserve missing unmounted consumer boundaries and failures.

C34 SHA9b124b82 delivered. Clean sync root intake nowc81876df; sole conflict
server_snapshot dataclass line resolved preserving both Translocator and Ball.
Ashe and Ball existing implementation all retained, no worker-tree edits.

C35: original skill-lock history query required by B38 Grapple
------------------------------------------------------------
B source01CA/sombra_emp.first_lock_after(script,frame,tick) reads original
emp_previous_windows+emp_window, starts//script.frame_ms, first frame<start<=tick.
Actual sole SombraControls already has entity->list[Lock(application,until,pulse)],
which expired effects remove; no historical windows existed. Add lock_history
inside that same manager: entity->(actual recipient component, component-ms
windows). Record both real Lock append sites (body and device), directly from
application.start and evaluated duration. Overlap merges as original; only
unconsumed older windows survive, latest remains after active effect expires,
recipient death/body retirement removes its record. No second EMP manager,
application, pool, generic full-control window or fake old-name alias.

Controls.first_lock_after(script,frame,tick) public API: bounds are actual
recipient BODY frames, not world ticks. Stored timestamp is application.start
on its real recipient component; identity check prevents another VM generation
inheriting the record. The API does not infer1000+ bodyframe vs50worldtick offset.
B explicitly knows original caller's fight.tick gap and is retaining it until a
real anchor/caller mapping is available. Generic control/sleep/frost windows must
not be used as Hack locks because Hack preserves ordinary primary/movement.
No argument semantics guessed to make that mismatch disappear.

In existing Controls.interrupt, nonhack_only for actual Ball calls existing
ball.cancel_grapple(fight,player) before stopping blocked Ability states. This
releases current tether now through its sole motion host. B38 owns actual
ball._grapple_interrupt_after call to this API and full command/retire/death/settle
consumers; do not edit Ball or install a fakeNone stub. B source suppress-button
latch is being adapted to existing Controls.held, not a second EMP field.
Stored Controls field registration includes actual lock_history tuple/component
and windows, no new generic BodyScript field or timer.

Actual source SombraCombatTests real scene and press(2,55) installed a real
0B1F Lock on target component. start=application.start//script.frame_ms=1090;
first_lock_after(start-1,start)==start and first_lock_after(start,start+1)==None.
Actual effect world expiry via Controls.update removes active locks, but original
query still returns1090. This tests production record/query, no fakeapplication.
Exact v9 command: python -B -m unittest test_game_ana_sombra_control
 test_game_script_runtime test_game_combat.DamageTests -v :40run56.723s,
39pass1originalskip (requires all five pinned external barrier meshes),
worker-c-sombra-lock-history-check.log. Existing skip/asserts unchanged.

Next C36: original Sombra charge host and replay (source ultimate_charge class
must be scoped Sombra and adapted beside existing canonical ultimate_charge
functions for Rein/Genji; never replace/double other owner charge). Original
012E/ultimate-context-research.json is its explicit input. This supplies actual
next_passive_frame prerequisite to original sombra_replay accepted-reaction
consumer. Then world-only Translocator throw/recall/lifetime, orphaned incoming
applications and actual Portals/Sentry/Torb device consumers. Pending original
Sombra generic .hacks fixture names must adapt to actual Controls.locks if only
observers; no alias manager and no claim absence solely from old import/attr.

C35 SHA4f866474 delivered; B38/root accepted. Continue C36 without waiting for
C37 world replay or full acceptance. Root owns missing original tools CLI; C has
no tools WIP. Candidate v14 selects immutable original ultimate/translocator data.

C36: original Sombra0B8B charge service
--------------------------------------
Relevant original012E ultimate_charge.UltimateCharge methods are mounted as
sombra_charge.Charge / fight.sombra_charge: enabled/_owner/initialize/sync/
next_passive_frame/prepare_retirement/retire_body/_target/_tags/native/damage.
Source Sombra formulas/cadence/admission stay in actual0B8B graph. Copy scoped to
Sombra; canonical ultimate_charge.py functions/context admission for Rein/Genji
unchanged, Roadhog and other delivered hosts remain sole existing owners. Do not
copy source RETAINED_HEROES/roadhog host across them. Unused source hosted_catalog
state is not added. Generic cost/60 and damage gain excluded only when this
original selected Sombra host is enabled, not for other heroes.

Native adapter is narrow nonstrictSombra0B8B before generic typed interception;
only source-recognized configs handled, NotImplemented falls through. Input is
loaded during real owned warmup (world.hero is actualSombra), and source native
resolves actual controller entity, not fake/aliased body. Source natural wait,
2426/config-change event,12918 debug pause,842 actual ultimate Ability suppression,
DAMAGE_DEALT params/tags and cap run through the existing VM. Before/after body
frame sync prevents a generic second clock. Actual resolved net pool loss passed
once; noncharacter device row remains ineligible. Static classification depends
on supplied source body/tag context; no invented missing tags or broad filters.

Original same-hero retention uses current controller/body lifetime: optional
Match.switch_hero preserve_ultimate=False added alongside preserve_pose/start_frame;
only Sombra automatic respawn suppliesTrue, other owners keep prior call and
retention paths. Explicit switch defaultsFalse/reset. Real leave clears pending
retention; body retirement clears pause key. Actual old/new graph cost clamps
saved points. Original active/paused/context/retained causal state registered.

v14 input path D:/overwatch1.7.4/verification/hero-lanes/012E/
 ultimate-context-research.json (actual directory includes space after overwatch),
SHA256 c7be92f1e83720d722a8e82cd1019e0178ad8a5c980862fc89f00a1a9106eee4.
Use v14 environment.json.environment/test_environment, including
ORPHEUS_ULTIMATE_DATA and ORPHEUS_TRANSLOCATOR_DATA; no source input bytes changed.
Raw source Charge fixture copied with only actual service API/import adaptation:
fight.ultimate_charge -> fight.sombra_charge, old .hacks -> actual
.sombra_controls.locks, source tags keyword -> hit_tags, lazy NPC import in its
own class setUpClass. AST literal Counter exactly matches source; expectations
unchanged. No legacy manager alias or fake fixtures introduced.

Initial normal module command python -B -m unittest test_game_sombra_charge -v:
15player methods52.434s; first run8pass5fail2error plusNPCsetup error (reported
failures5/errors3). Two fixture API errors corrected, real retention missing
consumer fixed; same-hero/excluded-tags/actual EMP-spend3 allpass4.222s. An initial
signature patch had missed current multi-line switch_hero definition; fixed
actual kw declaration before final checks, never weakened the original check.

Exact final core: normal import SombraChargeTests, unittest.getTestCaseNames,
exclude exactly these4 pending world-only replay methods (all original asserts
remain in the module):
 test_lethal_rifle_before_natural_deadline_preserves_only_earned_charge;
 test_natural_pause_restores_the_existing_profile_multiplier;
 test_world_only_emp_recovery_keeps_natural_charge_deadlines;
 test_world_only_natural_charge_caps_and_admits_a_fresh_emp.
Other11 allpass42.025s,worker-c-sombra-charge-core-check.log. Includes exact5/s,
cap/scalar,actualpistol/melee netloss, armour/shields/fataloverkill, no-credit miss/
wall/ally/receiveddamage/noncharacter beacon, ultimate suppression/tag exclusions,
paid EMP spend, retention/reset and actual full context/pause/deferred-retention
snapshot. Core excludes world-only subcase method wholly, not just its failing
subcase. The untouched full module and first failed log are retained.

Shared exact python -B -m unittest test_genji_passive_charge test_symmetra_charge
 test_game_torbjorn_charge test_orisa_charge test_game_script_runtime -v:
97run14.538s,39pass58skip (Torb/Orisa fixture env markers absent), no failures.
worker-c-sombra-charge-shared-check.log. Do NOT claim97activechecks. To exercise
actual other owners, same v14 plus ORPHEUS_TORBJORN_GRAPH_BUNDLE pointing at the
existing canonical base game_graphs_174.json.gz (supplement stays selected) and
ORPHEUS_ORISA_CHARGE pointing at existing canonical data/orisa_charge_174.json:
python -B -m unittest test_orisa_charge
 test_game_torbjorn_charge.TorbjornChargeTests.test_native_passive_timer_adds_five_per_one_second_wait
 test_game_torbjorn_charge.TorbjornChargeTests.test_native_combat_and_passive_modifiers_are_not_bypassed
 test_game_torbjorn_charge.TorbjornChargeTests.test_native_permission_and_enabled_links_reject_charge
 test_game_torbjorn_charge.TorbjornChargeTests.test_native_cap_then_actual_damage_completes_charge_and_ultimate_starts
 -v :22run11.405s,17pass5originalNPCdataset skips,
worker-c-sombra-charge-owner-check.log. No test skip conditions modified.

Next C37: mount original sombra_replay to consume actual next_passive_frame and
accepted EMP/Translocator releases, same physical Mover for world-only recall
poses, source retirement/application clocks. Avoid duplicate C33 Camo consumer
and preserve genuine owner/world frame anchor instead of assuming equal epochs.
Then Sombra actual Portal/Sentry/Torb device consumers and remaining source wall/
retained sentry/Pharah impulse cover/Bastion NPC boundary audits. Current remaining
rough14-28 active C hours (low confidence, overlapping categories), not a calendar
promise; largest unreviewed surface is shared consumer equivalence, not raw old
observer names. Do not hold functional batch for these unmounted replay checks.


## C37: original Sombra owner replay on actual world/body anchors

Parent base3ae8e793, includes root16e57a68 Mercy driver indentation fix.
No driver._run edits in this batch. Mounted the original012E sombra_replay
owner functions and bounded480-step accepted-reaction scheduler, adapting the
actual sole sombra_charge/translocators hosts. The old Camo-only world consumer
now delegates to this single scheduler. It selects original Camo/EMP/throw/recall
and sparse natural-charge deadlines, not fresh player input. Original64-frame
EMP early replay precedes retained combat tail. Endpoint remains open; retained
intermediate ticks finish their accepted reactions after world contacts.

body_frame/world_frame derive solely from actual script.world_frame_anchor.
Temporary match/combat clocks are restored after replay; application releases
use their mapped world time. Neutral replay accepts only original Translocator
volley0C19, not pistol fire. Airborne recall uses the existing physical Mover and
Player._step_body_pose, with explicit replay_accepted propagated through early
branches. Optional flags defaultFalse. Beacon expiry cooldown now translates
world expiry into the same owner body clock, instead of assuming identical epochs.

Fixture adaptation: one original charge assertion reads round(Lock.until/TICK)
instead of absent old until_tick. Expected at+334 and all original literals/
assertions unchanged; no new runtime alias/duplicate Lock expiry clock.

v15 environment/test_environment selected, original inputs unchanged. Normal
python -B -m unittest test_game_sombra_charge.SombraChargeTests plus original
SombraCombatTests damage-Camo-world-gap/camo-speed/primary-exit/lethal-Helix and
 test_game_script_runtime -v:36run63.218s,35pass1preservedfailure. All15 normal
charge methods now pass, including all4 formerly pending world-only methods.
Original Helix lethal fixture still death_tick None vs cast+2=1053; assertion
retained (also part of original C33 known lethal-world difference), not claimed
fixed. worker-c-sombra-replay-owner-check.log. Initial run14pass1oldLock APIerror
was corrected only by the real expiry query adaptation above.

Five selected original TranslocatorTests methods compiled as untouched class AST
with real globals (normal full module still imports unmounted old control/NPC
protocols): world_only_throw_releases_on_time_and_unlocks_recall;
world_only_airborne_recall_matches_descent_and_landing;
camo_replay_preserves_an_already_accepted_throw;
world_only_authored_lifetime_starts_cooldown_at_expiry;
authored_manual_recycle_starts_cooldown_on_the_device_clock.
All5 pass44.677s, worker-c-sombra-replay-translocator-check.log. No method/assert
AST edits. Existing10 C34 basic cases not represented as rerun here.

Actual differing-clock probe: switch original Sombra with start_frame2000 on
actual world1050 => anchor(1050,2000); original charge deadline body2063 maps to
world1113, update1114 yields exactly5/faultNone, bodyframe2063. Log
worker-c-sombra-replay-clock-probe-corrected.log. Earlier probe(1050,1000) also
awarded5 but had an invalid probe-only at<1000 assumption; not implementation
failure and not used as passing evidence. Parse/duplicate-method audit clean.
Initial shared command misspelled test_game_npc_navigation/test_game_movement;
13actual Genji cases passed but two module imports errored. Corrected command
uses actual test_npc_navigation/test_game_mover, not altered test fixtures.

Remaining original sombra_replay application_replay_until/advance_applications
not yet mounted: actual non-Sombra recipients often lack world_frame_anchor.
Next C38 must record known application recipient-clock/world-clock association
on the same actual Lock, consume incoming EMP/source-retirement waits without
recipient input, and preserve live-source world expiry policy. Do not guess
body1000+/world50 correspondence or create another application manager/timer.
Then actual Sombra device Portal/Sentry/Torb consumers; remaining Mei wall,
Symm retained-sentry/placement, Pharah impact cover, Bastion NPC boundaries remain
as previously recorded. Functional C37 is independent of those remaining gaps.

Final corrected shared command: python -B -m unittest test_genji_passive_charge
 test_npc_navigation test_game_mover test_game_mercy_charge
 test_game_sombra_charge.SombraChargeTests.test_world_only_natural_charge_caps_and_admits_a_fresh_emp
 -v :115tests allpass175.104s,worker-c-sombra-replay-shared-corrected-check.log.
Includes all original Mercy scope/idle/retained charge and physical Mover checks;
no Python source edits during this corrected group. All original checks retained.
Next C38 priority refined by A55 actual evidence: canonical Controls.play uses
play_loaded_graph(contextKind0), so real0B1F has no Wait58 despite existing Lock.
Audit original0200 sombra_hack.invoke Queue.submit(request) and restore real
request start entries first, then incoming replay; no fake Wait/query alias.
