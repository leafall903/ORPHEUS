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


## C38: actual Hack/EMP request start entries and receiver predicate

Parent-authorized narrow runtime.Context.native bridge; root confirms no overlap.
A55 found actual0B1F application with no Wait58 despite outer Controls Lock.
Both existing Controls body/device delivery sites now use original PlayScript
Queue.submit(request) with already-loaded graph, native request params/context/
wire flag/schema and existing policy. Ready transient request executor only;
no second manager, resource provider, target acquisition or retained queue.
Cross-component device value deepcopy is retained via immutable Request/Resource
replacement, preserving original tags/source/pools.

Queue alone restored actual BB4AC733 start events/parameter bindings but did not
reach Wait58: generic typed filter returnedFalse for original0B1F node21. Mounted
original0200 predicate semantics on exact cfg identity in sole Controls.native:
0B1F node21 can_receive reads actual live recipient BodyScript/component and
existing14679 reject var;0B32 node8 is body receiverFalse, attached shields use
existing drain service. Context.native non-strict early forwarding only those
graphs and recognized exactcfg; all other cfgs returnNotImplemented before the
existing typed paths. Existing HACK owner native clauses now explicitly guarded
by graph ID so EFFECT/PULSE cannot dereference absent HACK node204. Strict mode,
existing typed body-owner unavailable guard and health/filter checks retained.

Actual ordinaryHack65tick and paidEMP31tick both produce real0B1F Wait58 active,
5000ms, faultNone; original parameters/source bindings now execute. First queue-
only probe failed Wait58; first predicate draft hit absent HACK node204 and was
fixed by exact graph guard before final checks. No fabricated Wait/state used.
Actual Ashe recipient with both attacks also passes exact original0200
instance_for/locked functions compiled unchanged AST: lockedTrue, start-inclusive,
end-exclusive andfaultNone. worker-c-sombra-ashe-original-query-probe.log;
worker-c-sombra-request-receiver-probe-corrected.log.

Original SombraCombat fixture old .hacks reads adapted to actual
.sombra_controls.locks; old until_tick queries use math.ceil(real until/TICK).
Same rounding used by C37 charge fixture. All original literals Counter exactly
matches frozen012E for both modules; expected values/assertions unchanged. No
runtime until_tick alias or duplicate expiry timer created.

v15 environment/test_environment; python -B -m unittest
 test_game_sombra_combat.SombraCombatTests
 test_game_sombra_charge.SombraChargeTests test_script_play_queue
 test_script_play_loaded test_script_body_owner test_script_entity_filter
 test_game_script_runtime -v:129run88.667s,123casepass,5failedmethods(8failure
records due4initialize subcases),1importerror. All15 charge and all76shared queue/
loaded/body-owner/filter/runtime cases pass. Combat32pass/5casefail/1error.
Actual paid EMP pulse/expiry without recipient commands now passes normally.
Remaining source assertions retained: Hack initialized start1270vs1271 normal
and1050vs1271 idle(4subcases); three old888 flag/sprint/Helix release cases still
None (C33 already had None flags); original lethalHelix death_tick Nonevs1053.
Source replacement method imports missing original test_game_sombra_grenade;
module not faked/skipped. Full log worker-c-sombra-request-start-check.log.

No Python source edits during final129 group. Duplicate-method and diff checks
clean. No client acceptance/network/publication. This is a functional delivery
batch; not held for incoming clock/source-retirement or old888 modifier consumers.
Next C39: actual recipient application/world clock association and original
advance_applications/source-retirement waits; audit original888 modifier and
restore raw original grenade/control helpers with real dependencies. Root owns
C37 mapped pose tick/history-tail correction; sync its accepted boundary before
editing replay further. Device Portal/Sentry/Torb original consumers remain.


## C39: original body-silence variable lifetime on existing Controls

Clean boundary merged root9f2da275 as a807cd83, preserving C37 root mapped pose
world tick and all7 history fields plus A55 original read-only Hack helpers.
Only CHANGELOG conflict, both entries retained. C39 does not modify replay/driver.

Original012E apply_body_hack explicitly saves entity_var888.base and setsTrue;
retire_body_hacks restores saved base after final effect. This was not a graph
modifier feature. Integrated on same Controls host as body_bases[entity] =
(actual component, originalbase). Body Lock creation setsTrue; final world expiry,
retire/death/replacement restores same component's base. Overlapping effects do
not overwrite the original value. Device locks do not receive character888 policy.
Exact causal field body_bases added to existing Controls snapshot registration;
no new pool/manager/input controller and no original expiry values changed.

v16 actual same profile plus original view catalog now selected. Exact command:
python -B -m unittest
 test_game_sombra_combat.SombraCombatTests.test_hack_blocks_actual_helix_and_releases_it_after_effect_expiry
 test_game_sombra_combat.SombraCombatTests.test_hack_keeps_enemy_primary_fire_and_melee_available
 test_game_sombra_combat.SombraCombatTests.test_hack_interrupts_enemy_sprint_while_its_input_stays_held
 test_game_sombra_combat.SombraCombatTests.test_hacked_body_death_retires_effect_and_ownership
 test_game_sombra_combat.SombraCombatTests.test_hero_replacement_does_not_inherit_the_retired_body_silence
 test_ashe_hack test_ashe_emp test_sombra_replay_pose_adapter
 test_game_sombra_charge.SombraChargeTests.test_full_snapshot_captures_context_pause_and_deferred_respawn_charge
 -v:21run63.770s,13casepass/5failedmethods/2erroredmethods/1originalSigmameshskip.
Error records5 = refreshWait58missing1 + four oldworld_barriers observer subcases
in one method. All original assertions unchanged. Log
worker-c-sombra-body-silence-check.log. Actual Soldier Helix suppressed then reused
at expiry, held Sprint interrupted, body death/replacement cleanup now pass.
Root C37 pose adapter and source full snapshot method pass.

Exact remaining failures: Soldier actualrifle health160vs180 (before final888
assert); diagnostic on same v16 with ONLY silence_body monkeypatched off gives
same160/888None, restored implementation gives160/888True. This diagnostic is
not a passing acceptance test; it separates888 code from changed baseline ray/
weapon geometry. Log worker-c-sombra-flag-rifle-baseline-probe.log.
AsheHack69/AsheEMP74 fail onlyexpiryQ spent0vs2240: earlier heldskills, ordinary
fire/ADS/reload/movement checks passed; root independently reproduces sameC38.
BOB charge test253 shots0vs1 and activegun test131 shots7vs6 are expiry edge
comparisons, not proof of wholly absent pause; detailed stacks sent A who owns
actual ashe/companions. EMP refresh142 realWait58 missing afterreapply needs
receiver lifecycle audit. Shell case only old fight.world_barriers AttributeError,
actual shared Win shell host remains fight.barriers.world. No dummy alias added.

Actual flag restoration probe starts888False, appliesrealHack65frames, confirms
saved(component,False)/888True, advances through originalworldexpiry1404 to1405:
888False/basesempty/faultNone. worker-c-sombra-body-base-restore-probe.log.
Next C40: exact original input latch/expiry-frame admission, reapply request
entry and incoming application world/body clock association/source-retirement;
keep true accepted pose anchors and preserve root C377-field history adapter.
Other retained nonappearance owner boundaries unchanged; no client/network test.


## C40: consume actual recipient lock history at the expiry input boundary

Read-only originalAsheHack endpoint probe observed world1504/time24.064,
Lock.until24.056 alreadyremoved, but authored Wait58 ends within recipient
frame1504. IncomingQ32 was unmasked andspent2240; no assertion/expiry value changed.
worker-c-sombra-expiry-input-probe.log retains failing baseline and exactinputs.

Existing Controls.lock_history now observes actual0B1F Wait58 start+ms and
retains its real end in the existing application interval. Original impact start
and first_lock_after component-domain API remain unchanged. input() admits
SKILL_INPUT using that same actual recipient component/frame window as well as
live world Locks, then uses the existing held bits/release path. It does not
extend Lock.until, add a timer/manager, assume equal epochs, mask ordinary fire/
reload/melee or use generic hard-control histories. Latest original window still
survives effect expiry as before; owner/component retirement cleanup is unchanged.

v16 environment/test_environment; python -B -m unittest
 test_ashe_hack.AsheHackTests.test_hack_locks_ashe_abilities_but_preserves_ads_reload_and_movement
 test_ashe_emp.AsheEmpTests.test_emp_blocks_abilities_without_suppressing_viper_and_releases_at_expiry
 test_game_sombra_combat.SombraCombatTests.test_hack_blocks_actual_helix_and_releases_it_after_effect_expiry
 test_game_sombra_charge.SombraChargeTests test_sombra_replay_pose_adapter
 test_game_script_runtime -v:36allpass53.598s,
worker-c-sombra-expiry-admission-check.log. Both originalAshe methods now pass
complete—including heldlock/fire/ADS/reload/movement, exactexpiryQrejection,
release andfreshQresource spend—and originalSoldierHelix release remains correct.
All15Sombracharge plusroot7-field/differing-epoch replay adapter/runtime pass.
No fixture/assert changes, no Python edits during group, diff check clean.

Next C41: incoming application reapply/start clocks/source-retirement and actual
Portal/Sentry/Torb device routing. Audit found legacy DeviceWorld assumes
fight.symmetra_portals andoldmodule endpoint APIs, while actualhost is
fight.portals + actor-backed PortalPair; actualSentries are fight.sentries
(symmetra_turrets.Turrets), actualTorb fight.turrets. These must consume their
real actors/components and unique Vitals, not mount legacy duplicate managers.
A owns BOB timeout/gun continuation; its exactexpiry/refresh stack sent separately.
C39/body888 remains merged unchanged. No client/network acceptance performed.


## C41: replace the original recipient graph before reapply

Read-only refresh probe: ordinaryHack Wait instance25 existed; actualEMP created
new effect27 with realWait58, while prior effect25 stayed first in actual
component.instances but lost its Wait through shared state lifecycle. Original
instance_for legitimately returned that stale first effect, causing KeyError58.
worker-c-sombra-reapply-baseline-probe.log preserves both actualinstances/vars.

Mounted the explicit original0200 sombra_hack.invoke recipient prelude: destroy
matching prior graph on the actual component before submitting current ready
request. Body-only delivery; existing device policy unchanged. One existing
component, same source/context/params andouter Lock lifetimes retained. Does not
fake/repoint query functions, add an effect instance, extend expiry or change
original template data. Existing outerworld locks/history still preserve their
independent accepted intervals until normal cleanup; actual priorVM retires.

v16 environment/test_environment; python -B -m unittest
 test_ashe_emp.AsheEmpTests.test_emp_refreshes_the_lock_from_its_own_impact_after_ordinary_hack
 test_ashe_hack
 test_game_sombra_combat.SombraCombatTests.test_emp_target_denies_hack_until_expiry_without_spending_cooldown
 test_game_sombra_combat.SombraCombatTests.test_paid_emp_matches_pulse_and_expiry_with_no_player_commands
 test_game_sombra_combat.SombraCombatTests.test_hacked_body_death_retires_effect_and_ownership
 test_game_sombra_combat.SombraCombatTests.test_hero_replacement_does_not_inherit_the_retired_body_silence
 test_game_sombra_charge.SombraChargeTests test_script_play_queue
 test_script_play_loaded test_game_script_runtime -v:79allpass71.646s,
