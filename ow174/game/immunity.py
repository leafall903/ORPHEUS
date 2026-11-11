"""Scoped zero-duration ability startup and authored immunity phase detection."""

REAPER = 0x02E0000000000002
ZENYATTA = 0x02E0000000000020
MEI = 0x02E00000000000DD
MOIRA = 0x02E00000000001A2
ORISA = 0x02E000000000013E
ZERO_STARTUPS = {
    REAPER: (0x0020, 10, 1),
    ZENYATTA: (0x017B, 28, 13),
    MEI: (0x05FE, 0, 41),
    MOIRA: (0x0F7C, 9, 1),
}


def advance_startup(component):
    """Complete scoped zero-duration startup services and let the graphs own lifetime."""
    if component.strict or component.owner_dead:
        return
    binding = ZERO_STARTUPS.get(component.world.hero)
    if binding is None:
        return
    graph_index, ability_index, startup_index = binding
    for instance in tuple(component.instances.values()):
        if instance.graph.index != graph_index:
            continue
        ability = instance.states.get(ability_index)
        startup = instance.states.get(startup_index)
        if (ability is None or not ability.flags & 1 or startup is None or not startup.active
                or startup.node.cls != 'STU_54FEB21C'):
            continue
        duration = startup.node.field('m_ED97A97C')
        # Cryo-Freeze's authored startup leaves the duration unset.
        if ((graph_index == 0x05FE and duration is None)
                or startup.evaluate(duration, watch=False) == 0):
            startup.request_finish()
            component.run_now()


def wraith_active(owner, entity=None):
    if getattr(getattr(owner, 'hero', None), 'guid', None) != REAPER:
        return False
    script = getattr(owner, 'body_script', None)
    component = getattr(script, 'component', None)
    if (component is None or script.retired or component.owner_dead
            or (entity is not None and component.entity != entity)):
        return False
    for instance in component.instances.values():
        if instance.graph.index != 0x20:
            continue
        ability, phase = instance.states.get(10), instance.states.get(0)
        if ability is not None and ability.flags & 1 and phase is not None and phase.active:
            return True
    return False


def _active_flag(owner, entity, hero, identifier):
    if getattr(getattr(owner, "hero", None), "guid", None) != hero:
        return False
    script = getattr(owner, "body_script", None)
    if (script is None or script.retired or script.component.owner_dead
            or script.component.entity != entity):
        return False
    flag = script.component.entity_var(identifier, create=False)
    return flag is not None and flag.value() is True


def untargetable(owner, entity):
    # Fade's authored 0F7C position 49 exists only within its active phase.
    return wraith_active(owner, entity) or _active_flag(owner, entity, MOIRA, 6604)


def cryo_active(owner, entity):
    """Read Cryo-Freeze's authored entity slot used by charge graph 1537."""
    return _active_flag(owner, entity, MEI, 3692)


def fortify_active(owner, entity):
    """Read the active Fortify Stack from 0CF4 position 17."""
    return _active_flag(owner, entity, ORISA, 9161)


def heavy_carry_active(owner, entity):
    """Read the victim flag written by Heavy Assault's 1407 Stack."""
    script = getattr(owner, "body_script", None)
    if (script is None or getattr(script, "retired", False)
            or script.component.entity != entity or script.component.owner_dead):
        return False
    flag = script.component.entity_var(2024, create=False)
    return flag is not None and flag.value() is True


def _fortify_modifier(owner, entity):
    if getattr(getattr(owner, "hero", None), "guid", None) != ORISA:
        return None
    script = getattr(owner, "body_script", None)
    if (script is None or script.retired or script.component.owner_dead
            or script.component.entity != entity):
        return None
    for instance in script.component.instances.values():
        phase = instance.states.get(4) if instance.graph.index == 0x0CF4 else None
        if phase is not None and phase.active:
            return phase
    return None


def blocks_headshot(owner, entity):
    # Retail Fortify suppresses headshot bonuses. Scope that reconstruction to
    # the active combat modifier while its native field meanings remain open.
    return _fortify_modifier(owner, entity) is not None


def damage_scale(owner, entity):
    """Use Fortify's active 0CF4 combat modifier before pool mitigation."""
    phase = _fortify_modifier(owner, entity)
    if phase is not None:
        modifier = phase.evaluate(phase.node.field("m_50530EAB"), watch=False)
        if type(modifier) in (int, float) and -1.0 <= modifier <= 0.0:
            return 1.0 + modifier
    return 1.0


def damage_immune(owner, entity):
    if untargetable(owner, entity) or cryo_active(owner, entity):
        return True
    if getattr(getattr(owner, "hero", None), "guid", None) != ZENYATTA:
        return False
    script = getattr(owner, "body_script", None)
    if (script is None or script.retired or script.component.owner_dead
            or script.component.entity != entity):
        return False
    for instance in script.component.instances.values():
        phase = instance.states.get(24) if instance.graph.index == 0x017B else None
        # Tag 02EF also occurs on barriers. Use the scoped active State.
        if phase is not None and phase.active:
            return True
    return False
