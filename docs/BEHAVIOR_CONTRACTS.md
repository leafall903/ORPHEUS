# Shared Behavior Contracts (`rust`)

ORPHEUS stores language-neutral JSON contracts under `fixtures/behavior/`. Cross-language consumers compare Python (`main`) and Rust (`rust`) through production code paths. Rust-only regression contracts identify their narrower scope explicitly. Public fixtures use synthetic inputs.

The Rust-only `script-actions-v1.json` adds 15 historical assignment/branch cases pinned to `f763beb`. Both `orpheus-statescript` public workspace lanes run `script_init::tests::actions_contract::actions_preserve_the_pinned_synthetic_reference_contract`; it is not registered in the Python/Rust differential job. See the [action development guide](STATESCRIPT_DEVELOPMENT.md) for provenance, current module boundaries and the synthetic benchmark.

The Rust-only [runtime state fixture](../fixtures/behavior/script-runtime-state-metadata-v1.json) checks class-level plain fallback and instance-level client scope through the production loader and VM. Native coverage compares both wire views and preserves codec validation.

The Rust-only [inherited base factory fixture](../fixtures/behavior/script-inherited-base-factory-v1.json) checks the base lifecycle at two new graph placements in both tagged and legacy bundles. It also checks client-only exclusion and diagnostics for an unrelated unknown class.

The Rust-only fixtures for [parent-owned state](../fixtures/behavior/script-parent-owned-state-v1.json), [native direction](../fixtures/behavior/statescript-native-direction-v1.json), and [placement collection](../fixtures/behavior/script-tagged-placement-collection-v1.json) use synthetic graphs or host inputs. They cover child cleanup, pitched direction lookup, and ordered scene placement queries. The [parent-owned duration fixture](../fixtures/behavior/script-parent-owned-duration-v1.json) checks the authored timer and cancellation through the same lifecycle. The tests check unsupported operands and missing host data at the same entry points.

The [meter activity contract](../fixtures/behavior/script-meter-body-activity-v1.json) checks gain and deferred health awards across activity changes. The [owner activity contract](../fixtures/behavior/script-owner-room-activity-v1.json) exercises the native room and body factory at startup, death, and same-wire replacement.

The [synchronous owner halt contract](../fixtures/behavior/owner-synchronous-script-halt-v1.json) triggers a native script fault from defender and sighting messages. It checks room-side effect cleanup, retained health, generation-specific publication, and replacement of older queued health. The owner can retire the script before the actor identity acknowledgement.

The [owner fault shield contract](../fixtures/behavior/owner-fault-temporary-shields-v1.json) covers stacked and depleted temporary grants during owner VM unload. The owner and observers receive explicit clears through the health component. A lost owner update retries the same record. Permanent pool IDs, maximums and current amounts survive the unload.

The Rust-only [base action fixture](../fixtures/behavior/script-base-action-placement-v1.json) runs the same valid continuation at two original placements. It also checks malformed action markers and an invalid remote slot on an action.

The Rust-only [stat action fixture](../fixtures/behavior/script-stat-action-facts-v1.json) exercises relocated single-stat and list forms, the authored continuation, and the distinct owner and service availability checks. It rejects unavailable facts and unsupported field shapes.

The Rust-only [stat state fixture](../fixtures/behavior/script-stat-state-facts-v1.json) checks the lifetime of both validated stat selectors when the bound actor has no player stats record. It covers the distinct stat and combat host queries and rejects unknown stats context and malformed selectors.

The Rust-only [frame wait fixture](../fixtures/behavior/script-frame-wait-v1.json) checks the existing VM clock for literal waits at two original placements and in both server-running scopes. It covers zero duration and rejects malformed durations, rearm and remote bindings.

The Rust-only [class declaration fixture](../fixtures/behavior/script-authored-classes-v1.json) uses two invented graphs to check shared class behavior through the production loader and VM. It runs in both workspace lanes and needs no prepared runtime.

The Rust-only [local message fixture](../fixtures/behavior/script-local-message-v1.json) checks parameter capture, continuation, graph-order delivery and client-only scope through the production VM. It also checks that a malformed receiver binding leaves earlier outputs and the active receiver branch intact. It needs no prepared runtime.

The Rust-only [room message fixture](../fixtures/behavior/script-room-message-v1.json) checks typed admission and payload capture, explicit listener scope, and delivery in the recipient's next VM frame. Native room tests cover sender and target generation checks, shared request order, and next-frame delivery. Portable coverage validates the opt-in native numeric route and ordinary local handling.

The Rust-only [projected message topics fixture](../fixtures/behavior/script-projected-message-topics-v1.json) checks explicit client scope, loading conflicts, ordinary state entry and exit, and server dispatch exclusion. The native test compares owner and observer activation frames and removal deltas with the existing send codec.

The Rust-only [room cue fixture](../fixtures/behavior/script-room-cue-v1.json) covers authored audience selection, actor and mode speakers, and host admission. It also checks that delayed health events skip replaced or departed listeners without dropping other recipients. Native coverage checks selected player generations, late listener death, and reliable notice admission.

The Rust-only [received damage audio fixture](../fixtures/behavior/received-damage-audio-v1.json) follows settled damage through the authored target filter, room routing, and reliable game frame. It verifies the victim connection, body replacement before reliable admission, and the decoded listener and line for a fresh hit.

The Rust-only [confirmed headshot audio fixture](../fixtures/behavior/confirmed-headshot-audio-v1.json) loads a synthetic hurt-volume profile through the selected body template, then traces room hitscan, critical settlement, and decoded reliable delivery to the shooter. Its control rays cover a miss, body damage, and head contact with the weapon's critical multiplier disabled.