worker-c-sombra-reapply-check.log. Originalrefresh distinctnewWait/laterend,
originalcooldown rejection/no-spend, sourceownership, paidworld-onlyEMP and
retirement all pass. All15charge andentireAsheHack class preserved. No fixture/
assert edits or Python edits during the group. No client/network validation.

Next C42: actual actor device consumers (Torb already consults soleLocks,
Sentry still lacks its originaldisablebeam bridge; PortalPair uses actual
fight.portals/Bot endpoints). Body source-retirement/incoming clock helper remains
unmounted; no equal-epoch assumption will be introduced to close oldfixtures.
C40 retained actual Wait input history andC39 base lifetime remain unchanged.


## C42: actual actor-backed Torb, Sentry and Portal device receivers

Replaced optional legacy torbjorn_turrets/symmetra_sentries/symmetra_portals
consumer lookup with actual fight.turrets actors, fight.sentries.sentries Bot
and fight.portals PortalPair endpoints. Character targeting/body invocation now
excludes those actual devices; attached Sentry eligibility retained. Original
Torb34/Sentry36F/Portal417+60A/2EC metadata applies only to those actual actors
and selected0B1F/0B32 queries, not a global character-tag implementation. Existing
ball_mines sole manager path including flightEMP and Baptiste device path retained.
B44 adds Mine exclusion andB45 preciseMine-only expiry on root; preserve both
those predicates/expiry when receiving C42 (C42 does not change Lock.until).

Existing receiver cache uses actual Bot.body_script.component when present,
not a second DeviceWorld VM. Same actor/entity/Vitals/team; no manager alias,
new actor, pool or source data. Oldbody entry loop excludes known device bindings,
so application delivered once. Actual Sentry lock clears its existing slow/aura
and existing Turrets.update skipsbeam; actual PortalPair interaction rejects
locked endpoints. Torb already had actual soleLocks fire gate, preserved.

Original endpoint tags/slots copied as readonlymetadata into actualPortals.
endpoint_pair/endpoint_owner expose existingpair and present original ownerVM;
no guessed owner after sourcebody retirement. Actual0B32 possessed-owner selectors
read that existingcomponent; BotWorld only original1624/1625 foreign slot bridge
returns the actualpublished Var when endpoint identity agrees. Other NPC,
controller, Archives andDVa slot routes unchanged; unknownreturnsNotImplemented.
Owner runtime body native and existingEMP drain use actual Vitals, not copied
endpoint shield storage. Parent explicitly authorized narrowBotWorld bridge.

Three actual fixture-driven probes on v16:
- TorbjornTurretTests().arena(), switch enemySombra, warm50, place relative to
 actual turret pose, aim realtarget midpoint, originalbutton2 untilapplication:
10sec/8912True/faultNone; Lock.application.component is actor.body_script.component.
 worker-c-sombra-turret-actor-positioned-probe.log.
- SentryTests.setup_arena/launch with originalwall(), switch enemySombra and
warm50; use TeleporterTests.place to synchronize actualphysicalBody outside
10m beam butinside15mHack. Originalbutton2 gives10sec/sameactualcomponent/faultNone.
Move back to(2,1,1) through samephysicalBody,80worldticks HP200stays200 andslowNone.
 worker-c-sombra-sentry-actor-physical-probe.log. Old8912observer isNone for this
 actualSentry; do not claim originalAnaDevice allasserts passed or fake that var.
- TeleporterTests.deploy(), enemySombra(4,0,0), warm50, paidEMP +35ticks:
 near entrance shield250->0; remote29.5m endpoint shield250unchanged but both
 endpoint originalowner-slot relay locks10sec, same Botcomponents/faultNone.
 Actualowner interaction remains atz0 whilelocked.
 worker-c-sombra-portal-actor-probe.log. Physical/real graphs, no injected locks.
Earlier probe mistakes retained: assumed zero-origin whileactualTorb mapspawn
(54.5654,1,43.6475); Sentry cast withinbeam interrupted bydamage, and later raw
position edits were overwritten by livephysicalBody because teleport was omitted.
These invalid setups are not feature/test failures or claimedpassing evidence.

Original32 allpass12.729s: threeSentryTests launch_attach_boot_damage_slow_and_real_movement,
actual_sentry_damage_credits_its_living_owner,three_beams_stack_slow_and_each_retirement_removes_only_its_effect;
TeleporterTests confirm_creates_two_authored_bodies_and_owner_can_use_both_ways,
pair_survives_actual_owner_death_and_dead_owner_cannot_use_it,
owner_leave_and_hero_switch_remove_both_endpoints;
TorbjornTurretTests build_then_actual_turret_fire_credits_owner,
owner_death_preserves_turret_but_hero_change_retires_it;
 test_game_npc test_game_script_runtime -v. v16 + originalTorbfixture marker to
existingcanonicalbase, additive supplement remainsselected.
worker-c-sombra-actor-device-shared-check.log.

Control regression exact python -B -m unittest
 test_game_sombra_charge.SombraChargeTests test_ashe_hack
 test_ashe_emp.AsheEmpTests.test_emp_refreshes_the_lock_from_its_own_impact_after_ordinary_hack
 test_ashe_emp.AsheEmpTests.test_emp_blocks_abilities_without_suppressing_viper_and_releases_at_expiry
 test_game_sombra_combat.SombraCombatTests.test_paid_emp_matches_pulse_and_expiry_with_no_player_commands
 test_script_body_owner test_script_entity_filter -v:43allpass60.997s,
worker-c-sombra-actor-device-control-check.log. No Python edits during finalgroups;
parse/duplicate-method/diff audits clean. Original fixtures/asserts unmodified.
No client/network validation. No fullAnaDevice acceptance claim; oldprototype
fixtures/private observers still need realactor adaptation with assertions retained.

Next C43: originalincoming application source-retirement/recipient-clock replay
and broad ability/logicalbutton consumers, normalimport fullSombraEMP/control/
grenade/NPC helpers with actual dependencies. Shareddevice retirement/ownerdeath
and expiry may expose crossowner originalsource protocols; persist, do not gate
functional commits. Symmretainedsentry/wall placement, Mei geometry and remaining
Pharah/Bastion boundaries remain as previously recorded.


## C43: original ability classification and logical-button consumers

Mounted frozen012E body_ability_allowed/body_button_allowed policy on the sole
Controls: weapon-ancestor classification, passive/quickmelee/manualNPCmelee
exceptions, drawn0317 Dragonblade retention, weapon-hosted SombraHack53 andRein
shield136 exclusion, Soldier secondary29 distinction, original Tracer(29,35),
Pharah(37,11,29),Sigma29 and01BB36 logical bindings. Actual recipient component
history fromC40 retains endpoint admission; no world timer extension or inferred
epoch. NPC melee exception delegates existing npc_melee.ability selection rather
than adding a parallel manager/fixture. Unknown target is not a fake weapon.

Original World.body_button_allowed(defaultTrue) protocol is forwarded by existing
BodyWorld/BotWorld to Controls with strict bypass. LogicalButton.blocked,
Ability.held/button and original7DEB522D condition consume it. Existing skill_locked
and Punch hard-control checks, strict paths, counters and work budgets preserved.
No driver._run change or0xE/controller/Pin edits. A65 edits only separate
LinkedTarget class in nodes.py; parent receives both narrow segments.

v16 exact python -B -m unittest test_genji_blade test_pharah_control
 test_script_button_transitions test_game_npc test_game_script_runtime
 test_game_sombra_charge.SombraChargeTests test_ashe_hack -v:
66run89.437s,64casepass/2failedmethods(4subcase records), no errors/skips.
worker-c-sombra-original-input-gates-check.log. All originalGenjiBlade, strict/
buttontransition/VM/NPC,15SombraCharge andwholeAsheHack pass. OriginalPharah
failures retained: enemy_primary_after_roll partial/empty health60vs130;
flashbang_brackets_concussion frame4 firststun9vs10 andframe5 originalNone.
These are recorded original timing/geometry differences, not removed assertions.

ActualHack producer probe (no injected lock): ordinarySombra2/65 frames applies
to realTracer; RMBbutton2 (not the existing0x38 physical skill mask) leaves pose
unchanged, while ordinaryFIRE still removes12HP (target188). DrawnGenji0317
flagsRUNNING1 persists through actualHack. The same exploratory melee pose
expected90HP incorrectly: diagnostic at identicaltimeline with actualHackFalse
andTrue both gives target200, bladeflag1,disabled{},faultNone. Thus this setup is
not evidence of a Hack swing regression nor claimed passing sword-hit evidence;
originalproperGenjiBlade fixture remains allpass. Logs
worker-c-sombra-original-weapon-logical-probe.log,
worker-c-sombra-genji-blade-weapon-detail.log,
worker-c-sombra-genji-swing-baseline-probe.log.

RequestedC42 nonempty receiver storage probe used actualServerSnapshotTests.scene
(two=True), actualPeer.message21616 selectingSymm/Sombra, actualnetwork owner
commands4E/1confirm andSombra50neutral/paidEMP+30release. Realserver world1012/body
1004, actualpair withbothendpointLocks. Both export_component_vm succeed as
ow174.component-vm-storage.v1. WholeServer capture under run_now-fails guard
is stabletwice; mutate EXISTINGowner1624.base changesdigest, restore returnssame.
No alternateVar or secondnamespace created. Whole digest atthisstabletree
f91aebe6fd59b27cef53a8220b9ae536826ea19395719b6f90153fc53d26d222.
worker-c-sombra-portal-server-storage-corrected-probe.log. Initial probe imported
nonexistent unusedcomponent_vm_digest; corrected onlyprobe import, notruntime.

Further realserver endpoint_slot identity probe: bothendpoint getters1624/1625
are `is owner.body_script.component.entity_var(var,create=False)`; actualwatcher
counts1and2 respectively, sameobjectsseenbyboth. These are realnonempty watcher
maps, not synthetic Vars. worker-c-sombra-portal-slot-identity-probe.log. This is
strictstored export/causalprobe, not fulljournal replay/client acceptance. Source
VM fields/protocols remain strict; no source edits during groups/probes.
Parse/duplicate-method anddiff audits clean.

Next C44: originalincoming application world/body clock/source-retirement replay
and normal fullSombraEMP/control/grenade/NPC helpers with actualdependencies;
legacyTracer fixture imports refer to different old sombra_hack constants/old
Vitals.hack_until and must adapt queries to existinghosts with asserts retained,
not add a fakeHERO/HACK/health namespace. Device lifetime/ownerretire boundary,
Symmretainedsentry/placement, Mei geometry and Pharah/Bastion priorboundaries open.


## C44: incoming application replay with observed delivery clocks

Lock gains optional clock=(actual fight.tick, application.start recipientms) at
the same accepted body/device invocation. Existing3-positional construction
remains valid; missingclock never assumes equalepochs. No newtimer/queue/host.
Original incoming replay now advances only retained0B32 andorphaned0B1F instances,
translating each known clockassociation. operation/budgets remain; componentframe/
now always restored. It does not step recipient BodyScript/consumeinput/movebody.
Actual dead/retiredsource follows original _shown_sources rule, includingstill-
shown bodies. DeviceWorld exact0B1F source-exists predicate now uses sameknown
shown-source set rather than unknownbaseWorld answer. Other nativepaths untouched.

