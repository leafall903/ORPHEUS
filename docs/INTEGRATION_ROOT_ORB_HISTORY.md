# Existing orb and NPC history integration

Original0016 replays_orb_routes/_fly_omitted_projectiles previously had no
current route consumer: an actual Trooper orb was labelled1272 instead of
before the retained window1209. Restore the source admission and authored
remaining-flight budget (min remaining lifetime, original25s), using the
current shared scheduled-flight tick set rather than a second projectile loop.
All admitted current projectiles and existing world consumers keep the same
clock ordering. Match defers affected route/impulse poses from its endpoint
pass, and Combat invokes the same Match NPC driver at historical flight ticks.
No second NPC, mover, route store, VM or clock field is introduced.

The existing actual NPC script's time-dependent pace must remain sampled along
the admitted route span even if an orb impacts early. Otherwise Assassin
movement stays at its early0.774 multiplier during a bulk tail rather than
following its actual current VM. Retain that bounded route span through the
existing tick loop; ordinary expired flight/contact guards remain unchanged.
The existing route and impulse driver already prevents duplicate per-tick poses.
This port preserves original phase/frost/wall/charge consumers through the
current shared loop. It does not claim the separate retained player-replacement
scope has been integrated; that remains the next source consumer audit.

The complete original route method and20-subcase test are retained. The source
Vitals.last_damage_at observer applies only to current shield-recovery owners;
NPC timing therefore observes actual successful Combat._damage calls instead.
All gameplay numbers/assertions remain. Initial baseline and subsequent logs
are kept externally. After integration the original whole method retains two
Assassin absolute stop assertions: current0.0068813 versus original0. These
also fail in the ordinary paced fixture, so are not removed to obtain green.

A separate observation-only source-setup probe compares all20 actual stepped/
gap pairs, including those absolute-stop cases. All20 pairs match exactly in
contact tick, original120/0 damage, charge, pose, velocity, successful damage
timestamps and HERO_DAMAGE, with actual NPC component faults absent. It stops
before the original postconditions to observe state; it does not replace or
relax the retained original test, and is not counted as whole-source acceptance.
Trooper's four complete original scenarios pass as well. Shared Zarya sparse
flight, damage and runtime17 checks in the earlier18-run pass; the retained
route method contains the remaining two absolute-position failures.

Receipts/probes: parallel-merge/root-orb-route-{baseline,first,trooper,five-npc,
lifecycle,native-pace}-check.log, root-orb-assassin-route-diagnostic.log,
and root-orb-route-causal-probe.py/.log. Original tests use the explicit immutable
0016 npc-catalog-with-movers.json and selected v18 graph environment.
