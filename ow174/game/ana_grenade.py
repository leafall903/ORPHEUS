"""104319 normal-human 0C71 grenade host values and healing filters."""

from ow174.game.script import expr

HERO = 0x02E000000000013B
EFFECT = 0x04A0000000000267
DAMAGE = 60.0  # 0C72/node6
HEAL = 100.0  # 0B99/node7
DURATION = 4.0  # Both relation graphs' st1 wait
BOOST_DELAY = 0.05  # 0B99/st8
BOOST = 1.5  # 0B99/node8
CLEANSE_VARS = (6604, 8440, 478, 33551)  # 0C72/node8 hostile cleanup predicates


def healing_factor(owner, vitals, now):
    until = vitals.antiheal_until
    if until is not None and now < until:
        script = getattr(owner, "body_script", None)
        if script is None or not any(
            var is not None and expr.truthy(var.value())
            for var in (script.component.entity_var(key, create=False) for key in CLEANSE_VARS)
        ):
            return 0.0
        vitals.antiheal_until = None
    boost = vitals.healing_boost
    return BOOST if boost is not None and boost[0] <= now < boost[1] else 1.0