Original application_replay_until adds orphaned acceptedinterval before retained
combat tail; lastpart remains existingcatchup. Controls removes only stopped
orphaned actualVMs early; live-source originalworldexpiry policy unchanged. Existing
recipient history truncates at observedstop when no other application remains,
not at guessedworld/frame offset. No secondVar/pool/status controller.

Actual ordinary/differentepoch probes:
- clock(1091,17455), recipientlastbody/component1115/now17855; afteractualcaster
hero replacement +world-only ticks, VM stops/world1306<originalexpiry1404,
888restored; bodyframe/componentframe/now exactlyunchanged, faultNone.
- recipientconstructorbody2000 whileworld1091: clock(1091,32015); stoppedworld1304
<1404; body/component2000/now32015 unchanged,faultNone. Known capturedpairs differ
withoutinventedglobalanchors. worker-c-sombra-incoming-clock-probe.log.
- Same shiftedbody, single10000-worldtick gap to11115: early originalapplication
chain retiresoldVM/locks, body/component/now(2000,2000,32015) unchanged,faultNone.
worker-c-sombra-incoming-large-gap-probe.log. No fakeapplication/control injected.

Restored complete original012E test_game_sombra_emp.py. Only actualservice .hacks
->.sombra_controls.locks, ceil(realuntil/TICK) queries, andactual selected-script
router name adapted. All original constant Counter identical. No expectedvalue/
stateclass rename rewritten. Normalimports retained, not ASTselected subset.
v16 exact python -B -m unittest test_game_sombra_emp
 test_game_sombra_charge.SombraChargeTests test_ashe_hack
 test_ashe_emp.AsheEmpTests.test_emp_refreshes_the_lock_from_its_own_impact_after_ordinary_hack
 test_ashe_emp.AsheEmpTests.test_emp_blocks_abilities_without_suppressing_viper_and_releases_at_expiry
 test_script_body_owner test_script_entity_filter test_game_script_runtime -v:
70run92.723s,68pass2retainedfail. All15charge,wholeAsheHack/refresh/expiry,
stricttypedguards/runtime pass; originalEMP9pass2fail. FullsourceEMP verifies
cost/delay/whiff/range/terrain/team/shield-only/Helix/deadsource/no-out-of-scope-
resource resolution. Two failures preserved: snapshotliteral ModifyBodyHealth
absent(currentactualclass ModifyHealth, strictexport itselfsucceeds), secondEMP
atline143 total50.72vs50 becausecanonicalZenyatta intrinsicshield regen resumes;
not mislabeledC44regression/baseline without comparison. EMP pooldrain currently
bypasses ordinarydamage shield-recovery notifications; auditandconnectexisting
shieldhost in nextbatch, not weakenregen/asserts. Full logfile6.4MB contains
unittest inlineVMJSON: inspect boundedlineprefixes, neverprintthewholefailure.
worker-c-sombra-incoming-original-check.log. Parse/duplicate-method/diff audits
clean; no Python edits duringgroup. No client/journal/fullacceptance claim.

Next: saveC44 independently, thenC45 missing EMP shieldnotification consumers/
originalmodule dependencies and code-equivalence inventory below. Do not block
this implementedreplay because two preservedoriginaldifferences remain.

## Current owner consumer inventory after C44 (not an acceptance matrix)

0006 Torbjorn: Overload/armour/tenpellets/hammer/bodyandsurfaces/realTurret throw,
repair/death/ownership/Molten/charge allhaveactualconsumers(C13-19); actorSombra
receiverC42 nowmounted. No knownunmountedTorb featuremodule identified, butfinish
source-ref equivalenceaudit rather than inferfullcompletion fromtests/ancestry.
Rough0-1activehour owncodeaudit; sharedobserver/client limits remainseparate.

0008 Pharah: Hover/JumpJet/Barrage set/curves/abort/reflect/eye/independentcover/
playerandBotimpulse consumers mounted(C4-12). Knownremaining actualedge is frozen
impact-time cover/broken_particle_barriers forcehandling. CompleteHook counterpart
ownedB; preservePharah-specific consumer once actualHook lands. Rough1-3hours.
Do not treat four oldPharah fixturegeometry/timing failures as missingwholemodule.

0015 Bastion: Repair/charge/Reconturret/Sentry/Tank frozenprojectile lifetimes
mounted(C1-3); root mountedrealSniper consumer. Remainingcrossowner behavior:
Heavycapture currentlySentry->Recon affects wall295vs235; HeavySentrymelee correct
amountbutmissingoriginaldisplacement. Audittheseactualstance/movement paths,
keeporiginalassertions. Rough1-2hours; no needrepeatNPCfullfixture foroldnames.

0016 Symmetra: primary/orb/recovery/charge/Sentries/PortalPair andactualPhoton
plane-or-meshQ mounted(C20-26/C29). Knownunmounted sourceSentry historicalphase
inputs: Combat calls sentries.update({}, {}, ()); actualsource needsdamage_starts,
cryo_expiries/barrier_cover_ends andretainedreplay inputs. Existing_recorded_pose
compares4tuple whilecanonicalhistory7fields, so must preservecurrenttail and
actuallybridgecorrectsample, not merely passgraphfile. Genericstrictplacement
FA02 stillunsupported; auditoriginalstaticconsumer equivalence. Earlier sparse
Photonexpiry/passivecharge split remainedunaudited; verifyactualcallers before
claimmissingorcomplete. Sombra endpoint/sentry disableconsumersC42 nowmounted.
Rough3-6hours, largestuncertainty actualhistoricalgeometry interlocks.

00DD Mei: resources/reload/charge/frost aggregate/realNPCpartialslow/Blizzard/
singleCryoHealing statusledger consumers mounted(C27/28/30-32). Originalwall
lift/splash family equivalence remains: footprint/lift/lift_clear/lift_positions/
move_bot/move_player/show/splash/splash_contacts/step_lifted_bot/volume_clear
have no same-namedmountedsource interface; actualWalls hasothergeometry, so audit
realconsumers beforelabelabsence. B50 nowsole Mei-only constructorprotection-
birth pair/05FE4000ms worlddeadline; C hasnoMei WIP andsharedCryoHealing hasno
constructorpair toreuse(history.at ismutableintegrationcursor). Rough2-4hours.

012E Sombra: pistol/spread/Camo/nativeownercharge/actualBeacon/Hack/EMP request
entries,bodybases,reapply,logicalabilities,actualdevices,ownerreplay andnowincoming
replay mounted(C33-44). Remainingactualconsumer audit: EMPshielddrain needs
existingrecovery/damage notifications; sourcefullphases/control/grenade/NPC/
healthpack helpers stillolddependencies/queries and requireactualhost mapping.
LegacyTracer expects differentold sombra_hack.HERO/HACK andVitals.hack_until;
do not fabricatealiases/secondhealthservice. Existingprivate sourceobservations
andworldcontactHelix timingremainrecorded. Rough2-4hours, plusunidentifiedshared
consumer conflicts. Completeoriginalsource import/protocolaudit, not justtests.

0065 Junkrat and0200 Ashe wereformallyreassignedA, not C remainingqueue. Bowns
sharedHook andMine lifecycle; A ownsremaining000E/Pin/LinkedTarget wirecoupling.
AggregateC remaining roughly9-20activeworkerhours(lowconfidence, overlaps);
unknownsamefunctionconsumer equivalence/geometry canraiseit. Notcalendar estimate
or linear extrapolation oftestcounts. No acceptance/client workadded to scope.


## C45: select each incoming instance's actually observed component clock

Cleanmerge root8fa1a320 as6f45a37e, preserving originalcontroller/Pin/LinkedTarget
andMine consumers. OneControl conflict combined B45 preciseMine worldcursor until
withC44 actualLock.clock; Mine+device_binding exclusions both retained. Upstream
Mineflight fixture alreadyhasextraEOFblankline; C didnotrewriteitsoriginalfile.

Root review prompted actualalias audit: pulse is0B32 recipientEMP, notcaster0B31.
TypicalbodyEMP sharesapplicationcomponent; Portal remoteeffect referencesnear
endpointpulse. SimplefirstEMP actualclocks happened equal andordernearfirst, not
proof thatsourceclockwrong. Real sequence exposingbug: originalPortal deploy,
actualremoteBotScript.advance_live(fight.tick,position) (nofieldmutation), ordinary
Hack remote from(0,0,19), moveactualSombra near(4,0,0), paidEMP. Existingdict order
remote->near; remoteclock(1119,16895),near(1119,16095), sameactualnear0B32pointer
referencedbyremoteLock. C44 instance.update atworld1121 used16911ms whenactual
pulsecomponentobservedpair requires16111ms. Read-onlywrapperobserved this800ms
prematurecursor, no fakeVar/namespace/epoch. Logs
worker-c-sombra-pulse-alias-probe.log,
worker-c-sombra-foreign-pulse-clock-baseline-probe.log,
worker-c-sombra-foreign-pulse-overlap-baseline-probe.log.

Incomingreplay now builds ephemeral component->latestactualLock.clock lookup;
EFFECT keepsitsownacceptedclock, pulseusesclockobservedonitsexactComponent.
No knownassociation meansskip, never borrowforeigncomponentepoch. Instance
identitydedupe and finally clockrestoration retained. No newstoredclockfield,
sourcecontroller clock,0B31 replay, timer, effect or manager.
Sameactualoverlapprobe nowobserves(1121,16111,16111); pulsecomponent(frame1005,
now16095) restoredexactly. worker-c-sombra-foreign-pulse-overlap-corrected-probe.log.

v17 environment/test_environment; python -B -m unittest
 test_game_sombra_charge.SombraChargeTests test_ashe_hack
 test_ashe_emp.AsheEmpTests.test_emp_refreshes_the_lock_from_its_own_impact_after_ordinary_hack
 test_ashe_emp.AsheEmpTests.test_emp_blocks_abilities_without_suppressing_viper_and_releases_at_expiry
 test_game_sombra_combat.SombraCombatTests.test_paid_emp_matches_pulse_and_expiry_with_no_player_commands
 test_game_script_runtime -v:39allpass132.358s,
worker-c-sombra-component-clock-shared-check.log. No source/assert edits duringgroup.

NextC46: original EMPshielddrain damageconsumer currentlydirectpoolmutation loses
canonicalshieldrecovery/receiveddamage/stats callbacks. Do not suppress existing
Zenyatta regen foroldfixture. Thenprioritize actualSymm historicaldamage_starts/
cryo_expiries/barrier_cover_ends pipeline andmissingretainedsupport helpercalls
rootidentified cover_ray/trace_barrier/_splash_targets/_splash_barriers/
visible_target; bridgecurrenthelpers, nevercopywholelegacyCombat.
No client/journal/fullacceptance claim; previousperownerinventorystillapplies.


## C46: restore shield-only EMP through the existing damage consumer