The Rust-only [mode cue retry fixture](../fixtures/behavior/mode-cue-retry-v1.json) follows three distinct room eliminations through an authored count and cue thresholds. Two opponents die in the same settlement frame. Nonlethal damage and damage to an already defeated body do not count. Only the selected peer receives the cues. The test decodes the mode speaker, listener, line, and reliable ordinal across the original send, retransmission, and next cue. Duplicate acknowledgements leave the retry count unchanged. The graph and cue identifiers are synthetic.

The Rust-only [timed elimination fixture](../fixtures/behavior/script-timed-elimination-cues-v1.json) exercises an authored opponent filter, count window, and cue thresholds through the production VM. It checks same-frame eliminations, expiry, stale and missing settlement frames, and ineligible damage. Its policy and assets are synthetic.

The [actor voice fixture](../fixtures/behavior/player-voice-v1.json) covers first-playable stimulus selection, skin overrides, and line variation. The Rust stock bot case runs an authored criteria action through the existing room cue queue and checks team and free-for-all audiences.

The [directed room message fixture](../fixtures/behavior/script-room-message-v1.json) also runs through two stock bot VMs. The room test checks typed bindings, delivery on the next receiver frame, isolation from ordinary entries, and a recipient who dies after a message was admitted.

The Rust-only [wheel selection fixture](../fixtures/behavior/wheel-selection-ingress-v1.json) covers typed client input, reliable ordering and retries, possession admission, and an actor replacement with the same wire ID. The prepared 104319 probe checks parameter binding in both authored receivers.

The Rust-only [view tracker fixture](../fixtures/behavior/script-view-targets-v1.json) gives the same native field layout to two synthetic graph placements. It covers ordered output, periodic sampling, a successful empty result, and an unavailable room query. The room test uses a frozen actor snapshot and synthetic sight profiles to check range, aperture, ranking, result limits, and occlusion by walls and actors.

The Rust-only [authored pairs fixture](../fixtures/behavior/script-authored-pairs-v1.json) exercises an asset-keyed table holding an expression-built parameter map. It checks ordered integer keys, typed display-text values, and rejection of malformed expression stacks. The prepared wheel probe checks that the selected action resolves to its own parameter row.

The Rust-only [action texture fixture](../fixtures/behavior/script-asset-texture-v1.json) checks that keys with the same low 16 bits resolve independently. It distinguishes an absent catalog, a missing action in a supplied catalog, and an invalid asset kind.

The Rust-only [health action fixture](../fixtures/behavior/script-health-change-v1.json) checks signed health request admission, continuation, and rejection diagnostics. Room tests cover player and bot settlement, stale body identity, and death and health updates.

The Rust-only [scripted training bot respawn fixture](../fixtures/behavior/training-bot-scripted-respawn-v1.json) checks the ordinary replacement deadline even when authored damage is processed before room combat starts. The [bot respawn delivery fixture](../fixtures/behavior/training-bot-respawn-delivery-v1.json) covers successive replacements behind delayed health or identity acknowledgements, while independent bots continue through their own delivery order.

The Rust-only [stock bot script isolation fixture](../fixtures/behavior/stock-bot-script-isolation-v1.json) checks that a zeroed owner selector is valid and that an unsupported public selector suspends only the affected bot's script projection. Both physical bots remain in the room. The [mode presentation variables fixture](../fixtures/behavior/mode-presentation-variables-v1.json) checks mode selection and preservation of typed values in the mode bootstrap.

The Rust-only [local velocity edit fixture](../fixtures/behavior/script-local-velocity-edit-v1.json) checks authored world-space velocity sets and horizontal caps on a synthetic graph. It rejects unknown channels and priorities. The prepared 104319 Practice Range probe verifies that the stock Combat Roll animation enters both projected streams while its velocity changes are applied.

The Rust-only [combat hurt volumes fixture](../fixtures/behavior/combat-hurt-volumes-v1.json) checks head and body classification, lateral contact, open space, and wall occlusion. Hitscan and projectiles use the same combat shape. Bodies without an optional hurt profile keep the compatibility proxy.

The Rust-only [projectile self-splash fixture](../fixtures/behavior/projectile-self-splash-v1.json) covers the authored scale, visibility checks, and absent configuration. The owner can take splash without blocking their own projectile. A room regression covers health and death feedback.

The Rust-only [combat knockback fixture](../fixtures/behavior/combat-knockback-v1.json) exercises authored ring order, an exclusive outer radius, and the horizontal and upward channels independently of damage. Companion tests cover the authored self-impulse switch and scale, occluded and zero-damage self impulses, a stationary bot's movement, and owner correction with and without an active script. The remaining ring field and original-client movement are still unverified.

The prepared knockback probe evaluates the stock Helix and Particle Cannon profiles with the active mode's actor rules. Some projectile coefficients reference mode rules instead of storing literal values. A pack missing those rules can resolve the coefficients to zero. Use the [mode and actor rules preparation guide](STATESCRIPT_DEVELOPMENT.md#set-mode-and-actor-rules) when preparing the local graph data. The profile probe exercises expression evaluation independently of stock body admission and client movement.

The Rust-only [contact combat fixture](../fixtures/behavior/script-contact-combat-v1.json) runs a linked target volume and damage state at two synthetic graph placements. It checks one settlement per activation and confirms that the same force ring evaluator used by projectiles can reject an impulse without suppressing damage. The room regression checks teammate exclusion, static occlusion, and generation-bound damage and impulse admission.

