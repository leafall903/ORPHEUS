"""Bounded normal-human grenade consumer for104319, not native effect execution.

0B97/st0 ->04A0267 (.053/0267) ->0C71 ->friendly0B99/enemy0C72.
Those graphs provide100 heal/60 damage,4s,1.5/0 healing modifiers and the
enemy cleanse predicates. The volley also carries50 splash; this reconstruction
selects the effect graph's normal-human value once, rather than adding both.
Native filter flags, AI/mode alternatives, payloads and projectile motion stay
open. This module does not force unknown native predicates to succeed.
"""

HERO = 0x02E000000000013B
GRENADE_EFFECT = 0x04A0000000000267
DAMAGE = 60.0  #0C72/node6 normal branch; node0 is damage kind1
HEAL = 100.0  #0B99/node7 normal branch; node0 is healing kind0
DURATION = 4.0  #both graphs' wait st1
BOOST_DELAY = 0.05  #0B99/st8 before its !8202 switch
BOOST = 1.5  #0B99/node8, combat-mod031A
ANTI = 0.0  #0C72/node17, combat-mod031A (293E exclusion not reconstructed)
CLEANSE_VARS = (6604, 8440, 478, 33551)  #0C72/node8 terminates the hostile graph


def is_ana(actor):
    return getattr(getattr(actor, "hero", None), "guid", None) == HERO

#0BDE/node51 ->224E profile676B. Normal duration includes the final wake phase.
SLEEP_EFFECT = 0x04A000000000026F
SLEEP_DURATION = 5.0  #node51 override215
SLEEP_DAMAGE_GRACE = 0.5  #224E/node166: remaining < initial -0.5
SLEEP_WAKE = 0.5  #224E/node145 remaining, node54 wake animation