Original012E apply_emp_health called _damage(shield_drain=True); narrowcanonical
Controls.drain directpoolwrites skipped existingrecovery/receiveddamage/hitstats.
Restored that optionalkw(defaultFalse) on the same Combat._damage. Source mode
bypasses outgoing/incoming/Nano/Discord/armour scaling and uses Vitals.drain_shields
on existingtemporary/extra/conditional/intrinsicSHIELD records only. Basehealth,
armour/Overload/wavehealth andpool IDs/TTLs remain. Existingbarrier/protection and
single authoritative postdamage callbacks/stats/wake/HPsync nowconsume actual
shieldloss; no seconddamage manager/pool/healing service. Mine EMP remainsits
originalcontrollerdisable withouthealthdamage, preservingB44/45 fastpath.

Sole actual0B32 receiver calls that path with actualsourcecomponent.frame when
available, explicitworldtick/healthtags, source_scaledTrue andaward_ultFalse.
No missingcaster is invented. Only shieldloss reaches ordinarydamage observers;
no loss remainsno damage/charge/hit. PermanentHPhealingdeficit not increased by
shield-only drain, asoriginalsourcepolicy. Ordinary_damage defaults/path retained.
Rootconfirmed no_damageWIP; sourceSniper _bot_weapons/_interrupt separate.

v17 exact class-based unittest suite (normalimports, unchanged originalasserts):
 SombraEmpTests(all11), SombraChargeTests(all15),DamageTests(all8),
 runtime ExecutionTests/ValueTests/RealGraphTests(all17), plusoriginalAsheEMP
 refresh/expiry/projectedbarrier3. 54run171.396s,52pass2retainedfail.
worker-c-sombra-shield-damage-check.log. Customresult formatter onlytruncates
individualfailureloglines to600chars toavoidprior6MBinlineVMJSON, nottestresults.
Firstold50.72aftersecondEMP assertion nowpasses; lateroriginalHP50 expectation
atfirstexpiry is110.72 becauseexistingZenyatta30/s intrinsicregenerationafter
actual3-second damagefreeinterval. Do notdisablethatotherownerfeature. Other
failure isoriginalModifyBodyHealth classname string absent; actualstrictVM export
stillworks, nofakeclassrename. All15charge/no-EMP-credit,8ordinarydamage,
17VM andAshe3 pass. No fixtures/asserts rewritten, no sourceedits duringgroup.

Actualproducerprobe: normaloriginalSombraEmpTests.make(Zenyatta), paidEMP30 =>
HP50/SHIELD0, sourceHERO_DAMAGE150, ultcharge0; existingZen.last_damage17.952,
currentnow18.08. Beforeobserved+3secs shield0; twofurtherframes shield0.72,
provingknownrecoveryhost resumesratherthanbeinggloballydisabled. Actualpool
probe withsingleVitals existing APIs: HP200/armor100/Overload100/wavehealth75,
baseSHIELD150+temporary50+extra30+conditional20 =>drain250; allHP/armour/wave
unchanged andsamepoolID/slot records. No clonedpool introduced.
worker-c-sombra-shield-damage-consumers-probe.log. Initialreviewcaught source-
scaled replacement had hit rootMine fastpath first; fixedboth intendedbranches
beforetests, preservingMine disable-only behavior. Parse/diff audit clean.
No client/journal/fullacceptance claim.

Next C47 priority: actualSymm historyconsumer. Source0016 Combat
_advance_phase_clocks(761),update946/1032 definesinputs; currentactualTurrets
alreadyretains rate*max(start,ready,cryo_expiry,barrier_start,damage_starts)
algorithm butCombat stillpasses{}, {}, (). RootB50 actual Mei constructor pair
via mei.cryo_world_deadline/expired suppliesknownworlddomain, reuse thatsingle
phasehost. Retainedsource methods call missing cover_ray/trace_barrier/
_splash_targets/_splash_barriers/visible_target; map actualcurrenthelpers per
consumer, notcopy wholelegacyCombat orcreate phase/health ledgers. Sync root
cleanboundary afterC46 to getB50 and latestsharedSniper/controller source.


## C47 — actual sentry cover-history inputs (2026-11-12)

Base: clean merge a0e2e589 of canonical0d9d2254 after C46. C46 is now
accepted by root as d0ff1ecd. This batch replaces Combat's empty sentry inputs
with the original0016 finite beam-window consumers, using existing actors only.
Photon removal records retain actual actor geometry and its world expiry;
WorldBubbles removal records map the existing00FC Chase11 lifetime from the
actor's observed born_tick/origin_ms pair. cover_end uses existing Photon
plane/mesh contact and original sphere entry/exit shell contact, including
combat_owner/team filtering. No second barrier/phase/health manager or timer.
Mei's existing B50 cryo_world_deadline supplies per-body and occluding-ray
cutoffs in the same world domain, without interpreting mutable cryo_samples.at
as a constructor anchor or stepping body VM. Sentry recorded-pose matching reads
first4 fields from the actual7-field history, preserving all critical/stance tail.

Validation on this stable WIP with selectedv18 environment+test_environment:
`python -B -m unittest test_symmetra_turrets test_game_ana_symmetra_sentry
 test_game_combat.DamageTests`:37run72.287s. All14 original Sentry +8 original
Damage pass. The15 AnaSentry methods include2skips and13 failed methods,
with19error records (old symmetra_sentries/ice_wall observer APIs) and1final
empty-outcome assertion; no fake manager aliases or rewritten assertions.
Log: ../worker-c-symm-history-core-check.log.

Restored entire frozen0016 tests/test_symmetra_cryo.py byte-for-byte,
SHA256 c33af8d4ead3f866d07fe8b22cc04027c0cd08eb11cd13be664dda0ebf0fdee4.
Two selected original methods test_ice_occludes_sentry_to_rear_then_sentry_resumes
and test_expiring_cryo_only_limits_sentry_catch_up_on_occluded_ray run7.070s,
both still fail: existing allied Cryo ray fails to occlude (target remainsbody),
and original cryo.active stillTrue at world-only exit. These are genuine
remaining cover/phase consumers; deadline inputs alone do not fix them.
Log: ../worker-c-symm-history-cryo-check.log. Full restored file not yet run.

Actual producer probe uses original ReinhardtWinstonBarrierTests.deploy(8),
switches its attacking owner to actual Symmetra, launches/attaches a real
Sentry at(0,2.3,2.01), and crosses original sphere expiry1617 with64worldticks.
Prior empty-input callback baseline damages armoured Winston28.672; mounted
history damages2.24 only after expiry, same target and owner faultNone.
No actual actor health/lifetime/geometry or source assertion changed. Gaps16,
64,80 each yield2.24; contiguous last-tick delta .448 (28/s against armour).
Logs: ../worker-c-symm-history-sphere-baseline-probe.log and
../worker-c-symm-history-sphere-probe.log. This demonstrates real callback
consumption, not only presence of retained fields. Client/full replay OPEN.

Next C48: actual Cryo cover/exit interpretation, then remaining retained frozen
Sentry consumers. Generic phase transition damage_starts (Reaper/Moira/etc),
respawn history, original frozen replay_ice/replay_owners/replay_frozen selection
are still unmounted. Missing retained-support calls cover_ray/trace_barrier/
_splash_targets/_splash_barriers/visible_target still need narrow mappings to
existing shared helpers, not whole old Combat. End-of-update Torb turret attacks
currently occur after the in-loop removal sampling; include their barrier
removals before final sentry settlement in the next boundary. Do not claim
complete0016 equivalence or let these retained failures block this batch.


## C48 — solid Cryo cover and actual sentry release (2026-11-12)

C47 saved as2e4a24022925f74ae127367199ca942e2b0ba40f. This batch mounts original
0016 cover_ray1605/_cryo_contacts1617 semantics inside existing _cryo_contact:
all-team actual body capsule contacts are merged with the retained native actor
proxy distance. Original source explicitly includes all active Cryo bodies,
without owner/team exclusion; no new target, actor or inferred geometry.
Acquisition now excludes Cryo itself (original _enemies1148 rule scoped to the
existing Sentry host). Existing Combat.command's actual Mei protection admission
releases the same sentry target/aura immediately, as source command686 did.
B50's proven cryo_world_expired is reused in the pure active query and the
existing barrier _live gate: stale physical ice retires at that same known
birth-pair deadline without body VM stepping, input lease or world timer.
Protection stack's existing authored exit phase is preserved on ordinary input.

Stable v18 checks: full restored original10Cryo run25.164s before the final
acquisition/release additions:1pass/9failedmethods/11assertrecords. World-only
expiry now gets past old cryo.active True failure; later source expects5 beam
ticks3.2 but actual B50 birth-pair endpoint admits6ticks3.84. No clock shifted to
force old assertion. Other retained failures cover old cancel/exit expectations,
self-heal sampling .75vs200/16.75vs16, grenade/melee/orb cover; these consumers
are not claimed complete. Log ../worker-c-symm-cryo-cover-check.log.

After acquisition exclusion: original2CryoSentry + actual rawMei epoch +14Sentry
+8Damage,25run197.792s:23pass/2retainedfail. Actual raw strict-storage probe
passes with birthpair(1012,1044), world1020/body1085/deadline1303; world-only
expiry leaves actual state35 active and body frame/now(1085,17375) frozen yet
allows original130 damage, no inputlease; strict digests/replace checks pass.
Log ../worker-c-symm-cryo-target-check.log. No source changes during this group.

Final immediate-release change reran the2 original CryoSentry methods7.669s:
first now passes solid-cover/no-target/no-damage assertions before remaining
post-cancel reacquisition failure at180. Second now passes immediate clear of
target, slow, old aura identity and variable, plus20tick no-slow assertions;
only post-cancel active query at253 remainsTrue during existing protection exit.
Original first baseline failed176, second baseline failed246; actual consumers
progressed rather than rewriting assertions. No fullpass claim.
Log ../worker-c-symm-cryo-release-check.log. All original assertions/files retained.

Next C49: finish barrier removal observation after final Torb turret attacks;
map retained Sentry helpers to actual cover/splash/phase/Heavy/Zarya services and
mount proven frozen-history selection and per-tick update calls. Source0016
phase_clocks, respawn isolation, replay_ice/replay_owners/frozen policies remain
explicit gaps. Cryo cancellation exit and source0016 bare-world healing do not
justify disabling the current single HealingEffects ledger or B50 raw epoch gate.


## C49 — bounded frozen sentry replay through the sole NPC driver (2026-11-12)

C48 saved asd2c0cae04d625bc9887bd39ef6469db2aeea0fcf; clean sync40b523a4
included original Flux/Player/native and A70 consumers before this batch.
Combat.frozen_sentry_replay mounts the original bounded proof for the no-support
case: original COMBAT_CATCH_UP=64 plusceil(1300/16), one recorded frozen lock,
no respawn/live protective world actors/Cryo/Blizzard/firing/impulse exceptions.
Match.update reuses its existing _advance_bots→Combat.update loop from the old
tick+1 when admitted, rather than endpoint route stepping followed by another
mover. Current Hook/Orisa/Knockback/navigation/controller driver remains sole.
Sentry._held_frozen_pose explicitly samples previous_tick: current frost defaults
to Match.tick, already the incoming endpoint, which would hide the prior freeze.
Original concussion exclusion maps to actual Rocket.source_graph007B, not a
fake field. Actual projected-barrier presence reads the one existing scripted
Zarya host. No new actor/health/mover/control/history pool or stored state.