The Rust-only [projectile delivery fixture](../fixtures/behavior/projectile-alias-release-v1.json) covers staggered acknowledgments for two predictions of one shot. A confirmed prediction is released when the authoritative impact is due. An introduction still in flight can create another confirmation after the original release. Its own acknowledgment permits retirement without repeating the original release. A lost introduction admits no alias. The [projectile expiry fixture](../fixtures/behavior/projectile-expiry-v1.json) drives the authored gravity and detonation switch through the production parser. It checks additive gravity, conditional disable, distance-limited flight, occlusion, and single retirement. Range and lifetime have independent budgets. The final sweep runs only as far as the remaining distance permits. Direct-hit falloff uses the same accumulated distance. [Pinned sweep reference](https://github.com/leafall903/ORPHEUS/blob/a9096f5d5d5ef0d5fe5b48660d9737d26a2029f5/src/ow174/game/combat.py#L9028-L9049).

The Rust-only [posed projectile aim fixture](../fixtures/behavior/projectile-posed-aim-v1.json) runs a single predicted shot through the owner VM with absent aim or either of two literal direct-launch slots. It checks that the supported configurations use the sampled command direction. Unresolved aim channels and spread configs do not fall back to a straight flight. This follows the [pinned Python command-aim path](https://github.com/leafall903/ORPHEUS/blob/a9096f5d5d5ef0d5fe5b48660d9737d26a2029f5/src/ow174/game/combat.py#L6699-L6710). The contract covers server launch direction. Native muzzle and additional aim-channel semantics remain open.

The prepared encrypted ground-shot regression applies the same assertions to the stock Helix and Particle Cannon shots. It follows a confirmed projectile ID through impact retirement, reads all owner health pools from the update, and checks a movement correction in the first eight commands after impact. Room snapshots confirm upward motion. The test does not run the original client.

The Rust-only [creator body fixture](../fixtures/behavior/statescript-creator-body-v1.json) checks the default creator relation on a typed entity operand. It distinguishes explicit absence from an unavailable relation and rejects other relation modes and invalid entity references. The expression passes the selected entity to the host for authoritative lookup. [Pinned source relation reference](https://github.com/leafall903/ORPHEUS/blob/a9096f5d5d5ef0d5fe5b48660d9737d26a2029f5/src/ow174/game/ball_mines.py#L190-L197).

The [glancing wall fixture](../fixtures/behavior/projectile-glancing-wall-v1.json) sweeps a projectile against both sides of a synthetic triangle. The real map query supplies the surface normal for blast visibility. The exposed target receives damage and impulse, and the wall blocks both for the far target. The collision point still determines range and falloff. [Pinned blast visibility reference](https://github.com/leafall903/ORPHEUS/blob/a9096f5d5d5ef0d5fe5b48660d9737d26a2029f5/src/ow174/game/combat.py#L9212-L9220).

The Rust-only [terrain hull fixture](../fixtures/behavior/projectile-terrain-hull-v1.json) routes an authored projectile radius through the room's movement contact world. Contact occurs in the first tick for the sphere and in the second tick with the radius absent. Splash still uses a point ray and reaches only the exposed target. Actor hit radius remains independent. Rooms without contact geometry retain the centreline ray. [Pinned reference sweep](https://github.com/leafall903/ORPHEUS/blob/a9096f5d5d5ef0d5fe5b48660d9737d26a2029f5/src/ow174/game/junkrat_projectile.py#L79-L105).

The Rust-only [terrain rebound fixture](../fixtures/behavior/projectile-terrain-rebound-v1.json) evaluates the authored enable expression, count, and normal and tangential velocity coefficients. It sweeps two synthetic walls through the map index and checks that a nearer actor wins, that the remainder of the tick continues after a rebound, and that rebound travel counts toward the same range limit. A rebound does not terminate the projectile. Missing count or normal restitution retain the existing terminal-contact behavior until their native defaults are established. Native collision hulls, additional surface response fields, and original-client rendering remain open. [Pinned terrain response reference](https://github.com/leafall903/ORPHEUS/blob/a9096f5d5d5ef0d5fe5b48660d9737d26a2029f5/src/ow174/game/junkrat_projectile.py#L91-L137).

The Rust-only [impact-spawn fixture](../fixtures/behavior/projectile-impact-spawn-v1.json) reads the landed entity definition and optional graph from the volley. It moves the existing canister and field configuration to another graph and a different player body. The legacy projection handles its declared resources. A separately attached actor graph remains outside that projection. [Pinned impact-spawn reference](https://github.com/leafall903/ORPHEUS/blob/a9096f5d5d5ef0d5fe5b48660d9737d26a2029f5/src/ow174/game/steel_trap.py#L49-L57).

The Rust-only [unposed owner volley fixture](../fixtures/behavior/owner-unposed-volley-v1.json) checks that a due round with no pose is skipped at its authored time and a later posed round in the same volley still fires and spends ammunition.

The Rust-only [staged nameplate fixture](../fixtures/behavior/npc-nameplate-staged-admission-v1.json) checks admission after the mode and bot body acknowledgments, exactly retried introduction, and lifetime protection for a bot that died before admission.

The Rust-only [combat feedback fixture](../fixtures/behavior/combat-feedback-owner-body-v1.json) checks reliable hit and elimination admission after shooter possession, replacement of the shooter before acknowledgment, and independent target health delivery.

The Rust-only [gameplay defaults fixture](../fixtures/behavior/profile-gameplay-defaults-v1.json) checks login settings with an unsaved, null, or explicitly saved gameplay block. The shared profile fixture continues to exercise the pinned Python baseline.

The Rust-only [NPC script packet fixture](../fixtures/behavior/npc-script-packet-isolation-v1.json) checks that a peer sends reliable messages and NPC script chunks separately, retries a lost chunk, and retains its pending turn after a transport error. Other scene-script chunks can still share a datagram with reliable messages. The scheduling rule follows the [Python implementation and client observation](https://github.com/leafall903/ORPHEUS/commit/858e54dfa43e0e19abd59b07efa548b8270038f0).

The Rust-only [ordinary health pool placement fixture](../fixtures/behavior/script-base-health-pool-placement-v1.json) exercises the same health pool shape at two positions through the VM and Vitals. It covers registration, remaining health on exit, cleanup, and rejection of an unsupported shape.

The Rust-only [health query target fixture](../fixtures/behavior/script-health-target-v1.json) checks current and maximum queries for ordinary health and selected pools. An unbound target yields zero without a host read, including an integer-zero reset in a direct target slot. Missing or malformed operands and bound targets with unavailable vitals retain their diagnostics.
[Pinned selected-pool reference](https://github.com/leafall903/ORPHEUS/blob/c73e5a499ec73d499c97eaae46357af30c9e4b39/src/ow174/game/script/scene.py#L2714-L2724), [pinned ordinary reference](https://github.com/leafall903/ORPHEUS/blob/c73e5a499ec73d499c97eaae46357af30c9e4b39/src/ow174/game/script/scene.py#L2778-L2793)

The Rust-only [button lock fixture](../fixtures/behavior/script-button-lock-v1.json) exercises scoped input suppression and release through the production VM. The native room integration also starts a timed lock through a directed message.

The Rust-only [movement scale fixture](../fixtures/behavior/script-movement-scale-v1.json) checks overlapping movement modifiers, timing and cleanup through the production VM. Native room coverage checks when a message-triggered modifier enters and exits the receiver's movement timeline.

The [horizontal cap fixture](../fixtures/behavior/flashbang-horizontal-cap-v1.json) covers the one-row selector, integer and floating bounds, and unrelated graph placements. Native tests verify that admitted effects are queued for the next movement frame.

The [vertical impulse fixture](../fixtures/behavior/script-vertical-velocity-impulse-v1.json) covers an ordered vertical reset followed by a world-space velocity addition. The VM admits the complete edit before following its continuation. The native mover consumes it at the next pose, lets positive lift leave supported ground, and runs ordinary collision and gravity. The authored row shape was checked against the project's Python reference at `b0e0f09f`.

The [degree trigonometry fixture](../fixtures/behavior/script-degree-trigonometry-v1.json) covers single-precision results, null coercion and vector composition through the expression evaluator. A companion test checks empty stacks and infinite operands. The operator meanings come from the project's Python evaluator at `b0e0f09f`.

The Rust-only [movement edge fixture](../fixtures/behavior/script-movement-edges-v1.json) checks flag writes before takeoff and landing, landing before a renewed takeoff, and event deduplication when the VM samples a retained pose. A renewed takeoff in the air requires a new source movement frame. Unknown movement samples retain a diagnostic.

The Rust-only [stack claim fixture](../fixtures/behavior/script-stack-claim-v1.json) checks priority, ties, scope, dependent values and release through the production VM. The native room regression combines stack admission with a tag filter and verifies the lifetime of the input and movement effects.

The [hardpoint membership fixture](../fixtures/behavior/statescript-hardpoint-membership-v1.json) checks a true or false model fact, an unavailable model, and malformed operands through the StateScript condition. The [owner model fixture](../fixtures/behavior/owner-model-hardpoints-v1.json) checks the same condition on queued selection and skin replacement through the native owner factory. An unavailable model leaves the preceding body and runtime active.

The [tagged target pair fixture](../fixtures/behavior/script-tagged-target-pair-v1.json) exercises literal include and exclude tags, output ordering, and a missing scene query at a synthetic placement.

The [voice projection fixture](../fixtures/behavior/statescript-voice-empty-projection-v1.json) checks lifecycle, owner and observer projection, and rejection of missing host facts or an incompatible codec.

The [linked state fixture](../fixtures/behavior/script-linked-state-lifecycle-v1.json)
covers cross-instance link order, server-only entries, retirement, and invalid types.
The [static model protection fixture](../fixtures/behavior/script-static-model-protection-v1.json)
covers authored slot validation, host admission, and restoration after the last state retires.
The [self shield recharge fixture](../fixtures/behavior/script-self-shield-recharge-v1.json)
covers field-based admission, ticking, rate changes, and retirement.
The [named shield fixture](../fixtures/behavior/script-named-shield-v1.json)
exercises the owner query, lifecycle cleanup, exhaustion of a played
grant and periodic decay with a changing authored rate. Native Vitals
and charge tests compare pool-row aggregation and distinguish decay
from damage that contributes a named-pool loss.
The [immediate self-damage fixture](../fixtures/behavior/script-immediate-self-damage-v1.json)
checks admission at invented positions, native integer rounding, one-shot
execution, the different death rules, and rejection of unreviewed modifiers.
The [mode entity fixture](../fixtures/behavior/script-mode-entity-query-v1.json)
covers available, absent, and unknown mode script context through the owner VM.

The Rust-only [server root allocation fixture](../fixtures/behavior/script-server-root-ids-v1.json) checks root identity, installation order and exhaustion at the boundary of the public and private ranges. Native projection coverage compares owner and observer wire bytes with an active private meter and a public child.

The Rust-only [authored resource meter fixture](../fixtures/behavior/script-authored-meter-v1.json) checks body-specific settings on one shared graph. It exercises settled health events, affiliation filtering, passive gains and saturation in the production loader and VM. The [authored charge route](../fixtures/behavior/script-authored-charge-route-v1.json) checks private mount validation, deferred health events, and owner and observer projection.

The Rust-only charge compatibility contract covers an opt-in mode list,
an ordinary damage receiver mounted as a public root or an existing
native child, an optional ability inhibit, and rejection of conflicting
authored charge configuration. Integration validation is pending.
[Synthetic charge compatibility fixture](../fixtures/behavior/script-charge-compatibility-v1.json)

The Rust-only [local health outcomes fixture](../fixtures/behavior/script-local-health-outcomes-v1.json) checks actual damage from AI callbacks and capped field healing. Room tests also exercise source provenance across body replacement.
The Rust-only [directed charge sender fixture](../fixtures/behavior/script-directed-charge-sender-v1.json) checks that both supported server-only target operands use the same finite numeric delivery route and bind on the receiver's next VM frame.

The [settled bonus fixture](../fixtures/behavior/script-settled-bonus-v1.json) follows actual room healing through a configured directed numeric message and an authored recipient meter. It checks frame order, recipients, capping, same-wire replacement on both sides of request admission, a stale reply to an already replaced source, and awards from fresh events after replacement.

The Rust-only [host health event fixture](../fixtures/behavior/script-host-health-events-v1.json) checks authenticated delivery through the ordinary VM event timeline, typed receipt parameters, and separation from script-sent messages. It distinguishes ordered hit tags, actual named shield losses, explicitly empty lists, and clearing either binding when data is unavailable. The same contract tests the immediate compatibility damage message alongside ordinary messages on the same VM clock. The [settled hit fixture](../fixtures/behavior/script-settled-hit-tags-v1.json) exercises native player and bot impact settlement and the captured tag list. It also checks which target facts survive settlement and how the room handles source and target replacement. Portable coverage checks native queries and direct `EntityMatches` filters with the original target's tags, affiliation and liveness. Native coverage checks delivery after target retirement and distinguishes team modes, free-for-all and unavailable room state. The portable relay cases compare typed source and target parameters, numeric aliases, and independent same-frame messages after replacement. The native tests also capture the targets of healing and damage without impact metadata when the source is a distinct room body. They check capped amounts, unavailable hit tags and source delivery after a scripted death. Named shield coverage compares the actual loss from a weapon impact, the remainder drained by direct damage, an absent list on healing, and a known empty list on ordinary damage.

The Rust-only [mode ruleset fixture](../fixtures/behavior/script-mode-rulesets-v1.json) checks mode isolation, actor overrides, typed values, and explicit null results through the production StateScript loader. The same fixture covers the separate engine settings table, including an omitted table, an empty table, and an absent key. The owner host reads these values through its bound mode.

The Rust-only [radial health fixture](../fixtures/behavior/script-radial-health-v1.json) checks the shared spatial filters, distance falloff, and action continuation. Native room tests cover atomic batch admission and generation checks.

The Rust-only [interval and health fixture](../fixtures/behavior/script-interval-health-v1.json) composes the authored interval and health action. It exercises scheduling, skipped frames, live operands, cancellation and reactivation through the component VM.

The [elapsed wait fixture](../fixtures/behavior/script-elapsed-wait-v1.json) covers duration changes, optional restart, abort and restart, and rejected operands. The [cinematic boundary fixture](../fixtures/behavior/script-cinematic-boundary-v1.json) checks the entry and release frame in owner and observer payloads at a synthetic placement.

The Rust-only [target child fixture](../fixtures/behavior/script-target-children-v1.json) checks target ordering, duplicate removal, child reuse, invalid and unavailable samples, and cleanup on target removal, abort and teardown. A fatal work-budget case also checks that a halted component releases the child's host effect once and discards an already-due timer. The room-actor cases exercise replacement under the same entity ID, cancellation of due timers and queued messages, and recovery after an unavailable identity.

The Rust-only [entity spatial fixture](../fixtures/behavior/statescript-entity-spatial-v1.json) checks authored position and direction reads at two host snapshots. An in-memory VM test also exercises these operands through the ordinary component message queue.

The Rust-only [bound body angle fixture](../fixtures/behavior/script-bound-body-angle-v1.json) checks owner and NPC host reads across modes using the same packed pose samples. It distinguishes an unbound target, an unavailable foreign body, and an out-of-range angle.

The Rust-only [room actor pose fixture](../fixtures/behavior/script-room-actor-pose-v1.json) checks frozen movement, aim, and eye anchors across movement, retirement, death, and generation changes. It also covers the scoped view installed on consecutive replacements in the same room frame.

The [native eye position fixture](../fixtures/behavior/statescript-native-eye-position-v1.json) checks a finite anchor read and unavailable-host diagnostics. The [pose demand fixture](../fixtures/behavior/script-native-pose-demand-v1.json) checks when the loader requests room poses for native expressions.

The Rust-only [owner startup spatial fixture](../fixtures/behavior/script-owner-startup-spatial-v1.json) checks a one-shot query through scene admission before body publication. It covers unavailable context, an available empty room, and another actor's frozen pose with a separate owner command clock.

The Rust-only [owner replacement fixture](../fixtures/behavior/owner-admission-transaction-v1.json) drives the room input queue through a factory error, an invalid runtime identity, an initial snapshot packing error, an observer-only link error, and a successful retry. The observer-only candidate has a valid owner baseline that fits the datagram. The fixture checks the current body and script, controller counters, replication records, and room events while input continues.

The Rust-only [owner fault hitscan fixture](../fixtures/behavior/owner-script-fault-hitscan-v1.json) compares split and batched input across a VM fault and queued replacement. Only completed hitscan steps reach room settlement. The [owner fault vitals fixture](../fixtures/behavior/owner-script-fault-vitals-v1.json) checks cumulative health updates, later healing and death, and body identity after script retirement. Bodies without modeled vitals still leave health queries unavailable.

The Rust-only [hitscan order fixture](../fixtures/behavior/owner-hitscan-before-switch-v1.json) checks that an acknowledged shot settles once before a queued hero or skin replacement, under split and shared input budgets. The [owner death notice fixture](../fixtures/behavior/owner-death-notice-v1.json) checks that a waiting notice stays with the victim generation and that a current death can still enter the reliable queue after mode UI readiness. The room regression uses the shipped respawn rule and verifies a fresh body with full health at the original spawn point. The player delivery regression verifies that dead bodies ignore movement input while still consuming commands.

The Rust-only [Practice Range selection fixture](../fixtures/behavior/practice-owner-selector-v1.json) starts two distinct catalog definitions through the ordinary scene selector and checks their graph roots. It distinguishes an invalid authored definition from an identity absent from the selector.

The Rust-only [Practice Range owner content fixture](../fixtures/behavior/practice-owner-content-v1.json) covers a scene with no owner script inputs. It also verifies explicit startup errors for a partial catalog, including misplaced and malformed owner body declarations.

The Rust-only [reentrant button lock fixture](../fixtures/behavior/script-reentrant-button-lock-v1.json) has a press callback acquire a lock on its own button. The queued lock can take effect before the next branch runs. The nested release leaves the locked listener up and discards its superseded down branch. A same-frame input pulse still runs both branches in sequence when no lock applies.

The Rust-only [buffered button readiness fixture](../fixtures/behavior/script-buffered-button-readiness-v1.json) starts a dormant input listener within its authored buffer. The press can queue a readiness change in time for the down branch to see it. The same queue lets an input lock invalidate that branch.

The Rust-only [clock-driven readiness fixture](../fixtures/behavior/script-clock-driven-readiness-v1.json) checks a BooleanSwitch reading a linked countdown. The ready branch runs when the clock brings the value to zero, even in the absence of an authored variable write.

The Rust-only [static map explosion fixture](../fixtures/behavior/script-static-map-explosion-v1.json) checks the native state lifecycle in a host that confirms the absence of dynamic map rigid bodies. It covers relocated states, authored transitions and unsupported contexts through the production VM.

The [local combat accumulator fixture](../fixtures/behavior/script-local-combat-accumulator-v1.json) checks that the VM keeps the state lifecycle and skips tick operands when the host confirms the context is absent. Available and unknown contexts remain diagnostic.

The Rust-only [player host facts fixture](../fixtures/behavior/script-player-host-facts-v1.json) checks known body and controller presence, unknown entities, the team slot assigned at spawn, and membership counts from the admitted player roster.

The Rust-only [owned entity count fixture](../fixtures/behavior/statescript-owned-entity-count-v1.json) distinguishes an explicitly unbound owner, an unavailable ownership registry, and a known count. The native startup regression uses the production human owner selector and verifies the assignment completes without an AI controller.

The Rust-only [radius query fixture](../fixtures/behavior/statescript-entities-in-radius-v1.json) checks the inclusive boundary, sorting, deduplication, and optional team, directional, and static visibility filtering. Room coverage checks shared per-frame samples in team and free-for-all modes.

The Rust-only [local command fixture](../fixtures/behavior/statescript-local-command-v1.json) checks that a known missing process-local input remains absent across remote owner commands. Unknown host context and malformed operands still yield diagnostics.

The Rust-only [segment query fixture](../fixtures/behavior/statescript-segment-clear-v1.json) checks endpoint validation, evaluation order, work limits, and the distinction between a blocked segment and unavailable geometry.

The Rust-only [static ray fixture](../fixtures/behavior/statescript-ray-cast-v1.json) checks geometry query order and budget, direction normalization, and distinct results for a hit, a miss, and unavailable geometry. Native room coverage uses the existing scene collision query for endpoint hits and bounded traversal.

The Rust-only [actor ray fixture](../fixtures/behavior/statescript-entity-ray-cast-v1.json) checks team and tag selection, stable candidate IDs, budget exhaustion, a zero-distance hit, and unavailable geometry. Native room tests exercise frozen capsules and static occlusion. The Inspector rows verify room and VM frames, source node, actor and wall outcomes, and a temporary outage during held targeting.

The [capsule entry surface fixture](../fixtures/behavior/actor-capsule-entry-surface-v1.json) checks side and cap normals, contained starts, bounded misses and exact endpoint hits. The room actor query carries the selected contact normal from that kernel. The existing expression contract still returns entity, distance and point.

The Rust-only observation sample contract checks repeated successful values,
unavailable geometry, known misses, failure callbacks, and retirement before
callback dispatch.
[Synthetic observation sample fixture](../fixtures/behavior/statescript-observation-sample-v1.json)

The deferred facing cases check that two rays can share one output while
each queued claim initializes from its own sample. They cover direct and
forwarded action links, ordinary reads after the callback, and
cancellation of starts by the source activation or destination state.

The Rust-only held input lifecycle contract verifies automatic listener
registration, branch ownership, and cancellation of sibling restarts
queued during release. It also covers late entry, lock release, owner death
during a timer-only update, and early press ownership after a binding
change. Native room coverage exercises both listener forms with the same
actor ray, movement scope, owner projection and body replacement scenario.
[Synthetic logical input fixture](../fixtures/behavior/script-logical-input-owner-lifecycle-v1.json)

The Rust-only owner instance visibility contract verifies owner and observer
wire frames, resolved stack claims, and child instances.
[Synthetic instance visibility fixture](../fixtures/behavior/script-owner-instance-visibility-v1.json)

The Rust-only [facing claim fixture](../fixtures/behavior/statescript-facing-claim-v1.json) checks live direction updates, exact claim release on finish or abort, and teardown after a VM work-budget fault. Its native NPC scenario checks the ray sampled while a claim is active and the navigation pose published when the claim ends.

The Rust-only [assignment fallback fixture](../fixtures/behavior/script-assignment-client-fallback-v1.json) checks config group selection and flattening through authored client-only wrappers, including opaque client values and scalar defaults.

The Rust-only [room entity filter fixture](../fixtures/behavior/statescript-room-entity-filter-v1.json) checks affiliation, liveness, and tag presence for individual and spatial predicates. Component and room tests cover snapshot recovery and use of shared actor metadata.

The Rust-only [direct actor tag fixture](../fixtures/behavior/actor-direct-target-tags-v1.json) checks that a server predicate with a foreign selector requests tag facts without enabling actor rays. The native room regression covers authored tags, live grants and suppressions, missing actors, and tag facts captured before and after body replacement.

The Rust-only [observation fixture](../fixtures/behavior/script-observe-radius-v1.json) exercises the radius query in an authored state across VM frames and through state activation and abort.

Public synthetic fixtures must never contain game binaries, raw captures, signing keys, or private credentials.

For the proposed private, per-tick Python/Rust gameplay comparison and its evidence limits, see [Golden gameplay traces](GOLDEN_TRACE.md).

## Opt-in Soldier wire reader check

The Sprint HUD and rifle ammo/reload variable check reads Rust-produced prepared Soldier chunks with the Python client-reader model at the same pinned commit as `shared-behavior-contracts`. Supply a clean Python `main` checkout at `b28139d4fa230480c0cea147d369724f97569c30` explicitly; the scripts verify its contents before import and do not download it. These commands use isolated Python and do not require changes to CI:

```sh
# From an existing Python repository containing the pinned commit:
git -C /path/to/python-repository worktree add --detach \
  /path/to/pinned-python-checkout b28139d4fa230480c0cea147d369724f97569c30

# Invented frames only; no prepared runtime needed.
ORPHEUS_PYTHON_REFERENCE=/path/to/pinned-python-checkout \
  python3 -I -S -B -m unittest discover -s scripts/tests -p test_sprint_wire.py -v

# Separately prepared target-tagged 104319 Practice Range runtime required.
python3 -I -S -B scripts/check_sprint_wire.py \
  --python-reference /path/to/pinned-python-checkout --runtime /path/to/runtime
```

Without `ORPHEUS_PYTHON_REFERENCE`, the reader-dependent synthetic tests are explicitly skipped; the standalone validator tests still run. A checkout at the wrong revision or with modified reference sources fails. The prepared check writes private chunks only to a fresh temporary directory, which it removes. It checks the direct owner `BodyStream` rather than queued transport, and does not establish original-client behavior or state-link coverage.

In the prepared schedule, the independent reader finds one parentless rifle graph `0x0254`. Variable `53` is an unbound integer: baseline 30, repeated 30 at tick 188, 29 at tick 189, 16 at tick 278, 30 at tick 347, and 20 at tick 634. Variable `51` is absent until unbound boolean `true` at tick 300, then `false` at tick 393. The check asserts every write and cumulative value across all chunks, including later firing and repeated values; these wire ticks need not coincide with the Rust projection transitions. The pinned `src/ow174/game/script/decode.py` reads full presence and delta mask/named variables in `_instance_vars`, decodes types and bindings in `read_value`/`read_var`, and persists values in `ClientBody.apply`; `graph.py` defines the graph sync-variable entries. In the prepared graph, `53` is an instance sync variable while `51` is read from the named list. A control flips a real encoded ammo payload bit and must fail specifically on `53 writes`; existing Sprint and framing controls remain in place. This is evidence for this owner stream and prepared runtime only, not transport, game behavior, or cross-OS acceptance.

### Synthetic two-instance state-link wire check

[`scripts/tests/test_state_link_wire.py`](../scripts/tests/test_state_link_wire.py) exercises owner and observer two-instance state-link encoding and decoding against the same pinned Python `main` reader checkout (`b28139d4fa230480c0cea147d369724f97569c30`):

```sh
# Python reader synthetic frames and Rust-to-Python state-link chunks (no runtime pack needed):
ORPHEUS_PYTHON_REFERENCE=/path/to/pinned-python-checkout ORPHEUS_STATE_LINK_WIRE_RUST=1 \
  python3 -I -S -B -m unittest discover -s scripts/tests -p test_state_link_wire.py -v

# Without ORPHEUS_PYTHON_REFERENCE (or without ORPHEUS_STATE_LINK_WIRE_RUST=1 for Rust->Python), checks skip cleanly:
env -u ORPHEUS_PYTHON_REFERENCE \
  python3 -I -S -B -m unittest discover -s scripts/tests -p test_state_link_wire.py -v
```

The graphs (`0x0CE0` -> `0x0D03` model) and codec table are synthetic, so no runtime pack is required. Default CI runs only the Rust unit test (`synthetic_two_instance_registered_state_link_encodes_for_owner_and_observer`), while the Python and Rust-to-Python checks are opt-in and skipped without those environment variables. The `ClientCrash` (`0x7FF78AA8385A`) assertions reflect the pinned Python reader model, not a proven original-client crash; the original client was not run.

---

## Cross-Language CI (`shared-behavior-contracts`)

In [`.github/workflows/public-checks.yml`](../.github/workflows/public-checks.yml), the `shared-behavior-contracts` job checks the pinned Python reference against **`rust`'s own `fixtures/behavior/` files**. The Windows default workspace job runs the Rust consumers and checks their reported results. To run the consumers directly:

```powershell
# Python consumer side (run from a main checkout with ORPHEUS_BEHAVIOR_FIXTURES pointed at rust)
python -S -B -m unittest tests.public.test_behavior_contracts tests.public.test_world_query_contracts tests.public.test_profile_settings_contracts -v

# Rust consumer side
cargo test --locked -p orpheus-wire jam_framing::tests::shared_framing_contract_survives_every_fragmentation -- --exact
cargo test --locked -p orpheus --test world_queries
cargo test --locked -p orpheus --test profile_settings
```

### Standard Fixture Schema

| Field | Purpose |
| --- | --- |
| `schema_version` | Contract schema version (`1`) |
| `case_id` | Stable semantic case identifier |
| `target_build` | Client build (`104319`) |
| `source` | Pinned source commit and code paths establishing expected results |
| `evidence` | Evidence category (e.g. `synthetic-reference`) |
| `client_acceptance` | Live client status (`pending` or recorded session notes) |
| `input` | Explicit inputs (hex strings for byte slices) |
| `expected` | Expected output bytes, decoded frames, errors, or state snapshots |

---

## Contract Inventory

### 1. Cross-Language Contracts Executed in Hosted CI

| Fixture | Scope | Consumers |
| --- | --- | --- |
| `jam-framing-v1.json` | JAM stream cipher framing across all byte splits and 1-byte feeding | Python `test_behavior_contracts` + Rust `orpheus-wire` |
| `world-queries-v1.json` | Strict World query semantics (`origin`, `position`, foreign vars, missing vs. null vs. unavailable service) | Python `test_world_query_contracts` + Rust `--test world_queries` |
| `profile-settings-v1.json` | Settings packets (`22200`–`22204`), `+0x130` int32/int64 bounds, save/reload & `20802` wire output | Python `test_profile_settings_contracts` + Rust `--test profile_settings` |

### 2. Synced Python Reference Fixtures from `main`

Mirrored under `fixtures/behavior/` so `rust` maintains a strict superset of `main`:
- **Verified by Python CI in `shared-behavior-contracts`** (Rust consumers tracked in [#61](https://github.com/leafall903/ORPHEUS/issues/61)):
  - `seismic-slam-wave-v1.json` (shockwave reach sampling & candidate dedup)
  - `seismic-slam-placement-v1.json` (airborne selector states & MoveTo handoff)
  - `body-short-action-retry-v1.json` (short frame-action stamps, loss and ACK retirement, captured entity and instance variables after a later callout, and late observer admission)
  - `server-only-movement-v1.json` (channel-2 throttle mods & owner record tail window)
- **Runtime-backed Python fixtures** (verified on `main` via `tests/runtime/`):
  - `player-voice-v1.json`, `wheel-emote-presentation-v1.json`, `wheel-emote-weapon-v1.json`

### 3. Native C++, Room Policy & Preview Contracts

| Fixture | Scope & Consumer |
| --- | --- |
| `owner-first-command-anchor-v1.json` | First command admission with no body, a new initialized body, retransmissions and continuing input history. The native room regression uses the same fallback rule as the frozen Python command queue. |
| `gallery-preview-policy-v1.json` | C++ client relay NPC preview resource fallback & 64-node tree pruning (`python3 -B scripts/check_gallery_preview_policy.py`) |
| `room-isolation-v1.json` | Encrypted worker room fault/panic retirement while sibling rooms stay live (`encrypted_worker_satisfies_room_recovery_contract`) |
| `junkenstein-validation-v1.json` | Normal Cassidy validation overrides (`--junkenstein-validation`) & post-AI immunity damage routing |

### 4. StateScript VM, Combat & Junkenstein Contracts

The Windows default CI job requires these named contracts to pass in the full workspace run. The `main`-compatible subset moved to `tests/public/test_statescript_*.py` under [#61](https://github.com/leafall903/ORPHEUS/issues/61), retiring 19 standalone verifiers. The body-field and spatial-owner-absence Python helpers remain available for the pinned research revision.

- **Portable VM core (`-p orpheus-statescript`, `script_init::tests::*`):** `shared_unkeyed_array_contract`, `shared_corpse_retirement_contract`, `shared_contains_condition_contract`, `shared_voice_lifecycle_contract`, `shared_nested_destination_contract`, `shared_unbound_loadout_action_contract`, `shared_foreign_notification_contract`, `shared_spatial_owner_absence_contract`, `shared_ability_press_window_contract`, `shared_stack_admission_contract`.
- **Portable expressions & foreign reads (`-p orpheus-statescript`, `script_expr::tests::*`, `script_entity_state::tests::*`):** `shared_engine_setting_contract`, `shared_foreign_component_contract`, `shared_velocity_contract`, `shared_total_health_contract`, `shared_hostile_filter_contract`, `shared_combat_target_contract`, `shared_ally_filter_contract`, `shared_native_filter_tag_clauses`, `shared_body_field_contract`, `shared_body_angle_contract`, `shared_flashbang_filter_admission_contract`, `shared_absent_target_filter_contract`.
- **Host/Combat/Junkenstein integrations (`-p orpheus`, `script_init_host_tests::tests::*`, `script_enemy::tests::*`, `linked_aim::tests::*`, `game_bots::tests::*`, `script_player::tests::*`, `junkenstein_voice::tests::*`, `junkenstein_validation::tests::*`):** `shared_charge_contract`, `shared_flashbang_base_state_contract`, `shared_victory_view_route_contract`, `shared_boss_speech_contract`, `shared_captured_aim_numeric_contract`, `shared_captured_aim_lifecycle_contract`, `shared_retired_entity_contract`, `shared_self_damage_clock_contract`, `shared_flashbang_effect_dispatch_contract`, `shared_flashbang_entry_guard_contract`, `shared_flashbang_exclusion_guard_contract`, `shared_flashbang_targeted_message_contract`, `shared_flashbang_velocity_action_contract`, `shared_flashbang_horizontal_motion_contract`, `shared_deadeye_aim_ray_visibility_contract`, `shared_junkenstein_validation_contract`.

*(Note: when running the remaining helper verifier scripts under `scripts/check_*_contract.py` with `--python-reference`, they expect the pinned research revision `e1128509` where their fixtures originated.)*
