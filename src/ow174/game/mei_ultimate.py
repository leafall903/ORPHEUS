"""Explicit Mei charge reconstruction, not native ultimate-component equivalence.

104319 graphs supply338 cost1610 and064A's342 reset. Five passive points/s,
one point/effective enemy health damage or Cryo self-healing, no barrier or
Blizzard damage credit and death retention are reconstructed policies. Native
coefficients and full active-area policy remain unaccepted.104319 schema07
supplies253C total,32DE combat,3276 passive generation and32E0 spawn-ready.
Names, defaults and scalar bounds are resource-backed; multiplying total by
the specialized scalar and spawn-ready precedence are reconstructed policies.
"""

import math

from ow174.game import mei
from ow174.game.script import expr, nodes

PASSIVE_RATE = 5.0
BASE_COST = 1610.0
DEFAULT_SECONDS = BASE_COST / PASSIVE_RATE
DAMAGE_RATE = HEAL_RATE = 1.0
BLIZZARD_LOADOUT = 0x0B900000000000D5
GENERATION, COMBAT_GENERATION, PASSIVE_GENERATION, SPAWN_READY = 0x253C, 0x32DE, 0x3276, 0x32E0
ULTIMATE_ENABLED = 0x253A


def setting(script, key):
    slot = script.component.entity_var(key, create=False)
    value = slot.value() if slot is not None else None
    return script.world.ruleset(key) if value is None else value


def scalar(script, key):
    value = setting(script, key)
    number = 1. if value is None else expr.to_float(value)
    if not math.isfinite(number):
        number = 1.
    return expr.f32(max(.1 if key == GENERATION else 0., min(5., number)))


def generation(script, *, passive=False):
    if script.hero != mei.HERO:
        return 1.
    branch = PASSIVE_GENERATION if passive else COMBAT_GENERATION
    return expr.f32(scalar(script, GENERATION) * scalar(script, branch))


def spawn_ready(script):
    return script.hero == mei.HERO and expr.truthy(setting(script, SPAWN_READY))


def initialize(script):
    if spawn_ready(script):
        script.set_ult_charge(max(0., expr.to_float(script.component.entity_var(338).value())))


def command_edges(script, edges):
    """Scoped253A reconstruction: block new Q and cancel its unreleased windup.

    Generation is controlled separately. Released projectiles/areas persist;
    stopping a spent windup does not refund the authored charge reset.
    """
    if script.hero != mei.HERO:
        return edges
    value = setting(script, ULTIMATE_ENABLED)
    if value is None or expr.truthy(value):
        return edges
    for ability in script.ultimates:
        ability.presses.clear()
        ability.stop()
    filtered = [(button, pressed) for button, pressed in edges if button != 37]
    if 37 in script.component.held:
        filtered.insert(0, (37, False))
    return filtered


def grant(actor, effective, *, healing=False, loadout=None):
    script = getattr(actor, 'body_script', None)
    if (script is None or getattr(script, 'hero', None) != mei.HERO or effective <= 0
            or script.component.owner_dead or loadout == BLIZZARD_LOADOUT
            or any(s.flags & nodes.Ability.RUNNING for s in script.ultimates)):
        return
    component = script.component
    cost = expr.to_float(component.entity_var(338).value())
    current = expr.to_float(component.entity_var(342).value())
    if cost > 0:
        credit = effective * (HEAL_RATE if healing else DAMAGE_RATE) * generation(script)
        script.set_ult_charge(min(cost, max(0., current) + credit))