The original ice-support/projected-impact proofs are NOT enabled by this partial
boundary: walls/projected actors still decline admission until their actual
phase/Heavy/splash/collision consumers map. All retained source implementation
and assertions remain. Do not call0016 complete. Earlier notes assuming300
retained frames were mistaken; both frozen source and current COMBAT_CATCH_UP
are64. This batch uses the real constant, never that assumption.

Cover sampling now also runs after existing hero_torbjorn.update_turrets and
before final Sentry settlement. A single local observer handles in-loop and
end-of-update removals; same barriers/geometry/expiry records, no double damage.

Original frozen source test selection (2 methods from frozen0016
 test_symmetra_npc_combat.SymmetraNpcResourceTests):
 test_sentry_keeps_damage_and_slow_through_actual_mei_freeze_on_all_five_npcs,
 test_sentry_kills_at_same_tick_in_a_single_gap_through_thaw_on_all_five_npcs.
AST loads these original methods/create/sentry_across_npc_thaw plus pure
Photon fixture move/add_target, not alternate production modules. First run
4.638s leaves15subcase errors before the feature at missing Bot.movement_data.
Original npc.Definition also lacks delivered optional mover/controller metadata;
root explicitly took that original consumer gap. Did not turn the identity
assert into a tautology, add fake Bot fields, or count source methods as passed.
Log ../worker-c-symm-frozen-original-check.log. Sole observer adaptation in the
unexecuted later helper: Frost object.sample[2] instead of old tuple[2].

Separate ACTUAL producer evidence (not the failed original methods):
`python -B ../parallel-merge/worker-c-frozen-probe.py` with v18 env+testenv.
Each of the five selected real NPC definitions spawns in the same server,
actual Symm deploys/attaches a Sentry, actual Mei spray causes full freeze,
then the actual NPC receives its route. Normal89 consecutive world updates vs
one89tick gap produce identical damage/pose/death/target/source-stat tuples:
Trooper56.96 atz1.97152; Enforcer56.96/z1.8448; Sniper56.96/z1.76032;
Assassin53.76/z4.36302 (leaves beam range and clears target in both);
Heavy56.96/z1.37171. All actual frozen proofs nonempty/all VMfaultNone.
Same89tick Trooper producer with only the new admission disabled reproduces
prior40.96damage instead of56.96 (25 lost beam ticks), same pose/faultNone.
Logs ../worker-c-symm-frozen-producer-corrected-probe.log and
../worker-c-symm-frozen-basic-baseline-probe.log. Initial producer caught old
missing Rocket.concussion before mapping it; retained diagnostic log, notpass.

Stable source checks after all code edits:
`python -B -m unittest test_mei_frost test_symmetra_turrets
 test_game_combat.DamageTests`:31/31pass77.780s, no source edits during group.
Log ../worker-c-symm-frozen-shared-check.log. Diff parse/whitespace clean.

Next C50: original ice-support/projected-impact retained proofs. Map old
fight.ice_wall/trace_barrier/cover_ray/visible_target/_splash_targets/
_splash_barriers and old Heavy/phase/Zarya queries to actual existing hosts;
route-preview stepping must use current Bot.step signature/modifiers/pillars,
not copy the old Match.step_bot_pose over the real NPC driver. Then lift the
partial admission exclusions and exercise the corresponding original cases.
Generic phase transition/respawn history, wall support expiry/replay families
remain real gaps, not acceptance-only labels. No client/full replay claim.


## C50 — retained ice-supported sentry pulses and settled mover proof (2026-11-12)

C49 saved6ff487df6de0c2e92d14734cbb25019e4f3fd345. Existing Combat retained
loop now admits moved/ice-supported Sentries and calls original
sentries.update(...,replay_ice=True) on its intermediate ticks. Original eligible
selection preserves endpoint-only new launches; existing Walls expiry/break
callback retires the same Sentry/aura, no second driver. Only actual IceWall
PILLAR Deployables are excluded from unit acquisition (original0016 units were
separate from solid wall objects); all other existing Deployable types retained.

Root Flux evidence corrected the frozen proof's old field-existence assumption:
a grounded manual NPC may keep its sole impulse_mover after movement settles.
_private _external_motion reads actual airborne/velocity/non-neutral command,
existing Knockback pending/active, Hook control, Orisa control. Both general
frozen proof and recorded frozen-pose proof use it; never clear or replace mover.
Actual Trooper producer: ensure_manual_mover yields sameobject,grounded,velocity0
and nonempty frozen proof. Actual Bot.apply_impulse(1,0,0), at supported no-route
admission boundary, retains that sameobject,velocity(1,0,0),proofempty. Initial
attempt withroute present correctly returnedFalse and was not counted as an
active-impulse test. Corrected log
../worker-c-symm-frozen-settled-mover-corrected-probe.log; initial retained too.

Exact original0016 IceSentry AST selection uses original duel/deploy/scene/launch/
assertions, only fight.ice_wall query mapped to actual fight.walls. Reproducer
../parallel-merge/worker-c-ice-sentry-source-check.py, with v18 env+testenv.
Initial2 methods18.706s/5failrecords: Sentry selected actual wire Pillar instead
of victim, source contiguousdamage0. Actual probe identified distinct targets:
physics-support3221229590,wirePillar3221229583(definition0932),Sentry3221229593,
victim2684355329. See ../worker-c-symm-ice-target-probe.log.
After precise Pillar acquisition exclusion:2methods22.330s/1pass/1failedmethod,
1remaining breaksubcase. Entire original
 test_sentry_damage_until_pillar_expires_inside_retained_gap passes: active and
in-flight branches, contiguous-vs-gap damage/tick equality, original80gap leaves
exactly64-5=59beam ticks, repeated update inert. Attachment/cancel/natural-expiry
subcases also pass; break still5piecesvs4 after original physics piece.health=.5
seed. No assertions/literals/skip conditions altered; selected actual input opted
in. Log ../worker-c-symm-ice-sentry-corrected-check.log.

Stable final shared `python -B -m unittest test_mei_frost test_symmetra_turrets
 test_game_combat.DamageTests`:31/31pass67.001s; code unchanged throughout group.
Log ../worker-c-symm-ice-sentry-shared-check.log; diff whitespace clean.

Remaining real gap exposed: existing Walls geometry still allocates a separate
internalpiece ID/health timer from the original graph-backed wire Pillar actor.
Originalbreak seed reaches physics health but direct attack debits actualactor
pool, so expected retirement does not follow. This is not merely old observer
API: reconcile same actor identity/Vitals/lifetime for geometry rather than
add another pool or fake a mirrored view. C51 prioritises this owner boundary,
then full frozen ice-support/projected-impact helper mapping/phase/respawn
consumers. Parent root owns optional NPC mover/controller metadata merge.
All original source/failed checks retained; no full0016 or client claim.


## C51 — one graph-backed Ice Wall pillar identity/pool/lifetime (2026-11-12)

Implementation checkpoint0dcad5d2 (root accepted as769b27b7). Synced6946e560
with root's optionalNPC mover/controller metadata via isolated stash/pop; no
source overwrite or lingeringstash. C51 replaces Walls.create's extra5IDs,
standalone0636 Components and independent400HP/5sec clocks with geometry records
bound to the original0932 Deployable, its Vitals and expires_tick. Pillar.health
and expires read/write that same existing ledger/timer; fallback scalar fields
are only unhosted placement candidates, not another published actor/pool.
Actual owner6275..6279 slots validate definition/sourcebody/owner. Original
accepted placement/rotation can use the same spawn factory and exact authored
PlayScript recipient when replacing/creating that column; no guessed nearby
owner or newclock. Source command's actual ground ray supplies the accepted
anchor when native2478 is not its original rotated placement; source geometry
keeps its original double-precision point, while actor wire pose remains existing
native encoding. Preserve original rectangle/lift/placement algorithms and
provided NPC movement_data. No full asset copies or input-pack modifications.

Geometry and native actor removal now retire each other once, clear the one
Vitals entry, original owner slots and actual child VM/foreign watchers. Removed
actor damage is inert, rather than recreating a dead pool. Existing body+surface
contacts avoid double debits for Rein, Winston Tesla/Jump/Primal, NPC area and
same ice-impact blast; unrelated actors/abilities remain on their existing paths.
Objects receive blast damage, without an invalid character.mover impulse.

Original0016 IceSentry2 selected methods allpass17.984s (same AST fixture asC50,
only old walls observation name adapted); former .health=.5 break now retires
its actual actor, geometry and attached Sentry. Log
../worker-c-symm-single-pillar-original-check.log. Root independently revalidated
same original2/2pass9.922s on accepted checkpoint.

Real unequal-epoch ServerSnapshot scene uses actual Mei selection+wall buttons
16/0/1/0:5geometry records==the same5real0932actor IDs, bodyfaultNone,
world1020/body1088. Whole-server strict export stable on two captures;
piece.health=.5 changes sole pool and actor health with same poolIDs(0,),
changes strict digest; removal empties both maps/Vitals and real child instances,
then strict export stillpasses. Log
../worker-c-symm-single-pillar-server-storage-probe.log. No fake VM or data view.

Broad source Wall/placement/Rein/Damage64run140.539s produced23failure and18error
records (not fullpass): old APIs (_splash/pinned/charge_drives/controlled/Brig),
tuple-Frost fixture and actual geometry/splash/cancel differences retained. It
also found two real new-code gaps (Vec3 fallback subscripting and object radial
impulse) corrected before final checks. After fixes, handle15604 terminal:
27run25.997s,20passedmethods/6failedmethods(7assertrecords)/1StopIteration;
all8Damage and actual Rein single85-hit method pass. Do not count it allgreen.
Log ../worker-c-symm-single-pillar-fallback-check.log.

Final accepted-source-anchor/source-point correction: handle41210 terminal,
5run7.676s,4pass/1retainedStopIteration. Original preview/rotation now creates5,
offcentre obstruction omits correct column in BOTH rotations, unsafe-lift rejects
then accepts fresh confirmation, and standing-on-wall creates the expected upper
wall. All original literal assertions preserved. Remaining existing-overlap
fixture makes no initial wall (StopIteration182), to audit next rather than
remove the check. Log ../worker-c-symm-single-pillar-confirmation-check.log.

Root requested C52 source-observer updates: AnaSentry ice_wall_arena must read
sole fight.walls; retain all old assertions/other Sentry differences. Original
ServerSnapshot.test_ice_wall_is_causal must use a real Mei5actor input fixture;
old nonMei directWalls.create is intentionally not a way to create unhosted
placeholder actors. Keep original health/expiry/digest causal assertions.
Then continue full original frozen ice-support/projected-impact helpers,
phase/respawn history and owner-native geometry/command differences. Current
active work estimate8–18 Cworkerhours lowconfidence (Mei/Symm largest5–11,
Pharah1–3,Bastion1–2,Sombra/Torb audit1–2); not calendar time or test-count
percentage. Existing failing checks are retained and do not hold this checkpoint.


## C52 — real Ice Wall causal fixture and sole wall observations (2026-11-12)

