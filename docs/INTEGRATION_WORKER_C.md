# Integration worker C

Assigned by the user-authorized three-worker merge on 2026-11-12.

- Worktree: `verification/merge-worker-c`; branch: `integration/merge-worker-c`.
- Starting canonical commit: `bf7fad86`.
- Assigned deliveries: 0006 Torbjorn, 0008 Pharah, 0015 Bastion,
  0016 Symmetra, 0065 Junkrat, 00DD Mei, 012E Sombra, 0200 Ashe.
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
