"""Scoped consumers of authored Wraith, Fade, Fortify and Cryo-Freeze states.

DATA: graph0020/st1's zero startup parameter; authored main Wait3s, exit0.4s,
cooldown8s and active registered phase st0. RECONSTRUCTED: completing the missing
zero-parameter startup service synchronously, phase damage/control immunity and
projectile pass-through. Fade uses the same scoped startup service at 0F7C/2,
with its own authored phase and timers. Fortify uses its live resource state.
Native startup, presentation, filters, stacking and cleanse remain open.
"""

import math

MEI = 0x02E00000000000DD
MOIRA = 0x02E00000000001A2
ORISA = 0x02E000000000013E
REAPER = 0x02E0000000000002

PHASE_ABILITIES = {REAPER: (0x0020, 10), MOIRA: (0x0F7C, 9)}


def _active_state(owner, hero, graph, index, entity=None):
    if getattr(getattr(owner, "hero", None), "guid", None) != hero:
        return None
    script = getattr(owner, "body_script", None)
    if (script is None or script.retired or script.component.owner_dead
            or (entity is not None and script.component.entity != entity)):
        return None
    for instance in script.component.instances.values():
        state = instance.states.get(index) if instance.graph.index == graph else None
        if state is not None and state.active:
            return state
    return None


def damage_scale(owner, entity):
    fortify = _active_state(owner, ORISA, 0x0CF4, 4, entity)
    if fortify is not None:
        scalar = fortify.evaluate(fortify.node.field("m_50530EAB"), watch=False)
        if type(scalar) in (int, float) and math.isfinite(scalar):
            return max(0.0, min(1.0, 1.0 + scalar))
    return 1.0


def control_immune(owner, entity=None):
    return (phased_out(owner, entity) or cryo_active(owner, entity)
            or _active_state(owner, ORISA, 0x0CF4, 4, entity) is not None)


def advance_startup(component):
    """Complete scoped zero-duration startup services and let the graphs own lifetime."""
    if component.strict or component.owner_dead:
        return
    if component.world.hero == MEI:
        for instance in tuple(component.instances.values()):
            startup = instance.states.get(41) if instance.graph.index == 0x05FE else None
            if startup is not None and startup.active and startup.node.cls == "STU_54FEB21C":
                startup.request_finish()
                component.run_now()
        return
    profile = PHASE_ABILITIES.get(component.world.hero)
    if profile is None:
        return
    graph, ability_state = profile
    for instance in tuple(component.instances.values()):
        if instance.graph.index != graph:
            continue
        ability, startup = instance.states.get(ability_state), instance.states.get(1)
        if (ability is None or not ability.flags & 1 or startup is None or not startup.active
                or startup.node.cls != 'STU_54FEB21C'):
            continue
        if startup.evaluate(startup.node.field('m_ED97A97C'), watch=False) == 0:
            startup.request_finish()
            component.run_now()


def phased_out(owner, entity=None):
    hero = getattr(getattr(owner, "hero", None), "guid", None)
    profile = PHASE_ABILITIES.get(hero)
    if profile is None:
        return False
    graph, ability_state = profile
    phase = _active_state(owner, hero, graph, 0, entity)
    ability = phase.instance.states.get(ability_state) if phase is not None else None
    return ability is not None and bool(ability.flags & 1)


def wraith_active(owner, entity=None):
    return getattr(getattr(owner, "hero", None), "guid", None) == REAPER and phased_out(owner, entity)


def cryo_active(owner, entity=None):
    return _active_state(owner, MEI, 0x05FE, 6, entity) is not None