C51 evidence documentation savedad9b8f594c7a66fe394cc7388ad0bb5563d77a06;
actual implementation0dcad5d2/root769b27b7. Parent requested narrow fixture
alignment rather than a production alias or an unhosted test pool.
All AnaSentry fight.ice_wall observations now read the sole fight.walls,
without changing any original literal assertion. Other old Sentry API/timing
assumptions remain intact for subsequent owner audit, not silently stubbed.

ServerSnapshotTests.test_ice_wall_is_causal now selects actual Mei via real
Peer21616/commands, opens real preview and confirms real16/0/1/0 cast. It keeps
original before/preview/deployed/damaged/delayed/retired digest assertions and
health30/expiry+1 mutations, adding5actor identity checks. It no longer asks a
Soldier to manufacture an unhosted single column with directWalls.create.

Stable selectedv18 `python -B -m unittest -v
 test_server_snapshot.ServerSnapshotTests.test_ice_wall_is_causal
 test_game_ana_symmetra_sentry.AnaSymmetraSentryTests.test_mei_recall_retires_ice_attached_sentry_and_clears_slow
 test_game_ana_symmetra_sentry.AnaSymmetraSentryTests.test_support_expiry_preserves_retained_beam_pulses_and_clears_target`:
3run40.385s. Real Meifiveactor causal test passes. Two Ana methods proceed past
old wallname but retain3errors at missing symmetra_sentries in sharedthrow
fixture and1dependent empty-outcome assertion; not countedpass. Source original
0016 actual owner Sentry checks remain different fixtures and already pass.
Log ../worker-c-actual-wall-causal-observer-check.log. No production source or
assertion values changed in this batch; no new matrix/provider/actor pool.

Next: normal-import original0016 NPC frozen methods now root metadata restored,
then full retained ice-support/projected-impact helpers and phase/respawn
consumers. Ana's old devices/next_shot/instance/output interface still requires
actual owner/protocol audit; do not create a facade manager to fake it. Inspect
any real missing parent VM output/timing consumer before calling it merely an
observer. Full wall overlap setup/quantisation/cancel/splash differences persist.


## C53 — natural allied ice support through frozen history (2026-11-12)

C52 saved66a3cb561b4053aee491357b48898a000c5365d0; root independently passed
realMeifiveactor causal36.818s. This narrow production batch removes the global
walls.pieces rejection from the existing bounded frozen proof. Acquisition and
proof now share the same unit query, excluding solid0932 columns, so a support
actor cannot be misread as another beam enemy. Original allied natural/cancelled
support can use the same Match NPC/combat world loop through the freeze; no
second mover/actor/ledger. A temporary explicit support_impacts=False keeps
impact/projected-retarget contexts declined until their actual service adapters
are mounted, rather than calling missing APIs. Default helper retains original
impact path; this partial boundary is NOT full impact/projected integration.

Stable selectedv18 shared `python -B -m unittest test_symmetra_turrets
 test_game_combat.DamageTests`:22/22pass26.915s.
Log ../worker-c-symm-natural-support-shared-check.log.

Original source selected2 methods from frozen0016 SymmetraNpcResourceTests:
 test_ice_supported_sentry_expires_on_both_sides_of_npc_thaw and
 test_ice_supported_sentry_cancels_at_checkpoints_around_npc_thaw.
They execute create/ice_sentry_across_npc_thaw plus pure original pose fixtures,
with actual original0016/npc-catalog-with-movers.json (immutable, no pack edit).
The selected runtime's default fallback catalog intentionally has mover=None;
using it initially stopped25subcases3.366s, not a missing restored mover API.
Explicit original catalog passes original identity/capsule setup after root's
metadata restoration. Initial oldconstant observation stopped25subcases8.672s;
reader maps combat.mei_frost.FREEZE_MS to actual combat.mei.FREEZE_MS, same1300.
Existing observation-only adaptations: fight.ice_wall→fight.walls and old Frost
[2]→actual.sample[2]. No source assertion/literal expectation changed.

Large original fixture installs dedicated-rocket observers even for these
no-dedicated-rocket cases. Current Combat has no old _projectile_body_impact
method:25subcase errors53.260s were preserved, not countedpass. In the selected
fixture ONLY, install its body/explosion observer only when its own rocket
variable is notNone; all their assertion branches already require shot is rocket.
No production method/None stub/fake manager is installed, and actual current
projectile/damage handling runs untouched. With body observer conditional,
2methods109.290s reached20 successful nonHeavy subcases;5Heavy subcases hit
old observe_explosion required-heading signature on an incidental existing
projectile. Those are retained diagnostics; not allgreen.
Logs ../worker-c-symm-frozen-ice-{original,current-owner,case-observer}-check.log.
Final same no-dedicated-rocket explosion-observer condition check is running as
handle60071, log ../worker-c-symm-frozen-ice-natural-check.log. Do not count a
pending outcome as pass; code remains stable while it runs. Reproducer
../parallel-merge/worker-c-frozen-ice-source-check.py. This is AST-selected source
coverage, not normal import of the entire7k-line NPC test module or client proof.

Root has explicitly taken FIVE public readonly Combat adapters:
cover_ray/visible_target/trace_barrier/_splash_targets/_splash_barriers. C will not
edit those new methods. Required contracts sent: trace returns same support
piece and actualwalls manager identity; splash returns actualobject/service/
amount, respects passed preview Target poses and impact exclusion. Root will
map current sole Photon/Win/Rein/Walls/Orisa/Sigma geometry, no managers/pools.
C54 continues old phase/Heavy/Zarya/Rocket fields and route-preview signatures
inside Sentry proof. Source imports phase_defenses and calls old heavy phase/
instance, zarya_barriers.projected, rocket.implicit_absorbable/impact_damage,
Match.step_bot_pose; these are not yet real current consumers. Generic owner
phase-expiry/respawn history remains separate. Preserve original guards/asserts.


## C53 terminal outcome and C54 retained-impact service adapters

C53 handle60071 reached its natural terminal: original two selected Ice-supported
Sentry expiry/cancel methods,25 source subcases,allpass134.540s. Earlier pending
text above is superseded by this terminal result. Original expectations unchanged.

C54 synchronized faec1f1c (root five readonly geometry queries plus current A/B
hosts). The original support-impact proof now consumes actual Walls identity,
Heavy charge active instance, phased-out/Deflect/Grasp/Matrix state, original
Pharah launch marker and the actual Rocket direct falloff fields. Route previews
use the same Bot.step/data/modifiers/pause/pillar inputs as the sole live NPC
host. Preview rejects owned physical movers rather than mutating them through a
shallow copy; this is an explicit conservative impact-proof boundary, distinct
from C50's natural-expiry admission of already-settled manual movers.

Combat now actually enables support-impact admission into the existing world/NPC
loop. Five original-source setup scenarios (Trooper/Enforcer/Sniper/Assassin/Heavy)
with actual in-flight Helix and82tick frozen gap all return nonempty admission;
world tick,flight position,actual support health and NPC pose are unchanged by
the query. Reproducer ../parallel-merge/worker-c-frozen-impact-admission-probe.py,
log ../worker-c-frozen-impact-admission-probe.log. This extracts the original
fixture setup up to its observer phase, preserving its setup assertions; it is
not a passing full source method or a second driver.

Original full selected Helix method remains5failure records19.238s: every
stepped baseline fails the original sentry-presence assertion at source line2173,
before comparison with the skipped schedule. No assertion or numeric expectation
was removed. Its old nonexistent _projectile_body_impact observer is excluded for
this support-only scenario (no body-contact outcome), and its explosion observer
forwards the current keyword-only signature. Both changes are external fixture
adaptations, not production aliases. Reproducer
../parallel-merge/worker-c-frozen-ice-impact-source-check.py; log
../worker-c-symm-frozen-ice-impact-check.log. Earlier5missing helper errors2.852s
are retained diagnostics, superseded by loading the original rifle helper.

The single projected-recipient query reads the existing native Pet/activation/
slot536 and actual Vitals pool, not the old separate Zarya manager. Actual native
cast identifies the same recipient without running components or changing the
pool; actual break retires it and query returnsNone. New focused regression plus
Sentry and Damage suites:25/25pass26.969s, log
../worker-c-c54-shared-check.log. The old encounter(protect=True) fixture itself
still fails its cast-active precondition; the real NativePetApplicationTests
setup/cast is used for this single-host query evidence.

Remaining: the global projected-barrier/respawn guard still declines those
complex replay windows; restoring the read query does not claim their complete
consumer admission. Next C55 audit phase/respawn and actual projected windows,
then Pharah impact-force and Bastion Heavy stance/push interlocks. Preserve all
original files/assertions and current A/B hosts. Estimated remaining C effort
6–15 active hours, low confidence; largest uncertainty is complex old phase/
retarget timing versus actual native owner lifecycles. No client operation,
publication or network access performed.


## C55 actual bound-pillar projectile geometry and blast consumers

C54 source Helix failures were actual shared-consumer omissions, not merely old
observation names. The generic Rocket body sweep saw the same native0932 pillar
before its solid slab face, so it destroyed support before the original expected
contact tick. _sweep_rocket now excludes ONLY a target whose same entity maps to
a Walls piece AND whose owner is that exact piece.actor. Native pillars without
this geometry binding and every other device/Mine remain body candidates.

With that fix, all five original methods advanced beyond the former presence
assertion and stopped at source2569: adjacent columns had no splash damage.
Actual _explode had never consumed original wall closest-face splash; it instead
tried generic pillar capsule exposure, which was blocked by the pillar's own
solid geometry. It now samples original _splash_barriers before health changes,
retains only the existing Walls service, excludes the exact impact piece, skips
same-bound actor body splash, and applies each sampled amount through existing
Walls.damage into the same actual native actor/Vitals. No extra pool/actor,
geometry provider or driver. No query run when no physical pieces exist.

Original selected test_in_flight_helix_breaks_ice_sentry_support_before_npc_thaw:
all5NPC stepped/gap subcases pass34.340s, unchanged original assertions/numbers.
Same external observation/signature adaptations documented C54. Log
../worker-c-c55-helix-splash-check.log. The intermediate geometry-only result
retained5failures17.764s at adjacent-wall expectation (superseded by the complete
source algorithm). Focused original DamageTests+ProjectileTimingTests+WallTests.
test_projectiles_stop_damage_one_pillar_and_do_not_hit_body_behind:16/16pass3.715s,
../worker-c-c55-projectile-shared-check.log.

Original on/after-thaw source method (5NPC x2 offsets) remains running as
handle1866; not countedpass. Reproducer
../parallel-merge/worker-c-frozen-ice-thawed-impact-source-check.py, log
../worker-c-c55-thawed-impact-check.log. Current implementation is saved without
waiting for that whole-family result. Next persist terminal outcome, continue
actual projected-recipient replay admission/phase clocks and Pharah impact cover;
Bastion Heavy stance/push interlocks remain. Remaining C effort6–14 active hours,
low confidence. No client/network/publication action.


## C55 terminal and C56 Pharah impact-time force cover

C55 handle1866 terminal: original on/after-thaw method,10NPC/offset subcases,
allpass89.317s. With preceding5before-thaw cases, all15selected Helix original
step/gap comparisons pass. Original expectations unchanged.

