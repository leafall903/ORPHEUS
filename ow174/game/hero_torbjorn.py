"""Delivered104319 Torbjorn owner consumers, incrementally mounted in the combined host.

Overload is mounted here; turret, hammer and Molten Core families are reconciled
in later batches rather than treated as complete from prototype ancestry.
"""

from ow174.game.script import expr

HERO = 0x02E0000000000006


def preserve_cast_during_control(state):
    """Selected control policy for an already-applied Overload buff.

    1721/19 owns the short cast wait, while3 and its lifetime live outside that
    scope. Stopping the cast skips its normal cooldown path and permits early
    recasting after the buff expires. Preserve that authored path while the
    control host separately suppresses input/movement. Native CC dispatch and
    animation interruption remain unverified; this is a reconstructed policy.
    """
    instance = state.instance
    buff = instance.states.get(3)
    return instance.graph.index == 0x1721 and state.index == 19 and buff is not None and buff.active


def _overload_ability(owner):
    script = owner.body_script
    if script is not None and owner.hero.guid == HERO:
        for instance in script.component.instances.values():
            if instance.graph.index == 0x1721:
                return instance.state(19)
    return None


def overload_cooldown_remaining(owner):
    """Wall time until reuse when replacing this body."""
    ability = _overload_ability(owner)
    if ability is None:
        return 0.0
    instance = ability.instance
    duration = instance.state(1)
    if duration is not None and duration.active:
        # Graph 1721 chases entity var 9143 to zero, then node 39 writes
        # the configured cooldown from instance var 472.
        buff_left = expr.to_float(instance.find_var(expr.ENTITY, 9143).value())
        cooldown = expr.to_float(instance.find_var(expr.INSTANCE, 472).value())
        rate = ability.rate()
        return buff_left + cooldown / (rate if rate > 0 else 1.0)
    rate = ability.cooldown_rate
    return ability.remaining() / (rate if rate > 0 else 1.0)


def set_overload_cooldown(owner, seconds):
    ability = _overload_ability(owner)
    if ability is not None:
        target = expr.lvalue(ability.node.field("m_out_CooldownVar"))
        if target is not None:
            rate = ability.rate()
            ability.instance.write(target, seconds * (rate if rate > 0 else 1.0))


def overload(fight, player, script):
    """Mount authored1721/st3 as temporary armour while its real state is active."""
    from ow174.game import combat

    if player.hero.guid != HERO:
        return
    target = next((t for t in fight._targets() if t.owner is player), None)
    if target is None:
        return
    vitals = fight.vitals_of(target)
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1721), None)
    state = instance.states.get(3) if instance is not None else None
    source, amount = None, 0.0
    if not vitals.dead and state is not None and state.active:
        source = (instance.id, state.counter)
        amount = combat._number(state.evaluate(state.node.field("m_amount"), watch=False), 0.0)
    if vitals.set_over_armour(source, amount):
        fight._sync_body_health(player, vitals)
