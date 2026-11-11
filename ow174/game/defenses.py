"""Selected defensive casts, using authored scopes and world-clock lifetimes.

The zero-output startup callbacks and damage/control consumers are explicit
server bridges. They do not assign a generic meaning to 54FEB21C, 9028E035 or
D07EBF01, or implement native cleanse/presentation. Reaper's Wraith also lets
damage rays pass through; Zen and Mei retain their body collision proxies.
"""

import math

from ow174.game import deflection
from ow174.game.script import expr

REAPER, ZENYATTA, MEI = 0x02E0000000000002, 0x02E0000000000020, 0x02E00000000000DD
# Graph, startup state/node, protection state/node, lifetime state/node.
PROFILES = {
    REAPER: (0x0020, 1, 2, 0, 1, 3, 6),
    ZENYATTA: (0x017B, 13, 26, 8, 14, 3, 8),
    MEI: (0x05FE, 41, 64, 17, 31, 6, 13),
}


def _instance(script):
    if script is None or script.retired or script.component.owner_dead:
        return None, None
    profile = PROFILES.get(script.hero)
    if profile is None:
        return None, None
    instance = next((i for i in script.component.instances.values() if i.graph.index == profile[0]), None)
    return instance, profile


def advance_entry(script):
    """Complete only three declared zero-output authoritative startup callbacks."""
    if script.world.match is None or script.hero not in PROFILES:
        return
    instance, profile = _instance(script)
    if instance is None:
        return
    startup = instance.states.get(profile[1])
    if (startup is None or not startup.active or startup.node.pos != profile[2]
            or startup.node.cls != 'STU_54FEB21C'):
        return
    if script.hero == REAPER:
        ability = instance.states.get(10)
        if ability is None or not ability.flags & ability.RUNNING:
            return
    auxiliary = startup.node.field('m_E5790891') or {}
    if (expr.to_float(startup.evaluate(startup.node.field('m_ED97A97C'), watch=False)) != 0
            or any(startup.node.field(name) is not None
                   for name in ('m_7C8AD485', 'm_C7A42E2A', 'm_F8CE386A', 'm_C46403A5'))
            or auxiliary.get('links') or auxiliary.get('m_FF3DAF1E')):
        raise ValueError('unsupported defensive startup callback configuration')
    startup.request_finish()
    if script.hero == REAPER:
        script.component.run_now()
    else:
        instance.update()


def _active(actor, now):
    hero = getattr(getattr(actor, 'hero', None), 'guid', None)
    if hero not in PROFILES:
        return None
    script = getattr(actor, 'body_script', None)
    if script is None or script.hero != hero or script.component.entity != actor.body:
        return None
    instance, profile = _instance(script)
    if instance is None:
        return None
    phase = instance.states.get(profile[3])
    clock = instance.states.get(profile[5])
    if phase is None or phase.node.pos != profile[4] or clock is None or clock.node.pos != profile[6]:
        return None
    if hero == REAPER:
        ability = instance.states.get(10)
        if phase.node.cls != 'STU_9028E035' or ability is None or not ability.flags & ability.RUNNING:
            return None
    elif (phase.node.cls != 'STU_D07EBF01'
          or phase.node.field('m_target') != {'$': 'STU_B5A0CAF0', 'm_EE729DCB': 0}):
        return None
    deadline = deflection.state_deadline(clock)
    return (instance, phase, deadline) if deflection.world_active(phase, now, deadline) else None


def invulnerable(actor, now):
    return _active(actor, now) is not None


def intangible(actor, now):
    return getattr(getattr(actor, 'hero', None), 'guid', None) == REAPER and invulnerable(actor, now)


def healing(actor, now):
    """Declared total-over-duration healing, restricted to the selected active scopes."""
    if getattr(getattr(actor, 'hero', None), 'guid', None) not in (ZENYATTA, MEI):
        return None
    active = _active(actor, now)
    if active is None:
        return None
    instance, phase, deadline = active
    mei = actor.hero.guid == MEI
    node = instance.graph.node(27 if mei else 12)
    if mei:
        state = instance.states.get(node.state)
        if state is None or not state.active:
            return None
        radius, offset = 0.0, (0.0, 0.0, 0.0)
    else:
        tracker = instance.states.get(0)
        if tracker is None or not tracker.active:
            return None
        radius = expr.to_float(tracker.evaluate(tracker.node.field('m_radius'), watch=False))
        position = tracker.evaluate(tracker.node.field('m_05BE62DD'), watch=False)
        if not isinstance(position, expr.Vec3):
            raise ValueError('unsupported Transcendence healing offset')
        offset = (position.x, position.y, position.z)
    amount = expr.to_float(instance.evaluate(node.field('m_modifyHealth')['m_amount']))
    duration = expr.to_float(instance.evaluate(node.field('m_95F1FB0B')))
    if (not all(math.isfinite(v) for v in (amount, duration, radius, *offset))
            or amount < 0 or duration <= 0 or radius < 0):
        raise ValueError('invalid defensive healing profile')
    start = phase.start / 1000
    return start, min(start + duration, deadline), amount / duration, mei, radius, offset


ORISA = 0x02E000000000013E


def fortify(actor, now):
    """Selected 0CF4 body damage modifier and #9161 crowd-control guard.

    Reduction before armour and critical rejection are server projections of
    the authored 0318 filter. Generic filter and cleanse semantics stay open.
    """
    if getattr(getattr(actor, 'hero', None), 'guid', None) != ORISA:
        return None
    script = getattr(actor, 'body_script', None)
    if (script is None or script.retired or script.component.owner_dead
            or script.hero != ORISA or script.component.entity != actor.body):
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0CF4), None)
    if instance is None:
        return None
    phase, clock = instance.states.get(4), instance.states.get(1)
    if (phase is None or phase.node.pos != 10 or phase.node.cls != 'STUStatescriptStateCombatModFilter'
            or phase.node.field('m_60703B1E') != '0x0D80000000000318' or clock is None or clock.node.pos != 6
            or not deflection.world_active(phase, now, deflection.state_deadline(clock))):
        return None
    scale = 1.0 + expr.to_float(phase.evaluate(phase.node.field('m_50530EAB'), watch=False))
    if not math.isfinite(scale) or not 0 <= scale <= 1:
        raise ValueError('invalid Fortify damage modifier')
    marker = script.component.entity_var(9161, create=False)
    return scale, marker is not None and expr.truthy(marker.value())