C56 restores original0008 frozen impact exposure for force: compute the existing
Pharah radial impulses before this blast spends wall/shield health, keep only a
local tuple for this call, then dispatch once through the same existing physical
mover/Bot.apply_impulse after damage. Still recheck alive/protected at dispatch.
Actual scripted Zarya bodies and broken_particle_barriers protect the same shot's
force. No stored queue/provider/second force or pool. Accretion branch B64 remains
owned B/root and must be preserved when receiving overlapping _explode context.

Initial diagnostic was invalid evidence: switch_hero relocated the shooter onto
the same side of the wall. Its nonzero velocity did not prove leaking cover.
Corrected explicit opposite-side3m fixture was outside force range (old/new2pass).
Final actual opposite-side2m wall scenario proves the boundary: old2828ff70
_explode/_pharah_impulses AST functions fail wall assertion with Mei position
Y1→1.021484375 and Z42.1474609375→42.1318359375; current functions pass without
motion. Actual20HP native projected Pet same-shot case passes old and new,
retaining prior behavior. Old1pass1fail2.451s/current2pass1.067s in
../worker-c-c56-near-cover-baseline-check.log. No original source assertion
modified; these are actual producer interaction regressions, not mocked shields.

Final actual cover2 + originalPharahImpulse/PharahCombat/Damage suites:
26/26pass16.142s, ../worker-c-c56-final-cover-shared-check.log. Earlier initial
incorrect-fixture2fail24pass20.920s remains recorded in
../worker-c-c56-cover-shared-check.log. Tests now explicitly position shooter
following actual hero replacement, preserving the intended geometry.

Next C57: source live boost for new Walls splash/direct consumers (Walls.damage
uses source_scaled=True, so source outgoing modifiers must be consumed once),
and _support_retarget_isolated old reinhardt_barriers.states→actual raised actor
service. Root owns pure five geometry adapters; A owns true projected-NPC Pet
recipient admission (current _recipient_live onlyplayers); no alias manager.
Mei/Symm phase/retarget, Pharah remaining cover interlocks and Bastion Heavy
stance/push audit stayopen. Estimated5–12active C hours, lowconfidence; no client,
network or publication performed.


## C57 projectile wall source scaling and raised-barrier proof guard

Synchronized root96ec3824 afterC56, retaining B64 Accretion caster damage/force,
A86 launchNano/offense_scale and its optional field positional compatibility.
Walls.damage keeps its original source_scaled=True default for already-scaled
cross-owner callers; adds optional source_scaled/source_body forwarding into the
same Combat._damage. Only three existing _explode wall direct/splash consumers
opt into current source rules. offense_scale=None routes raw amount through the
existing live team/supercharger/Nano host once. NonNone amounts already contain
original03DB/03E2 launchNano; multiply only current team/supercharger then pass
source_scaled=True, preserving frozenNano without double multiplication.
Readonly splash prediction still returns raw authored amounts, no boost/query side
effects or second source provider. Other Walls.damage callers retain defaults;
their individual pre-scaling contracts require separate owner audit.

Actual producer evidence: Ana casts Nano on Pharah, command-deployed five native
Mei pillars share their actual Vitals; original center120 becomes180 and all four
adjacent face splash amounts are exactly1.5 times unboosted amounts. Pre-fix real
probe confirmed current liveNanoTrue but center remained280HP (raw120 loss).
Actual Zarya launches secondary underNano1.5 with blast70.5, Nano expires during
flight, and same real wall receives70.5 once (400→329.5), retaining launch snapshot.
New live/frozen wall2 + sameimpact2 + originalDamage8/ProjectileTiming7:
19/19pass8.053s, ../worker-c-c57-frozen-source-check.log. Earlier live-only18pass
6.144s is superseded by this broader original-launch-aware scope.

_support_retarget_isolated no longer reads nonexistent oldmanager.states; it
conservatively checks existing fight.barriers.reinhardt.actors without syncing or
creating a shield. Root requested this actual service mapping. Complex retarget/
projected admission remains unaccepted until its original scenarios execute;
this guard correction does not claim that wholefamily complete.

Next: original phase damage-start transitions through already-owned idle clocks,
retarget/projected actual windows (NPC target mount owned A), remaining0008
cross-owner source force and0015 Heavy Sentry stance/displacement. No client,
network/publication action. Remaining C estimate5–12 activehours, lowconfidence;
shared geometry/phase timing is the largest unknown.


## C58 original phase expiry feeds the existing Sentry integral

Original phase_defenses.advance_idle reports the first vulnerable world tick to
Combat's damage_starts. The combined owners already advance Reaper/Fade through
their sole existing world_frame_anchor/moira_clocks drivers, but that report was
missing from Sentry integration. Actual source helper showed Reaper gap1=3.84,
gap16=10.24,gap64/80=40.96; Fade gap1=3.84,gap16=10.24,gap40=25.6. Mei existing
reported expiry already kept all gaps at3.84. These are real accepted gaps, not
private observer failures; log ../worker-c-c58-phase-gap-probe.log.

Add only pure immunity.phase_world_deadline: read actual Wraith/Fade Wait3 start/
ms and their existing current-component clock pair, map that deadline to world
time, never run a VM or create a ledger/driver. Combat.update feeds it to the
existing sentry_damage_starts max integral, alongside existing Mei expiry. It
does not delay protection globally or change current Reaper/Moira/Mei clocks,
input admission, phase health rules or B50 Mine/Cryo deadlines.

After restoration all11 source helper hero/gap scenarios produce identical3.84,
../worker-c-c58-phase-gap-restored.log. Original full selected method remains
3literal failures3.825s: its contiguous expected3.2 versus existing3.84 (one tick)
occurs before gaps are compared. Original source assertions are unchanged and
that boundary difference is not countedpass. The old phase_defenses.active
observation was mapped externally to actual immunity.phased_out; original helper
setup/assertions are retained, no production fake phase manager or alias.

New tracked causal regression copies the original setup/helper and compares its
accepted gap against actual contiguous behavior; it does not change the original
literal check. A second actual producer test supplies body command frame+1000,
uses the recorded original owner pair, verifies readonly deadline under run_now/
run_frame rejection and actual protection on each side of that deadline. Both
heroes pass; no guessed epoch offset is used. New2 + original Sentry14/Damage8:
24/24pass39.233s, ../worker-c-c58-phase-history-check.log.

Next: source retarget/projected windows, remaining defense/respawn transitions,
then Bastion Heavy Sentry capture stance and displacement. Original0016 projected
NPC native recipient is A-owned pending actual mount; no second manager. Keep
remaining Pharah/Torb/Sombra source-consumer audits explicit. Remaining estimate
4–11 C activehours, low confidence. No client/network/publication action.


## C59 retained Heavy support damage keyword compatibility

Root authorized narrow three damage_tags→hit_tags changes in retained
heavy_charge.py impact/crush/finish_frame. Actual Combat._damage uses hit_tags;
source tag expressions and mathematical amounts are unchanged. All three literal
call contracts validate against its current signature, Damage8pass2.473s.

Important boundary correction: this module is NOT the current NPC damage/drive
host. Current BotWorld.modify_health delegates heavy_assault.modify_health and
Combat.advance_heavy_capture drives the existing1407 victim. Sentry proof reads
heavy_charge.active, but no current caller mounts this module's damage/advance
functions. These keyword corrections retain compatible original implementation;
they are not evidence of newly integrated Heavy gameplay or line completion.
Do not add another Heavy pin/force manager to make old names resolve.

Original source two-method Bastion/Heavy probe first ended14.442s with3old
heavy_drives observer errors and4assertion failures. Mapping that observer to
heavy_charge.active was too broad: it includes windup, so its velocity0 vs old
expected-15 does not establish a drive regression. Second probe12.755s records
3such premature velocity failures plus3Trooper melee0 damage failures and original
Heavy Sentry no-displacement failure. These assertions remain unchanged. Actual
regular _interrupt alone preserves configured Sentry mode2 and scalar.8 through
the next pose, so do not globally alter that service to chase oldcapture235.
Logs ../worker-c-c59-bastion-heavy-{baseline,tags}-check.log and reproducer
../parallel-merge/worker-c-bastion-heavy-source-check.py. Source full-module
imports still require old owner interfaces; no fake aliases installed.

Next C60 audit existing heavy_assault/Combat capture and shared physical motion
consumers, with actual drive admission rather than invented heavy_drives views.
Any source support still unmounted remains explicit. C58 phase integration and
C57 wall-source consumers already committed; no live check at this checkpoint.
No client/network/publication action.

## C64 — bounded source-consumer closeout after C63

This audit supersedes the C44 remaining-work inventory above; those historical
estimates and gaps are retained as history, not a current implementation queue.
No production change or new acceptance requirement is introduced by this batch.

00DD original body_scale has four production consumers: Combat quick melee,
target sampling, eye origin, and BodyWorld FDBB. C63 mounts all four on the same
Mover.values/property. The current actual Matrix paths in deflection.matrix_contact,
deflection.matrix_distance and matrix.active_boxes evaluate authored box corners
through their existing state.evaluate; the same BodyWorld FDBB answers their size
expressions. A missing old dva_matrix fixture/module name is not evidence for
another Matrix provider. The original third Matrix scale method remains a
verification tail, not a known missing implementation; it has not been claimed run.

012E Translocator.team is the only missing method name in the compared source
translocators API. Original returns owner.team; the current actual Deployable
constructor stores that same owner.team. AST assignment audit of all ow174 Python
production files found team writes only in Player/Bot constructors (match.py241,
bots.py147 at this revision). Translocators.update retires on owner body/client/
death changes. No same-body production team mutation was identified that would
make this a missing consumer. Keep the existing actor/team/snapshot field rather
than invent a computed-property backing field or another team service.

BodyWorld still contains old optional symmetra_portals fallback names. Actual
1571 delivered placement routes through confirm_placement to fight.portals.confirm,
and create_world_entity to fight.portals.factory; actual1647 relocation first
routes through symmetra.teleport_body. These are the current sole services. The
unreached old fallback name alone does not justify a second portal manager.
Source0016 Mover.body_at remains mapped by symmetra_portals._body_at as recorded
in C63; its independent CharacterBody is a temporary geometric probe only.

Bounded AST API comparisons found no missing top-level/class methods in original
hero_torbjorn/molten_core/torbjorn_charge, symmetra/symmetra_charge/
symmetra_turrets/symmetra_portals, mei/mei_frost/mei_blizzard/mei_ultimate,
sombra_camo/sombra_replay. This is a naming aid, not proof of complete gameplay;
0008/0015 original inline implementations are already recorded by C1–12/C60–61,
and original012E inline lock/charge paths by C33–46. Do not infer module absence
by looking for newly named pharah/bastion/sombra_control modules in frozen trees
that implemented those functions inline in Combat/driver instead.

Root completed original0016 orb/NPC route history in92626c86/mainf0a94cc1:
current scheduled-flight ticks and sole Match driver are now the consumer for original Combat.replays_orb_routes/_step_route_poses/
_fly_omitted_projectiles and original Match.update route consumer. Root has now handed C
the equivalence review of _respawn_owners_before_window/_respawn_retained_owners/
_replace_retained_player/_respawn(allocation_clock) against the current per-frame
loop. C will sync root
route changes before that review. No additional still-unmounted
owner implementation has been established by this bounded audit. Old numeric/
stance/fixture differences, retained assertions and unexecuted client acceptance
are separate from this implementation handoff and must not block submitted code.

Estimated remaining C-side source-boundary review is approximately1–3 active
hours, low confidence; no new implementation hours are assigned without a
concrete source consumer. The retained-respawn review is C
next, with existing allocator and sole respawn loop; no second scheduler.
No tests rerun for this documentation-only batch. C63's16 checks remain the latest
functional evidence. All external input/source refs remain unchanged.


## C60 actual Heavy melee force on configured Bastion

Original0015 test_trooper_and_heavy_melee_hit_each_bastion_form_once_per_press
requires Heavy80*Ironclad-5 damage and negativeZ displacement in each form.
The actual manual BotWorld route intercepted Heavy140E/node5 before the original
heavy_assault.modify_health/_push path, then called generic _radial_impulse.
Its current modifier26 response is0 on configured Sentry, suppressing the source
Heavy force. Original _push constructs the authored vector directly, without
that generic response scalar. Preserve this explicit source mode behavior only:
actualHeavy140E/node5 + actualBastion1216==2 consumes original _push vector through
the already-owned Knockbacks queue at the recipient's next physical action.
Other targets retain _radial_impulse/mod26/Fortify behavior; _push's default
immediate behavior and its other callers remain unchanged. No global mod26 edit,
new driver, impulse pool or helper manager. Correction remains original.

Earlier SET-only diagnosis was an unproven and over-narrow hypothesis. Exact
_has_velocity_set gating still failed the Sentry test; it was removed. Do not
write that Sentry necessarily carries a visible tupleSET as established fact.
The authoritative evidence is original configured-mode force plus the actual
manual branch/mod26 suppression and successful same-existing-queue execution.

New actual producer case verifies original59damage, preserved mode2, negativeZ
movement, stop/no repeat damage and emptied action queue. Final newcase+Damage8:
9/9pass5.219s, ../worker-c-c60-scoped-heavy-impulse-check.log. Original complete
melee method now Heavy Recon/Sentry/Tank3subcases allpass12.193s; only the3known
Trooper25/19/19expected-vs0 failures remain. No original assertion/numeric change.
../worker-c-c60-original-melee-restored.log. Before scoped restoration source had
those3 plusHeavySentry displacement failure (4fail12.146s).

The superseded SET-gated broad run27241 naturally terminated46run45pass1newcase
fail163.658s; it is not claimed fullgreen or current final-suite evidence. No
process was killed: a later exact-command stop attempt found no running matching
process because it had finished. Broad log
../worker-c-c60-final-heavy-impulse-check.log. Initial only-_push edit had8Damage
pass andtheSentryfailure4.132s because the actual manual route bypassed _push.
These diagnostic outcomes are retained, not hidden by replacing assertions.

Next C61: actualTrooper melee target/native consumer (threezero-damage failures),
then Heavy capture mode/timing using the real existing1407/1408 host. Retained
heavy_charge support functions are not a second gameplay driver. Remaining
4–10 C activehours lowconfidence; no client/network/publication action.


## C61 Trooper gate audit and producer regression (no production rewrite)

Actual1422 tracker5 already publishes real375=(Entity(Bastion),), its capsule
candidate search hits and nomination succeeds. Health4 never starts at2m because
its ONLY input edge is38.m_truePlug. Original node38 evaluates real target/self
STU_71A87E92 positions with authored1.5 distance condition. Actual1m yields25
armour-adjusted damage/health4.counter1/gateTrue;1.5/2m yield0/counter0/gateFalse.
Do not bypass that input edge, invoke a fake health state or relax the data.

Frozen0015 runtime.World.position returns Vec3(0,0,0), and its BotWorld lacks the
current explicit1422 real-position consumer. That explains the original2m test
expectation's dependence on a less complete position host; this source limitation
is preserved as evidence, not translated to a new physical-range rule. A confirms
A65 touched only1177/224E LinkedTarget scopes, not general mode3. This was not a
lost linked-array initial notification or missing health driver.

New actual Trooper1m/2m producer regression keeps authored graph unchanged and
verifies real hit counter, damage and no movement; with C60 Heavy test2/2pass
7.571s (../worker-c-c61-trooper-gate-check.log). Original3Trooper25/19/19-vs0
assertion failures at2m remain untouched in the source and documented C60 log.
No production change is needed for this audit. Sync56557266 already brought A's
real NPC projected-recipient host; root requests next sync5ca8f867 with strict
NPCPet boundary before C62 actual projected replay admission.

Root audit also found retained oldprototype _hitscan kwargs in
symmetra_sentry.py:237(source_entity/beam) and torbjorn_turret.py:190(source_entity).
These are not the actual damage consumers: sole current Symm uses
symmetra/symmetra_turrets with native Bots/Vitals; sole Torb uses original
hero_torbjorn/Turret actor path. nodes imports oldTorb HAMMER constant only.
Do not report these prototype kwargs as current gameplay bugs or install duplicate
managers. Root six actual geometry families are now verified readonly under raw
Server identity/splash/hash; no duplicate rerun requested from C.

Next C62 actual NPCProjected frozen-support guard, then Heavycapture source
boundary with real existing drive/1407; estimates4–10 activeC hours lowconfidence.
No client/network/publication action.


## C62 actual projected-NPC frozen-support replay admission

Synced root5ca8f867, including original native NPC recipient and strict Pet
boundary (777e1c9c), into clean tree dfaea595 before this batch. The existing
single-projection proof now identifies actual native NPC recipient/Pet/Vitals.
Ten original projected-talon/Helix setup cases (all5NPC primary/nextNPC receiver,
capacities200/30) proved realrecipientTrue, sourceproofTrue, readonly inputs, but
the outer Combat.frozen_sentry_replay blanket Zarya guard still rejected each.
Baseline ../worker-c-c62-projected-admission-baseline.log (10/10driverFalse).

Narrow only that outer guard to personal actors (recipient isNone). Projected
actors now reach the existing original single-projection/support impact proof;
multiple/unproven projections still decline there. Preserve personal/respawn/
other-defense guards and the same world/NPC driver. No new actor, health pool,
projection manager, scheduler or lifetime. The real native Pet driver alone
handles damage/expiry/removal, including root strict storage changes.

Actual producer comparison then ran each original setup in contiguous and single
accepted gap schedules. All10 cases have EXACTLY equal12-field outcomes:
primary/recipient HP,position,dead;actual samePet pool;support/sentry existence;
ownerHERO_DAMAGE;bothbodyfaultNone. Terminal10sameTrue/no exceptions, log
../worker-c-c62-projected-causal-check.log, reproducer
../parallel-merge/worker-c-projected-support-causal-probe.py. Original setup's
constant Hero maps realbarriers.ZARYA; contact observation maps actual
barriers.authored_for(caster.body).parent/Pet; remaining uses actual.vitals.total
and explicit same pool capacity write. No fake legacy manager or pool view.

This is original-setup plus actual-world causal evidence, NOT a claim that the
entire original huge observation fixture passes. Original full observer still
uses absent oldbody-impact and oldcontact signatures/absolute destruction
expectations; some current stepped outcomes retain support in this complex
projection scene. Keep those boundaries for further source consumer audit.
Normal shared Sentry/nativePet-query/Damage:25/25pass33.732s,
../worker-c-c62-projected-shared-check.log. No source assertion/literal expectation
modified, no client/network/publication action.

Next C63 original projected full-observer/retarget impact consumers and remaining
player/respawn phase windows; Heavy capture still original source boundary.
Remaining estimate4–10activeC hours, lowconfidence. Actor/service uniqueness and
actual current no-fault/causal outcomes take precedence over ancestry/testcounts
when recording integration completeness.


## C63 original00DD body-size implementation and actual consumers

Root's precise source-symbol audit exposed a REAL remaining production gap,
not merely a missing private name. Original00DD Mover.BODY_SIZE_MOD12 and its
modded size branch were absent: scale stand/crouch height/radius/pogo and ground
circle by the source f32 formula, invalid/nonpositive size falls back1. Restore
that branch while preserving current vectorSET/ADD handling and all other mods.
Restore source body_scale=f32(values.stand_height/data.stand_height), a pure
property with no new stored field. Original AND current _capsule use base data;
do not change that physical policy based on a mistaken current-values claim.

Connect all original consumers: Combat player_targets uses sameactualmover.values
and scaled eye; _eye uses same scale; only Mei quick-melee reach/radius scales;
BodyWorld FDBB nonstrict self-owner query returns same property, retaining its
strict/default rules. Current Winston/Ball/critical/head/core/history tail logic
stays in place. No alternate mover, geometry/health pool or scale cache.

Original00DD MeiScale test_scaled_mei_melee_reaches_the_target_and_resets_to_unit_scale
passes unchanged200/170/reset expectations2.332s. Restored tracked tests retain
that complete method plus original scaledGenji real-icicle method; only old
query genji_deflect.active(fight,actor) observation maps existing
 defenses.deflect_state(actor). Both original methods/four scale subcases pass,
plusMoverMod6+Damage8=16/16pass4.959s,
../worker-c-c63-scale-shared-check.log. Original MatrixScale third method stays
in frozen source with its old Player-mech/dva_matrix fixture API; this is not
proof that current actual Matrix scale is missing and does not block this batch.

Other source-name audit resolutions: source0016 Mover.body_at is already mapped
by current symmetra_portals._body_at, which makes an independent TEMPORARY
CharacterBody from the same _capsule/_lift/category/peer_mask/scene and probes
fits/solid pillar placement. It does not allocate a persistent second mover.
Source013B _wall_contacts is replaced by actualice_wall movement-world/mei_wall
constrain; root owns that mapping. Do not resurrect duplicate physics providers.

Heavy capture contract probe is preserved separately: actual1407 creation at
contact changes original012F mode2→0 through its control stacks, then actual
wall hit loses295 versus source0015 prototype235. 1407 writes33/2024/888/7539,
not1216 directly; ordinary_interrupt alone preserves mode2. Both VMs faultNone.
No missing consumer was established there; do not remove1407/stance or add a
snapshot mitigation ledger for the old expectation. Log
../worker-c-c63-bastion-capture-lifecycle-probe.log.

Precise remaining production mapping versus validation: root now OWNS source0016
Combat.replays_orb_routes/_step_route_poses/_fly_omitted_projectiles and Match's
route-history consumer, comparing current _fly retention then reusing the same
existing world/NPC loop. C will not edit that update/Match region concurrently.
Also sent root original retained-respawn methods1855/1873/1890/1902 and update
calls to compare against current ordinary_respawn/update endpoint: an existing
per-frame loop may make them equivalent without another scheduler. That scope
is not merely source-name absence; it needs actual time/identity mapping.

All own core hero modules previously submitted remain mounted on their sole
hosts. Old literal3.2vs3.84, Trooper2m fallback-vsreal1.5gate, Heavy235vs295,
privateprototype/fixture names and unexecuted client groups are verification/
contract differences, not newly invented host work or gates on code submission.
Next C owner audit will report only a concrete still-unmounted source production
consumer, or explicit equivalence, rather than indefinitely chasing old matrices.
No client/network/publication action.
